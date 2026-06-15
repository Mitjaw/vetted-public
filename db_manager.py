import os
import sqlite3
import json
from datetime import datetime, timezone, date, timedelta

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vetted.db")


# ---------------------------------------------------------------------------
# Chart gap-filling helpers
# ---------------------------------------------------------------------------

def _fill_weekly_gaps(rows):
    """
    Given weekly-bucketed rows (each must have 'week' and 'week_start' keys),
    insert zero rows for every missing week between the first and last data point.
    Prevents Chart.js from drawing diagonal lines across empty periods.
    """
    if len(rows) <= 1:
        return rows
    by_week = {r["week"]: r for r in rows}
    numeric_keys = [k for k in rows[0] if k not in ("week", "week_start") and isinstance(rows[0][k], (int, float))]
    result = []
    current = date.fromisoformat(rows[0]["week_start"])
    last = date.fromisoformat(rows[-1]["week_start"])
    while current <= last:
        week_key = current.strftime("%Y-%W")
        if week_key in by_week:
            result.append(by_week[week_key])
        else:
            zero_row = {"week": week_key, "week_start": current.isoformat()}
            for k in numeric_keys:
                zero_row[k] = 0
            result.append(zero_row)
        current += timedelta(weeks=1)
    return result


def _fill_daily_gaps(rows, date_key="date"):
    """
    Given daily rows (each must have a date field named date_key),
    insert zero rows for every missing day between first and last data point.
    """
    if len(rows) <= 1:
        return rows
    by_date = {r[date_key]: r for r in rows}
    numeric_keys = [k for k in rows[0] if k != date_key and isinstance(rows[0][k], (int, float))]
    result = []
    current = date.fromisoformat(rows[0][date_key])
    last = date.fromisoformat(rows[-1][date_key])
    while current <= last:
        d = current.isoformat()
        if d in by_date:
            result.append(by_date[d])
        else:
            zero_row = {date_key: d}
            for k in numeric_keys:
                zero_row[k] = 0
            result.append(zero_row)
        current += timedelta(days=1)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_conn():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db():
    """Create all tables if they don't exist."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # -- channels ------------------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT,
            language TEXT,
            added_at TEXT,
            last_scanned TEXT
        )
        """
    )

    # -- videos --------------------------------------------------------------
    # Extended to include channel_id, transcript_language, analyzed_at while
    # retaining the legacy columns (channel_handle, buy_signals_json) so that
    # save_video_data / get_cached_video continue to work unchanged.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT,
            url TEXT,
            title TEXT,
            upload_date TEXT,
            transcript TEXT,
            transcript_language TEXT,
            analyzed_at TEXT,
            channel_handle TEXT,
            buy_signals_json TEXT,
            FOREIGN KEY (channel_id) REFERENCES channels(id)
        )
        """
    )

    # -- mentions ------------------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            ticker TEXT,
            company_name TEXT,
            mention_count INTEGER,
            sentiment TEXT,
            confidence REAL,
            recommendation TEXT,
            context TEXT,
            is_real_stock_mention INTEGER,
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        )
        """
    )

    # Migrate: add upload_date to mentions if not present
    try:
        cursor.execute("ALTER TABLE mentions ADD COLUMN upload_date TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate: asset_type on mentions
    try:
        cursor.execute("ALTER TABLE mentions ADD COLUMN asset_type TEXT DEFAULT 'stock'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate: verified — 1=found in transcript, 0=not found, NULL=legacy/unknown
    try:
        cursor.execute("ALTER TABLE mentions ADD COLUMN verified INTEGER DEFAULT NULL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Idempotency guard: one row per (video_id, ticker, asset_type).
    # Reanalysis re-runs were previously additive — this index makes
    # save_mention's INSERT OR IGNORE a no-op for already-extracted picks.
    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mentions_unique "
            "ON mentions(video_id, ticker, COALESCE(asset_type, 'stock'))"
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        # Existing duplicates would block index creation — log and continue.
        # Production data has zero dupes (verified 2026-04-28).
        logging.warning("idx_mentions_unique not created: %s", exc)

    # Migrate: transcript health tracking + backfill exclusion on channels
    for col, definition in [
        ("transcript_attempts", "INTEGER DEFAULT 0"),
        ("transcript_failures", "INTEGER DEFAULT 0"),
        ("skip_backfill",       "INTEGER DEFAULT 0"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE channels ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # Backfill upload_date from the videos table for existing rows
    cursor.execute("""
        UPDATE mentions SET upload_date = (
            SELECT v.upload_date FROM videos v WHERE v.video_id = mentions.video_id
        ) WHERE upload_date IS NULL
    """)
    conn.commit()

    # Migrate: video metadata enrichment columns
    for col, definition in [
        ("description",           "TEXT"),
        ("duration_seconds",      "INTEGER"),
        ("view_count",            "INTEGER"),
        ("like_count",            "INTEGER"),
        ("metadata_fetched_at",   "TEXT"),
        # tracks how many times this video was analyzed and returned 0 mentions
        # so the pipeline can skip persistent zero-mention videos after N attempts
        ("zero_mention_attempts", "INTEGER DEFAULT 0"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE videos ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # -- roi_tracking --------------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS roi_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mention_id INTEGER,
            ticker TEXT,
            price_at_publish REAL,
            price_7d REAL,
            price_30d REAL,
            roi_7d REAL,
            roi_30d REAL,
            last_updated TEXT,
            FOREIGN KEY (mention_id) REFERENCES mentions(id)
        )
        """
    )

    # Migrate: per-price fetch timestamps and actual trading dates on roi_tracking
    for col, definition in [
        ("price_at_publish_fetched_at", "TEXT"),
        ("price_7d_fetched_at",         "TEXT"),
        ("price_30d_fetched_at",        "TEXT"),
        # upload_date denormalized here so ROI rows are self-contained
        ("upload_date",                 "TEXT"),
        # actual trading day used for each price (may differ from upload_date/milestones
        # when publish falls on a weekend or holiday — the next trading day is used)
        ("price_at_publish_date",       "TEXT"),
        ("price_7d_date",               "TEXT"),
        ("price_30d_date",              "TEXT"),
        # current price — updated each time update_rois() runs
        ("price_current",               "REAL"),
        ("roi_current",                 "REAL"),
        ("price_current_date",          "TEXT"),
        ("price_current_fetched_at",    "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE roi_tracking ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # Back-fill upload_date on existing roi_tracking rows
    cursor.execute("""
        UPDATE roi_tracking SET upload_date = (
            SELECT v.upload_date
            FROM mentions m JOIN videos v ON v.video_id = m.video_id
            WHERE m.id = roi_tracking.mention_id
        ) WHERE upload_date IS NULL
    """)
    conn.commit()

    # -- daily_sentiment -----------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            ticker TEXT,
            total_mentions INTEGER,
            bullish_count INTEGER,
            bearish_count INTEGER,
            neutral_count INTEGER,
            avg_confidence_bullish REAL,
            avg_confidence_bearish REAL,
            avg_confidence_neutral REAL,
            channels_mentioning INTEGER
        )
        """
    )

    # -- roi_cache (legacy) --------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS roi_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            upload_date TEXT NOT NULL,
            roi_json TEXT NOT NULL,
            last_updated TEXT NOT NULL
        )
        """
    )

    # -- price_daily ---------------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS price_daily (
            ticker  TEXT NOT NULL,
            date    TEXT NOT NULL,
            close   REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_daily_ticker_date ON price_daily (ticker, date)")

    # -- analysis_configs -----------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_configs (
            name        TEXT PRIMARY KEY,
            config_json TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
        """
    )

    # -- migration: add config_name to videos if missing ----------------------
    try:
        cursor.execute("ALTER TABLE videos ADD COLUMN config_name TEXT")
    except Exception:
        pass  # column already exists

    # -- seed three_pass_haiku_v1 (INSERT OR IGNORE — safe on reruns) ---------
    _v1_config = {
        "passes": 3,
        "pass1": {
            "role": "discovery",
            "model": "claude-haiku-4-5-20251001",
            "temperature": 1.0,
            "max_tokens": 4096,
            "system_prompt": (
                "You are a specialist financial transcript scanner with deep experience reading "
                "unpunctuated auto-generated YouTube transcripts in German and English. "
                "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, and commodities. "
                "Cast a wide net. No sentiment, no judgement — discovery only. "
                "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
                "Prefer US ADR ticker where one exists; otherwise use local format (SAP.DE, P911.DE, BMW.DE). "
                "For commodities use the standard ETF proxy ticker: "
                "gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
                "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
                "For commodities and crypto: company_name must be the literal string 'NULL'. "
                "asset_type: 'stock', 'etf', 'crypto', or 'commodity'. "
                "Return entries in the order they first appear in the transcript. "
                "Do not invent tickers not present in the transcript."
            ),
        },
        "pass2": {
            "role": "analysis",
            "model": "claude-haiku-4-5-20251001",
            "temperature": 0.7,
            "max_tokens": 8192,
            "system_prompt": (
                "You are a sharp-tongued senior financial analyst expert at cutting through vague YouTuber commentary to identify the real signal. "
                "You know the difference between genuine conviction and performative neutrality. "
                "For each investment vehicle in the provided list: return exactly one mention object. "
                "Do not skip any. Do not add tickers beyond those listed. "
                "Use the exact ticker string as provided. "
                "If a vehicle cannot be found in the transcript: is_real_stock_mention=false, confidence=0.0, mention_count=0, explain in context. "
                "For commodities (gold=GLD, silver=SLV, oil=USO, etc.) and crypto: company_name must be the literal string 'NULL' — do not invent a company name. "
                "Sentiment: lean toward bullish/bearish when any directional signal is present — "
                "reserve neutral for genuinely balanced or purely informational mentions. "
                "Confidence reflects clarity of sentiment expression, not certainty about the asset's prospects."
            ),
        },
        "pass3": {
            "role": "verification",
            "model": "claude-haiku-4-5-20251001",
            "temperature": 0.5,
            "max_tokens": 8192,
            "system_prompt": (
                "You are a meticulous fact-checker reviewing extracted stock mentions from a YouTube transcript. "
                "Your job: verify each mention is accurate, fix any errors in sentiment/confidence/recommendation, "
                "and remove any hallucinated stocks. "
                "Be conservative — when in doubt, keep the original. "
                "Return the corrected list using exactly the same JSON format. "
                "Do not add new tickers. Do not remove tickers unless they are clearly wrong. "
                "Change tickers and the correlating company, when the context clearly suggests another company "
                "with a similar name or ticker."
            ),
        },
    }
    import json as _json
    cursor.execute(
        "INSERT OR IGNORE INTO analysis_configs (name, config_json, created_at) VALUES (?, ?, ?)",
        ("three_pass_haiku_v1", _json.dumps(_v1_config), "2026-03-24T00:00:00Z"),
    )

    _v2_config = {
        "passes": 3,
        "pass1": {
            "role": "discovery",
            "model": "claude-haiku-4-5-20251001",
            "temperature": 1.0,
            "max_tokens": 4096,
            "system_prompt": (
                "You are a specialist financial transcript scanner with deep experience reading "
                "unpunctuated auto-generated YouTube transcripts in German and English. "
                "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, and commodities. "
                "Cast a wide net. No sentiment, no judgement — discovery only. "
                "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
                "Prefer US ADR ticker where one exists; otherwise use the exchange-suffix format: "
                "XETRA/Germany → .DE (SAP.DE, P911.DE, BMW.DE, XDWD.DE), "
                "London → .L (HSBA.L), Paris → .PA (AIR.PA), Amsterdam → .AS (ASML.AS), "
                "Singapore → .SI (D05.SI). Never output bare ETF brand names like 'XTRACKERS' — "
                "always look up the actual exchange ticker. "
                "Do not prefix tickers with '$' — strip it if present in the transcript. "
                "For commodities use the standard ETF proxy ticker: "
                "gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
                "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
                "For commodities and crypto: company_name must be the literal string 'NULL'. "
                "asset_type: 'stock', 'etf', 'crypto', or 'commodity'. "
                "Return entries in the order they first appear in the transcript. "
                "Do not invent tickers not present in the transcript."
            ),
        },
        "pass2": {
            "role": "analysis",
            "model": "claude-haiku-4-5-20251001",
            "temperature": 0.7,
            "max_tokens": 8192,
            "system_prompt": (
                "You are a sharp-tongued senior financial analyst expert at cutting through vague YouTuber commentary to identify the real signal. "
                "You know the difference between genuine conviction and performative neutrality. "
                "For each investment vehicle in the provided list: return exactly one mention object. "
                "Do not skip any. Do not add tickers beyond those listed. "
                "Use the exact ticker string as provided. "
                "If a vehicle cannot be found in the transcript: is_real_stock_mention=false, confidence=0.0, mention_count=0, explain in context. "
                "For commodities (gold=GLD, silver=SLV, oil=USO, etc.) and crypto: company_name must be the literal string 'NULL' — do not invent a company name. "
                "Sentiment: lean toward bullish/bearish when any directional signal is present — "
                "reserve neutral for genuinely balanced or purely informational mentions. "
                "Confidence reflects clarity of sentiment expression, not certainty about the asset's prospects."
            ),
        },
        "pass3": {
            "role": "verification",
            "model": "claude-haiku-4-5-20251001",
            "temperature": 0.5,
            "max_tokens": 8192,
            "system_prompt": (
                "You are a meticulous fact-checker reviewing extracted stock mentions from a YouTube transcript. "
                "Your job: verify each mention is accurate, fix any errors in sentiment/confidence/recommendation, "
                "and remove any hallucinated stocks. "
                "Be conservative — when in doubt, keep the original. "
                "Return the corrected list using exactly the same JSON format. "
                "Do not add new tickers. Do not remove tickers unless they are clearly wrong. "
                "Change tickers and the correlating company, when the context clearly suggests another company "
                "with a similar name or ticker."
            ),
        },
    }
    cursor.execute(
        "INSERT OR IGNORE INTO analysis_configs (name, config_json, created_at) VALUES (?, ?, ?)",
        ("three_pass_haiku_v2", _json.dumps(_v2_config), "2026-03-24T00:00:00Z"),
    )

    _two_pass_v1_config = {
        "passes": 2,
        "pass1": _v2_config["pass1"],
        "pass2": _v2_config["pass2"],
    }
    cursor.execute(
        "INSERT OR IGNORE INTO analysis_configs (name, config_json, created_at) VALUES (?, ?, ?)",
        ("two_pass_haiku_v1", _json.dumps(_two_pass_v1_config), "2026-03-24T00:00:00Z"),
    )

    # -- db_stats_daily -------------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS db_stats_daily (
            date              TEXT PRIMARY KEY,
            total_videos      INTEGER,
            total_mentions    INTEGER,
            distinct_channels INTEGER,
            updated_at        TEXT
        )
        """
    )

    # -- roi_milestones -------------------------------------------------------
    # Normalized milestone ROI table. Covers 3d/7d/14d/30d/60d/90d/180d/1yr/2yr+
    # and benchmark-relative ROI (vs SPY, QQQ, EWG).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS roi_milestones (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            mention_id     INTEGER NOT NULL,
            milestone_days INTEGER NOT NULL,
            price          REAL,
            roi            REAL,
            roi_vs_spy     REAL,
            roi_vs_qqq     REAL,
            roi_vs_ewg     REAL,
            price_date     TEXT,
            fetched_at     TEXT,
            UNIQUE(mention_id, milestone_days),
            FOREIGN KEY(mention_id) REFERENCES mentions(id)
        )
        """
    )

    # -- tickers --------------------------------------------------------------
    # Per-ticker metadata: sector, industry, market cap. Fetched once via yfinance,
    # refreshed monthly. Powers sector breakdown and market cap tier analysis.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tickers (
            ticker          TEXT PRIMARY KEY,
            sector          TEXT,
            industry        TEXT,
            market_cap      REAL,
            market_cap_tier TEXT,
            exchange        TEXT,
            fetched_at      TEXT
        )
        """
    )

    # -- backfill existing analyzed videos ------------------------------------
    cursor.execute(
        "UPDATE videos SET config_name = 'three_pass_haiku_v1' WHERE config_name IS NULL AND analyzed_at IS NOT NULL"
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Analysis config functions
# ---------------------------------------------------------------------------

def ensure_config_exists(name, config_json):
    """Insert a config record if it doesn't already exist. Safe to call on every startup."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO analysis_configs (name, config_json, created_at) VALUES (?, ?, ?)",
        (name, config_json, _utc_now()),
    )
    conn.commit()
    conn.close()


