import logging
import os
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import db_manager
import channel_fetcher
import extract
import brain
import market_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scanner] %(message)s")

# Benchmark tickers always kept in sync with price_daily.
# SPY = US broad market, QQQ = US tech, EWG = iShares MSCI Germany (DAX proxy).
BENCHMARK_TICKERS = ["SPY", "QQQ", "EWG"]
# VIX stored as "VIX" in price_daily; yfinance needs "^VIX" to download it.
VIX_TICKER = "VIX"
_VIX_YF_SYMBOL = "^VIX"

# Milestone offsets in days. Yearly milestones beyond 2yr are added dynamically
# based on video age in _applicable_milestones().
_MILESTONE_DAYS_BASE = [3, 7, 14, 30, 60, 90, 180]


def _applicable_milestones(upload_date_str):
    """
    Return sorted list of milestone day offsets applicable for this video.
    Includes base milestones plus yearly milestones (365, 730, ...) up to
    ~30 days beyond the video's current age.
    """
    from datetime import date as _date
    upload = datetime.strptime(upload_date_str, "%Y-%m-%d").date()
    age_days = (_date.today() - upload).days
    milestones = list(_MILESTONE_DAYS_BASE)
    year = 1
    while 365 * year <= age_days + 30:
        milestones.append(365 * year)
        year += 1
    return sorted(milestones)


_REC_PRIORITY = {"buy": 4, "watch": 3, "sell": 3, "hold": 2, "reference": 1}

_COMPANY_SKIP_WORDS = {"inc", "corp", "corporation", "ltd", "ag", "se", "plc", "null", "the", "group"}


def _verify_mention(mention, transcript_lower):
    """Return 1 if ticker or company name found in transcript, else 0.
    Uses base ticker only (SAP.DE → sap) to handle exchange-suffix format.
    Skips terms ≤2 chars (ticker) or ≤3 chars (company word) to avoid false positives."""
    ticker = mention.get("ticker", "").lower().lstrip("$")
    base_ticker = ticker.split(".")[0]
    ticker_found = len(base_ticker) > 2 and base_ticker in transcript_lower

    company = mention.get("company_name", "").lower()
    words = [w for w in company.split() if w not in _COMPANY_SKIP_WORDS and len(w) > 3]
    company_found = bool(words) and words[0] in transcript_lower

    return 1 if (ticker_found or company_found) else 0


def _deduplicate_mentions(mentions):
    """
    Merge duplicate tickers returned by the AI for the same video.
    Per ticker: sum mention_count, take max confidence, most specific recommendation,
    keep context from the highest-confidence entry.
    """
    grouped = defaultdict(list)
    for m in mentions:
        grouped[m["ticker"].upper()].append(m)

    result = []
    for ticker, group in grouped.items():
        if len(group) == 1:
            result.append(group[0])
            continue
        best = max(group, key=lambda m: m.get("confidence") or 0)
        merged = dict(best)
        merged["ticker"] = ticker
        merged["mention_count"] = sum(m.get("mention_count", 1) for m in group)
        merged["confidence"] = max((m.get("confidence") or 0) for m in group)
        merged["recommendation"] = max(
            ((m.get("recommendation") or "reference") for m in group),
            key=lambda r: _REC_PRIORITY.get(r, 0),
        )
        result.append(merged)
    return result


# ---------------------------------------------------------------------------
# Phase 1 — Transcript fetch
# Fetches transcripts for new videos and stores them. No analysis.
# ---------------------------------------------------------------------------

_FETCH_WORKERS = int(os.getenv("VETTED_TRANSCRIPT_WORKERS", "4"))  # concurrent TranscriptAPI calls per channel


def _fetch_one(video):
    """Fetch a single transcript. Returns (video, transcript_or_None). Safe to call in a thread."""
    transcript = extract.get_transcript(video["video_id"])
    if transcript and len(transcript) > 80000:
        transcript = transcript[:80000]
    return video, transcript


