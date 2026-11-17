"""Step 1 - pull every extracted mention out of the production SQLite DB.

Output (private, contains verbatim transcript quotes in `context`):
    data/private/mentions_raw.csv   one row per (video, ticker) mention
    data/private/videos.csv         one row per video (no transcript text)
"""
import sqlite3
import pandas as pd
from common import SOURCE_DB, PRIVATE, direction_of, yahoo_symbol

con = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)

videos = pd.read_sql(
    """SELECT v.video_id, v.channel_id, c.name AS channel, c.language, v.title, v.upload_date,
              v.duration_seconds, v.view_count, v.config_name,
              v.transcript IS NOT NULL AS has_transcript, v.zero_mention_attempts
       FROM videos v LEFT JOIN channels c ON c.id = v.channel_id""", con)

mentions = pd.read_sql(
    """SELECT m.id AS mention_id, m.video_id, m.ticker AS ticker_raw, m.company_name,
              m.asset_type, m.sentiment, m.recommendation, m.confidence, m.mention_count,
              m.verified, m.context
       FROM mentions m""", con)

mentions = mentions.merge(
    videos[["video_id", "channel_id", "channel", "language", "title", "upload_date", "config_name"]],
    on="video_id", how="left", validate="m:1")
mentions["direction"] = mentions["recommendation"].map(direction_of)
mentions["ticker"] = mentions["ticker_raw"].map(yahoo_symbol)
mentions["asset_type"] = mentions["asset_type"].str.lower().str.strip()

PRIVATE.mkdir(parents=True, exist_ok=True)
videos.to_csv(PRIVATE / "videos.csv", index=False)
mentions.to_csv(PRIVATE / "mentions_raw.csv", index=False)
print(f"videos {len(videos):,} | mentions {len(mentions):,}")
print(mentions["direction"].value_counts(dropna=False).to_string())
