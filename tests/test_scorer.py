import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from evals import scorer as sc


def test_ticker_details_in_range(sample_annotations, matching_mentions):
    result = sc.score(sample_annotations, matching_mentions)
    details = {d["ticker"]: d for d in result["ticker_details"]}

    assert details["NVDA"]["ai_confidence"] == 0.9
    assert details["NVDA"]["in_range"] is True          # 0.9 in [0.8, 1.0]

    assert details["BMW.DE"]["ai_confidence"] == 0.45
    assert details["BMW.DE"]["in_range"] is True        # 0.45 in [0.3, 0.6]

    assert details["SAP.DE"]["ai_confidence"] == 0.55
    assert details["SAP.DE"]["in_range"] is None        # no range set


def test_ticker_details_out_of_range(sample_annotations):
    low_conf_mentions = [
        {"ticker": "NVDA",   "sentiment": "bullish", "recommendation": "buy",  "confidence": 0.5},
        {"ticker": "BMW.DE", "sentiment": "bearish", "recommendation": "sell", "confidence": 0.9},
        {"ticker": "SAP.DE", "sentiment": "neutral", "recommendation": "hold", "confidence": 0.3},
    ]
    result = sc.score(sample_annotations, low_conf_mentions)
    details = {d["ticker"]: d for d in result["ticker_details"]}

    assert details["NVDA"]["in_range"] is False     # 0.5 not in [0.8, 1.0]
    assert details["BMW.DE"]["in_range"] is False   # 0.9 not in [0.3, 0.6]
    assert details["SAP.DE"]["in_range"] is None    # still None — no range


def test_ticker_details_only_for_found(sample_annotations):
    """Missed and hallucinated tickers must not appear in ticker_details."""
    partial_mentions = [
        {"ticker": "NVDA", "sentiment": "bullish", "recommendation": "buy", "confidence": 0.9},
        {"ticker": "FAKE", "sentiment": "bullish", "recommendation": "buy", "confidence": 0.7},
    ]
    result = sc.score(sample_annotations, partial_mentions)
    tickers_in_details = {d["ticker"] for d in result["ticker_details"]}

    assert "NVDA"   in tickers_in_details
    assert "BMW.DE" not in tickers_in_details   # missed
    assert "SAP.DE" not in tickers_in_details   # missed
    assert "FAKE"   not in tickers_in_details   # hallucinated


def test_ticker_details_no_confidence_in_model_output(sample_annotations):
    """If model returns no confidence field, in_range must be None."""
    mentions_no_conf = [
        {"ticker": "NVDA",   "sentiment": "bullish", "recommendation": "buy"},
        {"ticker": "BMW.DE", "sentiment": "bearish", "recommendation": "sell"},
        {"ticker": "SAP.DE", "sentiment": "neutral", "recommendation": "hold"},
    ]
    result = sc.score(sample_annotations, mentions_no_conf)
    for detail in result["ticker_details"]:
        assert detail["ai_confidence"] is None
        assert detail["in_range"] is None


def test_confidence_acc_unchanged(sample_annotations, matching_mentions):
    """Existing confidence_acc aggregate must still compute correctly."""
    result = sc.score(sample_annotations, matching_mentions)
    # NVDA in range, BMW.DE in range, SAP.DE has no range → 2 eligible, 2 matches → 1.0
    assert result["confidence_acc"] == 1.0


def test_ticker_details_conf_min_max_stored(sample_annotations, matching_mentions):
    """conf_min and conf_max are echoed back in ticker_details for reference."""
    result = sc.score(sample_annotations, matching_mentions)
    details = {d["ticker"]: d for d in result["ticker_details"]}

    assert details["NVDA"]["conf_min"] == 0.8
    assert details["NVDA"]["conf_max"] == 1.0
    assert details["SAP.DE"]["conf_min"] is None
    assert details["SAP.DE"]["conf_max"] is None