def _fetch_transcripts_for_channel(channel):
    """
    Fetch and store transcripts for any new videos on this channel.
    Transcript HTTP calls run in parallel; DB writes are serialised on the caller thread.
    Does not run analysis — that is handled by reanalyze_stored_transcripts().
    """
    channel_id   = channel["id"]
    channel_name = channel["name"]

    videos = channel_fetcher.get_recent_videos(channel_id)
    if not videos:
        logging.info("Channel '%s': no recent videos.", channel_name)
        return

    new_videos = [v for v in videos if not db_manager.video_exists(v["video_id"])]
    if not new_videos:
        logging.info("Channel '%s': all videos already in DB.", channel_name)
        return

    fetched = 0
    failed  = 0

    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, v): v for v in new_videos}
        for future in as_completed(futures):
            video, transcript = future.result()
            video_id = video["video_id"]
            if transcript is None:
                logging.info("Video %s: no transcript available.", video_id)
                failed += 1
            else:
                fetched += 1
            db_manager.save_video(
                video_id=video_id,
                channel_id=channel_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=video["title"],
                upload_date=video["published_at"][:10],
                transcript=transcript,
            )

    db_manager.update_channel_last_scanned(channel_id)
    db_manager.increment_transcript_stats(channel_id, fetched + failed, failed)
    logging.info("Channel '%s': %d transcript(s) stored, %d unavailable.", channel_name, fetched, failed)

    # Enrich all new videos with YouTube metadata (description, duration, view/like counts)
    if new_videos:
        meta = channel_fetcher.fetch_video_metadata([v["video_id"] for v in new_videos])
        for vid_id, m in meta.items():
            db_manager.update_video_metadata(
                vid_id,
                description=m["description"],
                duration_seconds=m["duration_seconds"],
                view_count=m["view_count"],
                like_count=m["like_count"],
            )
        logging.info("Channel '%s': metadata enriched for %d video(s).", channel_name, len(meta))


def scan_all_channels():
    """
    Phase 1: fetch and store transcripts for all new videos across all channels.
    Phase 2: run analysis on all stored transcripts that have no mentions yet.
    Called manually via admin UI or POST /admin/scan/all.
    """
    channels = db_manager.get_all_channels()
    logging.info("Transcript fetch starting for %d channel(s).", len(channels))

    for channel in channels:
        _fetch_transcripts_for_channel(channel)

    logging.info("Transcript fetch complete. Starting analysis pass.")
    reanalyze_stored_transcripts()


def scan_single_channel(channel_id):
    """Fetch transcripts + analyze for a single channel. Used by manual trigger routes."""
    channels = db_manager.get_all_channels()
    channel  = next((c for c in channels if c["id"] == channel_id), None)
    if not channel:
        logging.warning("Channel %s not found in DB.", channel_id)
        return
    _fetch_transcripts_for_channel(channel)
    reanalyze_stored_transcripts()


# ---------------------------------------------------------------------------
# Backfill — fetch historical transcripts
# ---------------------------------------------------------------------------

