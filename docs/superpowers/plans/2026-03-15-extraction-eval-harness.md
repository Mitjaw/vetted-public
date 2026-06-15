# Extraction Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone CLI eval harness that lets the owner score any model/prompt combination against manually-annotated ground truth videos, producing precision, recall, sentiment accuracy, and F1 metrics in a comparison table.

**Architecture:** Self-contained `evals/` directory — no changes to the production app. Ground truth stored as JSON files (one per video). A runner script loads configs (model + prompt combinations), calls `brain.py` functions directly with the stored transcript, scores output against ground truth, and writes results to `evals/results/`. A reporter prints a side-by-side comparison table.

**Tech Stack:** Python 3.11+, existing `anthropic` SDK, `brain.py` functions called directly, `rich` library for terminal table output (already likely installed; fallback to plain print if not)

---

## File Structure

```
evals/
├── ground_truth/          # One JSON file per manually-annotated video
│   └── EXAMPLE.json       # Schema reference — fill in real ones yourself
├── results/               # One JSON per eval run (auto-named by timestamp + config)
├── configs.py             # Model/prompt configurations to compare
├── runner.py              # Entry point: loads GT + configs, runs models, scores, saves
└── scorer.py              # Pure scoring logic — no I/O, easy to unit-test
```

**Nothing in `evals/` touches the FastAPI app or DB.** It reads `brain.py` functions directly and uses the transcript text you paste into ground truth files.

---

## Chunk 1: Ground truth format + scorer

### Task 1: Create `evals/` directory and ground truth schema

**Files:**
- Create: `evals/ground_truth/EXAMPLE.json`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p evals/ground_truth evals/results
```

- [ ] **Step 2: Create the schema reference file**

Create `evals/ground_truth/EXAMPLE.json`:

```json
{
  "_instructions": "Copy this file, rename to <video_id>.json, delete this key. Watch the video yourself and fill in annotations.",
  "video_id": "dQw4w9WgXcQ",
  "title": "Meine 5 Top-Aktien für 2025 — diese kaufe ich jetzt!",
  "channel": "Finanzfluss",
  "language": "de",
  "notes": "Host clearly bullish on all 5. Mentions Nvidia twice as 'highest conviction'. BMW only briefly mentioned as something to avoid.",
  "transcript_source": "stored_in_db",
  "annotations": [
    {
      "ticker": "NVDA",
      "company_name": "NVIDIA",
      "is_real_stock_mention": true,
      "sentiment": "bullish",
      "recommendation": "buy",
      "notes": "Host's top pick, mentioned twice by name"
    },
    {
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "is_real_stock_mention": true,
      "sentiment": "bullish",
      "recommendation": "buy",
      "notes": "Part of the '5 picks' list"
    },
    {
      "ticker": "BMW.DE",
      "company_name": "BMW AG",
      "is_real_stock_mention": true,
      "sentiment": "bearish",
      "recommendation": "sell",
      "notes": "Mentioned briefly as one to avoid"
    }
  ]
}
```

**Key rules for filling in ground truth:**
- `ticker`: use the same format brain.py would use (US ADR preferred, local `.DE` if no ADR)
- `sentiment`: exactly `"bullish"`, `"bearish"`, or `"neutral"`
- `recommendation`: exactly `"buy"`, `"sell"`, `"hold"`, or `"reference"`
- `is_real_stock_mention`: `false` only if the stock has zero investment context
- `notes`: write what you heard — this helps debug model failures later
- Leave out `confidence` and `mention_count` — those aren't scored (subjective / count-dependent)

- [ ] **Step 3: Commit the scaffold**

```bash
git add evals/
git commit -m "feat: add evals/ directory and ground truth schema"
```

---

### Task 2: Write `evals/scorer.py` — pure scoring logic

**Files:**
- Create: `evals/scorer.py`

The scorer takes a list of ground truth annotations and a list of model-returned mentions, and returns a dict of metrics. It has **no I/O** — pure functions only, easy to test.

Matching rule: a model mention **matches** a ground truth annotation if `ticker.upper()` is equal. Case-insensitive, no fuzzy matching.

- [ ] **Step 1: Create `evals/scorer.py`**

```python
"""
Pure scoring functions for eval harness. No I/O.

Terminology:
  gt_annotations  — list of dicts from the ground truth JSON (what the human observed)
  model_mentions  — list of dicts returned by brain.analyze_transcript()

Matching: by ticker (case-insensitive exact match).
"""


