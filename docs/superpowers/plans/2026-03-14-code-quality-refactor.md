# Code Quality Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove dead code, extract all inline SQL from route handlers into db_manager.py, and standardise error handling across main.py — with no visible behaviour changes.

**Architecture:** Bottom-up: add the six new db_manager functions first (old route SQL still works in parallel), then swap each route to use them one by one, then delete the now-unused helpers, then replace silent `except: pass` with logged flash-message errors.

**Tech Stack:** Python 3.11, FastAPI, SQLite (sqlite3), Jinja2 templates, Anthropic Haiku (brain.py)

**Spec:** `docs/superpowers/specs/2026-03-14-code-quality-design.md`

> **Note on testing:** No test suite exists yet. Each task includes a manual smoke-test step — open the page in a browser and confirm it renders identically to before. Do not skip these steps.

---

## Files Modified

| File | What changes |
|---|---|
| `brain.py` | Delete `extract_all_buys()` (lines 14–64) |
| `db_manager.py` | Add 6 new query functions after existing `get_channel_mentions()` |
| `main.py` | Remove `_get_conn()`, `_export_rows()`, dead import; slim 7 routes; add logger; fix 7 `except: pass` |

No new files created. No template changes.

---

## Chunk 1: Dead Code Removal

### Task 1: Delete `extract_all_buys()` from `brain.py`

**Files:**
- Modify: `brain.py:14-64`

- [ ] **Step 1: Confirm nothing calls `extract_all_buys`**

  ```bash
  grep -rn "extract_all_buys" .
  ```

  Expected: only one hit — the definition in `brain.py`. If any other file imports or calls it, stop and investigate before deleting.

- [ ] **Step 2: Delete lines 14–64 of `brain.py`**

  Remove from the line starting `def extract_all_buys(transcript):` through its closing `return []`. The file should now start with `def analyze_transcript(transcript, title="", language="en"):` (after the imports block).

  After editing, `brain.py` should have exactly two top-level callables: nothing else and `analyze_transcript`.

- [ ] **Step 3: Verify the file is importable**

  ```bash
  python3 -c "import brain; print('OK')"
  ```

  Expected: `OK` with no errors.

- [ ] **Step 4: Start the server and confirm it still starts**

  ```bash
  uvicorn main:app --reload
  ```

  Expected: server starts without ImportError or AttributeError.

- [ ] **Step 5: Commit**

  ```bash
  git add brain.py
  git commit -m "refactor: delete dead extract_all_buys() from brain.py"
  ```

---

## Chunk 2: New db_manager Functions

Add six functions to the bottom of `db_manager.py`, before the `init_db()` call at line 1057. Insert them immediately after `get_channel_mentions()` (line 946).

### Task 2: `get_home_stats()`

**Files:**
- Modify: `db_manager.py` (append after line 946)

- [ ] **Step 1: Add the function**

  ```python
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
  ```

- [ ] **Step 2: Verify importable and callable**

  ```bash
  python3 -c "import db_manager; r = db_manager.get_home_stats(); print(r.keys())"
  ```

  Expected: `dict_keys(['recent_scans', 'channels_total'])` with no errors.

### Task 3: `get_stock_detail(ticker, days)`

**Files:**
- Modify: `db_manager.py`

- [ ] **Step 1: Add the function**

  ```python
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
              rt.price_at_publish,
              rt.roi_7d,
              rt.roi_30d
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
  ```

- [ ] **Step 2: Verify callable**

  ```bash
  python3 -c "import db_manager; r = db_manager.get_stock_detail('AAPL', 90); print(r.keys())"
  ```

  Expected: `dict_keys(['mentions', 'sentiment_summary', 'roi_data'])`.

### Task 4: `get_channel_detail(channel_id)`

**Files:**
- Modify: `db_manager.py`

- [ ] **Step 1: Add the function**

  ```python
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
  ```

