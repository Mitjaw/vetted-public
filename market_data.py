import logging
import os
import time
from dotenv import load_dotenv
load_dotenv()
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")

import requests
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)

# ==========================================
# 💰 MARKET DATA ENGINE (Tiingo Version - Vetted Intervals)
# ==========================================

# Module-level cache so repeated ticker lookups within a single run don't hit the network twice
_ticker_validity_cache: dict[str, bool] = {}


def is_valid_ticker(ticker: str) -> bool:
    """
    Check whether a ticker resolves to real price data.
    Tries yfinance (recent 5 trading days), falls back to Tiingo.
    Results cached in-memory for the lifetime of the process — safe to call in a loop.
    """
    if ticker in _ticker_validity_cache:
        return _ticker_validity_cache[ticker]

    valid = False

    # yfinance primary
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            valid = True
    except Exception:
        pass

    # Tiingo fallback
    if not valid:
        date_str = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        valid = _fetch_milestone_prices_tiingo(ticker, date_str) is not None

    _ticker_validity_cache[ticker] = valid
    return valid

def calculate_vetted_rois(ticker, upload_date_str):
    """
    Fetches historical data from upload date to today in ONE call.
    Calculates Entry, Current, 60-day, 180-day, and 365-day ROIs.
    """
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Token {TIINGO_API_KEY}'
    }
    
    _log.info("Tiingo: fetching historical data for %s from %s", ticker, upload_date_str)
    
    # One single API call fetches the entire timeline
    params = {'startDate': upload_date_str}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code != 200:
            return {"error": f"Tiingo API failed for {ticker}: {response.json().get('detail', 'Unknown error')}"}
            
        data = response.json()
        if not data:
            return {"error": f"No price data found for {ticker} since {upload_date_str}. (Is the ticker correct?)"}
            
        # Data is sorted chronologically. Index 0 is our entry point (automatically skips weekends!)
        entry_price = data[0]['adjClose']
        entry_date = datetime.strptime(data[0]['date'][:10], "%Y-%m-%d")
        
        results = {
            "Ticker": ticker.upper(),
            "Entry Date": data[0]['date'][:10],
            "Entry Price": round(entry_price, 2),
            "7-Day ROI": "N/A",
            "30-Day ROI": "N/A",
            "60-Day ROI": "N/A",
            "180-Day ROI": "N/A",
            "365-Day ROI": "N/A",
            "Current ROI": "N/A"
        }
        
        def calc_roi(current, entry):
            return round(((current - entry) / entry) * 100, 2)
            
        # Current ROI is simply the last day in our dataset
        results["Current ROI"] = f"{calc_roi(data[-1]['adjClose'], entry_price)}%"
        
        # Loop through the timeseries to find the closest trading days for our intervals
        for day in data:
            current_date = datetime.strptime(day['date'][:10], "%Y-%m-%d")
            delta_days = (current_date - entry_date).days
            
            # Grab the FIRST trading day that hits or crosses the threshold
            if results["7-Day ROI"] == "N/A" and delta_days >= 7:
                results["7-Day ROI"] = f"{calc_roi(day['adjClose'], entry_price)}%"

            if results["30-Day ROI"] == "N/A" and delta_days >= 30:
                results["30-Day ROI"] = f"{calc_roi(day['adjClose'], entry_price)}%"

            if results["60-Day ROI"] == "N/A" and delta_days >= 60:
                results["60-Day ROI"] = f"{calc_roi(day['adjClose'], entry_price)}%"

            if results["180-Day ROI"] == "N/A" and delta_days >= 180:
                results["180-Day ROI"] = f"{calc_roi(day['adjClose'], entry_price)}%"

            if results["365-Day ROI"] == "N/A" and delta_days >= 365:
                results["365-Day ROI"] = f"{calc_roi(day['adjClose'], entry_price)}%"
                
        return results

    except Exception as e:
        return {"error": f"Connection Error: {e}"}



