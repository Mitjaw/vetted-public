# TranscriptAPI Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the entire extract.py transcript fetching stack (youtube-transcript-api, yt-dlp, Webshare proxies, cookies.txt) with TranscriptAPI. Use their two free endpoints to eliminate YouTube Data API dependency for daily scan video discovery and for channel handle resolution.

**Architecture:** New `extract.py` (~60 lines) wraps a single HTTP GET to TranscriptAPI with full retry/backoff logic. `channel_fetcher.py` gains `resolve_channel_id()` (free) and replaces `get_recent_videos()` with the free RSS endpoint. `scanner.py` loses all `YouTubeRateLimitError` handling and pre-fetch sleeps. `requirements.txt` loses `youtube-transcript-api` and `yt-dlp`.

**Tech Stack:** `requests` (already in project), `TRANSCRIPTAPI_KEY` env var

---

## Free vs Paid — clear summary

| Feature | Endpoint | Cost | Used for |
|---|---|---|---|
| Transcript fetch | `/api/v2/youtube/transcript` | 1 credit | Every new video processed |
| Latest 15 videos | `/api/v2/youtube/channel/latest` | **FREE** | Daily scan — replaces YouTube Data API search |
| Resolve @handle → UC ID | `/api/v2/youtube/channel/resolve` | **FREE** | Admin "Add Channel" flow |
| Backfill pagination | *(not used — keep YouTube Data API)* | 0 | Playlist read has no IP block risk |

At 15 channels × avg 2 videos/day = 900 transcript credits/month. Starter plan ($5/mo) gives ~1,350 credits — comfortable headroom.

---

## File Map

| Action | Path | Reason |
|---|---|---|
| Delete + recreate | `extract.py` | Entire old stack gone; new ~60-line clean version |
| Modify | `channel_fetcher.py` | Replace `get_recent_videos()` with free RSS; add `resolve_channel_id()` |
| Modify | `scanner.py` | Remove `YouTubeRateLimitError` import/handling and pre-transcript sleeps |
| Modify | `.env.example` | Remove WEBSHARE vars; add TRANSCRIPTAPI_KEY |
| Modify | `requirements.txt` | Remove `youtube-transcript-api`, `yt-dlp` |

---

## Task 1: New `extract.py`

**Files:**
- Delete + recreate: `extract.py`

- [ ] **Step 1: Delete old `extract.py` and write the new one**

```python
"""
Transcript fetcher via TranscriptAPI.com.

Replaces the old youtube-transcript-api / yt-dlp / Webshare proxy stack entirely.
Single entry point: get_transcript(video_id) -> str | None

Retry policy (per TranscriptAPI best-practices docs):
  408, 503        — transient, exponential backoff 1s/2s/4s, max 3 attempts
  429             — rate-limited, respect Retry-After header then exponential backoff
  400/401/402/404/422 — non-retryable, return None immediately
  500             — maybe retryable, treat as transient (1 attempt)
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)

_BASE         = "https://transcriptapi.com/api/v2/youtube"
_MAX_RETRIES  = 3
_BACKOFF_BASE = 1.0   # seconds; doubled each attempt: 1s, 2s, 4s

# Log a warning when this many rate-limit credits remain for the minute window
_RATE_LIMIT_WARN_THRESHOLD = 20

_NO_RETRY_CODES = {400, 401, 402, 404, 422}


def _api_key() -> str | None:
    return os.getenv("TRANSCRIPTAPI_KEY")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_api_key()}"}


def _log_rate_headers(resp: requests.Response) -> None:
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        remaining = int(remaining)
        if remaining < _RATE_LIMIT_WARN_THRESHOLD:
            limit = resp.headers.get("X-RateLimit-Limit", "?")
            reset = resp.headers.get("X-RateLimit-Reset", "?")
            _log.warning(
                "TranscriptAPI rate limit low: %d/%s remaining (resets at %s)",
                remaining, limit, reset,
            )


def get_transcript(video_id: str) -> str | None:
    """
    Fetch a plain-text transcript for a YouTube video ID via TranscriptAPI.

    Returns the transcript string on success, or None if unavailable
    (video has no transcript, video not found, or account issue).
    Raises nothing — all failures are logged and return None.
    """
    if not _api_key():
        _log.error("TRANSCRIPTAPI_KEY not set — cannot fetch transcript for %s", video_id)
        return None

    url    = f"{_BASE}/transcript"
    params = {
        "video_url":         video_id,
        "format":            "text",
        "include_timestamp": "false",
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=_headers(), timeout=30)
        except requests.RequestException as e:
            _log.warning("TranscriptAPI request error (attempt %d/%d): %s", attempt, _MAX_RETRIES, e)
            if attempt == _MAX_RETRIES:
                return None
            time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
            continue

        _log_rate_headers(resp)

        if resp.status_code == 200:
            data = resp.json()
            text = data.get("transcript") or ""
            # format=text returns a plain string; guard against unexpected list
            if isinstance(text, list):
                text = " ".join(seg.get("text", "") for seg in text)
            text = text.strip()
            if text:
                _log.info("TranscriptAPI: transcript fetched for %s (%d chars)", video_id, len(text))
                return text
            _log.info("TranscriptAPI: empty transcript for %s", video_id)
            return None

        if resp.status_code in _NO_RETRY_CODES:
            _log.info(
                "TranscriptAPI: %d for %s — not retrying (no transcript or account issue)",
                resp.status_code, video_id,
            )
            return None

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", _BACKOFF_BASE * (2 ** (attempt - 1))))
            _log.warning(
                "TranscriptAPI: 429 rate limited for %s — waiting %.1fs (attempt %d/%d)",
                video_id, retry_after, attempt, _MAX_RETRIES,
            )
            if attempt == _MAX_RETRIES:
                return None
            time.sleep(retry_after)
            continue

        # 408, 500, 503 — transient
        wait = _BACKOFF_BASE * (2 ** (attempt - 1))
        _log.warning(
            "TranscriptAPI: %d for %s — waiting %.1fs (attempt %d/%d)",
            resp.status_code, video_id, wait, attempt, _MAX_RETRIES,
        )
        if attempt == _MAX_RETRIES:
            _log.error("TranscriptAPI: giving up on %s after %d attempts", video_id, _MAX_RETRIES)
            return None
        time.sleep(wait)

    return None
```

