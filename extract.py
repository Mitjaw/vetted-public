"""
Transcript fetcher via TranscriptAPI.com.

Single entry point: get_transcript(video_id) -> str | None

Retry policy (per TranscriptAPI docs):
  408, 503  — transient, exponential backoff 1s → 2s → 4s, max 3 attempts
  429       — rate-limited, respect Retry-After header then exponential backoff
  400/401/402/404/422 — non-retryable, return None immediately
  500       — treat as transient (same backoff)
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)

_BASE        = "https://transcriptapi.com/api/v2/youtube"
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds; doubles each attempt: 1s, 2s, 4s

_NO_RETRY_CODES = {400, 401, 402, 404, 422}


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('TRANSCRIPTAPI_KEY', '')}"}


def _log_rate_headers(resp: requests.Response) -> None:
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None and int(remaining) < 20:
        _log.warning(
            "TranscriptAPI rate limit low: %s/%s remaining (resets %s)",
            remaining,
            resp.headers.get("X-RateLimit-Limit", "?"),
            resp.headers.get("X-RateLimit-Reset", "?"),
        )


def get_transcript(video_id: str) -> str | None:
    """
    Fetch a plain-text transcript for a YouTube video ID via TranscriptAPI.

    Returns the transcript string on success, or None if unavailable.
    Never raises — all failures are logged and return None.
    """
    if not os.getenv("TRANSCRIPTAPI_KEY"):
        _log.error("TRANSCRIPTAPI_KEY not set — cannot fetch transcript for %s", video_id)
        return None

    url = f"{_BASE}/transcript"
    params = {"video_url": video_id, "format": "text", "include_timestamp": "false"}

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=_headers(), timeout=30)
        except requests.RequestException as e:
            _log.warning("TranscriptAPI connection error (attempt %d/%d): %s", attempt, _MAX_RETRIES, e)
            if attempt == _MAX_RETRIES:
                return None
            time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
            continue

        _log_rate_headers(resp)

        if resp.status_code == 200:
            data = resp.json()
            text = data.get("transcript") or ""
            if isinstance(text, list):
                text = " ".join(seg.get("text", "") for seg in text)
            text = text.strip()
            if text:
                _log.info("TranscriptAPI: fetched %s (%d chars)", video_id, len(text))
            else:
                _log.info("TranscriptAPI: empty transcript for %s", video_id)
            return text or None

        if resp.status_code in _NO_RETRY_CODES:
            _log.info("TranscriptAPI: %d for %s — not retrying", resp.status_code, video_id)
            return None

        wait = (
            float(resp.headers.get("Retry-After", _BACKOFF_BASE * (2 ** (attempt - 1))))
            if resp.status_code == 429
            else _BACKOFF_BASE * (2 ** (attempt - 1))
        )
        _log.warning(
            "TranscriptAPI: %d for %s — waiting %.1fs (attempt %d/%d)",
            resp.status_code, video_id, wait, attempt, _MAX_RETRIES,
        )
        if attempt == _MAX_RETRIES:
            _log.error("TranscriptAPI: giving up on %s after %d attempts", video_id, _MAX_RETRIES)
            return None
        time.sleep(wait)

    return None