def _fetch_milestone_prices_yfinance(ticker, upload_date_str):
    """
    Fetch price_at_publish, price_7d, price_30d using yfinance.
    Downloads a single window (upload_date to +45 days) and walks it for milestones.
    For personal/private use only — see Yahoo Finance ToS.
    Returns dict or None.
    """
    try:
        import yfinance as yf
        start = datetime.strptime(upload_date_str, "%Y-%m-%d")
        hist = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"),
            auto_adjust=True,
        )
        if hist.empty:
            return None

        # Normalise index to date objects
        dates = [d.date() if hasattr(d, "date") else d for d in hist.index]
        closes = list(hist["Close"])

        entry_date = dates[0]
        price_at_publish = round(float(closes[0]), 2)
        price_at_publish_date = entry_date.strftime("%Y-%m-%d")
        price_7d = price_7d_date = None
        price_30d = price_30d_date = None

        for d, c in zip(dates, closes):
            delta = (d - entry_date).days
            if price_7d is None and delta >= 7:
                price_7d = round(float(c), 2)
                price_7d_date = d.strftime("%Y-%m-%d")
            if price_30d is None and delta >= 30:
                price_30d = round(float(c), 2)
                price_30d_date = d.strftime("%Y-%m-%d")
            if price_7d is not None and price_30d is not None:
                break

        price_current      = round(float(closes[-1]), 2)
        price_current_date = dates[-1].strftime("%Y-%m-%d")

        return {
            "price_at_publish":      price_at_publish,
            "price_at_publish_date": price_at_publish_date,
            "price_7d":              price_7d,
            "price_7d_date":         price_7d_date,
            "price_30d":             price_30d,
            "price_30d_date":        price_30d_date,
            "price_current":         price_current,
            "price_current_date":    price_current_date,
        }
    except Exception:
        return None


def _fetch_milestone_prices_tiingo(ticker, upload_date_str):
    """
    Fetch price_at_publish, price_7d, price_30d using Tiingo.
    One API call from upload_date to today; walks the series for milestones.
    Returns dict or None.
    """
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Token {TIINGO_API_KEY}'
    }
    params = {'startDate': upload_date_str}

    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            return None
        data = response.json()
        if not data:
            return None

        # ROI baseline: always the closing price on the video's publish date.
        # data[0] is the first trading day on or after upload_date (weekends/holidays skipped).
        price_at_publish      = data[0]['adjClose']
        price_at_publish_date = data[0]['date'][:10]
        entry_date            = datetime.strptime(price_at_publish_date, "%Y-%m-%d")

        price_7d = price_7d_date = None
        price_30d = price_30d_date = None

        for day in data:
            current_date = datetime.strptime(day['date'][:10], "%Y-%m-%d")
            delta = (current_date - entry_date).days
            if price_7d is None and delta >= 7:
                price_7d      = day['adjClose']
                price_7d_date = day['date'][:10]
            if price_30d is None and delta >= 30:
                price_30d      = day['adjClose']
                price_30d_date = day['date'][:10]
            if price_7d is not None and price_30d is not None:
                break

        price_current      = data[-1]["adjClose"]
        price_current_date = data[-1]["date"][:10]

        return {
            "price_at_publish":      price_at_publish,
            "price_at_publish_date": price_at_publish_date,
            "price_7d":              price_7d,
            "price_7d_date":         price_7d_date,
            "price_30d":             price_30d,
            "price_30d_date":        price_30d_date,
            "price_current":         price_current,
            "price_current_date":    price_current_date,
        }
    except Exception:
        return None


