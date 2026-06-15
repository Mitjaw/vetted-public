import pytest


@pytest.fixture
def sample_annotations():
    """Ground truth annotations with and without confidence ranges."""
    return [
        {
            "ticker": "NVDA",
            "company_name": "NVIDIA",
            "is_real_stock_mention": True,
            "sentiment": "bullish",
            "recommendation": "buy",
            "confidence_min": 0.8,
            "confidence_max": 1.0,
        },
        {
            "ticker": "BMW.DE",
            "company_name": "BMW AG",
            "is_real_stock_mention": True,
            "sentiment": "bearish",
            "recommendation": "sell",
            "confidence_min": 0.3,
            "confidence_max": 0.6,
        },
        {
            "ticker": "SAP.DE",
            "company_name": "SAP SE",
            "is_real_stock_mention": True,
            "sentiment": "neutral",
            "recommendation": "hold",
            # no confidence range — in_range should be None
        },
    ]


@pytest.fixture
def matching_mentions():
    """Model output that matches all three annotations with in-range confidence."""
    return [
        {"ticker": "NVDA",   "sentiment": "bullish", "recommendation": "buy",  "confidence": 0.9,  "is_real_stock_mention": True},
        {"ticker": "BMW.DE", "sentiment": "bearish", "recommendation": "sell", "confidence": 0.45, "is_real_stock_mention": True},
        {"ticker": "SAP.DE", "sentiment": "neutral", "recommendation": "hold", "confidence": 0.55, "is_real_stock_mention": True},
    ]