def backfill_channel(channel_id, max_videos=500, date_from=None, date_to=None, remaining=None):
    """
    Fetch and store transcripts for historical videos on a single channel.
    Does not run analysis — call reanalyze_stored_transcripts() afterward.

    remaining: if set, stop after storing this many new transcripts (cross-channel budget).
    Returns the number of videos stored.
    """
    channels = db_manager.get_all_channels()
    channel  = next((c for c in channels if c["id"] == channel_id), None)
    if not channel:
        logging.warning("Backfill: channel %s not found in DB.", channel_id)
        return 0

    channel_name = channel["name"]
    logging.info("Backfill started for '%s' (max %d videos).", channel_name, max_videos)

    videos = channel_fetcher.get_all_videos(
        channel_id, max_videos=max_videos, date_from=date_from, date_to=date_to
    )
    if not videos:
        logging.info("Backfill: no videos found for '%s'.", channel_name)
        return 0

    skipped   = sum(1 for v in videos if db_manager.video_exists(v["video_id"]))
    new_videos = [v for v in videos if not db_manager.video_exists(v["video_id"])]

    # Apply budget cap before fetching
    if remaining is not None:
        new_videos = new_videos[:remaining]

    stored = 0
    failed = 0

    if not new_videos:
        logging.info("Backfill '%s': all %d videos already in DB.", channel_name, skipped)
        db_manager.update_channel_last_scanned(channel_id)
        return 0

    logging.info(
        "Backfill '%s': %d new videos to fetch (%d already in DB).",
        channel_name, len(new_videos), skipped,
    )

    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, v): v for v in new_videos}
        done = 0
        for future in as_completed(futures):
            video, transcript = future.result()
            video_id = video["video_id"]
            done += 1
            if transcript is None:
                logging.info("Backfill [%d/%d] '%s': %s — no transcript.",
                             done, len(new_videos), channel_name, video_id)
                failed += 1
            else:
                logging.info("Backfill [%d/%d] '%s': %s",
                             done, len(new_videos), channel_name, video["title"][:60])
                stored += 1
            db_manager.save_video(
                video_id=video_id,
                channel_id=channel_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=video["title"],
                upload_date=video["published_at"][:10],
                transcript=transcript,
            )

    db_manager.update_channel_last_scanned(channel_id)
    db_manager.increment_transcript_stats(channel_id, stored + failed, failed)
    logging.info(
        "Backfill '%s': %d stored, %d skipped, %d unavailable.",
        channel_name, stored, skipped, failed,
    )

    # Enrich newly stored videos with YouTube metadata
    if new_videos:
        meta = channel_fetcher.fetch_video_metadata([v["video_id"] for v in new_videos])
        for vid_id, m in meta.items():
            db_manager.update_video_metadata(
                vid_id,
                description=m["description"],
                duration_seconds=m["duration_seconds"],
                view_count=m["view_count"],
                like_count=m["like_count"],
            )
        logging.info("Backfill '%s': metadata enriched for %d video(s).", channel_name, len(meta))

    return stored


def backfill_all_channels(max_videos=500, date_from=None, date_to=None, max_new_videos=None):
    """
    Backfill all tracked channels, then run analysis on all stored transcripts.
    max_new_videos: total cap across all channels for this run.
    """
    channels = db_manager.get_all_channels()
    logging.info(
        "Backfill all: starting for %d channel(s)%s.",
        len(channels),
        f" (budget: {max_new_videos} videos)" if max_new_videos else "",
    )
    total_stored = 0

    for channel in channels:
        if max_new_videos is not None and total_stored >= max_new_videos:
            logging.info("Backfill all: budget of %d reached.", max_new_videos)
            break

        if channel.get("skip_backfill"):
            logging.info("Backfill all: skipping '%s' (excluded).", channel["name"])
            continue

        remaining = (max_new_videos - total_stored) if max_new_videos is not None else None
        total_stored += backfill_channel(
            channel["id"],
            max_videos=max_videos,
            date_from=date_from,
            date_to=date_to,
            remaining=remaining,
        )

    logging.info("Backfill all: %d transcript(s) stored. Running analysis.", total_stored)
    reanalyze_stored_transcripts()


# ---------------------------------------------------------------------------
# Phase 2 — Analysis
# Runs on all stored transcripts that have no mentions yet.
# ---------------------------------------------------------------------------

