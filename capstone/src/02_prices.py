"""Step 2 - build a fresh adjusted-close price cache for every ticker with a directional call.

Why not reuse the DB's price_daily table?  It stops on 2026-03-27 (so the last picks would
have no forward window), it contains corrupted series (negative / all-zero closes) and it
was filled with INSERT OR IGNORE, so later dividend adjustments were never applied.

Sources, in order of preference:
  1. Yahoo Finance (yfinance, auto_adjust=True -> split- and dividend-adjusted closes)
  2. Yahoo under the company's *current* symbol for known ticker changes (SQ -> XYZ ...)
  3. Tiingo adjClose for tickers Yahoo no longer serves (delisted / acquired / bankrupt).
     Without this step the sample silently loses failed companies -> survivorship bias.
  4. The old price_daily table as a last resort (ends 2026-03-27).

Every stage is cached in data/private so the script can be re-run (Tiingo's free tier is
rate-limited to ~50 requests/hour; re-running resumes where it stopped).

Output (private, never published): data/private/prices.pkl  (long: ticker, date, close, source)
                                   data/private/price_coverage.csv
"""
import os, re, sqlite3, sys, time
import pandas as pd
import requests
import yfinance as yf
from common import PRIVATE, PRICE_START, PRICE_END, SOURCE_DB

# Symbols that changed while the company kept trading (old -> current Yahoo symbol).
RENAMED = {"SQ": "XYZ", "FI": "FISV", "NYCB": "FLG", "CEIX": "CNR", "HHC": "HHH",
           "FB": "META", "SVB": "SIVBQ", "BLOCK": "XYZ"}

m = pd.read_csv(PRIVATE / "mentions_raw.csv")
d = m[m["direction"].notna()]
counts = d["ticker"].value_counts()
tickers = sorted(set(counts.index) | {"SPY", "QQQ", "^IRX"})     # ^IRX = 13-week T-bill yield (risk-free rate)
valid = [t for t in tickers if re.fullmatch(r"[A-Z0-9^][A-Z0-9.\-=^]{0,11}", t)]
print(f"{len(tickers)} tickers with a directional call, {len(valid)} syntactically valid")


def tidy(df, source):
    df = df.dropna(subset=["close"]).copy()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df["source"] = source
    return df[["ticker", "date", "close", "source"]]


def yahoo(symbols):
    df = yf.download(symbols, start=PRICE_START, end=PRICE_END, auto_adjust=True,
                     progress=False, threads=True, group_by="column")["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(symbols[0])
    long = df.stack().rename("close").reset_index()
    long.columns = ["date", "ticker", "close"]
    return long


# ---- 1. Yahoo, batched -----------------------------------------------------------------
cache = PRIVATE / "prices_yahoo.pkl"
if cache.exists():
    px = pd.read_pickle(cache)
else:
    frames = []
    for i in range(0, len(valid), 80):
        chunk = valid[i:i + 80]
        for attempt in range(3):
            try:
                frames.append(yahoo(chunk)); break
            except Exception as e:
                print("retry", i, e, file=sys.stderr); time.sleep(5)
        print(f"  yahoo {i + len(chunk)}/{len(valid)}", flush=True)
        time.sleep(1)
    px = tidy(pd.concat(frames, ignore_index=True), "yahoo")
    px.to_pickle(cache)

# ---- 1b/2. single-symbol retry (batch calls drop symbols on transient errors) + renames --
cache = PRIVATE / "prices_yahoo_retry.pkl"
if cache.exists():
    retry = pd.read_pickle(cache)
else:
    frames = []
    have = set(px["ticker"])
    for t in [t for t in valid if t not in have]:
        sym = RENAMED.get(t, t)
        try:
            one = yahoo([sym])
            one["ticker"] = t
            frames.append(tidy(one, "yahoo" if sym == t else f"yahoo:{sym}"))
        except Exception:
            pass
        time.sleep(0.2)
    retry = pd.concat(frames, ignore_index=True) if frames else px.iloc[0:0]
    retry.to_pickle(cache)
px = pd.concat([px, retry], ignore_index=True)
print(f"yahoo: {px['ticker'].nunique()} tickers ({retry['ticker'].nunique()} from retry/renames)")

# ---- 3. Tiingo for what Yahoo does not serve, most-mentioned first -----------------------
cache = PRIVATE / "prices_tiingo.pkl"
tried_file = PRIVATE / "tiingo_tried.txt"
tg = pd.read_pickle(cache) if cache.exists() else px.iloc[0:0]
tried = set(tried_file.read_text().split()) if tried_file.exists() else set()
key = None
if os.path.exists("/root/vetted/.env"):
    for line in open("/root/vetted/.env"):
        if line.startswith("TIINGO_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"')
have = set(px["ticker"])
todo = [t for t in counts.index if t in valid and t not in have and t not in tried
        and re.fullmatch(r"[A-Z]{1,5}(-[AB])?", t)]
for t in todo if key else []:
    r = requests.get(f"https://api.tiingo.com/tiingo/daily/{t}/prices",
                     params={"startDate": PRICE_START, "endDate": PRICE_END, "token": key}, timeout=20)
    if r.status_code == 429:
        print(f"tiingo rate limit reached with {len(todo) - todo.index(t)} tickers left - re-run later")
        break
    tried.add(t)
    rows = r.json() if r.status_code == 200 else []
    if isinstance(rows, list) and rows:
        one = pd.DataFrame(rows)[["date", "adjClose"]].rename(columns={"adjClose": "close"})
        one["ticker"] = t
        tg = pd.concat([tg, tidy(one, "tiingo")], ignore_index=True)
    time.sleep(0.3)
tg.to_pickle(cache)
tried_file.write_text("\n".join(sorted(tried)))
px = pd.concat([px, tg[~tg["ticker"].isin(set(px["ticker"]))]], ignore_index=True)

# ---- 4. last resort: the production DB's own price table --------------------------------
con = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
have = set(px["ticker"])
still = [t for t in valid if t not in have]
if still:
    q = ",".join("?" * len(still))
    db = pd.read_sql(f"SELECT ticker, date, close FROM price_daily WHERE ticker IN ({q})", con, params=still)
    px = pd.concat([px, tidy(db, "vetted_db")], ignore_index=True)

px = px[px["close"] > 0].drop_duplicates(["ticker", "date"]).sort_values(["ticker", "date"]).reset_index(drop=True)
px.to_pickle(PRIVATE / "prices.pkl")
cov = px.groupby("ticker").agg(n=("close", "size"), first=("date", "min"), last=("date", "max"),
                               source=("source", "first")).reset_index()
cov["directional_mentions"] = cov["ticker"].map(counts).fillna(0).astype(int)
cov.to_csv(PRIVATE / "price_coverage.csv", index=False)
print(cov.groupby("source").agg(tickers=("ticker", "size"), mentions=("directional_mentions", "sum")).to_string())
print(f"{len(px):,} price rows | directional mentions without any price: "
      f"{int(counts[~counts.index.isin(cov['ticker'])].sum())} of {int(counts.sum())}")