- [ ] **Step 2: Verify callable**

  ```bash
  python3 -c "
  import db_manager, sqlite3
  conn = sqlite3.connect('vetted.db')
  ch_id = conn.execute('SELECT id FROM channels LIMIT 1').fetchone()[0]
  conn.close()
  r = db_manager.get_channel_detail(ch_id)
  print(r.keys())
  "
  ```

  Expected: `dict_keys(['channel', 'videos', 'pick_accuracy', 'top_stocks', 'channel_mentions'])`.

### Task 5: `get_channels_list()`

**Files:**
- Modify: `db_manager.py`

- [ ] **Step 1: Add the function**

  ```python
  def get_channels_list():
      """Return all channels with video count. Superset used by /channels, /export, /analyst."""
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
              COUNT(v.video_id) AS video_count
          FROM channels c
          LEFT JOIN videos v ON v.channel_id = c.id
          GROUP BY c.id
          ORDER BY c.language ASC, c.name ASC
          """
      )
      rows = [dict(row) for row in cursor.fetchall()]
      conn.close()
      return rows
  ```

- [ ] **Step 2: Verify callable**

  ```bash
  python3 -c "import db_manager; rows = db_manager.get_channels_list(); print(len(rows), rows[0].keys() if rows else 'empty')"
  ```

  Expected: count ≥ 1, keys include `id`, `name`, `language`, `video_count`.

### Task 6: `get_stocks_list(days)`

**Files:**
- Modify: `db_manager.py`

- [ ] **Step 1: Add the function**

  ```python
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
  ```

- [ ] **Step 2: Verify callable**

  ```bash
  python3 -c "import db_manager; rows = db_manager.get_stocks_list(90); print(len(rows), rows[0].keys() if rows else 'empty')"
  ```

  Expected: list of dicts with `ticker`, `mention_count`, `bullish_count`, etc.

### Task 7: `get_export_rows()`

**Files:**
- Modify: `db_manager.py`

- [ ] **Step 1: Add the function**

  ```python
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
  ```

- [ ] **Step 2: Verify callable**

  ```bash
  python3 -c "import db_manager; rows = db_manager.get_export_rows(); print(len(rows), rows[0].keys() if rows else 'empty')"
  ```

  Expected: list of dicts with `date`, `channel`, `ticker`, `roi_7d`, `roi_30d`, etc.

- [ ] **Step 3: Commit all six new functions**

  ```bash
  git add db_manager.py
  git commit -m "refactor: add get_home_stats, get_stock_detail, get_channel_detail, get_channels_list, get_stocks_list, get_export_rows to db_manager"
  ```

---

## Chunk 3: Route Cleanup

For each route: replace the inline SQL block with a single db_manager call, then verify the page renders correctly in the browser before moving to the next route. Keep the server running with `--reload` while making changes.

**Start server before beginning this chunk:**
```bash
uvicorn main:app --reload
```

### Task 8: Slim `GET /` (home route)

**Files:**
- Modify: `main.py:63-133`

- [ ] **Step 1: Replace inline DB block in the home route**

  The home route currently calls `db_manager.get_consensus_picks()` and `db_manager.get_channel_leaderboard()` correctly, then opens its own connection for `recent_scans` and `channels_total`. Replace that second block.

  > **Note:** `get_home_stats()` uses `datetime.now(timezone.utc)` while the current route uses `datetime.utcnow()`. Both produce the same `%Y-%m-%d` UTC date string — no functional difference.

  Find this block (lines 81–98) inside the `try:`:
  ```python
  conn = _get_conn()
  cursor = conn.cursor()
  today = datetime.utcnow().strftime("%Y-%m-%d")
  cursor.execute(...)
  recent_scans = [dict(row) for row in cursor.fetchall()]
  cursor.execute("SELECT COUNT(*) AS cnt FROM channels")
  channels_total = cursor.fetchone()["cnt"]
  conn.close()
  ```

  Replace with:
  ```python
  stats = db_manager.get_home_stats()
  recent_scans = stats["recent_scans"]
  channels_total = stats["channels_total"]
  ```

