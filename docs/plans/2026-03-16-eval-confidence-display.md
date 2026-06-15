# Eval Confidence Display & Channel Handle Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show per-ticker AI confidence + ✓/✗ in eval results, and add a channel handle resolver to the Add Template form.

**Architecture:** `scorer.py` gains a `ticker_details` list in its output — one entry per matched ticker with `ai_confidence` and `in_range`. `runner.py` passes this through to the saved result JSON. The results modal reads it and renders a confidence breakdown table per video. The Add Template form gets a `@handle` input + Resolve button (client-side only — auto-fills the existing free-text Channel field). Tests live in `tests/` using pytest with temp directories for file I/O and in-memory data for pure functions.

**Tech Stack:** Python 3.11, pytest, Jinja2, vanilla JS

---

## File Map

| File | Change |
|------|--------|
| `evals/scorer.py` | Add `ticker_details` list to `score()` return value |
| `evals/runner.py` | Include `ticker_details` in per-video result dict |
| `evals/ground_truth/EXAMPLE.json` | Add `confidence_min`/`confidence_max` to example annotations |
| `templates/evals.html` | Add Template: channel handle + Resolve button; Results modal: confidence breakdown table |
| `tests/__init__.py` | Empty file (makes `tests/` a package) |
| `tests/conftest.py` | Shared pytest fixtures: temp GT dir, sample GT data with confidence ranges |
| `tests/test_scorer.py` | Unit tests for `ticker_details`, `in_range`, edge cases |
| `tests/test_evals_store.py` | Tests that GT templates with confidence ranges round-trip through `store.save_template` / `store.get_template` |

**Not changed:** `main.py` (channel resolve is client-side JS only — the `channel` field is already free-text and already saved), `evals/store.py` (already saves whatever dict is passed), `evals/custom_store.py`, `evals/configs.py`.

---

## Task 1: Update `scorer.py` to return `ticker_details`

**Files:**
- Modify: `evals/scorer.py`

### What to know
`score(gt_annotations, model_mentions)` currently returns a flat dict. We add one new key: `ticker_details` — a list of dicts, one per matched ticker, containing:
- `ticker` — the ticker string
- `ai_confidence` — the float the model returned (or `null` if missing)
- `in_range` — `true`/`false` if a confidence range was set on that annotation; `null` if no range was set

Tickers in `missed` or `hallucinated` are not included — only `found` tickers.

The existing `confidence_acc` aggregate stays unchanged.

- [ ] **Step 1: Add `ticker_details` to the return value of `score()`**

In `evals/scorer.py`, find the `return {` block (line ~66). Add `ticker_details` built from the `found` set:

```python
    # Per-ticker confidence details (found tickers only)
    ticker_details = []
    for t in sorted(found):
        ann        = expected[t]
        model_conf = returned[t].get("confidence")
        lo = ann.get("confidence_min")
        hi = ann.get("confidence_max")
        if lo is None and hi is None:
            in_range = None
        elif model_conf is None:
            in_range = None
        else:
            lo_val = float(lo) if lo is not None else 0.0
            hi_val = float(hi) if hi is not None else 1.0
            in_range = lo_val <= float(model_conf) <= hi_val
        ticker_details.append({
            "ticker":        t,
            "ai_confidence": round(float(model_conf), 3) if model_conf is not None else None,
            "in_range":      in_range,
            "conf_min":      lo,
            "conf_max":      hi,
        })

    return {
        "precision":      round(precision, 3),
        "recall":         round(recall, 3),
        "f1":             round(f1, 3),
        "sentiment_acc":  round(sentiment_acc, 3)  if sentiment_acc  is not None else None,
        "rec_acc":        round(rec_acc, 3)        if rec_acc        is not None else None,
        "confidence_acc": round(confidence_acc, 3) if confidence_acc is not None else None,
        "found":          found,
        "missed":         missed,
        "hallucinated":   hallucinated,
        "n_expected":     len(expected),
        "n_returned":     len(returned),
        "ticker_details": ticker_details,    # NEW
    }
```

---

