# Analyze Attempts Counter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track how many times each video has been analyzed with zero results, skip high-attempt videos during normal re-analysis, and expose the skip count + a force-reanalyze button in the Channels UI.

**Architecture:** Add an `analyze_attempts` INTEGER column to the `videos` table via the existing try/except migration pattern. `reanalyze_stored_transcripts()` gains a `force=False` parameter — when False it filters out videos with `analyze_attempts >= 2`; when True it processes everything. A new `POST /admin/reanalyze/force` route passes `force=True`. The Channels page GET query fetches the stuck count and passes it to the template, which renders it next to the Re-analyze row along with the new button.

**Tech Stack:** Python 3.11, SQLite (sqlite3), FastAPI, Jinja2

---

## File Map

| File | Change |
|------|--------|
| `db_manager.py` | Add `analyze_attempts` column migration; new `increment_analyze_attempts(video_id)`; update `get_videos_with_transcript_no_mentions(force)` to filter by attempts; new `count_stuck_videos()` |
| `scanner.py` | `reanalyze_stored_transcripts(force=False)` — call `increment_analyze_attempts` when analysis returns empty; pass `force` to db query |
| `main.py` | Add `POST /admin/reanalyze/force` route; pass `stuck_count` to channels template |
| `templates/channels.html` | Show stuck count in Re-analyze row; add Re-analyze All button |

---

## Task 1: DB migration + new db_manager functions

**Files:**
- Modify: `db_manager.py`

### What to know
The `init_db()` function (called at startup) runs all migrations. Existing column additions use a `try/except sqlite3.OperationalError: pass` pattern — the `ALTER TABLE` silently no-ops if the column already exists. Follow that exact pattern.

`get_videos_with_transcript_no_mentions()` is at line ~352. It needs a `force` parameter: when `force=False`, add `AND v.analyze_attempts < 2` to the WHERE clause.

- [ ] **Step 1: Add the migration in `init_db()`**

In `db_manager.py`, find the block that migrates the `channels` table columns (around line 148). Add a similar block immediately after it for the `videos` table:

```python
# Migrate: analyze_attempts counter on videos
try:
    cursor.execute("ALTER TABLE videos ADD COLUMN analyze_attempts INTEGER NOT NULL DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass  # column already exists
```

- [ ] **Step 2: Add `increment_analyze_attempts()`**

Add this function near `get_videos_with_transcript_no_mentions()` (after line ~374):

```python
def increment_analyze_attempts(video_id: str) -> None:
    """Increment the no-result analysis counter for a video."""
    conn = _get_conn()
    conn.execute(
        "UPDATE videos SET analyze_attempts = analyze_attempts + 1 WHERE video_id = ?",
        (video_id,),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 3: Update `get_videos_with_transcript_no_mentions()` to accept `force`**

Change the function signature and WHERE clause:

```python
def get_videos_with_transcript_no_mentions(force: bool = False):
    """
    Return all videos that have a stored transcript but zero mentions.
    force=False (default): skips videos with analyze_attempts >= 2.
    force=True: returns all regardless of attempt count.
    """
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    attempt_filter = "" if force else "AND v.analyze_attempts < 2"
    cursor.execute(
        f"""
        SELECT v.video_id, v.title, v.upload_date, v.transcript, v.channel_id,
               v.analyze_attempts, c.language
        FROM videos v
        JOIN channels c ON c.id = v.channel_id
        WHERE v.transcript IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.video_id = v.video_id)
          {attempt_filter}
        ORDER BY v.upload_date DESC
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
```

- [ ] **Step 4: Add `count_stuck_videos()`**

Add after `increment_analyze_attempts`:

```python
def count_stuck_videos() -> int:
    """Count videos with a transcript, no mentions, and analyze_attempts >= 2."""
    conn = _get_conn()
    cursor = conn.execute(
        """
        SELECT COUNT(*) FROM videos v
        WHERE v.transcript IS NOT NULL
          AND v.analyze_attempts >= 2
          AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.video_id = v.video_id)
        """
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count
```

- [ ] **Step 5: Verify manually**

Start the server and hit any page — `init_db()` runs on startup. Check the DB has the new column:

```bash
sqlite3 vetted.db "PRAGMA table_info(videos);"
```
Expected: a row for `analyze_attempts` with `dflt_value=0`.

---

## Task 2: Update `reanalyze_stored_transcripts()` in scanner.py

**Files:**
- Modify: `scanner.py` (lines ~257–311)

### What to know
`reanalyze_stored_transcripts()` fetches videos, runs `brain.analyze_transcript()`, and saves mentions. When `brain.analyze_transcript()` returns an empty list (no mentions found), we need to call `db_manager.increment_analyze_attempts(video_id)`. The `force` flag is threaded through to the DB query.

- [ ] **Step 1: Add `force` parameter and pass it to the DB call**

Change the function signature from `def reanalyze_stored_transcripts():` to:

```python
def reanalyze_stored_transcripts(force: bool = False):
    """
    Run the extraction pipeline on every video that has a stored transcript
    but zero mentions.
    force=False: skips videos with analyze_attempts >= 2.
    force=True:  processes all regardless of attempt count.
    """
    videos = db_manager.get_videos_with_transcript_no_mentions(force=force)
    logging.info(
        "Analysis: %d video(s) with transcript and no mentions%s.",
        len(videos),
        " (force=all)" if force else "",
    )
```

- [ ] **Step 2: Increment the counter when no mentions are found**

In the analysis loop, find the block that handles the empty-mentions case:

```python
        if not mentions:
            logging.info("Analysis %s: no mentions found.", video_id)
            processed += 1
            continue
```

Replace it with:

```python
        if not mentions:
            db_manager.increment_analyze_attempts(video_id)
            logging.info(
                "Analysis %s: no mentions found (attempt %d).",
                video_id,
                video.get("analyze_attempts", 0) + 1,
            )
            processed += 1
            continue
```

- [ ] **Step 3: Verify the log message**

Restart the server and trigger Re-analyze. For any video that comes back empty you should see a log line like:

```
Analysis abc123: no mentions found (attempt 1).
```

---

## Task 3: New route `POST /admin/reanalyze/force` in main.py

**Files:**
- Modify: `main.py`

### What to know
The existing route at `POST /admin/reanalyze` calls `scanner.reanalyze_stored_transcripts` as a background task. The force variant is identical but passes `force=True`. FastAPI's `BackgroundTasks.add_task` accepts kwargs.

Also, the `GET /channels` route currently passes only `channels` to the template. We need to also fetch and pass `stuck_count`.

- [ ] **Step 1: Add the force route**

Directly after the existing `/admin/reanalyze` route (around line 500):

```python
@app.post("/admin/reanalyze/force")
def admin_reanalyze_force(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Re-run extraction on ALL stored transcripts, including previously-skipped ones."""
    background_tasks.add_task(scanner.reanalyze_stored_transcripts, force=True)
    return RedirectResponse(url="/channels", status_code=303)