- [ ] **Step 2: Smoke test**

  Open `http://localhost:8000/` in a browser. Confirm:
  - KPI boxes show correct numbers
  - Consensus table renders
  - Recent scans table shows channels with last_scanned set
  - No 500 error

### Task 9: Slim `GET /stock/{ticker}`

**Files:**
- Modify: `main.py:146-244`

- [ ] **Step 1: Replace inline DB block**

  > **Note:** This step temporarily removes error handling from this route. It is added back in Task 17 Step 2. Do not skip to the next route until Task 17 Step 2 is also complete.

  The entire `try:` block (lines 152–232) and default variable setup (lines 148–151) become:

  ```python
  @app.get("/stock/{ticker}")
  def stock(ticker: str, request: Request, days: int = 90, credentials: HTTPBasicCredentials = Depends(verify)):
      data = db_manager.get_stock_detail(ticker, days)
      return templates.TemplateResponse(
          "stock.html",
          {
              "request": request,
              "ticker": ticker.upper(),
              "mentions": data["mentions"],
              "sentiment_summary": data["sentiment_summary"],
              "roi_data": data["roi_data"],
              "days": days,
          },
      )
  ```

- [ ] **Step 2: Smoke test**

  Navigate to any stock page, e.g. `http://localhost:8000/stock/AAPL`. Confirm:
  - Mentions table renders with channel names and dates
  - Sentiment summary shows correct bullish/bearish/neutral counts
  - ROI chart renders (or is empty if no ROI data)
  - No 500 error

### Task 10: Slim `GET /channel/{channel_id}`

**Files:**
- Modify: `main.py:251-337`

- [ ] **Step 1: Replace inline DB block**

  The route currently has a `try/except` block (lines 258–323) then a separate `db_manager.get_channel_mentions()` call (line 325). Replace the entire route body:

  ```python
  @app.get("/channel/{channel_id}")
  def channel(channel_id: str, request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
      data = db_manager.get_channel_detail(channel_id)
      return templates.TemplateResponse(
          "channel.html",
          {
              "request": request,
              "channel": data["channel"],
              "videos": data["videos"],
              "pick_accuracy": data["pick_accuracy"],
              "top_stocks": data["top_stocks"],
              "channel_mentions": data["channel_mentions"],
          },
      )
  ```

- [ ] **Step 2: Smoke test**

  Navigate to any channel page, e.g. `http://localhost:8000/channel/<any_channel_id>`. Confirm:
  - Channel name and metadata display
  - Videos list renders
  - Pick accuracy shows (or "N/A" if no buy picks with ROI)
  - Top stocks table renders
  - Mentions table renders (this is `channel_mentions`)
  - No 500 error

### Task 11: Slim `GET /channels`

**Files:**
- Modify: `main.py:344-372`

- [ ] **Step 1: Replace inline DB block**

  ```python
  @app.get("/channels")
  def channels(request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
      channels_list = db_manager.get_channels_list()
      return templates.TemplateResponse(
          "channels.html",
          {"request": request, "channels": channels_list},
      )
  ```

- [ ] **Step 2: Smoke test**

  Open `http://localhost:8000/channels`. Confirm:
  - All channels listed with names, language badges, video counts
  - No 500 error

### Task 12: Slim `GET /stocks`

**Files:**
- Modify: `main.py:379-418`

- [ ] **Step 1: Replace inline DB block**

  ```python
  @app.get("/stocks")
  def stocks(request: Request, days: int = 90, credentials: HTTPBasicCredentials = Depends(verify)):
      stocks_list = db_manager.get_stocks_list(days)
      return templates.TemplateResponse(
          "stocks.html",
          {"request": request, "stocks": stocks_list, "days": days},
      )
  ```

- [ ] **Step 2: Smoke test**

  Open `http://localhost:8000/stocks`. Confirm:
  - Tickers listed with mention counts and sentiment columns
  - No 500 error

### Task 13: Slim `GET /export` (page) and `GET /export/csv` + `GET /export/json`

**Files:**
- Modify: `main.py:457-695`