- [ ] **Step 2: Verify it imports and returns None when key is absent**

```bash
cd /Users/mitjawilms/DeInfluencer
python3 -c "
import os; os.environ.pop('TRANSCRIPTAPI_KEY', None)
from extract import get_transcript
r = get_transcript('dQw4w9WgXcQ')
print('result without key:', r)
assert r is None
print('ok')
"
```
Expected: `result without key: None` / `ok`

---

## Task 2: Update `channel_fetcher.py`

**Files:**
- Modify: `channel_fetcher.py`

Two changes:
1. Replace `get_recent_videos()` with TranscriptAPI free RSS endpoint (`channel/latest`)
2. Add `resolve_channel_id()` using free `channel/resolve` endpoint

The `get_all_videos()` function is **unchanged** — it uses YouTube Data API for backfill pagination which has no IP block risk and costs no TranscriptAPI credits.

- [ ] **Step 1: Replace `get_recent_videos()` with free RSS endpoint**

Replace the entire `get_recent_videos` function:

```python
def get_recent_videos(channel_id, hours=48):
    """
    Fetch videos published in the last `hours` hours for a YouTube channel.
    Uses TranscriptAPI channel/latest (FREE) — returns latest 15 videos via RSS.
    Filters client-side to the requested time window.
    Returns list of dicts: {video_id, title, published_at}
    Returns [] on error — never raises.
    """
    api_key = os.getenv("TRANSCRIPTAPI_KEY")
    if not api_key:
        print("get_recent_videos: TRANSCRIPTAPI_KEY not set")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        resp = requests.get(
            "https://transcriptapi.com/api/v2/youtube/channel/latest",
            params={"channel": channel_id},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        if resp.status_code != 200:
            print(f"get_recent_videos: TranscriptAPI returned {resp.status_code} for {channel_id}")
            return []

        data = resp.json()
        videos = []
        for item in data.get("results", []):
            # RSS field names — extract video_id and published date
            video_id   = item.get("videoId") or item.get("video_id") or item.get("id")
            title      = item.get("title", "")
            published  = item.get("published") or item.get("publishedAt") or item.get("pubDate") or ""

            if not video_id:
                continue

            # Parse published date — RSS can return ISO 8601 or RFC 2822
            pub_dt = None
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %z"):
                try:
                    pub_dt = datetime.strptime(published, fmt)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    break
                except (ValueError, TypeError):
                    continue

            if pub_dt is None:
                # Unknown date — include it to be safe
                videos.append({"video_id": video_id, "title": title, "published_at": published})
                continue

            if pub_dt >= cutoff:
                # Normalise to ISO 8601 string that the rest of the codebase expects
                videos.append({
                    "video_id":    video_id,
                    "title":       title,
                    "published_at": pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

        return videos

    except Exception as e:
        print(f"get_recent_videos error: {e}")
        return []
```

- [ ] **Step 2: Add `resolve_channel_id()` below `get_recent_videos()`**

```python
def resolve_channel_id(input_str: str) -> str | None:
    """
    Convert a YouTube channel @handle, URL, or UC... ID to a canonical UC... ID.
    Uses TranscriptAPI channel/resolve (FREE — no credits consumed).
    Returns the UC... channel_id string, or None on failure.
    """
    api_key = os.getenv("TRANSCRIPTAPI_KEY")
    if not api_key:
        print("resolve_channel_id: TRANSCRIPTAPI_KEY not set")
        return None

    try:
        resp = requests.get(
            "https://transcriptapi.com/api/v2/youtube/channel/resolve",
            params={"input": input_str},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("channel_id")
        print(f"resolve_channel_id: TranscriptAPI returned {resp.status_code} for '{input_str}'")
        return None
    except Exception as e:
        print(f"resolve_channel_id error: {e}")
        return None
```

- [ ] **Step 3: Remove the now-unused `YOUTUBE_API_KEY` from the top of `channel_fetcher.py` if no longer needed**

