"""Shared paths and small helpers for the Vetted capstone pipeline."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DB = Path("/root/vetted/vetted.db")          # private production database (read-only)
PRIVATE = ROOT / "data" / "private"                 # never committed: contexts, price cache
PUBLIC = ROOT / "data" / "public"                   # Kaggle export
RESULTS = ROOT / "results"
AUDIT = ROOT / "audit"

PRICE_START = "2023-01-01"   # one year of pre-sample history for beta estimation
PRICE_END = "2026-11-14"     # exclusive; last trading day in the snapshot is 2026-11-13

BUY_LABELS = {"buy", "strong_buy", "accumulate", "buy_and_hold", "dollar_cost_average",
              "dollar-cost-average", "dca", "buy_consistent", "soft buy", "outperform"}
BEARISH_LABELS = {"avoid", "sell", "short", "reduce", "underweight", "underperform", "trim",
                  "remove", "exclude", "reduce_exposure", "reduce_positions", "fade",
                  "avoid_longterm", "avoid_long_term", "sell_partial", "take_profits"}


def direction_of(label: str):
    """Map the 97 free-text LLM recommendation labels onto buy / bearish / None.

    Conditional labels (buy_on_dip, conditional_buy, avoid_at_current_price, ...) are
    deliberately NOT mapped: they are not calls a viewer could act on at the video date.
    """
    l = (label or "").strip().lower()
    if l in BUY_LABELS:
        return "buy"
    if l in BEARISH_LABELS:
        return "bearish"
    return None


def yahoo_symbol(ticker: str) -> str:
    """Normalise an extracted ticker to the Yahoo Finance convention."""
    t = (ticker or "").strip().upper().lstrip("$")
    m = re.fullmatch(r"([A-Z]+)\.([AB])", t)   # share classes: BRK.B -> BRK-B
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return t