- [ ] **Step 1: Slim the export page route**

  ```python
  @app.get("/export")
  def export_page(request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
      channels_list = db_manager.get_channels_list()
      return templates.TemplateResponse(
          "export.html",
          {"request": request, "channels": channels_list},
      )
  ```

- [ ] **Step 2: Update `GET /export/csv` to use `db_manager.get_export_rows()`**

  ```python
  @app.get("/export/csv")
  def export_csv(
      request: Request,
      ticker: str = None,
      channel_id: str = None,
      date_from: str = None,
      date_to: str = None,
      sentiment: str = None,
      credentials: HTTPBasicCredentials = Depends(verify),
  ):
      rows = db_manager.get_export_rows(ticker, channel_id, date_from, date_to, sentiment)
      output = io.StringIO()
      writer = csv.writer(output)
      writer.writerow(CSV_COLUMNS)
      for row in rows:
          writer.writerow([row.get(col) for col in CSV_COLUMNS])
      output.seek(0)
      return StreamingResponse(
          iter([output.getvalue()]),
          media_type="text/csv",
          headers={"Content-Disposition": "attachment; filename=vetted_export.csv"},
      )
  ```

- [ ] **Step 3: Update `GET /export/json` similarly**

  ```python
  @app.get("/export/json")
  def export_json(
      request: Request,
      ticker: str = None,
      channel_id: str = None,
      date_from: str = None,
      date_to: str = None,
      sentiment: str = None,
      credentials: HTTPBasicCredentials = Depends(verify),
  ):
      rows = db_manager.get_export_rows(ticker, channel_id, date_from, date_to, sentiment)
      return StreamingResponse(
          iter([json.dumps(rows)]),
          media_type="application/json",
          headers={"Content-Disposition": "attachment; filename=vetted_export.json"},
      )
  ```

- [ ] **Step 4: Smoke test**

  - Open `http://localhost:8000/export` — channels dropdown populates
  - Click "Download CSV" — file downloads with correct columns
  - Click "Download JSON" — file downloads as valid JSON array

### Task 14: Slim `GET /analyst`

**Files:**
- Modify: `main.py:480-534`

- [ ] **Step 1: Replace inline `all_channels` DB query**

  In the `analyst` route, remove the inline:
  ```python
  conn = _get_conn()
  cursor = conn.cursor()
  cursor.execute("SELECT id, name, language FROM channels ORDER BY language, name")
  all_channels = [dict(r) for r in cursor.fetchall()]
  conn.close()
  ```

  Replace with:
  ```python
  all_channels = db_manager.get_channels_list()
  ```

  The template uses only `id`, `name`, `language` from `all_channels` — all three are present in `get_channels_list()` results.

- [ ] **Step 2: Smoke test**

  Open `http://localhost:8000/analyst`. Confirm:
  - Channel multi-select populates with all channels grouped by language
  - Table renders in default mode
  - No 500 error

- [ ] **Step 3: Commit all route changes**

  ```bash
  git add main.py
  git commit -m "refactor: extract inline SQL from all routes into db_manager"
  ```

---

## Chunk 4: Cleanup + Error Handling

### Task 15: Delete `_export_rows()` and `_get_conn()` from `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Confirm nothing still calls `_export_rows` or `_get_conn` in main.py**

  ```bash
  grep -n "_export_rows\|_get_conn" main.py
  ```

  Expected: zero matches. If any remain, a route was missed in Chunk 3 — fix it before continuing.

- [ ] **Step 2: Delete `_get_conn()` (lines 53–56)**

  Remove:
  ```python
  def _get_conn():
      conn = sqlite3.connect(DB)
      conn.row_factory = sqlite3.Row
      return conn
  ```

- [ ] **Step 3: Delete `DB = "vetted.db"` constant (line 50) and `_export_rows()` function (lines 581–634)**

  The `DB` constant is only used by `_get_conn()` which is now gone. `_export_rows()` is replaced by `db_manager.get_export_rows()`.

  > **Important:** `CSV_COLUMNS` is defined at lines 637–641, immediately after `_export_rows()`. Stop the deletion at line 634. Do **not** delete `CSV_COLUMNS` — it is still used by the `/export/csv` route.