## Task 2: Write tests for `scorer.py`

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_scorer.py`

### What to know
`scorer.py` is pure — no I/O, no DB. Tests are simple unit tests. Run with `pytest tests/test_scorer.py -v` from the project root.

`conftest.py` provides a reusable `sample_gt` fixture — a list of annotation dicts matching the ground truth JSON format, including `confidence_min`/`confidence_max` on some tickers.

- [ ] **Step 1: Create `tests/__init__.py`**

```python
```
(empty file)

- [ ] **Step 2: Create `tests/conftest.py`**

```python
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
    """Model output that matches all three annotations."""
    return [
        {"ticker": "NVDA",   "sentiment": "bullish", "recommendation": "buy",  "confidence": 0.9,  "is_real_stock_mention": True},
        {"ticker": "BMW.DE", "sentiment": "bearish", "recommendation": "sell", "confidence": 0.45, "is_real_stock_mention": True},
        {"ticker": "SAP.DE", "sentiment": "neutral", "recommendation": "hold", "confidence": 0.55, "is_real_stock_mention": True},
    ]
```

- [ ] **Step 3: Create `tests/test_scorer.py`**

```python
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
        {"ticker": "NVDA",   "sentiment": "bullish", "recommendation": "buy",  "confidence": 0.9},
        {"ticker": "FAKE",   "sentiment": "bullish", "recommendation": "buy",  "confidence": 0.7},
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
        {"ticker": "NVDA",   "sentiment": "bullish", "recommendation": "buy"},   # no confidence key
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
    """conf_min and conf_max should be echoed back for reference."""
    result = sc.score(sample_annotations, matching_mentions)
    details = {d["ticker"]: d for d in result["ticker_details"]}
    assert details["NVDA"]["conf_min"] == 0.8
    assert details["NVDA"]["conf_max"] == 1.0
    assert details["SAP.DE"]["conf_min"] is None
    assert details["SAP.DE"]["conf_max"] is None
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/mitjawilms/DeInfluencer
pytest tests/test_scorer.py -v
```

Expected: all 6 tests PASS.

---

## Task 3: Write tests for `evals/store.py` (GT round-trip)

**Files:**
- Create: `tests/test_evals_store.py`

### What to know
`store.save_template(data)` writes a JSON file to `evals/ground_truth/{video_id}.json`.
`store.get_template(video_id)` reads it back.
Tests use pytest's `tmp_path` fixture + monkeypatching `store.GT_DIR` to point at a temp directory so no real files are touched.

- [ ] **Step 1: Create `tests/test_evals_store.py`**

```python
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
        "video_id":  "testVid123",
        "title":     "Test Video",
        "channel":   "TestChannel",
        "language":  "en",
        "notes":     "Test notes",
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
    tpl = _make_template()
    store.save_template(tpl)

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
    # Write EXAMPLE.json — it must be excluded from list_templates()
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
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_evals_store.py -v
```

Expected: all 5 tests PASS.

---

## Task 4: Thread `ticker_details` through `runner.py`

**Files:**
- Modify: `evals/runner.py` (line ~67)

### What to know
`run_config()` builds a per-video result dict. `ticker_details` lives on `metrics` (returned by `sc.score()`). Pull it out and store it at the top level of the result dict so the frontend can access it directly without digging into `metrics`.

- [ ] **Step 1: Add `ticker_details` to the result dict in `run_config()`**

Find the `results.append({...})` block (line ~67) and add one key:

```python
        results.append({
            "video_id":      video_id,
            "title":         gt.get("title", ""),
            "metrics":       metrics,
            "missed":        sorted(metrics["missed"]),
            "hallucinated":  sorted(metrics["hallucinated"]),
            "found":         sorted(metrics["found"]),
            "ticker_details": metrics.get("ticker_details", []),   # NEW
        })