def _gt_tickers(annotations):
    return {a["ticker"].upper() for a in annotations if a.get("is_real_stock_mention", True)}


def _model_tickers(mentions):
    return {m["ticker"].upper() for m in mentions}


def score(gt_annotations, model_mentions):
    """
    Returns a dict with:
      precision       — % of returned tickers that were expected (anti-hallucination)
      recall          — % of expected tickers that were returned (anti-miss)
      f1              — harmonic mean of precision and recall
      sentiment_acc   — % of matched tickers where sentiment is correct
      rec_acc         — % of matched tickers where recommendation is correct
      found           — set of tickers correctly returned
      missed          — set of expected tickers not returned
      hallucinated    — set of returned tickers not in ground truth
      n_expected      — total expected real mentions
      n_returned      — total model-returned mentions
    """
    gt_real = [a for a in gt_annotations if a.get("is_real_stock_mention", True)]
    expected = {a["ticker"].upper(): a for a in gt_real}
    returned = {m["ticker"].upper(): m for m in model_mentions}

    found       = set(expected) & set(returned)
    missed      = set(expected) - set(returned)
    hallucinated = set(returned) - set(expected)

    precision = len(found) / len(returned) if returned else 1.0
    recall    = len(found) / len(expected) if expected else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    sentiment_matches = sum(
        1 for t in found
        if returned[t].get("sentiment") == expected[t].get("sentiment")
    )
    rec_matches = sum(
        1 for t in found
        if returned[t].get("recommendation") == expected[t].get("recommendation")
    )

    sentiment_acc = sentiment_matches / len(found) if found else None
    rec_acc       = rec_matches       / len(found) if found else None

    return {
        "precision":     round(precision, 3),
        "recall":        round(recall, 3),
        "f1":            round(f1, 3),
        "sentiment_acc": round(sentiment_acc, 3) if sentiment_acc is not None else None,
        "rec_acc":       round(rec_acc, 3)       if rec_acc       is not None else None,
        "found":         found,
        "missed":        missed,
        "hallucinated":  hallucinated,
        "n_expected":    len(expected),
        "n_returned":    len(returned),
    }


def aggregate(per_video_scores):
    """
    Average metrics across multiple videos.
    Only averages over videos where the metric is not None.
    """
    keys = ["precision", "recall", "f1", "sentiment_acc", "rec_acc"]
    result = {}
    for k in keys:
        vals = [s[k] for s in per_video_scores if s.get(k) is not None]
        result[k] = round(sum(vals) / len(vals), 3) if vals else None
    result["n_videos"] = len(per_video_scores)
    return result
```

- [ ] **Step 2: Manually verify scorer logic with a quick sanity check**

Run in Python shell:

```python
import sys; sys.path.insert(0, '.')
from evals.scorer import score

gt = [
    {"ticker": "NVDA", "is_real_stock_mention": True, "sentiment": "bullish", "recommendation": "buy"},
    {"ticker": "AAPL", "is_real_stock_mention": True, "sentiment": "bullish", "recommendation": "buy"},
    {"ticker": "BMW.DE", "is_real_stock_mention": True, "sentiment": "bearish", "recommendation": "sell"},
]
# Perfect model output
model = [
    {"ticker": "NVDA", "sentiment": "bullish", "recommendation": "buy"},
    {"ticker": "AAPL", "sentiment": "bullish", "recommendation": "buy"},
    {"ticker": "BMW.DE", "sentiment": "bearish", "recommendation": "sell"},
]
s = score(gt, model)
assert s["precision"] == 1.0
assert s["recall"] == 1.0
assert s["f1"] == 1.0
assert s["sentiment_acc"] == 1.0