- [ ] **Step 4: Remove now-unused imports**

  Check which imports are no longer needed after removing `_get_conn` and `_export_rows`:

  ```bash
  grep -n "^import sqlite3" main.py
  ```

  `sqlite3` was only used by `_get_conn()` — remove it from the imports block.

  `datetime` was used in `_export_rows()` for the CSV filename timestamp in `analyst_export`. Check if it's still used elsewhere in `main.py`:

  ```bash
  grep -n "datetime" main.py
  ```

  If `datetime` is still used (e.g. in `analyst_export` filename), keep the import. If not, remove it.

- [ ] **Step 5: Verify the server still starts**

  ```bash
  python3 -c "import main; print('OK')"
  ```

  Expected: `OK`.

- [ ] **Step 6: Commit**

  ```bash
  git add main.py
  git commit -m "refactor: delete _get_conn, _export_rows, DB constant and unused imports from main.py"
  ```

### Task 16: Standardise error handling — add logger

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add logger to `main.py` imports block**

  After the existing `import os` line, add:
  ```python
  import logging
  logger = logging.getLogger(__name__)
  ```

- [ ] **Step 2: Verify logger import works**

  ```bash
  python3 -c "import main; print('OK')"
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add main.py
  git commit -m "refactor: add module-level logger to main.py"
  ```

### Task 17: Replace `except Exception: pass` in each route

**Files:**
- Modify: `main.py`

There are 7 `except Exception: pass` blocks remaining across route handlers after `_export_rows()` was deleted. Replace each with the standard pattern. Work through them one at a time.

The pattern to apply in each route:

```python
except Exception:
    logger.exception("<route_name> failed")
    return templates.TemplateResponse(
        "<template>.html",
        {
            "request": request,
            # same default empty vars the route initialised before
            "msg": "Data temporarily unavailable — check server logs.",
            # ... other required template vars ...
        },
    )
```

- [ ] **Step 1: Fix `GET /` (home route)**

  The home route's `try` block calls three db_manager functions. On failure, return:

  ```python
  except Exception:
      logger.exception("home route failed")
      return templates.TemplateResponse("home.html", {
          "request": request,
          "consensus": [],
          "leaderboard": [],
          "recent_scans": [],
          "channels_total": 0,
          "days": days,
          "min_channels": min_channels,
          "lang": lang,
          "sentiment": sentiment,
          "mood_pct": 0,
          "mood_label": "Unknown",
          "mood_bull": 0,
          "mood_bear": 0,
          "mood_neut": 0,
          "best_roi": None,
          "msg": "Data temporarily unavailable — check server logs.",
      })
  ```

- [ ] **Step 2: Fix `GET /stock/{ticker}`**

  After slimming in Task 9, the route no longer has a try/except — add one:

  ```python
  @app.get("/stock/{ticker}")
  def stock(ticker: str, request: Request, days: int = 90, credentials: HTTPBasicCredentials = Depends(verify)):
      try:
          data = db_manager.get_stock_detail(ticker, days)
      except Exception:
          logger.exception("get_stock_detail failed ticker=%s days=%s", ticker, days)
          return templates.TemplateResponse("stock.html", {
              "request": request,
              "ticker": ticker.upper(),
              "mentions": [],
              "sentiment_summary": {"bullish": 0, "bearish": 0, "neutral": 0},
              "roi_data": [],
              "days": days,
              "msg": "Data temporarily unavailable — check server logs.",
          })
      return templates.TemplateResponse("stock.html", {
          "request": request,
          "ticker": ticker.upper(),
          **data,
          "days": days,
      })
  ```