```

- [ ] **Step 2: Verify `ticker_details` is serialisable**

`store.save_result()` calls `serialise()` which already handles lists of dicts. No changes needed there.

---

## Task 5: Update `evals/ground_truth/EXAMPLE.json`

**Files:**
- Modify: `evals/ground_truth/EXAMPLE.json`

### What to know
The example file is the reference template that shows users what fields are available. Add `confidence_min`/`confidence_max` to both annotation entries so new users know the fields exist.

- [ ] **Step 1: Update EXAMPLE.json**

Replace the contents with:

```json
{
  "_instructions": "Copy this file, rename to <video_id>.json, delete this key. Watch the video yourself and fill in annotations.",
  "video_id": "REPLACE_WITH_VIDEO_ID",
  "title": "Video title here",
  "channel": "Channel name",
  "language": "de",
  "notes": "Write what you observed. e.g. Host clearly bullish on NVDA, mentioned twice. BMW only briefly as something to avoid.",
  "annotations": [
    {
      "ticker": "NVDA",
      "company_name": "NVIDIA",
      "is_real_stock_mention": true,
      "sentiment": "bullish",
      "recommendation": "buy",
      "confidence_min": 0.8,
      "confidence_max": 1.0,
      "notes": "Host's top pick, mentioned twice — clear conviction, expect high confidence"
    },
    {
      "ticker": "BMW.DE",
      "company_name": "BMW AG",
      "is_real_stock_mention": true,
      "sentiment": "bearish",
      "recommendation": "sell",
      "confidence_min": null,
      "confidence_max": null,
      "notes": "Mentioned briefly as one to avoid — leave range blank to skip confidence check"
    }
  ]
}
```

---

## Task 6: Add channel handle resolver to Add Template form

**Files:**
- Modify: `templates/evals.html`

### What to know
The Channel field (line ~339) is already a free-text input named `channel`. Videos from untracked channels already work — the route tries DB first, then calls `extract.get_transcript()`. The new `@handle` input is client-side only: clicking Resolve calls `/api/channel/resolve` (already exists) and on success auto-fills the `channel` input with the resolved channel name. No backend changes needed.

The JS function must be named differently from the one in `channels.html` (which also defines `resolveChannel()`) — this template is `evals.html`, so name it `resolveTemplateChannel()`.

- [ ] **Step 1: Replace the Channel field in the Add Template form**

Find this block in `templates/evals.html` (around line 337):

```html
        <div class="form-group">
          <label>Channel</label>
          <input type="text" name="channel" id="channel-input" placeholder="Finanzfluss" style="width:160px;">
        </div>
```

Replace with:

```html
        <div class="form-group">
          <label>Channel
            <span style="font-size:10px;color:var(--text-muted);font-weight:400;margin-left:4px;">
              — name or @handle for untracked channels
            </span>
          </label>
          <div style="display:flex;gap:6px;align-items:center;">
            <input type="text" name="channel" id="channel-input" placeholder="Finanzfluss" style="width:150px;">
            <input type="text" id="channel-handle-input" placeholder="@handle (optional)"
                   style="width:150px;background:var(--bg-raised);border:1px solid var(--border);
                          color:var(--text);padding:7px 10px;border-radius:4px;font-size:13px;">
            <button type="button" onclick="resolveTemplateChannel()"
                    class="btn btn-secondary" style="font-size:12px;padding:7px 10px;white-space:nowrap;"
                    title="Resolve @handle to confirm the channel exists and auto-fill the name — no credits.">
              Resolve &nbsp;<span class="badge-free">FREE</span>
            </button>
          </div>
          <span id="template-resolve-status"
                style="font-size:11px;color:var(--text-muted);display:block;margin-top:3px;">
            Enter @handle and click Resolve to auto-fill name, or type the name directly.
          </span>
        </div>