# Model misses BMW, hallucinates SAP
model2 = [
    {"ticker": "NVDA", "sentiment": "bullish", "recommendation": "buy"},
    {"ticker": "AAPL", "sentiment": "neutral", "recommendation": "hold"},  # wrong sentiment
    {"ticker": "SAP", "sentiment": "bullish", "recommendation": "buy"},    # hallucinated
]
s2 = score(gt, model2)
assert s2["recall"] == round(2/3, 3)           # found NVDA + AAPL, missed BMW
assert s2["precision"] == round(2/3, 3)        # 3 returned, 2 correct
assert s2["sentiment_acc"] == 0.5              # NVDA correct, AAPL wrong
assert "SAP" in s2["hallucinated"]
assert "BMW.DE" in s2["missed"]
print("All scorer assertions passed.")
```

Expected output: `All scorer assertions passed.`

- [ ] **Step 3: Commit**

```bash
git add evals/scorer.py
git commit -m "feat: add eval scorer with precision/recall/sentiment/rec metrics"
```

---

## Chunk 2: Configs + runner

### Task 3: Write `evals/configs.py` — model/prompt configurations

**Files:**
- Create: `evals/configs.py`

Each config is a dict with a `name`, a `description`, and a `run_fn` — a callable that takes `(transcript, title, language)` and returns a list of mention dicts (same shape as `brain.analyze_transcript()`).

This lets you test:
- The current two-pass pipeline
- The old single-pass pipeline
- A Sonnet version of two-pass
- Any prompt variation you want to try

- [ ] **Step 1: Create `evals/configs.py`**

```python
"""
Eval configurations — one entry per model/prompt variant to benchmark.

Each config:
  name        — short identifier used in results filenames and table headers
  description — human-readable label for the report
  run_fn      — callable(transcript, title, language) -> list[mention_dict]
                Must return the same shape as brain.analyze_transcript().
                Must NOT filter by is_real_stock_mention — scorer handles that.

Add new configs here to benchmark them. The runner picks up all entries
in CONFIGS automatically.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import brain


def _two_pass_haiku(transcript, title, language):
    """Current production pipeline: two-pass with claude-haiku-4-5-20251001."""
    return brain.analyze_transcript(transcript, title=title, language=language)


def _single_pass_haiku(transcript, title, language):
    """Legacy single-pass pipeline (fallback path in brain.py)."""
    return brain._single_pass(transcript, title, language)


def _two_pass_sonnet(transcript, title, language):
    """Two-pass pipeline using claude-sonnet-4-6 for both passes.
    Modify _discovery_pass and _analysis_pass model name for this run."""
    import anthropic, json
    from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS

    MODEL = "claude-sonnet-4-6"
    client = anthropic.Anthropic()
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""

    # Pass 1 — same system prompt as brain._discovery_pass, different model
    user1 = f"Language: {lang_label}\n{title_hint}\nTranscript:\n{transcript}\n\nReturn ONLY valid JSON.\n\nRequired output format:\n{{\"stocks\": [{{\"ticker\": \"AAPL\", \"company_name\": \"Apple Inc.\"}}]}}"
    try:
        r1 = client.messages.create(
            model=MODEL, max_tokens=4096,
            system=(
                "You are a specialist financial transcript scanner with deep experience reading "
                "unpunctuated auto-generated YouTube transcripts in German and English. "
                "Find EVERY investment vehicle — stocks, ETFs, crypto, commodities. "
                "Fix transcription errors. Prefer US ADR ticker; use local format (SAP.DE) if no ADR. "
                "Return in transcript order. Do not invent tickers."
            ),
            messages=[{"role": "user", "content": user1}],
        )
        raw1 = _strip_markdown(r1.content[0].text)
        discovered = json.loads(raw1).get("stocks", [])
    except Exception as e:
        print(f"  [sonnet discovery failed: {e}]")
        return []

    if not discovered:
        return []
    if len(discovered) > _MAX_DISCOVERED_STOCKS:
        discovered = discovered[:_MAX_DISCOVERED_STOCKS]

    # Pass 2
    n = len(discovered)
    stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
    user2 = f"Language: {lang_label}\n{title_hint}\nAnalyze exactly {n} investment vehicle{'s' if n != 1 else ''}:\n{stock_list}\n\nTranscript:\n{transcript}\n\nReturn ONLY valid JSON.\n\nRequired output format:\n{{\"mentions\": [{{\"ticker\": \"AAPL\", \"company_name\": \"Apple Inc.\", \"is_real_stock_mention\": true, \"sentiment\": \"bullish\", \"confidence\": 0.82, \"recommendation\": \"buy\", \"mention_count\": 4, \"context\": \"...\"}}]}}"
    try:
        r2 = client.messages.create(
            model=MODEL, max_tokens=8192,
            system=(
                "You are a sharp-tongued senior financial analyst expert at cutting through vague "
                "YouTuber commentary to identify the real signal. "
                "Return exactly one mention object per stock. Do not skip any. Do not add new ones. "
                "Use exact ticker string provided. Lean bullish/bearish when directional signal present."
            ),
            messages=[{"role": "user", "content": user2}],
        )
        raw2 = _strip_markdown(r2.content[0].text)
        mentions = json.loads(raw2).get("mentions", [])
    except Exception as e:
        print(f"  [sonnet analysis failed: {e}]")
        return []

    discovered_tickers = {s["ticker"].upper() for s in discovered}
    filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
    return [m for m in filtered if m.get("is_real_stock_mention") in (True, "true", 1)]


# ── Add new configs below. Runner picks up everything in this list. ──
CONFIGS = [
    {
        "name": "two_pass_haiku",
        "description": "Two-pass · claude-haiku-4-5-20251001 (production)",
        "run_fn": _two_pass_haiku,
    },
    {
        "name": "single_pass_haiku",
        "description": "Single-pass · claude-haiku-4-5-20251001 (legacy)",
        "run_fn": _single_pass_haiku,
    },
    {
        "name": "two_pass_sonnet",
        "description": "Two-pass · claude-sonnet-4-6",
        "run_fn": _two_pass_sonnet,
    },
]
```

- [ ] **Step 2: Commit**

```bash
git add evals/configs.py
git commit -m "feat: add eval configs (two-pass haiku, single-pass haiku, two-pass sonnet)"
```

---

### Task 4: Write `evals/runner.py` — entry point

**Files:**
- Create: `evals/runner.py`

The runner:
1. Loads all `evals/ground_truth/*.json` files (skipping EXAMPLE.json)
2. For each config in `configs.CONFIGS` (or a subset passed via CLI args), runs the `run_fn` against each video's transcript
3. Scores each result with `scorer.score()`
4. Aggregates across videos with `scorer.aggregate()`
5. Prints a comparison table
6. Saves full results to `evals/results/YYYY-MM-DD-HH-MM.json`

**Usage:**
```bash
python -m evals.runner                          # run all configs against all GT files
python -m evals.runner --configs two_pass_haiku single_pass_haiku  # subset of configs
python -m evals.runner --video dQw4w9WgXcQ     # single video only
```

- [ ] **Step 1: Create `evals/runner.py`**

```python
"""
Eval runner — entry point.