def reanalyze_stored_transcripts(date_from=None, date_to=None, active_only=False):
    """
    Run the extraction pipeline on every video that has a stored transcript
    but zero mentions. No TranscriptAPI calls — reads from DB only.
    Saves mentions, then rebuilds daily_sentiment.

    date_from   — only process videos on or after this date (YYYY-MM-DD)
    date_to     — only process videos on or before this date (YYYY-MM-DD)
    active_only — if True, skip channels with skip_backfill=1
    """
    videos = db_manager.get_videos_with_transcript_no_mentions(
        date_from=date_from, date_to=date_to, active_only=active_only
    )
    logging.info("Analysis: %d video(s) with transcript and no mentions.", len(videos))

    processed     = 0
    total_mentions = 0

    for video in videos:
        video_id   = video["video_id"]
        transcript = video["transcript"]
        title      = video["title"]
        language   = video.get("language", "en")
        upload_date = video["upload_date"]
        channel_id = video["channel_id"]

        raw_mentions, usage = brain.analyze_transcript(transcript, title=title, language=language)
        mentions = _deduplicate_mentions(raw_mentions)

        if not mentions:
            # Only increment the counter when the AI actually ran (input_tokens > 0).
            # Connection errors / parse failures return input_tokens=0 — those should
            # not count as an attempt so the video is retried on the next run.
            if usage.get("input_tokens", 0) > 0:
                db_manager.increment_zero_mention_attempts(video_id)
            logging.info("Analysis %s: no mentions found.", video_id)
            processed += 1
            continue

        transcript_lower = transcript.lower()
        for mention in mentions:
            db_manager.save_mention(
                video_id=video_id,
                ticker=mention["ticker"],
                company_name=mention.get("company_name", "NULL"),
                mention_count=mention.get("mention_count", 1),
                sentiment=mention.get("sentiment", "neutral"),
                confidence=mention.get("confidence", 0.5),
                recommendation=mention.get("recommendation", "reference"),
                context=mention.get("context", ""),
                is_real_stock_mention=True,
                upload_date=upload_date,
                asset_type=mention.get("asset_type", "stock"),
                verified=_verify_mention(mention, transcript_lower),
            )

        db_manager.set_video_config_name(video_id, brain.ACTIVE_CONFIG_NAME)
        processed     += 1
        total_mentions += len(mentions)
        logging.info("Analysis %s: %d mention(s) saved.", video_id, len(mentions))

    _rebuild_daily_sentiment()
    logging.info("Analysis complete: %d video(s) processed, %d mention(s) extracted.", processed, total_mentions)


# ---------------------------------------------------------------------------
# Daily sentiment rebuild
# ---------------------------------------------------------------------------