- [ ] **Step 3: Fix `GET /channel/{channel_id}`**

  ```python
  @app.get("/channel/{channel_id}")
  def channel(channel_id: str, request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
      try:
          data = db_manager.get_channel_detail(channel_id)
      except Exception:
          logger.exception("get_channel_detail failed channel_id=%s", channel_id)
          return templates.TemplateResponse("channel.html", {
              "request": request,
              "channel": None,
              "videos": [],
              "pick_accuracy": None,
              "top_stocks": [],
              "channel_mentions": [],
              "msg": "Data temporarily unavailable — check server logs.",
          })
      return templates.TemplateResponse("channel.html", {"request": request, **data})
  ```

- [ ] **Step 4: Fix `GET /channels`**

  ```python
  @app.get("/channels")
  def channels(request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
      try:
          channels_list = db_manager.get_channels_list()
      except Exception:
          logger.exception("get_channels_list failed")
          channels_list = []
      return templates.TemplateResponse(
          "channels.html",
          {"request": request, "channels": channels_list,
           **({"msg": "Data temporarily unavailable — check server logs."} if not channels_list else {})},
      )
  ```

- [ ] **Step 5: Fix `GET /stocks`**

  ```python
  @app.get("/stocks")
  def stocks(request: Request, days: int = 90, credentials: HTTPBasicCredentials = Depends(verify)):
      try:
          stocks_list = db_manager.get_stocks_list(days)
      except Exception:
          logger.exception("get_stocks_list failed days=%s", days)
          stocks_list = []
      return templates.TemplateResponse(
          "stocks.html",
          {"request": request, "stocks": stocks_list, "days": days,
           **({"msg": "Data temporarily unavailable — check server logs."} if not stocks_list else {})},
      )
  ```

- [ ] **Step 6: Fix `GET /export` page route**

  ```python
  @app.get("/export")
  def export_page(request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
      try:
          channels_list = db_manager.get_channels_list()
      except Exception:
          logger.exception("export_page get_channels_list failed")
          channels_list = []
      return templates.TemplateResponse(
          "export.html",
          {"request": request, "channels": channels_list},
      )
  ```

- [ ] **Step 7: Fix `GET /analyst`**

  Replace the existing route body. The filter-building logic at the top stays identical; only the try/except block changes:

  ```python
  @app.get("/analyst")
  def analyst(
      request: Request,
      table_mode: str = "ticker",
      chart_mode: str = "timeline",
      date_from: str = "",
      date_to: str = "",
      channels_filter: str = "",
      lang_filter: str = "",
      sentiment: str = "all",
      recommendation: str = "all",
      min_confidence: int = 0,
      tickers_filter: str = "",
      credentials: HTTPBasicCredentials = Depends(verify),
  ):
      filters = {
          "date_from": date_from or None,
          "date_to": date_to or None,
          "channels": [c.strip() for c in channels_filter.split(",") if c.strip()],
          "lang": [l.strip() for l in lang_filter.split(",") if l.strip()],
          "sentiment": sentiment,
          "recommendation": recommendation,
          "min_confidence": min_confidence,
          "tickers": [t.strip().upper() for t in tickers_filter.split(",") if t.strip()],
      }

      _ctx = {
          "request": request,
          "table_mode": table_mode,
          "chart_mode": chart_mode,
          "date_from": date_from,
          "date_to": date_to,
          "channels_filter": channels_filter,
          "lang_filter": lang_filter,
          "sentiment": sentiment,
          "recommendation": recommendation,
          "min_confidence": min_confidence,
          "tickers_filter": tickers_filter,
      }

      try:
          table_data = db_manager.get_analyst_data(table_mode, filters)
          chart_data = db_manager.get_analyst_chart_data(chart_mode, filters)
          all_channels = db_manager.get_channels_list()
      except Exception:
          logger.exception("analyst route failed")
          return templates.TemplateResponse("analyst.html", {
              **_ctx,
              "table_data": [],
              "chart_data": {},
              "all_channels": [],
              "row_count": 0,
              "msg": "Data temporarily unavailable — check server logs.",
          })

      return templates.TemplateResponse("analyst.html", {
          **_ctx,
          "table_data": table_data,
          "chart_data": chart_data,
          "all_channels": all_channels,
          "row_count": len(table_data),
      })
  ```