Usage:
  python -m evals.runner
  python -m evals.runner --configs two_pass_haiku single_pass_haiku
  python -m evals.runner --video <video_id>
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from evals.configs import CONFIGS
from evals import scorer as sc

GROUND_TRUTH_DIR = os.path.join(os.path.dirname(__file__), "ground_truth")
RESULTS_DIR      = os.path.join(os.path.dirname(__file__), "results")


def load_ground_truth(video_filter=None):
    files = [
        f for f in os.listdir(GROUND_TRUTH_DIR)
        if f.endswith(".json") and f != "EXAMPLE.json"
    ]
    ground_truth = []
    for fname in sorted(files):
        video_id = fname.replace(".json", "")
        if video_filter and video_id != video_filter:
            continue
        with open(os.path.join(GROUND_TRUTH_DIR, fname)) as fh:
            data = json.load(fh)
        # Remove schema instruction key if present
        data.pop("_instructions", None)
        ground_truth.append(data)
    return ground_truth


def run_config(config, ground_truth):
    """Run one config against all GT videos. Returns list of per-video result dicts."""
    results = []
    for gt in ground_truth:
        video_id = gt["video_id"]
        transcript = gt.get("transcript", "")
        if not transcript:
            # Try to load from DB
            try:
                import db_manager
                rows = db_manager.get_videos_with_transcript_no_mentions()
                match = next((r for r in rows if r["video_id"] == video_id), None)
                if match:
                    transcript = match["transcript"]
                else:
                    # Video has mentions so it won't appear above — query directly
                    import sqlite3
                    conn = sqlite3.connect(db_manager.DB_NAME)
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        "SELECT transcript FROM videos WHERE video_id = ?", (video_id,)
                    ).fetchone()
                    conn.close()
                    if row:
                        transcript = row["transcript"]
            except Exception as e:
                print(f"  [WARN] Could not load transcript for {video_id} from DB: {e}")

        if not transcript:
            print(f"  [SKIP] {video_id} — no transcript available (add 'transcript' key to GT file or ensure it's in DB)")
            continue

        print(f"  Running {config['name']} on {video_id} ({gt.get('title', '')[:50]})...")
        try:
            mentions = config["run_fn"](
                transcript=transcript,
                title=gt.get("title", ""),
                language=gt.get("language", "en"),
            )
        except Exception as e:
            print(f"  [ERROR] {config['name']} failed on {video_id}: {e}")
            mentions = []

        metrics = sc.score(gt["annotations"], mentions)
        results.append({
            "video_id":   video_id,
            "title":      gt.get("title", ""),
            "metrics":    metrics,
            "gt_tickers": sorted(metrics["found"] | metrics["missed"]),
            "returned":   [m.get("ticker") for m in mentions],
            "missed":     sorted(metrics["missed"]),
            "hallucinated": sorted(metrics["hallucinated"]),
        })
        _print_video_row(video_id, gt.get("title", ""), metrics)

    return results


