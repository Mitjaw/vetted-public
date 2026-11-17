"""Step 3 - turn 13,530 raw LLM mentions into an analysis-ready set of directional stock calls.

Every filter is applied in sequence and logged (rows before / removed / after) to
results/cleaning_log.json, which feeds the funnel on the site and the JOURNAL.

Outputs
    data/private/calls_clean.csv     analysis set incl. entry dates and forward returns
    data/public/vetted_calls.csv     Kaggle export: own data only (no transcript text, no prices,
                                     no returns)
    results/cleaning_log.json
"""
import json
import numpy as np
import pandas as pd
from common import PRIVATE, PUBLIC, RESULTS, AUDIT

HORIZONS = [30, 90, 180, 365]        # calendar days after entry
MAX_ENTRY_GAP = 5                    # entry must exist within 5 calendar days of the video
MIN_CHANNEL_VIDEOS = 50              # videos with >=1 extracted mention, see JOURNAL
MIN_ENTRY_PRICE = 1.0                # adjusted close on the entry day, USD
IN_DEPTH_MIN_MENTIONS = 4            # ticker named >=4 times in the video -> "in-depth" call

m = pd.read_csv(PRIVATE / "mentions_raw.csv", parse_dates=["upload_date"])
px = pd.read_pickle(PRIVATE / "prices.pkl")
log = []


def step(name, df, keep, why):
    out = df[keep].copy()
    log.append({"step": name, "before": len(df), "removed": int(len(df) - len(out)),
                "after": len(out), "why": why})
    print(f"{name:<34} {len(df):>6} -> {len(out):>6}  (-{len(df) - len(out)})")
    return out


raw_n, raw_videos, raw_channels = len(m), m["video_id"].nunique(), m["channel_id"].nunique()

# 1 -- directional calls only ------------------------------------------------------------
d = step("directional label", m, m["direction"].notna(),
         "reference / hold / watch / conditional labels are not calls a viewer can act on")

# 2 -- label and sentiment must agree ----------------------------------------------------
agree = ((d["direction"] == "buy") & (d["sentiment"] == "bullish")) | \
        ((d["direction"] == "bearish") & (d["sentiment"] == "bearish"))
d = step("label agrees with sentiment", d, agree,
         "e.g. 'buy' with bearish/neutral sentiment: the two LLM fields contradict each other")

# 3 -- single stocks only ----------------------------------------------------------------
# The LLM's asset_type is inconsistent per row (SPY is 'stock' in 40 rows), so each ticker
# gets the majority label across ALL its mentions.
majority = m.groupby("ticker")["asset_type"].agg(lambda s: s.value_counts().index[0])
d["asset_class"] = d["ticker"].map(majority)
d = step("single stocks", d, d["asset_class"] == "stock",
         "ETFs, funds, crypto, commodities and indices are not stock picks (an S&P 500 ETF "
         "has zero excess return over SPY by construction)")

# 4 -- US-listed, USD-quoted -------------------------------------------------------------
d = step("US listing", d, ~d["ticker"].str.contains(r"[.^=]", regex=True),
         "exchange-suffixed tickers (.DE, .L, .PA ...) are quoted in other currencies; "
         "comparing them with SPY would mix in FX moves")

# 5 -- a price series exists -------------------------------------------------------------
have = set(px["ticker"])
no_price = d[~d["ticker"].isin(have)]
d = step("price series found", d, d["ticker"].isin(have),
         "ticker does not resolve at Yahoo, Tiingo or the old price table - mostly garbled "
         "or invented symbols (NVIDIA, FORTNET, PALO ...)")

# 6 -- core channels ---------------------------------------------------------------------
vids = m.groupby("channel")["video_id"].nunique()
core = set(vids[vids >= MIN_CHANNEL_VIDEOS].index)
d = step("core channels", d, d["channel"].isin(core),
         f"channels with fewer than {MIN_CHANNEL_VIDEOS} analysed videos (added in a March "
         "2026 test, or missing channel metadata) say nothing about the channel")

# 7 -- one call per channel, ticker and day ----------------------------------------------
d = d.sort_values(["upload_date", "mention_id"])
d = step("one call per channel/ticker/day", d,
         ~d.duplicated(["channel", "ticker", "upload_date", "direction"]),
         "two videos by the same channel on the same day repeating the same call")

# ---- forward returns -------------------------------------------------------------------
wide = px.pivot(index="date", columns="ticker", values="close").sort_index()
cal = wide.index[wide["SPY"].notna()]                 # NYSE trading calendar
last_day = cal[-1]
last_px = px.groupby("ticker")["date"].max()


def price_on_or_before(series, day):
    s = series.loc[:day].dropna()
    return (s.index[-1], s.iloc[-1]) if len(s) else (pd.NaT, np.nan)