def set_video_config_name(video_id, config_name):
    """Record which analysis config was used for a video."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE videos SET config_name = ? WHERE video_id = ?",
        (config_name, video_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Channel functions
# ---------------------------------------------------------------------------

def add_channel(channel_id, name, url, language):
    """Insert a new channel. Ignore if already exists."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO channels (id, name, url, language, added_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (channel_id, name, url, language, _utc_now()),
    )
    conn.commit()
    conn.close()


def get_all_channels():
    """Return list of all channel dicts."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM channels")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_channel_last_scanned(channel_id):
    """Set last_scanned to current UTC timestamp."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE channels SET last_scanned = ? WHERE id = ?",
        (_utc_now(), channel_id),
    )
    conn.commit()
    conn.close()


def increment_transcript_stats(channel_id, attempted, failed):
    """Add attempted and failed counts to the channel's running totals."""
    if attempted == 0:
        return
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE channels
        SET transcript_attempts = COALESCE(transcript_attempts, 0) + ?,
            transcript_failures = COALESCE(transcript_failures, 0) + ?
        WHERE id = ?
        """,
        (attempted, failed, channel_id),
    )
    conn.commit()
    conn.close()


def set_channel_skip_backfill(channel_id, skip: bool):
    """Set or clear the skip_backfill flag for a channel."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE channels SET skip_backfill = ? WHERE id = ?",
        (1 if skip else 0, channel_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Video functions
# ---------------------------------------------------------------------------

def save_video(video_id, channel_id, url, title, upload_date, transcript, transcript_language=None):
    """Insert or replace a video row. Does not touch metadata columns enriched separately."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO videos (
            video_id, channel_id, url, title, upload_date,
            transcript, transcript_language, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            video_id, channel_id, url, title, upload_date,
            transcript, transcript_language, _utc_now(),
        ),
    )
    conn.commit()
    conn.close()


def update_video_metadata(video_id, description, duration_seconds, view_count, like_count):
    """
    Store YouTube metadata that doesn't come from transcript fetch.
    Uses UPDATE so existing transcript / analysis data is never overwritten.
    Records the exact UTC timestamp the metadata was fetched.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE videos
        SET description        = ?,
            duration_seconds   = ?,
            view_count         = ?,
            like_count         = ?,
            metadata_fetched_at = ?
        WHERE video_id = ?
        """,
        (description, duration_seconds, view_count, like_count, _utc_now(), video_id),
    )
    conn.commit()
    conn.close()


def video_exists(video_id):
    """Return True if video_id is already in the videos table."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def video_analyzed(video_id):
    """
    Return True only if the video should be permanently skipped:
    - It has at least one mention extracted (fully analyzed), OR
    - It was saved as non-finance (transcript IS NULL, analyzed_at set).
    Videos saved with a transcript but 0 mentions are NOT considered analyzed
    so they will be retried with the current extraction settings.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1 FROM mentions WHERE video_id = ?
        UNION
        SELECT 1 FROM videos WHERE video_id = ? AND transcript IS NULL AND analyzed_at IS NOT NULL
        LIMIT 1
        """,
        (video_id, video_id),
    )
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def get_videos_with_transcript_no_mentions(date_from=None, date_to=None, active_only=False, max_attempts=2):
    """
    Return videos that have a stored transcript but zero mentions.

    date_from    — only videos on or after this date (YYYY-MM-DD), optional
    date_to      — only videos on or before this date (YYYY-MM-DD), optional
    active_only  — if True, exclude channels with skip_backfill=1
    max_attempts — skip videos where zero_mention_attempts >= this value (default 3)
    """
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    filters = ["v.transcript IS NOT NULL",
               "NOT EXISTS (SELECT 1 FROM mentions m WHERE m.video_id = v.video_id)",
               f"COALESCE(v.zero_mention_attempts, 0) < {max_attempts}"]
    params = []

    if date_from:
        filters.append("v.upload_date >= ?")
        params.append(date_from)
    if date_to:
        filters.append("v.upload_date <= ?")
        params.append(date_to)
    if active_only:
        filters.append("c.skip_backfill = 0")

    where = " AND ".join(filters)
    cursor.execute(
        f"""
        SELECT v.video_id, v.title, v.upload_date, v.transcript, v.channel_id,
               c.language
        FROM videos v
        JOIN channels c ON c.id = v.channel_id
        WHERE {where}
        ORDER BY v.upload_date DESC
        """,
        params,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def increment_zero_mention_attempts(video_id):
    """
    Increment zero_mention_attempts for a video that returned 0 mentions.
    Called only when analysis completes successfully but finds nothing —
    NOT called on connection errors or parse failures.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE videos SET zero_mention_attempts = COALESCE(zero_mention_attempts, 0) + 1 WHERE video_id = ?",
        (video_id,),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Mention functions
# ---------------------------------------------------------------------------

def save_mention(video_id, ticker, company_name, mention_count, sentiment,
                 confidence, recommendation, context, is_real_stock_mention,
                 upload_date=None, asset_type="stock", verified=None):
    """Insert a mention row idempotently.

    Uses INSERT OR IGNORE against idx_mentions_unique (video_id, ticker,
    asset_type). Returns the new mention id, or 0 if an existing row already
    covers this (video, ticker, asset_type) tuple.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO mentions (
            video_id, ticker, company_name, mention_count, sentiment,
            confidence, recommendation, context, is_real_stock_mention,
            upload_date, asset_type, verified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            video_id, ticker, company_name if company_name is not None else "NULL", mention_count, sentiment,
            confidence, recommendation, context,
            1 if is_real_stock_mention else 0,
            upload_date, asset_type or "stock", verified,
        ),
    )
    mention_id = cursor.lastrowid if cursor.rowcount else 0
    conn.commit()
    conn.close()
    return mention_id


