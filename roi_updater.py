import time
import logging
from datetime import datetime, timezone, timedelta

from db_manager import get_pending_roi_updates, update_roi
from market_data import fetch_price_on_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s [roi_updater] %(message)s")


def update_pending():
    """
    Called by the scheduler every 3 hours.
    Finds mentions that have hit their 7d or 30d milestone and updates ROI.
    Uses the actual milestone date (upload_date + N days), not today's date,
    so ROI is always measured at the correct interval regardless of when this runs.
    """
    for days in [7, 30]:
        rows = get_pending_roi_updates(days)
        logging.info("%d row(s) pending %d-day ROI update.", len(rows), days)

        for row in rows:
            ticker = row["ticker"]
            price_at_publish = row["price_at_publish"]

            if not price_at_publish:
                logging.info("Ticker %s: no entry price recorded — skipping.", ticker)
                continue

            # Fetch price at the actual milestone date, not today.
            # fetch_price_on_date searches forward up to 7 days to find a trading day.
            upload_date = row["video_upload_date"]
            milestone_date = (
                datetime.strptime(upload_date, "%Y-%m-%d") + timedelta(days=days)
            ).strftime("%Y-%m-%d")

            price = fetch_price_on_date(ticker, milestone_date)
            time.sleep(1)

            if price is None:
                logging.info("Ticker %s: could not fetch price for %s — skipping.", ticker, milestone_date)
                continue

            roi = round(((price - price_at_publish) / price_at_publish) * 100, 2)
            update_roi(row["id"], days, price, roi)
            logging.info("Ticker %s: %dd ROI = %.2f%% (milestone %s)", ticker, days, roi, milestone_date)


if __name__ == "__main__":
    update_pending()