Keep `YOUTUBE_API_KEY` — it's still used by `get_all_videos()` for backfill.

- [ ] **Step 4: Verify imports cleanly**

```bash
python3 -c "from channel_fetcher import get_recent_videos, resolve_channel_id, get_all_videos; print('ok')"
```

---

## Task 3: Update `scanner.py`

**Files:**
- Modify: `scanner.py`

Three changes:
1. Remove `from extract import YouTubeRateLimitError` — no longer exists
2. Remove both `except YouTubeRateLimitError` blocks and the `raise YouTubeRateLimitError(...)` at end of each function
3. Remove both `time.sleep(3)` before transcript calls (only needed to avoid YouTube rate limits — no longer relevant)
4. Rename call site from `extract.get_clean_transcript(video_id)` to `extract.get_transcript(video_id)`

- [ ] **Step 1: Update imports at top of `scanner.py`**

Remove:
```python
from extract import YouTubeRateLimitError
```

Remove the import entirely. The line `import extract` stays.

- [ ] **Step 2: Update `_scan_channel()` — remove rate-limit handling and sleep**

Before (in the video loop):
```python
        time.sleep(3)  # Respect YouTube rate limits
        try:
            transcript = extract.get_clean_transcript(video_id)
        except YouTubeRateLimitError as e:
            logging.warning("Rate limited by YouTube — stopping scan early. %s", e)
            _rate_limited = True
            break
```

After:
```python
        transcript = extract.get_transcript(video_id)
```

Also remove the `_rate_limited` variable, its check at the bottom, and the final `raise`:
```python
    # Remove these:
    _rate_limited = False
    ...
    if _rate_limited:
        raise YouTubeRateLimitError("YouTube rate limit hit — aborting remaining channels.")
```

- [ ] **Step 3: Update `backfill_channel()` — same removals**

Before:
```python
        time.sleep(3)  # Respect YouTube rate limits — prevents IP ban during bulk backfill
        try:
            # max_retries=0: abort immediately on 429 — hard IP blocks last hours,
            # retrying just wastes time. Let the daily budget resume tomorrow.
            transcript = extract.get_clean_transcript(video_id, max_retries=0)
        except YouTubeRateLimitError as e:
            logging.warning("Rate limited by YouTube — stopping backfill early. %s", e)
            _rate_limited = True
            break
```

After:
```python
        transcript = extract.get_transcript(video_id)
```

Also remove `_rate_limited` variable, its check, and the `raise` at the bottom of `backfill_channel()`.

- [ ] **Step 4: Update `scan_all_channels()` and `backfill_all_channels()` — remove rate-limit catch**

In `scan_all_channels()`:
```python
    # Remove the except block:
    for channel in channels:
        try:
            _scan_channel(channel)
        except YouTubeRateLimitError:
            logging.warning("Scan aborted — YouTube rate limit reached. Remaining channels skipped.")
            break
```

Becomes:
```python
    for channel in channels:
        _scan_channel(channel)
```

Same for `backfill_all_channels()` — remove its `except YouTubeRateLimitError` block.

- [ ] **Step 5: Verify scanner imports cleanly**

```bash
python3 -c "import scanner; print('ok')"
```

---

## Task 4: Update `.env.example` and `requirements.txt`

**Files:**
- Modify: `.env.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Update `.env.example`**

Remove:
```
# Webshare residential proxies — required to bypass YouTube transcript rate limits.
# Sign up at https://proxy.webshare.io and get your credentials from the dashboard.
# Use the Residential plan; datacenter IPs are blocked by YouTube.
WEBSHARE_USERNAME=
WEBSHARE_PASSWORD=
```

Add:
```
# TranscriptAPI — handles YouTube transcript fetching without IP blocks.
# Get key at https://transcriptapi.com/dashboard/api-keys ($5/mo Starter plan)
TRANSCRIPTAPI_KEY=
```

- [ ] **Step 2: Remove unused packages from `requirements.txt`**

Remove these lines:
```
youtube-transcript-api
yt-dlp
```

Keep `requests` (already there, used by the new extract.py and channel_fetcher.py).

- [ ] **Step 3: Verify no other file imports the removed packages**

```bash
grep -r "youtube_transcript_api\|yt_dlp\|yt-dlp\|WebshareProxy\|GenericProxy\|cookies\.txt\|WEBSHARE" \
  /Users/mitjawilms/DeInfluencer --include="*.py" -l
```

Expected: no output (no remaining references).

- [ ] **Step 4: Commit everything**

```bash
cd /Users/mitjawilms/DeInfluencer
git add extract.py channel_fetcher.py scanner.py .env.example requirements.txt
git commit -m "feat: replace transcript stack with TranscriptAPI, use free RSS for daily scan"
```

---

## Notes on what we do NOT use (and why)

| Endpoint | Why not |
|---|---|
| `/search` | YouTube Data API search is free within quota and already works |
| `/channel/search` | Not needed in our flow |
| `/channel/videos` | Backfill uses YouTube Data API playlist — no IP block risk, already works |
| `/playlist/videos` | Same as above |

The only credit-costing endpoint we use is `/transcript`. Everything else is either free or handled by existing working code.