```

- [ ] **Step 2: Add `resolveTemplateChannel()` to the scripts block**

In `templates/evals.html`, inside the `<script>` block, add:

```javascript
function resolveTemplateChannel() {
  const handle = document.getElementById('channel-handle-input').value.trim();
  const status = document.getElementById('template-resolve-status');
  if (!handle) return;
  status.textContent = 'Resolving…';
  status.style.color = 'var(--text-muted)';
  fetch('/api/channel/resolve?input=' + encodeURIComponent(handle))
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(data => {
      status.textContent = '✓ Channel ID: ' + data.channel_id + ' — name filled above if blank';
      status.style.color = 'var(--positive)';
      const nameInput = document.getElementById('channel-input');
      if (!nameInput.value.trim()) nameInput.value = handle.replace(/^@/, '');
    })
    .catch(() => {
      status.textContent = '✗ Could not resolve — type the channel name directly instead';
      status.style.color = 'var(--negative)';
    });
}
```

---

## Task 7: Show confidence breakdown in results modal

**Files:**
- Modify: `templates/evals.html` (results display section)

### What to know
The results modal shows per-video details when the user expands a video row. Currently it shows found/missed/hallucinated as comma-separated ticker lists. We add a small table below the found tickers for any ticker that has `ai_confidence` or `in_range` set.

`ticker_details` is stored in the result JSON under each per-video entry. In the template, `result_data` for each run's per-video entry looks like: `{video_id, title, metrics, found, missed, hallucinated, ticker_details}`.

The confidence breakdown only renders if `ticker_details` has at least one entry with `in_range !== null` (i.e. at least one annotation had a confidence range set).

- [ ] **Step 1: Find where per-video results are rendered in the modal**

Search for the JS that builds the per-video HTML in the results section. It will be building HTML strings for `found`, `missed`, `hallucinated`. Find the function that renders a single per-video row and add the confidence breakdown after the found/missed/hallucinated section.

In the JS, after the section that renders `found`/`missed`/`hallucinated`, add:

```javascript
// Confidence breakdown — only shown if any ticker has a range set
const confDetails = (v.ticker_details || []).filter(d => d.in_range !== null);
let confHtml = '';
if (confDetails.length > 0) {
  const rows = confDetails.map(d => {
    const badge = d.in_range
      ? '<span style="color:#22c55e;font-weight:700;">✓</span>'
      : '<span style="color:#ef4444;font-weight:700;">✗</span>';
    const range = (d.conf_min !== null || d.conf_max !== null)
      ? `${d.conf_min ?? '—'} – ${d.conf_max ?? '—'}`
      : '—';
    return `<tr>
      <td style="padding:3px 10px 3px 0;font-size:12px;font-weight:600;">${d.ticker}</td>
      <td style="padding:3px 10px 3px 0;font-size:12px;">${d.ai_confidence !== null ? d.ai_confidence : '—'}</td>
      <td style="padding:3px 10px 3px 0;font-size:12px;color:var(--text-muted);">${range}</td>
      <td style="padding:3px 0;font-size:13px;">${badge}</td>
    </tr>`;
  }).join('');
  confHtml = `
    <div style="margin-top:8px;">
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;font-weight:600;letter-spacing:.04em;">CONFIDENCE</div>
      <table style="border-collapse:collapse;">
        <thead>
          <tr>
            <th style="padding:2px 10px 4px 0;font-size:10px;color:var(--text-muted);font-weight:600;text-align:left;">TICKER</th>
            <th style="padding:2px 10px 4px 0;font-size:10px;color:var(--text-muted);font-weight:600;text-align:left;">AI SCORE</th>
            <th style="padding:2px 10px 4px 0;font-size:10px;color:var(--text-muted);font-weight:600;text-align:left;">RANGE</th>
            <th style="padding:2px 0 4px 0;font-size:10px;color:var(--text-muted);font-weight:600;text-align:left;">IN RANGE</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}
```

Then include `confHtml` at the end of the per-video HTML block.

- [ ] **Step 2: Run a quick end-to-end check**

1. Open the Evals page
2. Add a template with at least one annotation that has `confidence_min`/`confidence_max` set
3. Run an eval against it
4. Open the results modal and expand the per-video row
5. Confirm the CONFIDENCE table appears with the AI score and ✓/✗

---

## Final Test Run

```bash
cd /Users/mitjawilms/DeInfluencer
pytest tests/ -v
```

Expected output:
```
tests/test_scorer.py::test_ticker_details_in_range PASSED
tests/test_scorer.py::test_ticker_details_out_of_range PASSED
tests/test_scorer.py::test_ticker_details_only_for_found PASSED
tests/test_scorer.py::test_ticker_details_no_confidence_in_model_output PASSED
tests/test_scorer.py::test_confidence_acc_unchanged PASSED
tests/test_scorer.py::test_ticker_details_conf_min_max_stored PASSED
tests/test_evals_store.py::test_save_and_retrieve_confidence_range PASSED
tests/test_evals_store.py::test_save_creates_json_file PASSED
tests/test_evals_store.py::test_template_exists PASSED
tests/test_evals_store.py::test_list_templates_excludes_example PASSED
tests/test_evals_store.py::test_annotations_confidence_ranges_in_list_templates PASSED

11 passed in <1s
```