def _rebuild_daily_sentiment():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    logging.info("Rebuilding daily_sentiment since %s.", since)

    conn = sqlite3.connect(db_manager.DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT m.ticker, m.mention_count, m.sentiment, m.confidence,
               v.channel_id, v.upload_date
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        WHERE v.upload_date >= ?
        """,
        (since,),
    )
    rows = cursor.fetchall()
    conn.close()

    ticker_rows = defaultdict(list)
    for row in rows:
        ticker_rows[row["ticker"]].append(dict(row))

    for ticker, ticker_data in ticker_rows.items():
        total_mentions = sum(r["mention_count"] for r in ticker_data)

        bullish_rows = [r for r in ticker_data if r["sentiment"] == "bullish"]
        bearish_rows = [r for r in ticker_data if r["sentiment"] == "bearish"]
        neutral_rows = [r for r in ticker_data if r["sentiment"] == "neutral"]

        def avg_conf(rows):
            return sum(r["confidence"] for r in rows) / len(rows) if rows else None

        db_manager.upsert_daily_sentiment(
            date=today,
            ticker=ticker,
            total_mentions=total_mentions,
            bullish_count=len(bullish_rows),
            bearish_count=len(bearish_rows),
            neutral_count=len(neutral_rows),
            avg_confidence_bullish=avg_conf(bullish_rows),
            avg_confidence_bearish=avg_conf(bearish_rows),
            avg_confidence_neutral=avg_conf(neutral_rows),
            channels_mentioning=len({r["channel_id"] for r in ticker_data}),
        )

    logging.info("daily_sentiment rebuilt: %d ticker(s) upserted.", len(ticker_rows))


def _benchmark_stale_pairs():
    """
    Return (ticker, start_date) pairs for benchmark tickers (SPY, QQQ, EWG, VIX)
    that are missing from price_daily or haven't been updated today.
    Start date is the earliest video upload_date we have, floored to 2020-01-01.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    earliest = db_manager.get_earliest_upload_date() or "2020-01-01"
    start_date = min(earliest, "2020-01-01")

    pairs = []
    for ticker in BENCHMARK_TICKERS + [VIX_TICKER]:
        latest = db_manager.get_latest_price_date(ticker)
        if latest is None or latest < today:
            pairs.append((ticker, start_date))
    return pairs


def _sync_vix(start_date):
    """
    Download VIX history from yfinance (symbol ^VIX) and store as ticker 'VIX'
    in price_daily. Returns number of rows inserted.

    Handles both old yfinance (plain DataFrame) and new yfinance (MultiIndex
    columns even for single-ticker downloads).
    """
    import yfinance as yf
    import pandas as pd
    try:
        raw = yf.download(_VIX_YF_SYMBOL, start=start_date,
                          auto_adjust=True, progress=False, threads=False)
        if raw is None or raw.empty:
            logging.warning("VIX: no data returned from yfinance.")
            return 0

        # Normalise MultiIndex columns → plain column names.
        # New yfinance wraps even single-ticker downloads in a MultiIndex.
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        if "Close" not in raw.columns:
            logging.warning("VIX: 'Close' column not found in yfinance response.")
            return 0

        close_series = raw["Close"]
        # If still a DataFrame after MultiIndex flatten (shouldn't happen, but guard it)
        if isinstance(close_series, pd.DataFrame):
            close_series = close_series.iloc[:, 0]

        rows = []
        for dt, val in close_series.items():
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if fval != fval:  # NaN check
                continue
            date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
            rows.append((VIX_TICKER, date_str, round(fval, 4)))

        db_manager.bulk_insert_price_daily(rows)
        logging.info("VIX: %d rows inserted.", len(rows))
        return len(rows)
    except Exception as e:
        logging.warning("VIX sync failed: %s", e)
        return 0


def sync_price_daily():
    """
    Fetch and store full daily price history for all tickers in roi_tracking
    that are missing data or haven't been updated today.
    Also always keeps benchmark tickers (SPY, QQQ, EWG, VIX) up to date.

    First run: downloads history for all tickers via yf.download() chunks.
    Subsequent runs: only fetches tickers whose newest price_daily date < today.
    Returns count of rows inserted.
    """
    stale = db_manager.get_tickers_needing_price_sync()

    # Add any benchmark tickers not already in the stale list
    stale_ticker_set = {r["ticker"] for r in stale}
    for ticker, start_date in _benchmark_stale_pairs():
        if ticker not in stale_ticker_set:
            stale.append({"ticker": ticker, "earliest_upload_date": start_date})

    if not stale:
        logging.info("price_daily: all tickers up to date.")
        return 0

    # Split VIX out — it needs the ^VIX yfinance symbol, everything else is standard
    vix_start = None
    regular_pairs = []
    for row in stale:
        if row["ticker"] == VIX_TICKER:
            vix_start = row["earliest_upload_date"]
        else:
            regular_pairs.append((row["ticker"], row["earliest_upload_date"]))

    logging.info("price_daily: syncing %d regular ticker(s)%s.",
                 len(regular_pairs), " + VIX" if vix_start else "")

    inserted = 0
    if regular_pairs:
        inserted += market_data.populate_price_daily(regular_pairs)
    if vix_start:
        inserted += _sync_vix(vix_start)

    logging.info("price_daily: %d row(s) inserted.", inserted)
    return inserted


def fetch_roi_baselines(since=None, date_from=None, date_to=None, channel_ids=None):
    """
    Set price_at_publish for every mention that has no roi_tracking row yet.
    Looks up prices from price_daily (no network calls) — call sync_price_daily()
    first to ensure data is available.
    Returns count of roi_tracking rows created.

    since:       optional ISO-8601 datetime; restricts by analyzed_at (standalone use).
    date_from/date_to: optional YYYY-MM-DD; restricts by upload_date range.
    channel_ids: optional list; restricts to specific channels.
    """
    pending = db_manager.get_mentions_without_roi_baseline(
        since=since, date_from=date_from, date_to=date_to, channel_ids=channel_ids
    )
    if not pending:
        logging.info("ROI baselines: nothing to fetch.")
        return 0

    grouped = defaultdict(list)
    for row in pending:
        grouped[(row["ticker"], row["upload_date"])].append(row["mention_id"])

    logging.info("ROI baselines: %d mention(s) across %d unique ticker/date pair(s).",
                 len(pending), len(grouped))

    created = 0
    for (ticker, upload_date), mention_ids in grouped.items():
        price, price_date = db_manager.get_price_on_or_after(ticker, upload_date)
        if price is None:
            continue

        for mention_id in mention_ids:
            db_manager.save_roi_tracking(
                mention_id, ticker, price,
                upload_date=upload_date,
                price_at_publish_date=price_date,
            )
            created += 1

    logging.info("ROI baselines complete: %d row(s) created.", created)
    return created


def update_rois():
    """
    Backfill roi_7d and roi_30d for all mentions whose milestone dates have passed.
    Looks up prices from price_daily (no network calls) — call sync_price_daily()
    first to ensure data is available.
    Returns (updated_7d, updated_30d) counts.
    """
    pending_7d  = db_manager.get_pending_roi_updates(7)
    pending_30d = db_manager.get_pending_roi_updates(30)
    logging.info("ROI update: %d pending 7d rows, %d pending 30d rows.", len(pending_7d), len(pending_30d))

    updated_7d  = 0
    updated_30d = 0

    for row in pending_7d:
        ticker      = row["ticker"]
        upload_date = row["video_upload_date"]
        baseline    = row["price_at_publish"]
        if not baseline:
            continue
        target_date = (datetime.strptime(upload_date, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        price, price_date = db_manager.get_price_on_or_after(ticker, target_date)
        if price is None:
            continue
        roi = (price - baseline) / baseline * 100
        db_manager.update_roi(row["id"], 7, price, roi, price_date=price_date)
        updated_7d += 1

    for row in pending_30d:
        ticker      = row["ticker"]
        upload_date = row["video_upload_date"]
        baseline    = row["price_at_publish"]
        if not baseline:
            continue
        target_date = (datetime.strptime(upload_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")
        price, price_date = db_manager.get_price_on_or_after(ticker, target_date)
        if price is None:
            continue
        roi = (price - baseline) / baseline * 100
        db_manager.update_roi(row["id"], 30, price, roi, price_date=price_date)
        updated_30d += 1

    logging.info("ROI update complete: %d 7d rows updated, %d 30d rows updated.", updated_7d, updated_30d)
    _update_current_rois()
    return updated_7d, updated_30d


def _update_current_rois():
    """
    Refresh price_current/roi_current for every ticker in roi_tracking.
    Looks up the latest close from price_daily (no network calls).
    One SQL update per ticker covers all rows for that ticker.
    """
    tickers = db_manager.get_all_roi_tickers()
    if not tickers:
        return

    updated = 0
    for ticker in tickers:
        price, price_date = db_manager.get_latest_price(ticker)
        if price is None:
            continue
        db_manager.update_current_roi_for_ticker(ticker, price, price_date)
        updated += 1

    logging.info("Current ROI update complete: %d ticker(s) refreshed.", updated)


def _load_price_cache(tickers):
    """
    Load all price_daily rows for the given tickers into an in-memory dict:
        { ticker: [(date_str, close), ...] }  — sorted ascending by date.

    Called once per update_roi_milestones() run. Replaces ~80k individual
    SQLite queries with a single bulk read + bisect lookups in RAM.
    """
    import bisect
    if not tickers:
        return {}
    conn = sqlite3.connect(db_manager.DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, date, close FROM price_daily "
        f"WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
        list(tickers),
    ).fetchall()
    conn.close()

    cache = {}
    for row in rows:
        t = row["ticker"]
        if t not in cache:
            cache[t] = []
        cache[t].append((row["date"], row["close"]))
    return cache


def _price_on_or_after_cached(cache, ticker, date_str):
    """
    Return (close, date) for the earliest trading day on or after date_str,
    using the in-memory price cache. Returns (None, None) if no data.
    """
    import bisect
    prices = cache.get(ticker)
    if not prices:
        return None, None
    dates = [p[0] for p in prices]
    idx = bisect.bisect_left(dates, date_str)
    if idx >= len(prices):
        return None, None
    return prices[idx][1], prices[idx][0]


def update_roi_milestones():
    """
    Populate roi_milestones for all mentions with a baseline price.

    Optimized: loads all needed price_daily rows into RAM in one query,
    then does bisect lookups instead of per-row SQLite calls.
    Writes are batched in a single bulk INSERT OR REPLACE per run.

    Requires sync_price_daily() to have been called first.
    Returns count of milestone rows upserted.
    """
    pending = db_manager.get_roi_milestone_pending()
    if not pending:
        logging.info("ROI milestones: nothing to update.")
        return 0

    # Collect all tickers we'll need prices for (stocks + benchmarks)
    stock_tickers = {row["ticker"] for row in pending}
    all_tickers = stock_tickers | set(BENCHMARK_TICKERS)
    logging.info("ROI milestones: loading price cache for %d ticker(s)…", len(all_tickers))
    cache = _load_price_cache(all_tickers)

    now = datetime.now(timezone.utc).isoformat()
    batch = []  # (mention_id, milestone_days, price, roi, roi_vs_spy, roi_vs_qqq, roi_vs_ewg, price_date, fetched_at)

    for row in pending:
        mention_id    = row["mention_id"]
        ticker        = row["ticker"]
        upload_date   = row["upload_date"]
        baseline      = row["price_at_publish"]
        existing_days = row["existing_milestone_days"]

        milestones = _applicable_milestones(upload_date)
        new_milestones = [d for d in milestones if d not in existing_days]
        if not new_milestones:
            continue

        # Cache benchmark baseline prices for this video's upload_date
        bm_baselines = {}
        for bm in BENCHMARK_TICKERS:
            p, _ = _price_on_or_after_cached(cache, bm, upload_date)
            bm_baselines[bm] = p

        for days in new_milestones:
            target_date = (
                datetime.strptime(upload_date, "%Y-%m-%d") + timedelta(days=days)
            ).strftime("%Y-%m-%d")

            price, price_date = _price_on_or_after_cached(cache, ticker, target_date)
            if price is None:
                continue

            if not baseline:  # guard against price_at_publish = 0 (bad data)
                continue

            roi = (price - baseline) / baseline * 100

            # Benchmark-relative ROI: stock_roi − benchmark_roi over same window
            roi_vs_spy = roi_vs_qqq = roi_vs_ewg = None
            for bm, attr in [("SPY", "spy"), ("QQQ", "qqq"), ("EWG", "ewg")]:
                bm_base = bm_baselines.get(bm)
                bm_end, _ = _price_on_or_after_cached(cache, bm, target_date)
                if bm_base and bm_end:
                    bm_roi = (bm_end - bm_base) / bm_base * 100
                    rel = round(roi - bm_roi, 4)
                    if attr == "spy":   roi_vs_spy = rel
                    elif attr == "qqq": roi_vs_qqq = rel
                    elif attr == "ewg": roi_vs_ewg = rel

            batch.append((mention_id, days, price, round(roi, 4),
                          roi_vs_spy, roi_vs_qqq, roi_vs_ewg, price_date, now))

    db_manager.bulk_upsert_roi_milestones(batch)
    logging.info("ROI milestones updated: %d rows upserted.", len(batch))
    return len(batch)


_TICKER_META_WORKERS = int(os.getenv("VETTED_TICKER_META_WORKERS", "6"))  # concurrent yfinance .info requests


def fetch_ticker_metadata():
    """
    Fetch sector, industry, market_cap, exchange for tickers missing metadata
    or with stale metadata (>30 days). Uses yfinance .info.

    Parallelized: 6 concurrent HTTP fetches via ThreadPoolExecutor.
    DB writes are done sequentially after all fetches complete — avoids
    any SQLite write contention entirely.

    Returns count of tickers updated.
    """
    import yfinance as yf

    to_fetch = db_manager.get_tickers_needing_meta(max_age_days=30)
    if not to_fetch:
        logging.info("Ticker metadata: all up to date.")
        return 0

    to_fetch = to_fetch[:500]
    logging.info(
        "Ticker metadata: fetching %d ticker(s) with %d workers.",
        len(to_fetch), _TICKER_META_WORKERS,
    )

    def _fetch_one(ticker):
        """Fetch .info for one ticker. Returns dict or None on error."""
        try:
            info = yf.Ticker(ticker).info or {}
            return {
                "ticker":     ticker,
                "sector":     info.get("sector"),
                "industry":   info.get("industry"),
                "market_cap": info.get("marketCap"),
                "exchange":   info.get("exchange"),
            }
        except Exception as e:
            logging.warning("Ticker meta failed for %s: %s", ticker, e)
            return None

    # ── Parallel fetch ────────────────────────────────────────────────────
    results = []
    with ThreadPoolExecutor(max_workers=_TICKER_META_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in to_fetch}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result is not None:
                results.append(result)
            if i % 100 == 0:
                logging.info("Ticker metadata: %d/%d fetched…", i, len(to_fetch))

    # ── Sequential write — zero contention risk ───────────────────────────
    for r in results:
        db_manager.upsert_ticker_meta(
            ticker=r["ticker"],
            sector=r["sector"],
            industry=r["industry"],
            market_cap=r["market_cap"],
            exchange=r["exchange"],
        )

    logging.info("Ticker metadata: %d ticker(s) updated.", len(results))
    return len(results)


def run_full_pipeline(
    date_from=None,
    date_to=None,
    channel_ids=None,
    skip_backfill=False,
    skip_reanalyze=False,
    skip_roi=False,
    skip_ticker_meta=False,
    skip_milestones=False,
    active_only=False,
    status=None,
):
    """
    Chains backfill → reanalyze → ROI baselines → ticker metadata → milestones.
    status: reference to main._pipeline_status dict, updated in-place throughout.
    """
    from datetime import datetime, timezone

    def _set(step, state, detail=""):
        if status and step in status.get("steps", {}):
            status["steps"][step]["state"] = state
            status["steps"][step]["detail"] = detail

    start_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # ── Step 1: Backfill ────────────────────────────────────────────────
        if skip_backfill:
            _set("backfill", "skipped")
        else:
            _set("backfill", "running")
            all_channels = db_manager.get_all_channels()
            targets = (
                [c for c in all_channels if c["id"] in channel_ids]
                if channel_ids else all_channels
            )
            if active_only:
                targets = [c for c in targets if not c.get("skip_backfill")]

            total_stored = 0
            for idx, channel in enumerate(targets, 1):
                _set("backfill", "running",
                     f"{idx}/{len(targets)} channels — {total_stored} stored")
                for attempt in range(2):
                    try:
                        stored = backfill_channel(
                            channel["id"],
                            date_from=date_from,
                            date_to=date_to,
                        )
                        total_stored += stored
                        break
                    except Exception as e:
                        if attempt == 1:
                            logging.warning(
                                "Pipeline backfill: '%s' failed twice — %s",
                                channel.get("name"), e,
                            )

            _set("backfill", "done", f"{len(targets)} channels, {total_stored} transcripts stored")

        # ── Step 2: Reanalyze ───────────────────────────────────────────────
        if skip_reanalyze:
            _set("reanalyze", "skipped")
        else:
            _set("reanalyze", "running")
            reanalyze_stored_transcripts(
                date_from=date_from,
                date_to=date_to,
                active_only=active_only,
            )
            _set("reanalyze", "done")

        # ── Step 3: ROI baselines + milestones ──────────────────────────────
        if skip_roi:
            _set("roi", "skipped")
            _set("milestones", "skipped")
        else:
            _set("roi", "running", "syncing prices…")
            sync_price_daily()
            _set("roi", "running", "fetching baselines…")
            fetch_roi_baselines(date_from=date_from, date_to=date_to, channel_ids=channel_ids)
            _set("roi", "running", "computing ROIs…")
            update_rois()
            _set("roi", "done")

            if skip_milestones:
                _set("milestones", "skipped")
            else:
                _set("milestones", "running")
                n = update_roi_milestones()
                _set("milestones", "done", f"{n} rows upserted")

        # ── Step 4: Ticker metadata ─────────────────────────────────────────
        if skip_ticker_meta:
            _set("ticker_meta", "skipped")
        else:
            _set("ticker_meta", "running")
            n = fetch_ticker_metadata()
            _set("ticker_meta", "done", f"{n} tickers updated")

    except Exception as e:
        logging.error("Pipeline failed: %s", e)
        if status:
            status["error"] = str(e)
    finally:
        if status:
            status["running"] = False
            status["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    scan_all_channels()
