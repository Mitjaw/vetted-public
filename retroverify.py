"""
retroverify.py — one-time retroactive verification of two_pass_haiku_v1 mentions.

Runs Pass 3 (_verification_pass) over every video analyzed with two_pass_haiku_v1,
corrects/removes hallucinations, then marks those videos as three_pass_haiku_v2.

Usage:
    python3 retroverify.py [--dry-run] [--limit N]

Options:
    --dry-run   Show what would change without writing to DB
    --limit N   Process at most N videos (useful for spot-checks)
"""

import argparse
import json
import logging
import sqlite3
import sys
import os
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [retroverify] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vetted.db")

SOURCE_CONFIG = "two_pass_haiku_v1"
TARGET_CONFIG = "three_pass_haiku_v2"


# ---------------------------------------------------------------------------
# Config seeding
# ---------------------------------------------------------------------------

def seed_config(conn):
    """
    Create three_pass_haiku_v2 in analysis_configs if not already present.
    Pass 1 + Pass 2 copied from two_pass_haiku_v1 in DB.
    Pass 3 = brain.py's _verification_pass prompts (includes ticker-correction sentence).
    """
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM analysis_configs WHERE name = ?", (TARGET_CONFIG,))
    if cur.fetchone():
        log.info("Config '%s' already exists — skipping seed.", TARGET_CONFIG)
        return

    cur.execute("SELECT config_json FROM analysis_configs WHERE name = ?", (SOURCE_CONFIG,))
    row = cur.fetchone()
    if not row:
        log.error("Source config '%s' not found in DB.", SOURCE_CONFIG)
        sys.exit(1)

    source = json.loads(row[0])

    config = {
        "passes": 3,
        "pass1": source["pass1"],
        "pass2": source["pass2"],
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

    cur.execute(
        "INSERT INTO analysis_configs (name, config_json, created_at) VALUES (?, ?, ?)",
        (TARGET_CONFIG, json.dumps(config), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    log.info("Seeded config '%s'.", TARGET_CONFIG)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_target_videos(conn):
    """Videos analyzed with two_pass_haiku_v1 that have at least one real mention."""
    cur = conn.cursor()
    cur.execute("""
        SELECT v.video_id, v.transcript, v.title, v.transcript_language AS language
        FROM videos v
        WHERE v.config_name = ?
          AND v.transcript IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM mentions m
              WHERE m.video_id = v.video_id
                AND m.is_real_stock_mention = 1
          )
    """, (SOURCE_CONFIG,))
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]


def get_mentions_for_video(conn, video_id):
    """Return all real mentions for a video, ordered by id."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ticker, company_name, mention_count, sentiment,
               confidence, recommendation, context, is_real_stock_mention, asset_type
        FROM mentions
        WHERE video_id = ? AND is_real_stock_mention = 1
        ORDER BY id
    """, (video_id,))
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]