def get_mention_chart_data(ticker, days=None):
    """Return weekly-bucketed mention counts by sentiment for chart display.
    Missing weeks are filled with zeros to prevent diagonal lines on sparse data."""
    conn = _get_conn()
    cursor = conn.cursor()
    params = [ticker.upper()]
    date_filter = ""
    if days:
        date_filter = "AND upload_date >= date('now', ?)"
        params.append(f"-{days} days")
    cursor.execute(
        f"""
        SELECT
            strftime('%Y-%W', upload_date) AS week,
            MIN(upload_date) AS week_start,
            SUM(mention_count) AS total,
            COUNT(*) AS distinct_videos,
            SUM(CASE WHEN sentiment='bullish' THEN 1 ELSE 0 END) AS bullish,
            SUM(CASE WHEN sentiment='bearish' THEN 1 ELSE 0 END) AS bearish,
            SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) AS neutral
        FROM mentions
        WHERE UPPER(ticker) = ?
          AND upload_date IS NOT NULL
          {date_filter}
        GROUP BY week
        ORDER BY week ASC
        """,
        params,
    )
    rows = _fill_weekly_gaps([dict(r) for r in cursor.fetchall()])
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# ROI tracking functions
# ---------------------------------------------------------------------------

def save_roi_tracking(mention_id, ticker, price_at_publish,
                      upload_date=None, price_at_publish_date=None):
    """
    Insert initial roi_tracking row with the entry price.

    upload_date          — video publish date (YYYY-MM-DD), denormalized for fast queries.
    price_at_publish_date — the actual trading day whose close was used as baseline.
                           May differ from upload_date when publish falls on a weekend
                           or market holiday (next trading day is used instead).
    """
    now = _utc_now()
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO roi_tracking (
            mention_id, ticker, price_at_publish,
            upload_date, price_at_publish_date,
            price_at_publish_fetched_at, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, NULL)
        """,
        (mention_id, ticker, price_at_publish,
         upload_date, price_at_publish_date, now),
    )
    conn.commit()
    conn.close()


def get_mentions_without_roi_baseline(since: str = None,
                                       date_from: str = None,
                                       date_to: str = None,
                                       channel_ids: list = None):
    """
    Return mentions that have no roi_tracking row yet — i.e. baseline price
    has never been fetched. Groups naturally by (ticker, upload_date) for
    efficient batching in the caller.

    since:       optional ISO-8601 datetime; restricts by v.analyzed_at >= since.
    date_from:   optional YYYY-MM-DD; restricts by v.upload_date >= date_from.
    date_to:     optional YYYY-MM-DD; restricts by v.upload_date <= date_to.
    channel_ids: optional list of channel IDs; restricts to those channels.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    conditions = [
        "rt.id IS NULL",
        "m.ticker IS NOT NULL",
        "v.upload_date IS NOT NULL",
    ]
    params = []
    if since:
        conditions.append("v.analyzed_at >= ?")
        params.append(since)
    if date_from:
        conditions.append("v.upload_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("v.upload_date <= ?")
        params.append(date_to)
    if channel_ids:
        placeholders = ",".join("?" * len(channel_ids))
        conditions.append(f"v.channel_id IN ({placeholders})")
        params.extend(channel_ids)
    where = " AND ".join(conditions)
    cursor.execute(f"""
        SELECT m.id AS mention_id, m.ticker, v.upload_date
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE {where}
    """, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_pending_roi_updates(days):
    """
    Return list of roi_tracking rows where:
    - The video's upload_date + days <= today
    - The corresponding roi column (roi_7d or roi_30d) is NULL
    - last_updated is NULL or older than 2.5 hours ago

    days must be 7 or 30.
    """
    if days not in (7, 30):
        raise ValueError("days must be 7 or 30")

    roi_col = f"roi_{days}d"
    cutoff = _utc_now()  # used for the 2.5-hour staleness check

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT rt.*, v.upload_date AS video_upload_date
        FROM roi_tracking rt
        JOIN mentions m ON m.id = rt.mention_id
        JOIN videos v ON v.video_id = m.video_id
        WHERE rt.{roi_col} IS NULL
          AND date(v.upload_date, '+{days} days') <= date('now')
          AND (
              rt.last_updated IS NULL
              OR (julianday('now') - julianday(rt.last_updated)) * 24 > 2.5
          )
        """,
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_roi(roi_tracking_id, days, price, roi, price_date=None):
    """
    Update price_Nd/roi_Nd for a row. days is 7 or 30.
    price_date — the actual trading day the price was taken from (YYYY-MM-DD).
    Records exact fetch timestamp.
    """
    if days not in (7, 30):
        raise ValueError("days must be 7 or 30")

    price_col     = f"price_{days}d"
    roi_col       = f"roi_{days}d"
    fetched_col   = f"price_{days}d_fetched_at"
    date_col      = f"price_{days}d_date"
    now           = _utc_now()

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        UPDATE roi_tracking
        SET {price_col} = ?, {roi_col} = ?, {fetched_col} = ?,
            {date_col} = ?, last_updated = ?
        WHERE id = ?
        """,
        (price, roi, now, price_date, now, roi_tracking_id),
    )
    conn.commit()
    conn.close()


def get_all_roi_tickers():
    """Return list of unique tickers that have at least one roi_tracking row."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM roi_tracking WHERE ticker IS NOT NULL")
    tickers = [row["ticker"] for row in cursor.fetchall()]
    conn.close()
    return tickers


def update_current_roi_for_ticker(ticker, price, price_date):
    """
    Update price_current/roi_current on every roi_tracking row for a ticker.
    Called once per ticker — one SQL statement covers all rows.
    roi_current is computed per-row in SQL using each row's own price_at_publish,
    so each video gets the correct ROI relative to its own publish price.
    """
    now = _utc_now()
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE roi_tracking
        SET price_current          = ?,
            price_current_date     = ?,
            price_current_fetched_at = ?,
            roi_current            = ROUND((? - price_at_publish) / price_at_publish * 100, 2),
            last_updated           = ?
        WHERE ticker = ?
          AND price_at_publish IS NOT NULL
        """,
        (price, price_date, now, price, now, ticker),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# price_daily — normalized daily close prices per ticker
# ---------------------------------------------------------------------------

def bulk_insert_price_daily(rows):
    """
    Insert (ticker, date, close) rows into price_daily.
    Uses INSERT OR IGNORE so re-runs are safe and existing data is preserved.
    rows: list of (ticker, date_str, close_float)
    """
    if not rows:
        return
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT OR IGNORE INTO price_daily (ticker, date, close) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def get_tickers_needing_price_sync():
    """
    Return list of (ticker, earliest_upload_date, latest_price_date) for tickers
    that need price_daily data: either no rows at all, or newest date < today.
    earliest_upload_date is the oldest upload_date for that ticker in roi_tracking
    (the furthest back we need prices from).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            src.ticker,
            MIN(src.upload_date)        AS earliest_upload_date,
            MAX(pd.date)                AS latest_price_date
        FROM (
            SELECT ticker, upload_date FROM roi_tracking WHERE ticker IS NOT NULL
            UNION
            SELECT ticker, upload_date FROM mentions
            WHERE ticker IS NOT NULL AND ticker != '' AND is_real_stock_mention = 1
              AND upload_date IS NOT NULL
        ) src
        LEFT JOIN price_daily pd ON pd.ticker = src.ticker
        GROUP BY src.ticker
        HAVING latest_price_date IS NULL OR latest_price_date < ?
        """,
        (today,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_price_on_or_after(ticker, date_str):
    """
    Return (close, date) for the earliest trading day on or after date_str.
    Used for baseline and milestone price lookups from price_daily.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT close, date FROM price_daily WHERE ticker = ? AND date >= ? ORDER BY date ASC LIMIT 1",
        (ticker, date_str),
    )
    row = cursor.fetchone()
    conn.close()
    return (row["close"], row["date"]) if row else (None, None)


def get_latest_price(ticker):
    """
    Return (close, date) for the most recent trading day available in price_daily.
    Used for roi_current calculation.
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT close, date FROM price_daily WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        (ticker,),
    )
    row = cursor.fetchone()
    conn.close()
    return (row["close"], row["date"]) if row else (None, None)


# ---------------------------------------------------------------------------
# Daily sentiment
# ---------------------------------------------------------------------------

def upsert_daily_sentiment(date, ticker, total_mentions, bullish_count,
                           bearish_count, neutral_count,
                           avg_confidence_bullish, avg_confidence_bearish,
                           avg_confidence_neutral, channels_mentioning):
    """Insert or replace daily_sentiment row for a date+ticker pair."""
    conn = _get_conn()
    cursor = conn.cursor()

    # Check if a row already exists for this date/ticker pair.
    cursor.execute(
        "SELECT id FROM daily_sentiment WHERE date = ? AND ticker = ?",
        (date, ticker),
    )
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            """
            UPDATE daily_sentiment
            SET total_mentions = ?, bullish_count = ?, bearish_count = ?,
                neutral_count = ?, avg_confidence_bullish = ?,
                avg_confidence_bearish = ?, avg_confidence_neutral = ?,
                channels_mentioning = ?
            WHERE date = ? AND ticker = ?
            """,
            (
                total_mentions, bullish_count, bearish_count, neutral_count,
                avg_confidence_bullish, avg_confidence_bearish,
                avg_confidence_neutral, channels_mentioning,
                date, ticker,
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO daily_sentiment (
                date, ticker, total_mentions, bullish_count, bearish_count,
                neutral_count, avg_confidence_bullish, avg_confidence_bearish,
                avg_confidence_neutral, channels_mentioning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date, ticker, total_mentions, bullish_count, bearish_count,
                neutral_count, avg_confidence_bullish, avg_confidence_bearish,
                avg_confidence_neutral, channels_mentioning,
            ),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Analyst page — flexible query builder
# ---------------------------------------------------------------------------

def _build_analyst_where(filters):
    """
    Build a SQL WHERE clause and params list from an analyst filters dict.
    Expects the query to have aliases: m=mentions, v=videos, c=channels.
    """
    clauses = ["1=1"]
    params = []

    if filters.get("date_from"):
        clauses.append("v.upload_date >= ?")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        clauses.append("v.upload_date <= ?")
        params.append(filters["date_to"])
    if filters.get("channels"):
        ph = ",".join("?" * len(filters["channels"]))
        clauses.append(f"c.id IN ({ph})")
        params.extend(filters["channels"])
    if filters.get("lang"):
        ph = ",".join("?" * len(filters["lang"]))
        clauses.append(f"c.language IN ({ph})")
        params.extend(filters["lang"])
    if filters.get("sentiment") and filters["sentiment"] != "all":
        clauses.append("m.sentiment = ?")
        params.append(filters["sentiment"])
    if filters.get("recommendation") and filters["recommendation"] != "all":
        clauses.append("LOWER(m.recommendation) = ?")
        params.append(filters["recommendation"].lower())
    if filters.get("min_confidence") and float(filters.get("min_confidence", 0)) > 0:
        clauses.append("m.confidence >= ?")
        params.append(float(filters["min_confidence"]) / 100.0)
    if filters.get("tickers"):
        upper = [t.strip().upper() for t in filters["tickers"]]
        ph = ",".join("?" * len(upper))
        clauses.append(f"UPPER(m.ticker) IN ({ph})")
        params.extend(upper)

    return " AND ".join(clauses), params


def get_analyst_data(table_mode="ticker", filters=None):
    """
    Return rows for the analyst table. table_mode controls GROUP BY / columns.
    Modes: 'ticker', 'channel', 'date', 'raw'
    """
    if filters is None:
        filters = {}
    conn = _get_conn()
    cursor = conn.cursor()
    where, params = _build_analyst_where(filters)

    base_joins = """
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        JOIN channels c ON c.id = v.channel_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
    """

    if table_mode == "ticker":
        sql = f"""
        SELECT
            m.ticker,
            MAX(m.company_name) AS company_name,
            COUNT(DISTINCT v.channel_id) AS channel_count,
            COUNT(DISTINCT m.video_id) AS video_count,
            SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish_count,
            SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish_count,
            SUM(CASE WHEN m.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral_count,
            ROUND(AVG(m.confidence), 2) AS avg_confidence,
            ROUND(AVG(rt.roi_7d), 1) AS avg_roi_7d,
            ROUND(AVG(rt.roi_30d), 1) AS avg_roi_30d
        {base_joins}
        WHERE {where}
        GROUP BY m.ticker
        ORDER BY channel_count DESC, video_count DESC
        LIMIT 200
        """
    elif table_mode == "channel":
        sql = f"""
        SELECT
            c.id,
            c.name AS channel_name,
            c.language,
            COUNT(DISTINCT m.video_id) AS video_count,
            COUNT(DISTINCT m.ticker) AS tickers_mentioned,
            COUNT(DISTINCT CASE WHEN LOWER(m.recommendation)='buy' THEN m.id END) AS buy_picks,
            SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish_count,
            SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish_count,
            SUM(CASE WHEN m.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral_count,
            ROUND(AVG(CASE WHEN LOWER(m.recommendation)='buy' AND rt.roi_30d IS NOT NULL
                          THEN rt.roi_30d END), 1) AS avg_roi_30d,
            ROUND(
                100.0 * SUM(CASE WHEN LOWER(m.recommendation)='buy' AND rt.roi_30d > 0 THEN 1.0 ELSE 0 END) /
                NULLIF(COUNT(CASE WHEN LOWER(m.recommendation)='buy' AND rt.roi_30d IS NOT NULL THEN 1 END), 0)
            , 1) AS accuracy
        {base_joins}
        WHERE {where}
        GROUP BY c.id
        ORDER BY buy_picks DESC, video_count DESC
        """
    elif table_mode == "date":
        sql = f"""
        SELECT
            v.upload_date AS date,
            COUNT(DISTINCT m.video_id) AS video_count,
            COUNT(DISTINCT m.ticker) AS ticker_count,
            COUNT(DISTINCT v.channel_id) AS channel_count,
            SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish_count,
            SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish_count,
            SUM(CASE WHEN m.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral_count
        {base_joins}
        WHERE {where}
        GROUP BY v.upload_date
        ORDER BY v.upload_date DESC
        LIMIT 365
        """
    else:  # raw
        sql = f"""
        SELECT
            m.ticker,
            m.company_name,
            m.sentiment,
            ROUND(m.confidence * 100, 1) AS confidence_pct,
            m.recommendation,
            m.context,
            v.title AS video_title,
            v.upload_date,
            c.name AS channel_name,
            c.language,
            rt.roi_7d,
            rt.roi_30d
        {base_joins}
        WHERE {where}
        ORDER BY v.upload_date DESC
        LIMIT 500
        """

    cursor.execute(sql, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_analyst_chart_data(chart_mode="timeline", filters=None):
    """Return chart data dict for the analyst page. chart_mode: timeline, channel, scatter, heatmap."""
    if filters is None:
        filters = {}
    conn = _get_conn()
    cursor = conn.cursor()
    where, params = _build_analyst_where(filters)

    base_joins = """
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        JOIN channels c ON c.id = v.channel_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
    """

    if chart_mode == "timeline":
        cursor.execute(f"""
            SELECT v.upload_date AS date,
                SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish,
                SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish,
                SUM(CASE WHEN m.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral,
                COUNT(*) AS total
            {base_joins} WHERE {where}
            GROUP BY v.upload_date ORDER BY v.upload_date ASC
        """, params)
        rows = _fill_daily_gaps([dict(r) for r in cursor.fetchall()], date_key="date")
        conn.close()
        return {"labels": [r["date"] for r in rows],
                "bullish": [r["bullish"] for r in rows],
                "bearish": [r["bearish"] for r in rows],
                "neutral": [r["neutral"] for r in rows],
                "total": [r["total"] for r in rows]}

    elif chart_mode == "channel":
        cursor.execute(f"""
            SELECT c.name AS channel_name,
                COUNT(DISTINCT m.video_id) AS video_count,
                COUNT(DISTINCT CASE WHEN LOWER(m.recommendation)='buy' THEN m.id END) AS buy_picks,
                SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish,
                SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish
            {base_joins} WHERE {where}
            GROUP BY c.id ORDER BY video_count DESC LIMIT 25
        """, params)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return {"labels": [r["channel_name"] for r in rows],
                "buy_picks": [r["buy_picks"] for r in rows],
                "bullish": [r["bullish"] for r in rows],
                "bearish": [r["bearish"] for r in rows]}

    elif chart_mode == "scatter":
        cursor.execute(f"""
            SELECT m.ticker, c.name AS channel_name,
                ROUND(m.confidence * 100, 1) AS confidence_pct,
                rt.roi_30d, m.sentiment
            {base_joins} WHERE {where}
              AND rt.roi_30d IS NOT NULL AND m.confidence IS NOT NULL
            ORDER BY v.upload_date DESC LIMIT 500
        """, params)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return {"points": rows}

    elif chart_mode == "heatmap":
        cursor.execute(f"""
            SELECT m.ticker, COUNT(*) AS cnt {base_joins}
            WHERE {where} GROUP BY m.ticker ORDER BY cnt DESC LIMIT 20
        """, params)
        tickers = [r["ticker"] for r in cursor.fetchall()]

        cursor.execute(f"""
            SELECT DISTINCT c.id, c.name AS channel_name
            {base_joins} WHERE {where} ORDER BY c.name
        """, params)
        channels = [dict(r) for r in cursor.fetchall()]

        if tickers and channels:
            ph = ",".join("?" * len(tickers))
            cursor.execute(f"""
                SELECT UPPER(m.ticker) AS ticker, c.name AS channel_name, COUNT(*) AS cnt
                {base_joins} WHERE {where} AND UPPER(m.ticker) IN ({ph})
                GROUP BY UPPER(m.ticker), c.id
            """, params + tickers)
            cells = {(r["ticker"], r["channel_name"]): r["cnt"] for r in cursor.fetchall()}
            matrix = [[cells.get((t, ch["channel_name"]), 0) for ch in channels] for t in tickers]
        else:
            matrix = []

        conn.close()
        return {"tickers": tickers,
                "channels": [ch["channel_name"] for ch in channels],
                "matrix": matrix}

    conn.close()
    return {}


# ---------------------------------------------------------------------------
# Consensus & Leaderboard (new dashboard)
# ---------------------------------------------------------------------------

def get_consensus_picks(days=30, min_channels=3, lang="all", sentiment="all"):
    """
    Return stocks mentioned by at least min_channels distinct channels.
    Ranked by channel_count DESC. Filtered by date window, language, sentiment.
    days=0 means all time.
    """
    conn = _get_conn()
    cursor = conn.cursor()

    params = []
    date_clause = ""
    if days > 0:
        date_clause = "AND v.upload_date >= date('now', ?)"
        params.append(f"-{days} days")

    lang_clause = ""
    if lang and lang != "all":
        lang_clause = "AND c.language = ?"
        params.append(lang)

    sent_clause = ""
    if sentiment and sentiment != "all":
        sent_clause = "AND m.sentiment = ?"
        params.append(sentiment)

    params.append(min_channels)

    cursor.execute(
        f"""
        SELECT
            m.ticker,
            MAX(m.company_name) AS company_name,
            COUNT(DISTINCT v.channel_id) AS channel_count,
            COUNT(DISTINCT m.video_id) AS video_count,
            SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish_count,
            SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish_count,
            SUM(CASE WHEN m.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral_count,
            ROUND(AVG(m.confidence), 2) AS avg_confidence,
            ROUND(AVG(rt.roi_7d), 1) AS avg_roi_7d,
            ROUND(AVG(rt.roi_30d), 1) AS avg_roi_30d
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        JOIN channels c ON c.id = v.channel_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE 1=1
          {date_clause}
          {lang_clause}
          {sent_clause}
        GROUP BY m.ticker
        HAVING COUNT(DISTINCT v.channel_id) >= ?
        ORDER BY channel_count DESC, bullish_count DESC
        LIMIT 50
        """,
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_channel_leaderboard(days=30):
    """
    Rank channels by buy-pick accuracy (% of buy picks with roi_30d > 0).
    Only includes channels with at least 1 buy pick in the period.
    days=0 means all time.
    """
    conn = _get_conn()
    cursor = conn.cursor()

    params = []
    date_clause = ""
    if days > 0:
        date_clause = "AND v.upload_date >= date('now', ?)"
        params.append(f"-{days} days")

    cursor.execute(
        f"""
        SELECT
            c.id,
            c.name,
            c.language,
            COUNT(DISTINCT CASE WHEN LOWER(m.recommendation)='buy' THEN m.id END) AS buy_picks,
            COUNT(DISTINCT CASE WHEN LOWER(m.recommendation)='buy' AND rt.roi_30d IS NOT NULL
                                THEN m.id END) AS picks_with_roi,
            ROUND(
                100.0 * SUM(CASE WHEN LOWER(m.recommendation)='buy' AND rt.roi_30d > 0 THEN 1.0 ELSE 0 END) /
                NULLIF(COUNT(CASE WHEN LOWER(m.recommendation)='buy' AND rt.roi_30d IS NOT NULL THEN 1 END), 0)
            , 1) AS accuracy,
            ROUND(AVG(CASE WHEN LOWER(m.recommendation)='buy' AND rt.roi_30d IS NOT NULL
                           THEN rt.roi_30d END), 1) AS avg_roi_30d
        FROM channels c
        JOIN videos v ON v.channel_id = c.id
        JOIN mentions m ON m.video_id = v.video_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE 1=1
          {date_clause}
        GROUP BY c.id
        HAVING buy_picks > 0
        ORDER BY
            CASE WHEN accuracy IS NULL THEN 1 ELSE 0 END,
            accuracy DESC,
            buy_picks DESC
        LIMIT 20
        """,
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


# Dashboard period views
# ---------------------------------------------------------------------------

def get_dashboard_data(period="weekly"):
    """
    Return aggregated mention data for dashboard period views.
    period: 'weekly' (last 7d, grouped by day),
            'monthly' (last 30d, grouped by day),
            'yearly' (last 365d, grouped by week ISO)
    Returns: top_tickers list and chart_data dict
    """
    conn = _get_conn()
    cursor = conn.cursor()

    if period == "weekly":
        date_filter = "date('now', '-7 days')"
        bucket = "upload_date"
    elif period == "monthly":
        date_filter = "date('now', '-30 days')"
        bucket = "upload_date"
    else:  # yearly
        date_filter = "date('now', '-365 days')"
        bucket = "strftime('%Y-%W', upload_date)"

    # Top tickers for the period.
    # total_mentions = distinct video count (not raw word frequency SUM(mention_count)).
    cursor.execute(
        f"""
        SELECT
            ticker,
            COUNT(DISTINCT video_id) AS total_mentions,
            SUM(CASE WHEN sentiment='bullish' THEN 1 ELSE 0 END) AS bullish_count,
            SUM(CASE WHEN sentiment='bearish' THEN 1 ELSE 0 END) AS bearish_count,
            SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) AS neutral_count,
            COUNT(DISTINCT video_id) AS video_count
        FROM mentions
        WHERE upload_date >= {date_filter}
          AND upload_date IS NOT NULL
        GROUP BY ticker
        ORDER BY total_mentions DESC
        LIMIT 25
        """
    )
    top_tickers = [dict(row) for row in cursor.fetchall()]

    # Chart data: mentions over time bucketed
    cursor.execute(
        f"""
        SELECT
            {bucket} AS bucket,
            MIN(upload_date) AS bucket_start,
            SUM(mention_count) AS total,
            COUNT(*) AS distinct_mentions,
            SUM(CASE WHEN sentiment='bullish' THEN 1 ELSE 0 END) AS bullish,
            SUM(CASE WHEN sentiment='bearish' THEN 1 ELSE 0 END) AS bearish,
            SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) AS neutral
        FROM mentions
        WHERE upload_date >= {date_filter}
          AND upload_date IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket ASC
        """
    )
    chart_rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    chart_data = {
        "labels": [r["bucket_start"] for r in chart_rows],
        "total": [r["total"] for r in chart_rows],
        "distinct": [r["distinct_mentions"] for r in chart_rows],
        "bullish": [r["bullish"] for r in chart_rows],
        "bearish": [r["bearish"] for r in chart_rows],
        "neutral": [r["neutral"] for r in chart_rows],
    }
    return top_tickers, chart_data


def get_channel_stock_chart_data(channel_id, ticker):
    """Return weekly-bucketed mention data for one ticker within one channel.
    Missing weeks are filled with zeros to prevent diagonal lines on sparse data."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            strftime('%Y-%W', m.upload_date) AS week,
            MIN(m.upload_date) AS week_start,
            SUM(m.mention_count) AS total,
            COUNT(*) AS distinct_videos,
            SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish,
            SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish,
            SUM(CASE WHEN m.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        WHERE v.channel_id = ?
          AND UPPER(m.ticker) = UPPER(?)
          AND m.upload_date IS NOT NULL
        GROUP BY week
        ORDER BY week ASC
        """,
        (channel_id, ticker.upper()),
    )
    rows = _fill_weekly_gaps([dict(r) for r in cursor.fetchall()])
    conn.close()
    return rows


def get_channel_mentions(channel_id):
    """Return all mention rows for a channel with video and ROI data."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            m.id AS mention_id,
            v.video_id,
            v.title AS video_title,
            v.upload_date,
            m.ticker,
            m.company_name,
            m.mention_count,
            m.sentiment,
            m.confidence,
            m.recommendation,
            m.context,
            m.verified,
            rt.price_at_publish,
            rt.roi_7d,
            rt.roi_30d
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE v.channel_id = ?
          AND m.is_real_stock_mention = 1
        ORDER BY v.upload_date DESC, m.mention_count DESC
        """,
        (channel_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Route query functions — one per route, no inline SQL in main.py
# ---------------------------------------------------------------------------

def get_home_stats():
    """Return recent scan summary and total channel count for the home route."""
    conn = _get_conn()
    cursor = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cursor.execute(
        """
        SELECT c.id, c.name, c.last_scanned, COUNT(v.video_id) AS videos_today
        FROM channels c
        LEFT JOIN videos v ON v.channel_id = c.id AND v.upload_date = ?
        WHERE c.last_scanned IS NOT NULL
        GROUP BY c.id
        ORDER BY c.last_scanned DESC
        LIMIT 8
        """,
        (today,),
    )
    recent_scans = [dict(row) for row in cursor.fetchall()]

    cursor.execute("SELECT COUNT(*) AS cnt FROM channels")
    channels_total = cursor.fetchone()["cnt"]

    conn.close()
    return {"recent_scans": recent_scans, "channels_total": channels_total}


def get_stock_detail(ticker, days=90):
    """Return mentions, sentiment summary, and ROI chart data for a single ticker.

    days=0 means all time (no date filter). ROI baseline is always the closing
    price on the video's publish date.
    """
    conn = _get_conn()
    cursor = conn.cursor()

    if days > 0:
        date_filter = "AND v.upload_date >= date('now', '-' || ? || ' days')"
        params_with_days = (ticker, days)
    else:
        date_filter = ""
        params_with_days = (ticker,)

    cursor.execute(
        f"""
        SELECT
            m.id AS mention_id,
            v.video_id,
            v.title AS video_title,
            c.name AS channel_name,
            c.id AS channel_id,
            v.upload_date,
            m.sentiment,
            m.confidence,
            m.recommendation,
            m.context,
            m.verified,
            rt.price_at_publish,
            rt.roi_7d,
            rt.roi_30d,
            rt.roi_current
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        JOIN channels c ON c.id = v.channel_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE UPPER(m.ticker) = UPPER(?)
        {date_filter}
        ORDER BY v.upload_date DESC
        """,
        params_with_days,
    )
    mentions = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        f"""
        SELECT m.sentiment, COUNT(*) AS cnt
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        WHERE UPPER(m.ticker) = UPPER(?)
        {date_filter}
        GROUP BY m.sentiment
        """,
        params_with_days,
    )
    sentiment_summary = {"bullish": 0, "bearish": 0, "neutral": 0}
    for row in cursor.fetchall():
        s = row["sentiment"]
        if s in sentiment_summary:
            sentiment_summary[s] = row["cnt"]

    cursor.execute(
        """
        SELECT
            v.upload_date AS date,
            rt.roi_7d,
            rt.roi_30d
        FROM roi_tracking rt
        JOIN mentions m ON m.id = rt.mention_id
        JOIN videos v ON v.video_id = m.video_id
        WHERE UPPER(m.ticker) = UPPER(?)
          AND (rt.roi_7d IS NOT NULL OR rt.roi_30d IS NOT NULL)
        ORDER BY v.upload_date ASC
        """,
        (ticker,),
    )
    roi_data = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {"mentions": mentions, "sentiment_summary": sentiment_summary, "roi_data": roi_data}


def get_channel_detail(channel_id):
    """Return channel row, videos, pick accuracy, top stocks, and all real mentions."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
    row = cursor.fetchone()
    channel = dict(row) if row else None

    cursor.execute(
        """
        SELECT v.*, COUNT(m.id) AS mention_count
        FROM videos v
        LEFT JOIN mentions m ON m.video_id = v.video_id
        WHERE v.channel_id = ?
        GROUP BY v.video_id
        ORDER BY v.upload_date DESC
        """,
        (channel_id,),
    )
    videos = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN rt.roi_30d > 0 THEN 1 ELSE 0 END) AS positive
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE v.channel_id = ?
          AND LOWER(m.recommendation) = 'buy'
          AND rt.roi_30d IS NOT NULL
        """,
        (channel_id,),
    )
    acc_row = cursor.fetchone()
    pick_accuracy = None
    if acc_row and acc_row["total"] and acc_row["total"] > 0:
        pick_accuracy = round(acc_row["positive"] / acc_row["total"] * 100, 1)

    cursor.execute(
        """
        SELECT m.ticker, COUNT(*) AS mention_count, m.sentiment AS dominant_sentiment
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        WHERE v.channel_id = ?
        GROUP BY m.ticker
        ORDER BY mention_count DESC
        LIMIT 15
        """,
        (channel_id,),
    )
    top_stocks = [dict(row) for row in cursor.fetchall()]

    conn.close()

    # Reuse existing function — preserves is_real_stock_mention = 1 filter
    channel_mentions = get_channel_mentions(channel_id)

    return {
        "channel": channel,
        "videos": videos,
        "pick_accuracy": pick_accuracy,
        "top_stocks": top_stocks,
        "channel_mentions": channel_mentions,
    }


def get_channels_list():
    """Return all channels with video count and transcript health stats."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            c.id,
            c.name,
            c.url,
            c.language,
            c.added_at,
            c.last_scanned,
            c.transcript_attempts,
            c.transcript_failures,
            c.skip_backfill,
            COUNT(v.video_id) AS video_count
        FROM channels c
        LEFT JOIN videos v ON v.channel_id = c.id
        GROUP BY c.id
        ORDER BY c.language ASC, c.name ASC
        """
    )
    rows = []
    for row in cursor.fetchall():
        r = dict(row)
        attempts = r.get("transcript_attempts") or 0
        failures = r.get("transcript_failures") or 0
        if attempts >= 3:
            r["failure_rate"] = round(failures / attempts * 100)
        else:
            r["failure_rate"] = None  # not enough data yet
        rows.append(r)
    conn.close()
    return rows


def get_admin_stats():
    """
    Aggregate stats across all tables for the /admin/stats owner page.
    Returns a single dict — all queries run in one connection.
    """
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # --- Database health ---
    c.execute("SELECT COUNT(*) n FROM channels")
    total_channels = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM channels WHERE skip_backfill = 1")
    excluded_channels = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM videos")
    total_videos = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM videos WHERE transcript IS NOT NULL")
    videos_with_transcript = c.fetchone()["n"]

    c.execute(
        """
        SELECT COUNT(*) n FROM videos
        WHERE transcript IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.video_id = videos.video_id)
        """
    )
    videos_no_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM mentions")
    total_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM roi_tracking")
    total_roi_rows = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM roi_tracking WHERE roi_7d IS NOT NULL")
    roi_7d_done = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM roi_tracking WHERE roi_30d IS NOT NULL")
    roi_30d_done = c.fetchone()["n"]

    # --- Extraction quality ---
    c.execute("SELECT sentiment, COUNT(*) cnt FROM mentions GROUP BY sentiment")
    sentiment_rows = {r["sentiment"]: r["cnt"] for r in c.fetchall()}

    c.execute("SELECT recommendation, COUNT(*) cnt FROM mentions GROUP BY recommendation")
    rec_rows = {r["recommendation"]: r["cnt"] for r in c.fetchall()}

    c.execute("SELECT ROUND(AVG(confidence), 3) avg FROM mentions WHERE is_real_stock_mention = 1")
    avg_confidence = c.fetchone()["avg"] or 0.0

    c.execute("SELECT COUNT(*) n FROM mentions WHERE is_real_stock_mention = 1")
    real_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM mentions WHERE is_real_stock_mention = 0 OR is_real_stock_mention IS NULL")
    filtered_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(DISTINCT UPPER(ticker)) n FROM mentions")
    distinct_tickers = c.fetchone()["n"]

    # --- ROI performance ---
    c.execute("SELECT ROUND(AVG(roi_7d), 2) avg FROM roi_tracking WHERE roi_7d IS NOT NULL")
    avg_roi_7d = c.fetchone()["avg"]

    c.execute("SELECT ROUND(AVG(roi_30d), 2) avg FROM roi_tracking WHERE roi_30d IS NOT NULL")
    avg_roi_30d = c.fetchone()["avg"]

    c.execute(
        """
        SELECT rt.ticker, m.company_name,
               ROUND(rt.roi_30d, 2) roi_30d,
               ROUND(rt.roi_7d,  2) roi_7d,
               v.title, v.upload_date,
               ch.name channel_name
        FROM roi_tracking rt
        JOIN mentions m  ON m.id       = rt.mention_id
        JOIN videos v    ON v.video_id = m.video_id
        JOIN channels ch ON ch.id      = v.channel_id
        WHERE rt.roi_30d IS NOT NULL
        ORDER BY rt.roi_30d DESC
        LIMIT 5
        """
    )
    top_picks = [dict(r) for r in c.fetchall()]

    c.execute(
        """
        SELECT rt.ticker, m.company_name,
               ROUND(rt.roi_30d, 2) roi_30d,
               ROUND(rt.roi_7d,  2) roi_7d,
               v.title, v.upload_date,
               ch.name channel_name
        FROM roi_tracking rt
        JOIN mentions m  ON m.id       = rt.mention_id
        JOIN videos v    ON v.video_id = m.video_id
        JOIN channels ch ON ch.id      = v.channel_id
        WHERE rt.roi_30d IS NOT NULL
        ORDER BY rt.roi_30d ASC
        LIMIT 5
        """
    )
    worst_picks = [dict(r) for r in c.fetchall()]

    # --- Activity timeline (last 12 months) ---
    c.execute(
        """
        SELECT strftime('%Y-%m', upload_date) month, COUNT(*) cnt
        FROM videos
        WHERE upload_date >= date('now', '-12 months')
        GROUP BY month ORDER BY month
        """
    )
    videos_by_month_raw = {r["month"]: r["cnt"] for r in c.fetchall()}

    c.execute(
        """
        SELECT strftime('%Y-%m', v.upload_date) month, COUNT(*) cnt
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        WHERE v.upload_date >= date('now', '-12 months')
        GROUP BY month ORDER BY month
        """
    )
    mentions_by_month_raw = {r["month"]: r["cnt"] for r in c.fetchall()}

    conn.close()

    # Fill missing months with 0 so charts have no gaps
    months = []
    d = date.today().replace(day=1)
    for _ in range(12):
        months.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    months.reverse()

    return {
        # health
        "total_channels":            total_channels,
        "active_channels":           total_channels - excluded_channels,
        "excluded_channels":         excluded_channels,
        "total_videos":              total_videos,
        "videos_with_transcript":    videos_with_transcript,
        "videos_without_transcript": total_videos - videos_with_transcript,
        "transcript_pct":            round(videos_with_transcript / total_videos * 100) if total_videos else 0,
        "videos_analyzed":           videos_with_transcript - videos_no_mentions,
        "videos_no_mentions":        videos_no_mentions,
        "total_mentions":            total_mentions,
        "total_roi_rows":            total_roi_rows,
        "roi_7d_done":               roi_7d_done,
        "roi_30d_done":              roi_30d_done,
        # extraction quality
        "bullish_count":   sentiment_rows.get("bullish",  0),
        "bearish_count":   sentiment_rows.get("bearish",  0),
        "neutral_count":   sentiment_rows.get("neutral",  0),
        "rec_buy":         rec_rows.get("buy",       0),
        "rec_sell":        rec_rows.get("sell",      0),
        "rec_hold":        rec_rows.get("hold",      0),
        "rec_reference":   rec_rows.get("reference", 0),
        "avg_confidence":  avg_confidence,
        "real_mentions":   real_mentions,
        "filtered_mentions": filtered_mentions,
        "distinct_tickers":  distinct_tickers,
        # roi
        "avg_roi_7d":   avg_roi_7d,
        "avg_roi_30d":  avg_roi_30d,
        "top_picks":    top_picks,
        "worst_picks":  worst_picks,
        # timeline
        "videos_by_month":   [{"month": m, "cnt": videos_by_month_raw.get(m, 0)}   for m in months],
        "mentions_by_month": [{"month": m, "cnt": mentions_by_month_raw.get(m, 0)} for m in months],
    }


def get_stocks_list(days=90):
    """Return all tickers with aggregated mention and sentiment counts.

    days=0 means all time (no date filter).
    """
    conn = _get_conn()
    cursor = conn.cursor()

    if days > 0:
        date_filter = "WHERE v.upload_date >= date('now', '-' || ? || ' days')"
        params = (days,)
    else:
        date_filter = ""
        params = ()

    cursor.execute(
        f"""
        SELECT
            m.ticker,
            m.company_name,
            COUNT(*) AS mention_count,
            SUM(CASE WHEN m.sentiment='bullish' THEN 1 ELSE 0 END) AS bullish_count,
            SUM(CASE WHEN m.sentiment='bearish' THEN 1 ELSE 0 END) AS bearish_count,
            SUM(CASE WHEN m.sentiment='neutral' THEN 1 ELSE 0 END) AS neutral_count,
            COUNT(DISTINCT v.channel_id) AS channels_count
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        {date_filter}
        GROUP BY m.ticker
        ORDER BY mention_count DESC
        """,
        params,
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_export_rows(ticker=None, channel_id=None, date_from=None, date_to=None, sentiment=None):
    """Build and execute the export query with optional filters. Returns list of dicts."""
    conditions = []
    params = []

    if ticker:
        conditions.append("UPPER(m.ticker) = UPPER(?)")
        params.append(ticker)
    if channel_id:
        conditions.append("c.id = ?")
        params.append(channel_id)
    if date_from:
        conditions.append("v.upload_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("v.upload_date <= ?")
        params.append(date_to)
    if sentiment:
        conditions.append("LOWER(m.sentiment) = LOWER(?)")
        params.append(sentiment)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            v.upload_date AS date,
            c.name AS channel,
            v.title AS video_title,
            m.ticker,
            m.company_name,
            m.sentiment,
            m.confidence,
            m.recommendation,
            m.context,
            rt.price_at_publish,
            rt.roi_7d,
            rt.roi_30d
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        JOIN channels c ON c.id = v.channel_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        {where_clause}
        ORDER BY v.upload_date DESC
    """

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# DB stats progression
# ---------------------------------------------------------------------------

def upsert_stats_snapshot():
    """Compute today's cumulative stats and upsert into db_stats_daily."""
    conn = _get_conn()
    c = conn.cursor()
    today = date.today().isoformat()

    c.execute("SELECT COUNT(*) n FROM videos")
    total_videos = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM mentions WHERE is_real_stock_mention = 1")
    total_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(DISTINCT channel_id) n FROM videos WHERE channel_id IS NOT NULL")
    distinct_channels = c.fetchone()["n"]

    c.execute("""
        INSERT OR REPLACE INTO db_stats_daily (date, total_videos, total_mentions, distinct_channels, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, (today, total_videos, total_mentions, distinct_channels, _utc_now()))
    conn.commit()
    conn.close()


def backfill_stats_daily():
    """
    Reconstruct historical db_stats_daily from analyzed_at timestamps.
    Only inserts rows that don't already exist (safe to re-run).
    """
    conn = _get_conn()
    c = conn.cursor()

    # Get all distinct analysis dates
    c.execute("""
        SELECT DISTINCT DATE(analyzed_at) d
        FROM videos
        WHERE analyzed_at IS NOT NULL
        ORDER BY d
    """)
    dates = [row["d"] for row in c.fetchall()]

    now = _utc_now()
    for d in dates:
        c.execute("""
            SELECT COUNT(*) n FROM videos WHERE DATE(analyzed_at) <= ?
        """, (d,))
        total_videos = c.fetchone()["n"]

        c.execute("""
            SELECT COUNT(*) n FROM mentions m
            JOIN videos v ON v.video_id = m.video_id
            WHERE m.is_real_stock_mention = 1
              AND DATE(v.analyzed_at) <= ?
        """, (d,))
        total_mentions = c.fetchone()["n"]

        c.execute("""
            SELECT COUNT(DISTINCT channel_id) n FROM videos
            WHERE channel_id IS NOT NULL AND DATE(analyzed_at) <= ?
        """, (d,))
        distinct_channels = c.fetchone()["n"]

        c.execute("""
            INSERT OR IGNORE INTO db_stats_daily (date, total_videos, total_mentions, distinct_channels, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (d, total_videos, total_mentions, distinct_channels, now))

    conn.commit()
    conn.close()


def get_stats_progression():
    """Return all db_stats_daily rows ordered by date for charting."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT date, total_videos, total_mentions, distinct_channels
        FROM db_stats_daily
        ORDER BY date
    """)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Raw table export
# ---------------------------------------------------------------------------

_EXPORTABLE_TABLES = {
    "channels": {
        "date_col": None,
        "query": "SELECT * FROM channels ORDER BY added_at",
    },
    "videos": {
        "date_col": "upload_date",
        "query": "SELECT video_id, channel_id, url, title, upload_date, transcript_language, analyzed_at, config_name, duration_seconds, view_count, like_count, zero_mention_attempts FROM videos",
    },
    "mentions": {
        "date_col": "upload_date",
        "query": "SELECT m.id, m.video_id, m.ticker, m.company_name, m.mention_count, m.sentiment, m.confidence, m.recommendation, m.context, m.is_real_stock_mention, m.asset_type, m.upload_date FROM mentions m",
    },
    "roi_tracking": {
        "date_col": "upload_date",
        "query": "SELECT * FROM roi_tracking",
    },
    "daily_sentiment": {
        "date_col": "date",
        "query": "SELECT * FROM daily_sentiment ORDER BY date",
    },
    "price_daily": {
        "date_col": "date",
        "query": "SELECT * FROM price_daily ORDER BY date, ticker",
    },
    "analysis_configs": {
        "date_col": None,
        "query": "SELECT name, created_at FROM analysis_configs ORDER BY created_at",
    },
    "db_stats_daily": {
        "date_col": "date",
        "query": "SELECT * FROM db_stats_daily ORDER BY date",
    },
    "roi_milestones": {
        "date_col": None,
        "query": "SELECT * FROM roi_milestones ORDER BY mention_id, milestone_days",
    },
    "tickers": {
        "date_col": None,
        "query": "SELECT * FROM tickers ORDER BY ticker",
    },
}


def get_export_table_metadata():
    """
    Return row count and date range for each exportable table.
    Used to show metadata on the export page. Lightweight — one query per table.
    """
    conn = _get_conn()
    c = conn.cursor()

    queries = {
        "channels":        ("SELECT COUNT(*) n, NULL mn, NULL mx FROM channels", None),
        "videos":          ("SELECT COUNT(*) n, MIN(upload_date) mn, MAX(upload_date) mx FROM videos", "upload_date"),
        "mentions":        ("SELECT COUNT(*) n, MIN(upload_date) mn, MAX(upload_date) mx FROM mentions", "upload_date"),
        "roi_tracking":    ("SELECT COUNT(*) n, MIN(upload_date) mn, MAX(upload_date) mx FROM roi_tracking", "upload_date"),
        "daily_sentiment": ("SELECT COUNT(*) n, MIN(date) mn, MAX(date) mx FROM daily_sentiment", "date"),
        "price_daily":     ("SELECT COUNT(*) n, MIN(date) mn, MAX(date) mx FROM price_daily", "date"),
        "analysis_configs":("SELECT COUNT(*) n, NULL mn, NULL mx FROM analysis_configs", None),
        "db_stats_daily":  ("SELECT COUNT(*) n, MIN(date) mn, MAX(date) mx FROM db_stats_daily", "date"),
        "roi_milestones":  ("SELECT COUNT(*) n, NULL mn, NULL mx FROM roi_milestones", None),
        "tickers":         ("SELECT COUNT(*) n, NULL mn, NULL mx FROM tickers", None),
    }

    result = {}
    for table, (sql, _) in queries.items():
        try:
            row = c.execute(sql).fetchone()
            result[table] = {
                "rows":     row["n"],
                "date_min": row["mn"],
                "date_max": row["mx"],
            }
        except Exception:
            result[table] = {"rows": None, "date_min": None, "date_max": None}

    conn.close()
    return result


def get_ai_analysis_export():
    """
    Flat denormalized join of mentions + videos + channels + roi_tracking.
    Intended for AI correlation analysis — all real mentions with full context and ROI.
    """
    sql = """
        SELECT
            v.upload_date,
            v.video_id,
            v.title          AS video_title,
            c.name           AS channel_name,
            c.language       AS channel_language,
            m.ticker,
            m.company_name,
            m.asset_type,
            m.sentiment,
            m.confidence,
            m.recommendation,
            m.mention_count,
            m.context,
            v.config_name,
            rt.price_at_publish,
            rt.roi_7d,
            rt.roi_30d,
            rt.roi_current,
            rt.price_7d_date,
            rt.price_30d_date
        FROM mentions m
        JOIN videos   v  ON v.video_id  = m.video_id
        JOIN channels c  ON c.id        = v.channel_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE m.is_real_stock_mention = 1
        ORDER BY v.upload_date DESC
    """
    conn = _get_conn()
    rows = [dict(row) for row in conn.execute(sql).fetchall()]
    conn.close()
    return rows


def get_verified_mentions_export():
    """
    Flat denormalized export of transcript-verified mentions only (verified=1).
    These are mentions where the ticker or company name was confirmed to exist
    as a literal string in the transcript — no hallucinations possible at the
    stock identity level.
    """
    sql = """
        SELECT
            v.upload_date,
            v.video_id,
            v.title          AS video_title,
            c.name           AS channel_name,
            c.language       AS channel_language,
            m.ticker,
            m.company_name,
            m.asset_type,
            m.sentiment,
            m.confidence,
            m.recommendation,
            m.mention_count,
            m.context,
            v.config_name,
            rt.price_at_publish,
            rt.roi_7d,
            rt.roi_30d,
            rt.roi_current,
            rt.price_7d_date,
            rt.price_30d_date
        FROM mentions m
        JOIN videos   v  ON v.video_id  = m.video_id
        JOIN channels c  ON c.id        = v.channel_id
        LEFT JOIN roi_tracking rt ON rt.mention_id = m.id
        WHERE m.is_real_stock_mention = 1
          AND m.verified = 1
        ORDER BY v.upload_date DESC
    """
    conn = _get_conn()
    rows = [dict(row) for row in conn.execute(sql).fetchall()]
    conn.close()
    return rows


def get_raw_table_export(table: str, date_from: str = None, date_to: str = None):
    """
    Return raw rows from a table with optional date range filter.
    Returns list of dicts with raw column names.
    """
    if table not in _EXPORTABLE_TABLES:
        raise ValueError(f"Unknown table: {table}")

    cfg = _EXPORTABLE_TABLES[table]
    base_query = cfg["query"]
    date_col = cfg["date_col"]

    conditions = []
    params = []
    if date_col and date_from:
        conditions.append(f"{date_col} >= ?")
        params.append(date_from)
    if date_col and date_to:
        conditions.append(f"{date_col} <= ?")
        params.append(date_to)

    if conditions:
        # Wrap in subquery or append WHERE clause
        sql = f"SELECT * FROM ({base_query}) WHERE {' AND '.join(conditions)}"
    else:
        sql = base_query

    conn = _get_conn()
    c = conn.cursor()
    c.execute(sql, params)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Ticker metadata (sector / industry / market cap)
# ---------------------------------------------------------------------------

def _market_cap_tier(market_cap):
    """Bucket market cap into human-readable tier string."""
    if market_cap is None:
        return None
    if market_cap >= 200_000_000_000:
        return "mega"
    if market_cap >= 10_000_000_000:
        return "large"
    if market_cap >= 2_000_000_000:
        return "mid"
    if market_cap >= 300_000_000:
        return "small"
    return "micro"


def upsert_ticker_meta(ticker, sector, industry, market_cap, exchange):
    """Insert or replace ticker metadata. market_cap_tier computed automatically."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO tickers
            (ticker, sector, industry, market_cap, market_cap_tier, exchange, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ticker, sector, industry, market_cap,
         _market_cap_tier(market_cap), exchange, _utc_now()),
    )
    conn.commit()
    conn.close()


def get_tickers_needing_meta(max_age_days=30):
    """
    Return distinct tickers from real mentions that are missing metadata
    or whose fetched_at is older than max_age_days.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT DISTINCT m.ticker
        FROM mentions m
        LEFT JOIN tickers t ON t.ticker = m.ticker
        WHERE m.is_real_stock_mention = 1
          AND m.ticker IS NOT NULL AND m.ticker != ''
          AND (t.ticker IS NULL OR t.fetched_at < ?)
        ORDER BY m.ticker
        """,
        (cutoff,),
    )
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows


def get_ticker_meta(ticker):
    """Return tickers row for a single ticker, or None if not present."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM tickers WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# ROI milestones
# ---------------------------------------------------------------------------

def upsert_roi_milestone(mention_id, milestone_days, price, roi,
                          roi_vs_spy, roi_vs_qqq, roi_vs_ewg, price_date):
    """Insert or replace a single roi_milestones row."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO roi_milestones
            (mention_id, milestone_days, price, roi,
             roi_vs_spy, roi_vs_qqq, roi_vs_ewg, price_date, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mention_id, milestone_days, price, roi,
         roi_vs_spy, roi_vs_qqq, roi_vs_ewg, price_date, _utc_now()),
    )
    conn.commit()
    conn.close()


def bulk_upsert_roi_milestones(rows):
    """
    Bulk INSERT OR REPLACE into roi_milestones.
    rows: list of (mention_id, milestone_days, price, roi,
                   roi_vs_spy, roi_vs_qqq, roi_vs_ewg, price_date, fetched_at)
    Single transaction — much faster than one commit per row.
    """
    if not rows:
        return
    conn = _get_conn()
    conn.executemany(
        """
        INSERT OR REPLACE INTO roi_milestones
            (mention_id, milestone_days, price, roi,
             roi_vs_spy, roi_vs_qqq, roi_vs_ewg, price_date, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def get_roi_milestone_pending():
    """
    Return all roi_tracking rows that have a baseline price, plus a set of
    already-populated milestone_days for each. Caller determines which milestones
    are still pending based on video age.
    """
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT
            rt.mention_id,
            rt.ticker,
            rt.upload_date,
            rt.price_at_publish,
            GROUP_CONCAT(rm.milestone_days) AS existing_milestones
        FROM roi_tracking rt
        LEFT JOIN roi_milestones rm ON rm.mention_id = rt.mention_id
        WHERE rt.price_at_publish IS NOT NULL
          AND rt.upload_date IS NOT NULL
          AND rt.ticker IS NOT NULL
        GROUP BY rt.mention_id
        """
    )
    rows = []
    for r in c.fetchall():
        d = dict(r)
        d["existing_milestone_days"] = {
            int(x) for x in (d.pop("existing_milestones") or "").split(",") if x
        }
        rows.append(d)
    conn.close()
    return rows


def backfill_roi_milestones_from_tracking():
    """
    One-time migration: copy existing 7d and 30d data from roi_tracking into
    roi_milestones. Safe to re-run — uses INSERT OR IGNORE.
    Returns total rows in roi_milestones after migration.
    """
    conn = _get_conn()
    c = conn.cursor()
    now = _utc_now()

    c.execute(
        """
        INSERT OR IGNORE INTO roi_milestones
            (mention_id, milestone_days, price, roi, price_date, fetched_at)
        SELECT mention_id, 7, price_7d, roi_7d, price_7d_date, ?
        FROM roi_tracking
        WHERE price_7d IS NOT NULL AND roi_7d IS NOT NULL
        """,
        (now,),
    )
    c.execute(
        """
        INSERT OR IGNORE INTO roi_milestones
            (mention_id, milestone_days, price, roi, price_date, fetched_at)
        SELECT mention_id, 30, price_30d, roi_30d, price_30d_date, ?
        FROM roi_tracking
        WHERE price_30d IS NOT NULL AND roi_30d IS NOT NULL
        """,
        (now,),
    )
    conn.commit()
    n = c.execute("SELECT COUNT(*) FROM roi_milestones").fetchone()[0]
    conn.close()
    return n


# ---------------------------------------------------------------------------
# Price helpers for benchmarks
# ---------------------------------------------------------------------------

def get_latest_price_date(ticker):
    """Return the most recent date in price_daily for this ticker, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(date) d FROM price_daily WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return row["d"] if row else None


def get_earliest_upload_date():
    """Return the earliest upload_date across all videos."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT MIN(upload_date) d FROM videos WHERE upload_date IS NOT NULL"
    ).fetchone()
    conn.close()
    return row["d"] if row else None


# ---------------------------------------------------------------------------
# Legacy functions — kept for compatibility with vetted_core.py
# ---------------------------------------------------------------------------

def save_video_data(
    video_id,
    url,
    title,
    upload_date,
    transcript,
    buy_signals,
    channel_handle="UNKNOWN",
):
    """Save video metadata, transcript and AI buy signals (legacy interface)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO videos (
            video_id,
            url,
            title,
            upload_date,
            channel_handle,
            transcript,
            buy_signals_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            video_id,
            url,
            title,
            upload_date,
            channel_handle,
            transcript,
            json.dumps(buy_signals),
        ),
    )

    conn.commit()
    conn.close()


def get_cached_video(video_id):
    """Retrieve cached video data and buy signals if present (legacy interface)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            url,
            title,
            upload_date,
            channel_handle,
            transcript,
            buy_signals_json
        FROM videos
        WHERE video_id = ?
        """,
        (video_id,),
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    url, title, upload_date, channel_handle, transcript, buy_signals_json = row

    try:
        buy_signals = json.loads(buy_signals_json) if buy_signals_json else []
    except json.JSONDecodeError:
        buy_signals = []

    return {
        "video_id": video_id,
        "url": url,
        "title": title,
        "upload_date": upload_date,
        "channel_handle": channel_handle,
        "transcript": transcript,
        "buy_signals": buy_signals,
    }


def save_roi_cache(ticker, upload_date, roi_data, last_updated):
    """Persist Tiingo ROI response for potential future use (legacy interface)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO roi_cache (ticker, upload_date, roi_json, last_updated)
        VALUES (?, ?, ?, ?)
        """,
        (ticker.upper(), upload_date, json.dumps(roi_data), last_updated),
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Initialise the database the moment this module is imported
# ---------------------------------------------------------------------------

init_db()