rows = []
for r in d.itertuples():
    s = wide[r.ticker].dropna()
    # Entry = first close strictly AFTER the upload date. Only the date is stored, so a
    # video published after the bell would otherwise be credited with that day's move.
    pos = cal.searchsorted(r.upload_date, side="right")
    entry_day = cal[pos] if pos < len(cal) else pd.NaT
    rec = {"mention_id": r.mention_id, "entry_date": pd.NaT}
    if pd.notna(entry_day) and (entry_day - r.upload_date).days <= MAX_ENTRY_GAP and entry_day in s.index:
        p0, b0, q0 = s[entry_day], wide.at[entry_day, "SPY"], wide.at[entry_day, "QQQ"]
        rec["entry_date"] = entry_day
        rec["entry_px"] = p0
        delisted = last_px[r.ticker] < last_day - pd.Timedelta(days=10)
        rec["delisted_on"] = last_px[r.ticker] if delisted else pd.NaT
        for h in HORIZONS:
            end = entry_day + pd.Timedelta(days=h)
            if end > last_day:
                continue                              # horizon not yet observable
            e_day, _ = price_on_or_before(wide["SPY"], end)
            s_day, p1 = price_on_or_before(s, end)
            b1, q1 = wide.at[e_day, "SPY"], wide.at[e_day, "QQQ"]
            if delisted and s_day < e_day - pd.Timedelta(days=7):
                # Delisted inside the window: take the last traded price and assume the
                # proceeds sit in the benchmark until the horizon ends.
                bL, qL = wide.at[s_day, "SPY"], wide.at[s_day, "QQQ"]
                rec[f"ret_{h}"] = (p1 / p0) * (b1 / bL) - 1
                rec[f"ret_q_{h}"] = (p1 / p0) * (q1 / qL) - 1
            elif s_day < e_day - pd.Timedelta(days=7):
                continue                              # unexplained gap in the series
            else:
                rec[f"ret_{h}"] = rec[f"ret_q_{h}"] = p1 / p0 - 1
            rec[f"spy_{h}"] = b1 / b0 - 1
            rec[f"qqq_{h}"] = q1 / q0 - 1
    rows.append(rec)
ret = pd.DataFrame(rows)
d = d.merge(ret, on="mention_id", how="left")

# 8 -- tradeable on the day after the video ----------------------------------------------
d = step("entry price available", d, d["entry_date"].notna(),
         f"no close within {MAX_ENTRY_GAP} days after the video: not listed yet, already "
         "delisted, or the ticker belongs to a different company at that time")

# 9 -- no sub-dollar shells --------------------------------------------------------------
# Found via an outlier: a bankrupt company quoted at $0.0001-0.0003 produced a "+9,900%"
# 30-day return from tick noise. Below $1 a quote is not a price anyone can trade at size.
d = step("entry price at least $1", d, d["entry_px"] >= MIN_ENTRY_PRICE,
         "penny-stock quotes below $1 (bankrupt shells, OTC leftovers) move in ticks of "
         "hundreds of percent and would dominate every average")

# 10 - at least the 30-day window is observable and physically possible --------------------
ok = d["ret_30"].notna() & (d["ret_30"] > -1)
d = step("30-day return observable", d, ok, "price series breaks off inside the first window")

for h in HORIZONS:
    d[f"ex_{h}"] = d[f"ret_{h}"] - d[f"spy_{h}"]         # excess return vs SPY
    d[f"exq_{h}"] = d[f"ret_q_{h}"] - d[f"qqq_{h}"]      # excess return vs QQQ

# ---- derived fields --------------------------------------------------------------------
d["in_depth"] = d["mention_count"] >= IN_DEPTH_MIN_MENTIONS
d = d.sort_values(["channel", "ticker", "direction", "upload_date"])
prev = d.groupby(["channel", "ticker", "direction"])["upload_date"].shift()
d["days_since_same_call"] = (d["upload_date"] - prev).dt.days
d["is_repeat_90d"] = d["days_since_same_call"] <= 90     # same channel repeated the call within 90d
d = d.sort_values(["upload_date", "mention_id"]).reset_index(drop=True)

keep_private = ["mention_id", "video_id", "channel", "language", "upload_date", "title", "ticker",
                "company_name", "direction", "recommendation", "confidence", "mention_count",
                "in_depth", "verified", "is_repeat_90d", "config_name", "entry_date", "delisted_on"] + \
               [f"{p}_{h}" for h in HORIZONS for p in ("ret", "spy", "qqq", "ex", "exq")]
d[keep_private].to_csv(PRIVATE / "calls_clean.csv", index=False)

# ---- public export: own data only --------------------------------------------------------
pub = d[["video_id", "channel", "language", "upload_date", "ticker", "company_name", "direction",
         "recommendation", "confidence", "mention_count", "in_depth", "is_repeat_90d"]].copy()
pub = pub.rename(columns={"upload_date": "video_date", "recommendation": "llm_label",
                          "confidence": "llm_confidence"})
pub.insert(0, "call_id", range(1, len(pub) + 1))
PUBLIC.mkdir(parents=True, exist_ok=True)
pub.to_csv(PUBLIC / "vetted_calls.csv", index=False)

# ---- summary -----------------------------------------------------------------------------
audit = pd.read_csv(AUDIT / "extraction_audit.csv")
audit["in_clean"] = audit["mention_id"].isin(d["mention_id"])
summary = {
    "raw": {"mentions": raw_n, "videos_with_mentions": raw_videos, "channels": raw_channels},
    "clean": {
        "calls": len(d), "buy": int((d.direction == "buy").sum()),
        "bearish": int((d.direction == "bearish").sum()),
        "videos": int(d.video_id.nunique()), "channels": int(d.channel.nunique()),
        "tickers": int(d.ticker.nunique()),
        "first_video": str(d.upload_date.min().date()), "last_video": str(d.upload_date.max().date()),
        "prices_through": str(last_day.date()),
        "in_depth_buy": int(((d.direction == "buy") & d.in_depth).sum()),
        "delisted_tickers": int(d.loc[d.delisted_on.notna(), "ticker"].nunique()),
        "calls_on_delisted": int(d.delisted_on.notna().sum()),
        "by_horizon": {str(h): int(d[f"ex_{h}"].notna().sum()) for h in HORIZONS},
    },
    "no_price_examples": no_price["ticker"].value_counts().head(15).to_dict(),
    "steps": log,
    "audit_rows_surviving": int(audit["in_clean"].sum()),
}
RESULTS.mkdir(exist_ok=True)
json.dump(summary, open(RESULTS / "cleaning_log.json", "w"), indent=1)
print(json.dumps(summary["clean"], indent=1))
print(d.groupby(["channel", "direction"]).size().unstack(fill_value=0).to_string())
