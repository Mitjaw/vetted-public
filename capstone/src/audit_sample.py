"""Draw the manual-audit sample for extraction quality (seeded, reproducible).

Strata: 70 buy calls with verified=1, 30 buy calls with verified=0, 40 bearish calls.
Writes a PRIVATE review file with transcript windows; the labels I assign by reading them
live in audit/extraction_audit.csv (no transcript text).
"""
import re, sqlite3
import pandas as pd
from common import SOURCE_DB, PRIVATE

m = pd.read_csv(PRIVATE / "mentions_raw.csv")
m = m[m["direction"].notna() & m["asset_type"].eq("stock")]
parts = [m[(m.direction == "buy") & (m.verified == 1)].sample(70, random_state=7),
         m[(m.direction == "buy") & (m.verified == 0)].sample(30, random_state=7),
         m[m.direction == "bearish"].sample(40, random_state=7)]
s = pd.concat(parts).reset_index(drop=True)
s[["mention_id", "direction", "verified"]].to_csv(PRIVATE / "audit_sample_ids.csv", index=False)

con = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
out = []
for i, r in s.iterrows():
    tr = con.execute("SELECT transcript FROM videos WHERE video_id=?", (r.video_id,)).fetchone()[0] or ""
    low = tr.lower()
    hits = []
    ctx = str(r.context).lower()[:40]
    p = low.find(ctx) if len(ctx) > 20 else -1
    if p >= 0:
        hits.append(p)
    name = re.sub(r"[^a-z0-9 ]", " ", str(r.company_name).lower()).split()
    keys = [k for k in ([name[0]] if name else []) + [str(r.ticker_raw).lower()] if len(k) >= 3]
    for k in keys:
        for mm in re.finditer(r"\b" + re.escape(k), low):
            if all(abs(mm.start() - h) > 900 for h in hits):
                hits.append(mm.start())
            if len(hits) >= 3:
                break
    if not hits:                                   # fall back to any later name token / spelled ticker
        alt = [k for k in name[1:] if len(k) >= 5 and k not in ("corporation", "holdings", "company", "group", "technologies")]
        alt.append(" ".join(str(r.ticker_raw).lower()))
        for k in alt:
            p = low.find(k)
            if p >= 0:
                hits.append(p); break
    n_win = 1 if (p >= 0 and len(hits) == 1) or len(hits) == 1 else 2
    wins = [tr[max(0, h - 350):h + 550].replace("\n", " ") for h in hits[:2]]
    out.append(f"### {i} | id={r.mention_id} | {r.direction} ver={r.verified} | {r.ticker_raw} / {r.company_name} "
               f"| rec={r.recommendation} conf={r.confidence} n={r.mention_count}\n"
               f"CH: {r.channel} | {r.upload_date} | TITLE: {r.title}\nCTX: {r.context}\n"
               + "\n".join(f"W{j}: {w}" for j, w in enumerate(wins)) + ("\n(no transcript hit)" if not wins else ""))
(PRIVATE / "audit_review.txt").write_text("\n\n".join(out))
print(len(s), "sampled;", sum("(no transcript hit)" in o for o in out), "without transcript hit")