```

- [ ] **Step 2: Pass `stuck_count` to the channels template**

Find `GET /channels` (around line 192) and update it:

```python
@app.get("/channels")
def channels(request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
    try:
        channels_list = db_manager.get_channels_list()
    except Exception:
        logger.exception("get_channels_list failed")
        channels_list = []
    try:
        stuck_count = db_manager.count_stuck_videos()
    except Exception:
        stuck_count = 0
    return templates.TemplateResponse(
        "channels.html",
        {
            "request": request,
            "channels": channels_list,
            "stuck_count": stuck_count,
            **({"msg": "Data temporarily unavailable — check server logs."} if not channels_list else {}),
        },
    )
```

---

## Task 4: Update the Channels UI

**Files:**
- Modify: `templates/channels.html`

### What to know
The Re-analyze op-row currently has a single form/button. We need to show the stuck count inline and add a second "Re-analyze All" button. Keep the two buttons close together but visually distinct — the normal button stays secondary; the force button gets an accent border to signal "this does more work". Both are inside separate `<form>` tags (different POST targets) to avoid any cross-submission issues.

The `stuck_count` Jinja2 variable is `0` when there are no stuck videos.

- [ ] **Step 1: Replace the Re-analyze op-row**

Find the existing Re-analyze op-row in `templates/channels.html`:

```html
  <!-- Re-analyze -->
  <div class="op-row">
    <div>
      <div class="op-label">Re-analyze Stored Transcripts &nbsp;<span class="badge-free">FREE</span></div>
      <div class="op-desc">Run the extraction pipeline on videos already in the DB that have a transcript but no mentions — zero API calls.</div>
    </div>
    <form method="POST" action="/admin/reanalyze">
      <button type="submit" class="btn btn-secondary">Re-analyze</button>
    </form>
  </div>
```

Replace with:

```html
  <!-- Re-analyze -->
  <div class="op-row">
    <div>
      <div class="op-label">Re-analyze Stored Transcripts &nbsp;<span class="badge-free">FREE</span></div>
      <div class="op-desc">
        Run the extraction pipeline on unanalyzed transcripts — zero API calls.
        {% if stuck_count > 0 %}
        <span style="color:#f59e0b;margin-left:6px;">
          {{ stuck_count }} video{{ 's' if stuck_count != 1 }} skipped (analyzed ≥2× with no result).
        </span>
        {% endif %}
      </div>
    </div>
    <div style="display:flex;gap:6px;align-items:center;">
      <form method="POST" action="/admin/reanalyze">
        <button type="submit" class="btn btn-secondary">Re-analyze</button>
      </form>
      <form method="POST" action="/admin/reanalyze/force">
        <button type="submit" class="btn btn-secondary"
                style="border-color:var(--accent);color:var(--accent);"
                title="Force re-analysis of all transcripts, including those skipped after 2+ empty attempts.">
          Re-analyze All
        </button>
      </form>
    </div>
  </div>
```

- [ ] **Step 2: Verify in browser**

Load `/channels`. If `stuck_count == 0`, the amber note is hidden and both buttons are visible. Trigger a re-analysis, let it finish, and check the stuck count updates on the next page load.

---

## Quick Smoke Test

After all tasks are complete:

```bash
# 1. Check column was added
sqlite3 vetted.db "SELECT video_id, analyze_attempts FROM videos LIMIT 5;"

# 2. Manually set one video to 2 attempts to test the skip
sqlite3 vetted.db "UPDATE videos SET analyze_attempts = 2 WHERE video_id = (SELECT video_id FROM videos WHERE transcript IS NOT NULL LIMIT 1);"

# 3. Hit /channels — stuck_count should be 1
# 4. Click Re-analyze — that video should be skipped in logs
# 5. Click Re-analyze All — that video should appear in logs
# 6. Reset
sqlite3 vetted.db "UPDATE videos SET analyze_attempts = 0;"
```
