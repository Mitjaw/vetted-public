import logging
import os
import re
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

_log = logging.getLogger(__name__)


def _parse_duration(iso: str) -> int | None:
    """Convert ISO 8601 duration string (e.g. PT1H4M13S) to total seconds."""
    if not iso:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mins * 60 + s


def fetch_video_metadata(video_ids: list) -> dict:
    """
    Fetch YouTube metadata for up to 50 video IDs per call.
    Returns {video_id: {description, duration_seconds, view_count, like_count}}.
    Missing or private videos are omitted. Never raises.
    """
    if not video_ids or not YOUTUBE_API_KEY:
        return {}

    result = {}
    # YouTube API accepts up to 50 IDs per request
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "snippet,contentDetails,statistics",
                    "id":   ",".join(batch),
                    "key":  YOUTUBE_API_KEY,
                },
                timeout=15,
            )
            if r.status_code != 200:
                _log.warning("fetch_video_metadata: YouTube API returned %d", r.status_code)
                continue
            for item in r.json().get("items", []):
                vid_id  = item["id"]
                snippet = item.get("snippet", {})
                details = item.get("contentDetails", {})
                stats   = item.get("statistics", {})
                result[vid_id] = {
                    "description":      snippet.get("description", ""),
                    "duration_seconds": _parse_duration(details.get("duration")),
                    "view_count":       int(stats["viewCount"])  if stats.get("viewCount")  else None,
                    "like_count":       int(stats["likeCount"])  if stats.get("likeCount")  else None,
                }
        except Exception as e:
            _log.warning("fetch_video_metadata error: %s", e)

    return result


def resolve_channel_id(input_str: str) -> str | None:
    """
    Convert a YouTube channel @handle, URL, or UC... ID to a canonical UC... ID.
    Resolution order:
      1. Already a UC... ID → return immediately (no API call)
      2. URL containing /channel/UC... → extract directly
      3. YouTube Data API forHandle lookup (@handle)
      4. TranscriptAPI fallback
    Returns the UC... channel_id string, or None on failure.
    """
    raw = input_str.strip()

    # 1. Already a UC channel ID
    if re.match(r"^UC[A-Za-z0-9_-]{22}$", raw):
        return raw

    # 2. URL containing /channel/UCxxx
    m = re.search(r"/channel/(UC[A-Za-z0-9_-]{22})", raw)
    if m:
        return m.group(1)

    # Extract @handle from URL or bare string
    handle = None
    m = re.search(r"/@([A-Za-z0-9_.-]+)", raw)
    if m:
        handle = m.group(1)
    elif re.match(r"^@", raw):
        handle = raw.lstrip("@")

    # 3. YouTube Data API — forHandle (free quota, no transcript credits)
    if handle and YOUTUBE_API_KEY:
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "id", "forHandle": handle, "key": YOUTUBE_API_KEY},
                timeout=15,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    return items[0]["id"]
        except Exception as e:
            _log.warning("resolve_channel_id YouTube API error: %s", e)

    # 4. TranscriptAPI fallback
    api_key = os.getenv("TRANSCRIPTAPI_KEY")
    if api_key:
        try:
            resp = requests.get(
                "https://transcriptapi.com/api/v2/youtube/channel/resolve",
                params={"input": raw},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("channel_id")
            _log.warning("resolve_channel_id: TranscriptAPI returned %d for '%s'", resp.status_code, raw)
        except Exception as e:
            _log.warning("resolve_channel_id TranscriptAPI error: %s", e)

    return None


def get_recent_videos(channel_id, hours=48):
    """
    Fetch videos published in the last `hours` hours for a YouTube channel.
    Uses TranscriptAPI channel/latest (FREE) — latest 15 videos via RSS.
    Filters client-side to the requested time window.
    Returns list of dicts: {video_id, title, published_at}
    Returns [] on error — never raises.
    """
    api_key = os.getenv("TRANSCRIPTAPI_KEY")
    if not api_key:
        _log.warning("get_recent_videos: TRANSCRIPTAPI_KEY not set")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        resp = requests.get(
            "https://transcriptapi.com/api/v2/youtube/channel/latest",
            params={"channel": channel_id},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            _log.warning("get_recent_videos: TranscriptAPI returned %d for %s", resp.status_code, channel_id)
            return []

        videos = []
        for item in resp.json().get("results", []):
            video_id  = item.get("videoId") or item.get("video_id") or item.get("id")
            title     = item.get("title", "")
            published = item.get("published") or item.get("publishedAt") or item.get("pubDate") or ""

            if not video_id:
                continue

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
                # Unknown date — include to be safe
                videos.append({"video_id": video_id, "title": title, "published_at": published})
                continue

            if pub_dt >= cutoff:
                videos.append({
                    "video_id":    video_id,
                    "title":       title,
                    "published_at": pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

        return videos

    except Exception as e:
        _log.warning("get_recent_videos error: %s", e)
        return []


def get_all_videos(channel_id, max_videos=500, date_from=None, date_to=None):
    """
    Fetch up to `max_videos` videos from a channel's full upload history.
    Uses YouTube Data API v3 uploads playlist — efficient for backfill,
    no IP block risk (playlist reads are not rate-limited like transcript fetches).
    date_from / date_to: optional 'YYYY-MM-DD' strings to filter by publish date.
    Returns list of dicts: {video_id, title, published_at}
    Returns [] on error — never raises.
    """
    try:
        # Step 1: get the uploads playlist ID for this channel
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "contentDetails", "id": channel_id, "key": YOUTUBE_API_KEY},
        )
        if r.status_code != 200:
            _log.warning("get_all_videos: channel lookup failed HTTP %d", r.status_code)
            return []
        items = r.json().get("items", [])
        if not items:
            _log.warning("get_all_videos: channel %s not found", channel_id)
            return []
        playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # Step 2: paginate through the uploads playlist (newest first)
        videos = []
        page_token = None
        done = False
        while len(videos) < max_videos and not done:
            params = {
                "part":       "snippet",
                "playlistId": playlist_id,
                "maxResults": 50,
                "key":        YOUTUBE_API_KEY,
            }
            if page_token:
                params["pageToken"] = page_token

            r = requests.get(
                "https://www.googleapis.com/youtube/v3/playlistItems", params=params
            )
            if r.status_code != 200:
                _log.warning("get_all_videos: playlistItems error HTTP %d", r.status_code)
                break

            data = r.json()
            for item in data.get("items", []):
                try:
                    vid_id     = item["snippet"]["resourceId"]["videoId"]
                    title      = item["snippet"]["title"]
                    published_at = item["snippet"]["publishedAt"]
                    pub_date   = published_at[:10]

                    if date_from and pub_date < date_from:
                        done = True
                        break
                    if date_to and pub_date > date_to:
                        continue

                    videos.append({"video_id": vid_id, "title": title, "published_at": published_at})
                except KeyError:
                    continue

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return videos[:max_videos]

    except Exception as e:
        _log.warning("get_all_videos error: %s", e)
        return []