- [ ] **Step 8: Fix `GET /export/csv`**

  `_export_rows()` previously swallowed errors silently. Now that it's replaced by `db_manager.get_export_rows()`, the route needs its own error handling:

  ```python
  @app.get("/export/csv")
  def export_csv(
      request: Request,
      ticker: str = None,
      channel_id: str = None,
      date_from: str = None,
      date_to: str = None,
      sentiment: str = None,
      credentials: HTTPBasicCredentials = Depends(verify),
  ):
      try:
          rows = db_manager.get_export_rows(ticker, channel_id, date_from, date_to, sentiment)
      except Exception:
          logger.exception("export_csv get_export_rows failed")
          rows = []

      output = io.StringIO()
      writer = csv.writer(output)
      writer.writerow(CSV_COLUMNS)
      for row in rows:
          writer.writerow([row.get(col) for col in CSV_COLUMNS])
      output.seek(0)
      return StreamingResponse(
          iter([output.getvalue()]),
          media_type="text/csv",
          headers={"Content-Disposition": "attachment; filename=vetted_export.csv"},
      )
  ```

- [ ] **Step 9: Fix `GET /export/json`**

  ```python
  @app.get("/export/json")
  def export_json(
      request: Request,
      ticker: str = None,
      channel_id: str = None,
      date_from: str = None,
      date_to: str = None,
      sentiment: str = None,
      credentials: HTTPBasicCredentials = Depends(verify),
  ):
      try:
          rows = db_manager.get_export_rows(ticker, channel_id, date_from, date_to, sentiment)
      except Exception:
          logger.exception("export_json get_export_rows failed")
          rows = []

      return StreamingResponse(
          iter([json.dumps(rows)]),
          media_type="application/json",
          headers={"Content-Disposition": "attachment; filename=vetted_export.json"},
      )
  ```

- [ ] **Step 10: Confirm zero silent `except: pass` blocks remain**

  ```bash
  grep -A1 "except Exception:" main.py | grep -c "pass"
  grep -A1 "except:" main.py | grep -c "pass"
  ```

  Expected: both commands output `0`. Any non-zero result means a silent block was missed.

- [ ] **Step 11: Commit**

  ```bash
  git add main.py
  git commit -m "refactor: replace silent except:pass with logged flash-message error handling in all routes"
  ```

---

## Chunk 5: Smoke Test Checklist

Run the server and verify every page end-to-end.

```bash
uvicorn main:app --reload
```

- [ ] `GET /` — Dashboard loads, KPI boxes show data, consensus table renders, leaderboard renders, recent scans show
- [ ] `GET /stocks` — Tickers listed with sentiment counts
- [ ] `GET /stock/AAPL` (or any ticker in DB) — Mentions table, sentiment summary, ROI chart section all render
- [ ] `GET /channels` — All channels listed with language badges and video counts
- [ ] `GET /channel/<any_id>` — Channel detail: name, videos, pick accuracy, top stocks, mentions all render
- [ ] `GET /analyst` — Filter panel renders, table renders, chart renders, channel multi-select populated
- [ ] `GET /export` — Page loads, channels dropdown populated; CSV download works; JSON download works
- [ ] Trigger a deliberate error: in `db_manager.py` line 5, temporarily change `DB_NAME = "vetted.db"` to `DB_NAME = "nonexistent.db"`, save, let uvicorn reload, visit `http://localhost:8000/` — confirm the flash message "Data temporarily unavailable — check server logs." appears instead of a 500 error or blank page. Then revert `DB_NAME` back to `"vetted.db"` and confirm all pages load normally again.
- [ ] Restore `DB_NAME = "vetted.db"`, confirm all pages load normally again

- [ ] **Final commit (if any cleanup from smoke test)**

  ```bash
  git add -p
  git commit -m "refactor: code quality cleanup — all smoke tests pass"
  ```