def populate_price_daily(ticker_date_pairs):
    """
    Fetch full daily OHLCV history for a list of tickers and bulk-insert into price_daily.

    ticker_date_pairs: list of (ticker, earliest_date_str) — one entry per ticker.
    Uses yf.download() in chunks of 50 with threads=True (yfinance's own internal
    parallelism). Falls back to Tiingo for tickers yfinance returns empty for.
    Returns count of rows inserted.
    """
    import yfinance as yf
    import db_manager

    if not ticker_date_pairs:
        return 0

    # Use the single earliest date across all tickers as the common start.
    # price_daily stores every trading day from that date → today; ROI lookups
    # use the first row on-or-after each video's upload_date, so no data is wasted.
    global_start = min(d for _, d in ticker_date_pairs)
    tickers = [t.lstrip("$") for t, _ in ticker_date_pairs]

    CHUNK = 50
    total_rows = 0

    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i : i + CHUNK]
        chunk_num = i // CHUNK + 1
        total_chunks = (len(tickers) + CHUNK - 1) // CHUNK

        try:
            raw = yf.download(
                chunk,
                start=global_start,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as e:
            logging.warning("price_daily chunk %d/%d failed: %s", chunk_num, total_chunks, e)
            time.sleep(2)
            continue

        rows_to_insert = []

        if len(chunk) == 1:
            # Single-ticker download returns a plain DataFrame (no MultiIndex)
            ticker = chunk[0]
            if raw is not None and not raw.empty and "Close" in raw.columns:
                for dt, row in raw.iterrows():
                    date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                    rows_to_insert.append((ticker, date_str, round(float(row["Close"]), 4)))
        else:
            # Multi-ticker download returns MultiIndex columns: (field, ticker)
            if raw is not None and not raw.empty and "Close" in raw.columns.get_level_values(0):
                close_df = raw["Close"]
                for ticker in chunk:
                    if ticker not in close_df.columns:
                        continue
                    series = close_df[ticker].dropna()
                    for dt, price in series.items():
                        date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                        rows_to_insert.append((ticker, date_str, round(float(price), 4)))

        # Tiingo fallback for any ticker in this chunk that got no rows
        tickers_in_batch = {r[0] for r in rows_to_insert}
        for ticker in chunk:
            if ticker not in tickers_in_batch:
                tiingo_rows = _fetch_daily_from_tiingo(ticker, global_start)
                rows_to_insert.extend(tiingo_rows)

        db_manager.bulk_insert_price_daily(rows_to_insert)
        total_rows += len(rows_to_insert)

        logging.info(
            "price_daily chunk %d/%d: %d tickers, %d rows inserted.",
            chunk_num, total_chunks, len(chunk), len(rows_to_insert),
        )
        time.sleep(2)

    return total_rows


def _fetch_daily_from_tiingo(ticker, start_date_str):
    """
    Fetch full daily close series from Tiingo for a single ticker.
    Returns list of (ticker, date_str, close) tuples.
    """
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    headers = {"Content-Type": "application/json", "Authorization": f"Token {TIINGO_API_KEY}"}
    params = {"startDate": start_date_str}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data:
            return []
        return [
            (ticker, row["date"][:10], round(float(row["adjClose"]), 4))
            for row in data
            if row.get("adjClose") is not None
        ]
    except Exception:
        return []


def fetch_milestone_prices(ticker, upload_date_str):
    """
    Returns dict with prices at publish date, +7d, +30d trading day milestones.
    Tries yfinance first (free, no daily quota), falls back to Tiingo for tickers
    yfinance can't resolve (uncommon non-US tickers, etc.).
    Returns None if both sources fail.
    """
    result = _fetch_milestone_prices_yfinance(ticker, upload_date_str)
    if result is not None:
        return result
    return _fetch_milestone_prices_tiingo(ticker, upload_date_str)


def fetch_price_on_date(ticker, date_str):
    """
    Fetch closing price for a ticker on or after date_str (YYYY-MM-DD).
    Tries yfinance first (free, no daily quota). Falls back to Tiingo for
    tickers yfinance can't resolve.
    ROI baseline: always the closing price on the video publish date.
    """
    # yfinance primary
    result = _fetch_milestone_prices_yfinance(ticker, date_str)
    if result is not None:
        return result["price_at_publish"]

    # Tiingo fallback
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Token {TIINGO_API_KEY}'
    }
    try:
        start = datetime.strptime(date_str, "%Y-%m-%d")
        end = start + timedelta(days=7)
        params = {'startDate': date_str, 'endDate': end.strftime("%Y-%m-%d")}
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            if data:
                return data[0]['adjClose']
    except Exception:
        pass

    return None


# ==========================================
# 🧪 TEST AREA
# ==========================================
if __name__ == "__main__":
    # Test with Tesla, over a year ago to see all intervals
    test_ticker = "TSLA"
    test_date = "2022-08-18" 
    
    print("-" * 40)
    roi_data = calculate_vetted_rois(test_ticker, test_date)
    
    if "error" in roi_data:
        print(f"❌ {roi_data['error']}")
    else:
        print(f"✅ VETTED ROI REPORT: {roi_data['Ticker']}")
        print(f"   Entry Price ({roi_data['Entry Date']}): ${roi_data['Entry Price']}")
        print(f"   60-Day ROI:  {roi_data['60-Day ROI']}")
        print(f"   180-Day ROI: {roi_data['180-Day ROI']}")
        print(f"   365-Day ROI: {roi_data['365-Day ROI']}")
        print(f"   Current ROI: {roi_data['Current ROI']}")
        print("-" * 40)