def _print_video_row(video_id, title, m):
    found_str     = ", ".join(sorted(m["found"]))      or "—"
    missed_str    = ", ".join(sorted(m["missed"]))     or "—"
    halluc_str    = ", ".join(sorted(m["hallucinated"])) or "—"
    sent_str      = f"{m['sentiment_acc']:.0%}" if m["sentiment_acc"] is not None else "—"
    rec_str       = f"{m['rec_acc']:.0%}"       if m["rec_acc"]       is not None else "—"
    print(
        f"    P={m['precision']:.0%} R={m['recall']:.0%} F1={m['f1']:.0%} "
        f"Sent={sent_str} Rec={rec_str} | "
        f"Found: {found_str} | Missed: {missed_str} | Halluc: {halluc_str}"
    )


def print_comparison_table(all_results):
    """Print a summary table comparing all configs."""
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    header = f"{'Config':<30} {'P':>6} {'R':>6} {'F1':>6} {'Sent':>7} {'Rec':>7} {'Videos':>7}"
    print(header)
    print("-" * 80)
    for config_name, per_video in all_results.items():
        agg = sc.aggregate([v["metrics"] for v in per_video])
        sent = f"{agg['sentiment_acc']:.0%}" if agg["sentiment_acc"] is not None else "—"
        rec  = f"{agg['rec_acc']:.0%}"       if agg["rec_acc"]       is not None else "—"
        print(
            f"{config_name:<30} "
            f"{agg['precision']:>5.0%} "
            f"{agg['recall']:>6.0%} "
            f"{agg['f1']:>6.0%} "
            f"{sent:>7} "
            f"{rec:>7} "
            f"{agg['n_videos']:>7}"
        )
    print("=" * 80)