def delete_mention(conn, mention_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM roi_tracking WHERE mention_id = ?", (mention_id,))
    cur.execute("DELETE FROM mentions WHERE id = ?", (mention_id,))


def update_mention(conn, mention_id, corrected):
    cur = conn.cursor()
    cur.execute("""
        UPDATE mentions SET
            ticker           = ?,
            company_name     = ?,
            sentiment        = ?,
            confidence       = ?,
            recommendation   = ?,
            context          = ?,
            mention_count    = ?,
            is_real_stock_mention = 1
        WHERE id = ?
    """, (
        corrected.get("ticker"),
        corrected.get("company_name"),
        corrected.get("sentiment"),
        corrected.get("confidence"),
        corrected.get("recommendation"),
        corrected.get("context"),
        corrected.get("mention_count", 1),
        mention_id,
    ))
    # If ticker changed, keep roi_tracking in sync
    cur.execute(
        "UPDATE roi_tracking SET ticker = ? WHERE mention_id = ?",
        (corrected.get("ticker"), mention_id),
    )


def mark_video_config(conn, video_id):
    cur = conn.cursor()
    cur.execute(
        "UPDATE videos SET config_name = ? WHERE video_id = ?",
        (TARGET_CONFIG, video_id),
    )


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def apply_verification(original_mentions, corrected_mentions):
    """
    Match Pass 3 output back to original DB rows.

    Strategy:
    1. Match by ticker (case-insensitive) for unchanged tickers.
    2. For unmatched originals, fall back to positional matching to catch
       ticker corrections (e.g. ABGO → AVGO).
    3. Originals with no match in either strategy → deleted (hallucinations).

    Returns:
        updates: list of (db_row_id, corrected_mention_dict)
        deletes: list of db_row_ids
    """
    # Filter out anything Pass 3 marked as not real
    valid_corrected = [
        m for m in corrected_mentions
        if m.get("is_real_stock_mention", True) and m.get("confidence", 1.0) > 0.0
    ]

    corrected_by_ticker = {m["ticker"].upper(): m for m in valid_corrected}

    updates = []
    deletes = []
    matched_corrected = set()

    # Pass 1: exact ticker match
    unmatched_originals = []
    for orig in original_mentions:
        orig_ticker = orig["ticker"].upper()
        if orig_ticker in corrected_by_ticker:
            c = corrected_by_ticker[orig_ticker]
            updates.append((orig["id"], c))
            matched_corrected.add(orig_ticker)
        else:
            unmatched_originals.append(orig)

    # Pass 2: positional fallback for unmatched (ticker corrections)
    unmatched_corrected = [m for m in valid_corrected if m["ticker"].upper() not in matched_corrected]
    for orig, corr in zip(unmatched_originals, unmatched_corrected):
        updates.append((orig["id"], corr))

    # Anything still unmatched → hallucination, delete
    n_positional = len(unmatched_corrected)
    for orig in unmatched_originals[n_positional:]:
        deletes.append(orig["id"])

    return updates, deletes


def process_video(conn, video, dry_run):
    import brain

    video_id = video["video_id"]
    transcript = video["transcript"]
    title = video.get("title") or ""
    language = video.get("language") or "en"

    original_mentions = get_mentions_for_video(conn, video_id)
    if not original_mentions:
        return 0, 0, 0.0

    # Format mentions for Pass 3 (matches what _verification_pass expects)
    mentions_for_pass3 = [
        {
            "ticker":               m["ticker"],
            "company_name":         m["company_name"],
            "is_real_stock_mention": bool(m["is_real_stock_mention"]),
            "sentiment":            m["sentiment"],
            "confidence":           m["confidence"],
            "recommendation":       m["recommendation"],
            "mention_count":        m["mention_count"],
            "context":              m["context"],
        }
        for m in original_mentions
    ]

    corrected, usage = brain._verification_pass(transcript, title, language, mentions_for_pass3)
    cost = usage.get("cost_usd", 0.0)

    updates, deletes = apply_verification(original_mentions, corrected)

    n_updated = len(updates)
    n_deleted = len(deletes)

    ticker_changes = [
        f"{next(o['ticker'] for o in original_mentions if o['id'] == uid)} → {c['ticker']}"
        for uid, c in updates
        if next((o['ticker'] for o in original_mentions if o['id'] == uid), None) != c['ticker']
    ]

    if dry_run:
        log.info(
            "[DRY RUN] %s: %d mentions → %d kept, %d deleted%s",
            video_id, len(original_mentions), n_updated, n_deleted,
            f", ticker changes: {ticker_changes}" if ticker_changes else "",
        )
        return n_updated, n_deleted, cost

    for mention_id, corrected_mention in updates:
        update_mention(conn, mention_id, corrected_mention)
    for mention_id in deletes:
        delete_mention(conn, mention_id)

    mark_video_config(conn, video_id)
    conn.commit()

    if ticker_changes:
        log.info("%s: %d kept, %d deleted, ticker changes: %s", video_id, n_updated, n_deleted, ticker_changes)
    elif n_deleted:
        log.info("%s: %d kept, %d deleted", video_id, n_updated, n_deleted)

    return n_updated, n_deleted, cost


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Retroactive Pass 3 verification.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N videos")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=30)

    seed_config(conn)

    videos = get_target_videos(conn)
    if args.limit:
        videos = videos[:args.limit]

    log.info("Found %d videos to verify.", len(videos))
    if args.dry_run:
        log.info("DRY RUN — no changes will be written.")

    total_updated = total_deleted = 0
    total_cost = 0.0

    for i, video in enumerate(videos, 1):
        try:
            n_up, n_del, cost = process_video(conn, video, dry_run=args.dry_run)
            total_updated += n_up
            total_deleted += n_del
            total_cost += cost
            if i % 50 == 0:
                log.info("Progress: %d/%d videos — $%.4f spent so far", i, len(videos), total_cost)
        except Exception as e:
            log.error("Failed on %s: %s", video["video_id"], e)
            continue

    conn.close()

    log.info("Done. %d videos processed.", len(videos))
    log.info("Mentions kept: %d | Deleted: %d", total_updated, total_deleted)
    log.info("Total cost: $%.4f", total_cost)


if __name__ == "__main__":
    main()
