import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from evals import store


@pytest.fixture(autouse=True)
def tmp_gt_dir(tmp_path, monkeypatch):
    """Redirect store's GT_DIR to a temp directory for every test."""
    monkeypatch.setattr(store, "GT_DIR", str(tmp_path))
    return tmp_path


def _make_template(**overrides):
    base = {
        "video_id":   "testVid123",
        "title":      "Test Video",
        "channel":    "TestChannel",
        "language":   "en",
        "notes":      "Test notes",
        "transcript": "some transcript text",
        "annotations": [
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
                "confidence_min": None,
                "confidence_max": None,
            },
        ],
    }
    base.update(overrides)
    return base


def test_save_and_retrieve_confidence_range(tmp_path):
    store.save_template(_make_template())

    loaded = store.get_template("testVid123")
    nvda = next(a for a in loaded["annotations"] if a["ticker"] == "NVDA")
    bmw  = next(a for a in loaded["annotations"] if a["ticker"] == "BMW.DE")

    assert nvda["confidence_min"] == 0.8
    assert nvda["confidence_max"] == 1.0
    assert bmw["confidence_min"]  is None
    assert bmw["confidence_max"]  is None


def test_save_creates_json_file(tmp_path):
    store.save_template(_make_template())
    expected_path = tmp_path / "testVid123.json"
    assert expected_path.exists()
    data = json.loads(expected_path.read_text())
    assert data["video_id"] == "testVid123"


def test_template_exists(tmp_path):
    assert not store.template_exists("testVid123")
    store.save_template(_make_template())
    assert store.template_exists("testVid123")


def test_list_templates_excludes_example(tmp_path):
    (tmp_path / "EXAMPLE.json").write_text(json.dumps({"video_id": "EXAMPLE"}))
    store.save_template(_make_template())
    templates = store.list_templates()
    ids = [t["video_id"] for t in templates]
    assert "testVid123" in ids
    assert "EXAMPLE"    not in ids


def test_annotations_confidence_ranges_in_list_templates(tmp_path):
    store.save_template(_make_template())
    templates = store.list_templates()
    tpl  = next(t for t in templates if t["video_id"] == "testVid123")
    nvda = next(a for a in tpl["annotations"] if a["ticker"] == "NVDA")
    assert nvda["confidence_min"] == 0.8
    assert nvda["confidence_max"] == 1.0