def save_results(all_results, configs_run):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
    fname = f"{timestamp}-{'_vs_'.join(configs_run)}.json"
    path  = os.path.join(RESULTS_DIR, fname)

    # Sets aren't JSON-serialisable — convert to sorted lists
    def make_serialisable(obj):
        if isinstance(obj, set):
            return sorted(obj)
        if isinstance(obj, dict):
            return {k: make_serialisable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [make_serialisable(i) for i in obj]
        return obj

    with open(path, "w") as fh:
        json.dump(make_serialisable(all_results), fh, indent=2)
    print(f"\nResults saved to {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="Run extraction evals")
    parser.add_argument("--configs", nargs="+", help="Config names to run (default: all)")
    parser.add_argument("--video",   help="Run on a single video_id only")
    args = parser.parse_args()

    config_map = {c["name"]: c for c in CONFIGS}
    if args.configs:
        selected = []
        for name in args.configs:
            if name not in config_map:
                print(f"Unknown config '{name}'. Available: {list(config_map)}")
                sys.exit(1)
            selected.append(config_map[name])
    else:
        selected = CONFIGS

    ground_truth = load_ground_truth(video_filter=args.video)
    if not ground_truth:
        print("No ground truth files found. Add JSON files to evals/ground_truth/ (see EXAMPLE.json).")
        sys.exit(0)

    print(f"\nRunning {len(selected)} config(s) against {len(ground_truth)} video(s).\n")

    all_results = {}
    for config in selected:
        print(f"\n── {config['description']} ──")
        all_results[config["name"]] = run_config(config, ground_truth)

    print_comparison_table(all_results)
    save_results(all_results, [c["name"] for c in selected])


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add evals/runner.py
git commit -m "feat: add eval runner with CLI, per-video output and comparison table"
```

---

## Chunk 3: Adding your first ground truth file and running it

### Task 5: Add a real ground truth file and run the eval

This is the workflow you'll repeat for each video you manually annotate.

- [ ] **Step 1: Pick a video you know well**

Find its video_id (the part after `?v=` in the YouTube URL, e.g. `dQw4w9WgXcQ`). Check if the transcript is already in your DB:

```bash
# In Python shell
import db_manager, sqlite3
conn = sqlite3.connect(db_manager.DB_NAME)
row = conn.execute("SELECT video_id, title, transcript IS NOT NULL as has_transcript FROM videos WHERE video_id = ?", ("YOUR_VIDEO_ID",)).fetchone()
print(row)
```

If `has_transcript = 1`, you're ready. If not, you'll need to add a `"transcript"` key directly in the JSON file with the raw text.

- [ ] **Step 2: Create the ground truth file**

Copy `evals/ground_truth/EXAMPLE.json` → `evals/ground_truth/YOUR_VIDEO_ID.json`.

Fill in:
- `video_id`, `title`, `channel`, `language`
- `notes` — what you observed watching the video
- `annotations` — every stock you heard mentioned, with your honest assessment of sentiment and recommendation

**Tips for good ground truth:**
- Be strict: only mark `"bullish"` if the host clearly expressed a positive investment view
- If the host says "I'm watching this but not buying yet" → `"hold"` or `"reference"`, `"neutral"`
- If a stock is mentioned purely as context ("NVIDIA is in the news") → `"is_real_stock_mention": false`
- Aim for 5-10 videos across different hosts and styles for meaningful eval results

- [ ] **Step 3: Run the eval**

```bash
cd /Users/mitjawilms/DeInfluencer
python -m evals.runner
```

Expected output:
```
Running 3 config(s) against 1 video(s).

── Two-pass · claude-haiku-4-5-20251001 (production) ──
  Running two_pass_haiku on abc123 (Meine 5 Top-Aktien für 2025...)...
    P=100% R=80% F1=89% Sent=75% Rec=75% | Found: AAPL, NVDA, SAP.DE | Missed: BMW.DE | Halluc: —

── Single-pass · claude-haiku-4-5-20251001 (legacy) ──
  Running single_pass_haiku on abc123 (Meine 5 Top-Aktien für 2025...)...
    P=75% R=60% F1=67% Sent=67% Rec=67% | Found: AAPL, NVDA | Missed: BMW.DE, SAP.DE | Halluc: TSLA

── Two-pass · claude-sonnet-4-6 ──
  Running two_pass_sonnet on abc123 ...
    P=100% R=100% F1=100% Sent=100% Rec=100% | Found: AAPL, BMW.DE, NVDA, SAP.DE | Missed: — | Halluc: —

================================================================================
SUMMARY
================================================================================
Config                           P      R     F1    Sent     Rec  Videos
--------------------------------------------------------------------------------
two_pass_haiku                100%    80%    89%     75%     75%       1
single_pass_haiku              75%    60%    67%     67%     67%       1
two_pass_sonnet               100%   100%   100%    100%    100%       1
================================================================================

Results saved to evals/results/2026-03-15-14-30-two_pass_haiku_vs_single_pass_haiku_vs_two_pass_sonnet.json
```

- [ ] **Step 4: Add more ground truth files and iterate**

Repeat Step 2 for more videos. 5-10 videos gives you statistically meaningful scores. Aim for variety:
- A "5 picks" list video (tests recall on clearly enumerated stocks)
- A commentary/analysis video (tests precision — model shouldn't hallucinate)
- A mixed German/English context video
- A video where the host is ambiguous (tests neutral classification)

Run `python -m evals.runner` after each addition to track whether scores improve.

- [ ] **Step 5: Add a new prompt config to test**

When you want to try a new prompt variation, add a new entry to `evals/configs.py`:

```python
def _my_new_prompt(transcript, title, language):
    # Copy _two_pass_haiku and modify the system prompts however you want to test
    ...

CONFIGS = [
    ...
    {
        "name": "my_new_prompt",
        "description": "Two-pass Haiku — experimental prompt v2",
        "run_fn": _my_new_prompt,
    },
]
```

Then run: `python -m evals.runner --configs two_pass_haiku my_new_prompt`

---

### Cost estimate per eval run

Each video runs through each config:
- Two-pass: 2 Haiku calls per video (~$0.0013/video)
- Sonnet two-pass: 2 Sonnet calls (~$0.01-0.03/video depending on transcript length)

For 10 ground truth videos × 3 configs = 50 API calls total ≈ **~$0.30 per full run**. Cheap enough to run freely.
