# Vetted

Vetted scans finance YouTube, pulls out every stock that gets mentioned, and tracks how those picks actually performed. The point is to answer something nobody indexes properly: which finance YouTubers are right, and by how much.

> **Status:** working prototype. About 2,500 videos processed across 26 months of content. The owner dashboard and a read-only consumer app are built. Not launched yet. See [Status & limitations](#status--limitations).

## What it does

- Pulls full transcripts from finance YouTube channels via [TranscriptAPI](https://transcriptapi.com). No scraping, no quota burned on transcripts.
- Runs a three-pass Claude Haiku pipeline over each transcript to pull out every ticker, plus sentiment, a conviction score, a recommendation, and a quote backing it up.
- Tracks ROI for every pick from its publish-date price, at 3, 7, 14, 30, 60, 90, and 180 days, each benchmarked against SPY, QQQ, and a DAX proxy (EWG).
- Ranks channels by how accurate their picks actually were once the returns settled, then surfaces picks from the channels that have been right.

## Screenshots

> Save your screenshots into `docs/screenshots/` and these will render.

Overview, the live database at a glance:
![Home dashboard](docs/screenshots/home.png)

Channel accuracy leaderboard:
![Channel leaderboard](docs/screenshots/channels.png)

Stats, with ROI distribution and monthly mention volume by sentiment:
![Stats](docs/screenshots/stats.png)

## How it works

```
YouTube channels
   ↓
TranscriptAPI        fetch transcript (retry with backoff, cap 80k chars)
   ↓
YouTube Data API     enrich metadata (title, duration, views) in a separate pass
   ↓
brain.py             three-pass Claude Haiku extraction
                       pass 1 (temp 1.0)  discovery: find every vehicle
                       pass 2 (temp 0.7)  analysis: sentiment, conviction, quote
                       pass 3 (temp 0.5)  verify: fix tickers, drop hallucinations
   ↓
ROI engine           yfinance primary, Tiingo fallback
                       baseline = close on publish date
                       milestones at 3 to 180 days, plus benchmark vs SPY/QQQ/EWG
   ↓
SQLite  →  FastAPI + Jinja2 + Chart.js dashboards
           scheduled with APScheduler: daily scan 06:00 UTC, ROI sync every 3h
```

### Why three passes, and why Haiku

One pass either misses tickers or makes them up. So I split it. The first pass runs hot to catch everything, the second works through each ticker carefully, and the third re-reads the transcript to fix mistakes and throw out anything hallucinated. If a pass fails it falls back to a single combined call. There’s also a plain string check that the ticker or company name actually shows up in the transcript.

Picking Haiku wasn’t a guess. The repo has an eval framework (`evals/`) that scores each setup against hand-annotated videos on precision, recall, F1, and F2. I rank on F2, because missing a real pick is worse than flagging one extra to go check.

|Config                           |Precision|Recall|F1   |F2   |Sentiment Acc|
|---------------------------------|---------|------|-----|-----|-------------|
|three_pass **haiku** (production)|0.859    |0.799 |0.827|0.810|0.945        |
|three_pass sonnet                |0.834    |0.814 |0.824|0.818|0.945        |
|two_pass haiku→sonnet            |0.812    |0.794 |0.803|0.797|0.945        |
|single_pass opus                 |0.613    |0.774 |0.659|0.714|0.938        |
|three_pass gemini-2.5-flash      |0.800    |0.083 |0.062|0.073|1.000        |

Three-pass Haiku scores about the same as Sonnet but costs roughly 20x less, which matters a lot when you run it over thousands of videos. Honest caveat: the ground-truth set is only 7 videos, so this is enough to choose a model, not enough to publish as a real accuracy claim.

## Scale

|Metric                        |Value                 |
|------------------------------|----------------------|
|Videos processed              |2,536                 |
|Channels tracked              |20                    |
|Date range                    |March 2024 to May 2026|
|Total stock mentions extracted|13,530                |
|Verified mentions             |11,297 (83%)          |
|Distinct tickers              |~2,000                |
|ROI outcomes settled          |11,699                |
|Daily closing prices cached   |687,737               |

## Tech stack

**Backend:** Python 3.11+, FastAPI, APScheduler (SQLite jobstore), Anthropic SDK (Claude Haiku 4.5)
**Data:** yfinance and Tiingo for prices, TranscriptAPI and YouTube Data API v3 for content, SQLite
**Frontend:** Jinja2, vanilla JS, Chart.js, custom CSS (no framework, no bundler)
**Ops:** bcrypt auth, session middleware, rate limiting, structured JSON logging, `/health` endpoints, scheduled backups

## Running locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY, TRANSCRIPTAPI_KEY, etc.

# Owner dashboard (scans, evals, channels, ROI, exports)
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
# → http://127.0.0.1:8000  (HTTP Basic Auth)
```

## Status & limitations

This is a prototype I built to test an idea, not a finished product. Where it actually stands:

- Not launched. The consumer app, auth, Stripe, and email are all wired up but switched off. No live keys, no users. The product side was never the goal. The pipeline was.
- Small eval set. Seven annotated videos is enough to choose a model, not to make accuracy claims.
- Some early ROI rows are noisy. A few pre-2025 rows have price-scale bugs from an old version of the fetch code. Recent data is clean. I wouldn’t quote one headline ROI number off the whole table without filtering first.

## What’s not in this repo

The pricing and subscription logic, the launch plan, and the live database stay private.

Built solo. The parts worth looking at are the prompt design in `brain.py` and the eval harness in `evals/`.