"""
File I/O layer for the eval harness.
Reads/writes ground truth templates and result files.
All paths are relative to the evals/ directory.
"""

import glob
import json
import os

_EVALS_DIR        = os.path.dirname(__file__)
GT_DIR            = os.path.join(_EVALS_DIR, "ground_truth")
RESULTS_DIR       = os.path.join(_EVALS_DIR, "results")
_LAYER_DIR          = os.path.join(RESULTS_DIR, "layers")
LAYER_RESULTS_FILE  = os.path.join(_LAYER_DIR, "layer_tests.json")


# ---------------------------------------------------------------------------
# Ground truth templates
# ---------------------------------------------------------------------------

def list_templates():
    """Return all GT templates as list of dicts, sorted by video_id. Skips EXAMPLE.json."""
    files = sorted(glob.glob(os.path.join(GT_DIR, "*.json")))
    result = []
    for f in files:
        if os.path.basename(f) == "EXAMPLE.json":
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            data.pop("_instructions", None)
            result.append(data)
        except Exception:
            pass
    return result


def get_template(video_id):
    path = os.path.join(GT_DIR, f"{video_id}.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        data = json.load(fh)
    data.pop("_instructions", None)
    return data


def template_exists(video_id):
    return os.path.exists(os.path.join(GT_DIR, f"{video_id}.json"))


def save_template(data):
    """Persist a ground truth template. data must contain 'video_id'."""
    os.makedirs(GT_DIR, exist_ok=True)
    path = os.path.join(GT_DIR, f"{data['video_id']}.json")
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)


def delete_template(video_id):
    path = os.path.join(GT_DIR, f"{video_id}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def recalculate_costs() -> dict:
    """
    Re-compute cost_usd in every result file using the current _calc_cost pricing.

    For single-model configs (snapshot has one model across all passes): recalculates
    exactly from stored input_tokens + output_tokens.
    For mixed-model or built-in configs: skips (their pricing was already correct).

    Returns {"files_updated": int, "entries_updated": int, "skipped": int}.
    """
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from brain import _calc_cost

    os.makedirs(RESULTS_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json")))
    files_updated = 0
    entries_updated = 0
    skipped = 0

    for fpath in files:
        try:
            with open(fpath) as fh:
                data = json.load(fh)
        except Exception:
            continue

        snapshots = data.get("_config_snapshots", {}) or {}
        changed = False

        for cfg_name, per_video in data.items():
            if cfg_name.startswith("_") or not isinstance(per_video, list):
                continue

            snap = snapshots.get(cfg_name, {})
            if not snap:
                # Built-in config — haiku pricing was already correct
                skipped += len(per_video)
                continue

            # Collect all models used across passes
            models = set()
            for pk in ("pass1", "pass2", "pass3"):
                m = snap.get(pk, {}).get("model")
                if m:
                    models.add(m)

            if len(models) != 1:
                # Mixed-model config — can't split token counts; haiku+sonnet was correct anyway
                skipped += len(per_video)
                continue

            model = next(iter(models))

            for entry in per_video:
                usage = entry.get("usage")
                if not usage:
                    skipped += 1
                    continue
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                new_cost = _calc_cost(model, inp, out)
                if abs(new_cost - usage.get("cost_usd", 0)) > 1e-9:
                    usage["cost_usd"] = new_cost
                    changed = True
                    entries_updated += 1
                else:
                    skipped += 1

        if changed:
            with open(fpath, "w") as fh:
                json.dump(data, fh, indent=2)
            files_updated += 1

    return {"files_updated": files_updated, "entries_updated": entries_updated, "skipped": skipped}


def delete_result(filename: str) -> bool:
    """Delete a single result file by filename (basename only, no path traversal)."""
    # Sanitize: only allow safe filenames
    safe = os.path.basename(filename)
    if not safe.endswith(".json") or safe != filename:
        return False
    path = os.path.join(RESULTS_DIR, safe)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False

def list_results():
    """Return past eval run results, newest first."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json")), reverse=True)
    results = []
    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            results.append({"filename": os.path.basename(f), "data": data})
        except Exception:
            pass
    return results


def save_result(data, config_names, snapshots=None, versions=None):
    """Save a result dict to evals/results/ with a timestamped filename.

    snapshots: optional dict {config_name: config_dict} — records prompts/params used.
    versions:  optional dict {config_name: version_int} — version at time of run.
    Both stored under '_meta' and '_config_snapshots' keys in the result file.
    """
    from datetime import datetime
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    joined = "_vs_".join(config_names)
    if len(joined) > 80:
        joined = f"{config_names[0]}_and_{len(config_names)-1}_more"
    fname = f"{timestamp}-{joined}.json"
    path  = os.path.join(RESULTS_DIR, fname)

    def serialise(obj):
        if isinstance(obj, set):
            return sorted(obj)
        if isinstance(obj, dict):
            return {k: serialise(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [serialise(i) for i in obj]
        return obj

    payload = serialise(data)

    # Drop configs where every video returned 0 mentions — rate-limit / crash artefacts
    empty_configs = []
    for cfg_name, per_video in list(payload.items()):
        if cfg_name.startswith("_") or not isinstance(per_video, list) or not per_video:
            continue
        all_zero = all(
            isinstance(r, dict) and r.get("metrics", {}).get("n_returned", 1) == 0
            for r in per_video
        )
        if all_zero:
            empty_configs.append(cfg_name)
            del payload[cfg_name]
    if empty_configs:
        import logging
        logging.getLogger(__name__).warning(
            "save_result: skipped %d config(s) with all-zero output (likely rate-limited): %s",
            len(empty_configs), empty_configs,
        )
    # If nothing useful remains, don't write the file at all
    real_keys = [k for k in payload if not k.startswith("_")]
    if not real_keys:
        import logging
        logging.getLogger(__name__).warning(
            "save_result: all configs had zero output — not saving result file"
        )
        return None

    if snapshots:
        payload["_config_snapshots"] = serialise(snapshots)
    payload["_meta"] = {
        "timestamp": timestamp,
        "versions":  versions or {},
    }

    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


def recheck_all_results() -> dict:
    """
    Re-score every result file against current ground truth annotations.
    Only updates entries that have 'raw_mentions' stored (new-format runs).
    Returns {"files_updated": int, "entries_updated": int, "skipped": int}.
    """
    from evals import scorer as sc
    templates = {gt["video_id"]: gt for gt in list_templates()}

    os.makedirs(RESULTS_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json")))
    files_updated = 0
    entries_updated = 0
    skipped = 0

    for fpath in files:
        try:
            with open(fpath) as fh:
                data = json.load(fh)
        except Exception:
            continue

        changed = False
        for cfg_name, per_video in data.items():
            if cfg_name.startswith("_") or not isinstance(per_video, list):
                continue
            for entry in per_video:
                vid = entry.get("video_id")
                raw = entry.get("raw_mentions")
                if raw is None:
                    skipped += 1
                    continue
                gt = templates.get(vid)
                if not gt:
                    skipped += 1
                    continue
                new_m = sc.score(gt["annotations"], raw)
                entry["metrics"] = {
                    k: new_m[k]
                    for k in ("precision", "recall", "f1", "f2",
                              "sentiment_acc", "rec_acc", "confidence_acc",
                              "quality_pts", "quality_max", "quality_display")
                }
                entry["found"]         = sorted(new_m["found"])
                entry["missed"]        = sorted(new_m["missed"])
                entry["hallucinated"]  = sorted(new_m["hallucinated"])
                entry["ticker_details"] = new_m.get("ticker_details", [])
                entries_updated += 1
                changed = True

        if changed:
            with open(fpath, "w") as fh:
                json.dump(data, fh, indent=2)
            files_updated += 1

    return {"files_updated": files_updated, "entries_updated": entries_updated, "skipped": skipped}


def list_results_grouped():
    """
    Load all result files, group by (config_name, version), average metrics per video.

    Returns a list of config dicts, sorted by config_name:
    [
      {
        "config_name": "two_pass_haiku",
        "versions": [             # newest version first
          {
            "version":   2,
            "run_count": 3,
            "agg":       {precision, recall, f1, sentiment_acc, rec_acc, confidence_acc, n_videos},
            "per_video": [{video_id, title, metrics, found, missed, hallucinated, run_count}],
            "runs":      [{filename, timestamp, per_video}],   # individual runs
            "snapshot":  config_dict or None,
          },
          ...
        ]
      },
      ...
    ]
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json")), reverse=True)

    # Collect raw runs: {(config_name, version): [{"filename", "timestamp", "per_video"}]}
    grouped: dict = {}

    for fpath in files:
        try:
            with open(fpath) as fh:
                data = json.load(fh)
        except Exception:
            continue

        filename  = os.path.basename(fpath)
        meta      = data.get("_meta", {})
        timestamp = meta.get("timestamp", filename[:19])
        v_map     = meta.get("versions", {})
        snapshots = data.get("_config_snapshots", {})

        for cfg_name, per_video in data.items():
            if cfg_name.startswith("_") or not isinstance(per_video, list) or not per_video:
                continue
            # Skip runs where every video returned 0 mentions (rate-limit / crash artefacts)
            if all(isinstance(r, dict) and r.get("metrics", {}).get("n_returned", 1) == 0
                   for r in per_video):
                continue
            version  = v_map.get(cfg_name, 1)
            key      = (cfg_name, version)
            snapshot = snapshots.get(cfg_name)

            grouped.setdefault(key, {
                "config_name": cfg_name,
                "version":     version,
                "snapshot":    snapshot,
                "runs":        [],
            })
            grouped[key]["runs"].append({
                "filename":  filename,
                "timestamp": timestamp,
                "per_video": per_video,
            })
            # Snapshot from the most recent run wins
            if snapshot:
                grouped[key]["snapshot"] = snapshot

    # Average metrics per (config, version) across all runs
    result_by_config: dict = {}
    for (cfg_name, version), group in grouped.items():
        per_video_agg = _average_runs(group["runs"])
        from evals import scorer as sc
        agg = sc.aggregate([v["metrics"] for v in per_video_agg])

        n = len(per_video_agg) or 1
        total_input  = sum(v.get("usage", {}).get("input_tokens", 0)  for v in per_video_agg)
        total_output = sum(v.get("usage", {}).get("output_tokens", 0) for v in per_video_agg)
        total_cost   = sum(v.get("usage", {}).get("cost_usd", 0.0)    for v in per_video_agg)
        agg["usage"] = {
            "input_tokens":  round(total_input  / n),
            "output_tokens": round(total_output / n),
            "cost_usd":      round(total_cost   / n, 6),
        }

        entry = {
            "version":   version,
            "run_count": len(group["runs"]),
            "agg":       agg,
            "per_video": per_video_agg,
            "runs":      group["runs"],
            "snapshot":  group["snapshot"],
        }
        result_by_config.setdefault(cfg_name, []).append(entry)

    # Sort versions newest-first within each config
    for cfg_name in result_by_config:
        result_by_config[cfg_name].sort(key=lambda e: e["version"], reverse=True)

    # Return sorted by config name
    return [
        {"config_name": cfg_name, "versions": versions}
        for cfg_name, versions in sorted(result_by_config.items())
    ]


def _average_runs(runs: list) -> list:
    """
    Given multiple runs (each with a per_video list), average numeric metrics
    per video_id.

    found/missed/hallucinated use majority-vote across runs (>50% → found,
    <50% → missed, exactly 50% → both). Each list entry is enriched with
    a 'rate' field (fraction of runs the ticker was found/hallucinated).
    """
    by_video: dict = {}
    for run in reversed(runs):   # oldest first so latest overwrites ticker_details
        for v in run["per_video"]:
            vid = v["video_id"]
            by_video.setdefault(vid, {"entries": [], "latest": None})
            by_video[vid]["entries"].append(v)
            by_video[vid]["latest"] = v

    averaged = []
    for vid, data in by_video.items():
        entries = data["entries"]
        latest  = data["latest"]
        # Exclude errored entries from all scoring — they have no valid metrics
        valid   = [e for e in entries if not e.get("error")]
        n_runs  = len(valid)

        avg_metrics = {}
        for key in ("precision", "recall", "f1", "sentiment_acc", "rec_acc", "confidence_acc"):
            vals = [e["metrics"][key] for e in valid if e["metrics"].get(key) is not None]
            avg_metrics[key] = round(sum(vals) / len(vals), 3) if vals else None

        # F2 can always be derived from precision + recall (works for old result files too)
        p, r = avg_metrics.get("precision"), avg_metrics.get("recall")
        if p is not None and r is not None and (4 * p + r) > 0:
            avg_metrics["f2"] = round(5 * p * r / (4 * p + r), 3)
        else:
            avg_metrics["f2"] = None

        # Quality: sum pts/max across valid runs only
        q_pts = sum(e["metrics"].get("quality_pts", 0) for e in valid)
        q_max = sum(e["metrics"].get("quality_max", 0) for e in valid)
        avg_metrics["quality_pts"]     = q_pts
        avg_metrics["quality_max"]     = q_max
        avg_metrics["quality_display"] = f"{q_pts}/{q_max}" if q_max else None

        # Per-ticker find rates across valid runs only.
        # ticker_counts: expected tickers → how many runs found them.
        ticker_counts: dict = {}
        for entry in valid:
            for t in entry.get("found", []):
                ticker_counts[t] = ticker_counts.get(t, 0) + 1
            for t in entry.get("missed", []):
                ticker_counts.setdefault(t, 0)  # ensure present even if never found

        # Hallucination rates: tickers returned but not expected.
        hal_counts: dict = {}
        for entry in valid:
            for t in entry.get("hallucinated", []):
                hal_counts[t] = hal_counts.get(t, 0) + 1

        # Majority-vote bucketing with rate annotation.
        found_rated  = []
        missed_rated = []
        for t, cnt in ticker_counts.items():
            rate = cnt / n_runs if n_runs else 0.0
            obj  = {"ticker": t, "rate": round(rate, 4)}
            if rate > 0.5:
                found_rated.append(obj)
            elif rate < 0.5:
                missed_rated.append(obj)
            else:  # exactly 50% — appears in both lists
                found_rated.append(obj)
                missed_rated.append(obj)

        # Sort: found → highest rate first; missed → lowest rate first (hardest misses upfront).
        found_rated.sort(key=lambda x: (-x["rate"], x["ticker"]))
        missed_rated.sort(key=lambda x: (x["rate"], x["ticker"]))

        # Hallucinated: show if hallucinated in >50% of runs, rate = fraction of runs.
        hal_rated = [
            {"ticker": t, "rate": round(c / n_runs, 4)}
            for t, c in hal_counts.items()
            if n_runs and c / n_runs > 0.5
        ]
        hal_rated.sort(key=lambda x: (-x["rate"], x["ticker"]))

        # Keep plain string lists for any consumers that expect them.
        found_tickers = sorted(o["ticker"] for o in found_rated)
        missed_tickers = sorted(o["ticker"] for o in missed_rated)
        hal_tickers   = sorted(o["ticker"] for o in hal_rated)

        total_input  = sum(e.get("usage", {}).get("input_tokens", 0)  for e in valid)
        total_output = sum(e.get("usage", {}).get("output_tokens", 0) for e in valid)
        total_cost   = round(sum(e.get("usage", {}).get("cost_usd", 0.0) for e in valid), 6)

        if n_runs == 0:
            continue  # all runs errored — exclude from aggregate entirely

        averaged.append({
            "video_id":      vid,
            "title":         latest.get("title", ""),
            "metrics":       avg_metrics,
            "found":         found_tickers,
            "missed":        missed_tickers,
            "hallucinated":  hal_tickers,
            "found_rated":   found_rated,
            "missed_rated":  missed_rated,
            "hal_rated":     hal_rated,
            "ticker_details": latest.get("ticker_details", []),
            "run_count":     n_runs,
            "error":         latest.get("error"),
            "usage": {
                "input_tokens":  total_input,
                "output_tokens": total_output,
                "cost_usd":      total_cost,
            },
        })
    return averaged


# ---------------------------------------------------------------------------
# Layer test results  (single-pass isolation tests)
# ---------------------------------------------------------------------------

def save_layer_result(entry: dict) -> str:
    """Append one layer test result. Returns the entry id."""
    import datetime
    os.makedirs(_LAYER_DIR, exist_ok=True)
    results = _load_layer_results_raw()
    entry.setdefault("id", f"lt_{int(datetime.datetime.now().timestamp()*1000)}")
    entry.setdefault("timestamp", datetime.datetime.now().isoformat(timespec="seconds"))
    results.append(entry)
    with open(LAYER_RESULTS_FILE, "w") as fh:
        json.dump(results, fh, indent=2)
    return entry["id"]


def list_layer_results() -> list:
    """Return all saved layer test results, newest first."""
    return list(reversed(_load_layer_results_raw()))


def delete_layer_result(entry_id: str) -> bool:
    results = _load_layer_results_raw()
    new = [r for r in results if r.get("id") != entry_id]
    if len(new) == len(results):
        return False
    with open(LAYER_RESULTS_FILE, "w") as fh:
        json.dump(new, fh, indent=2)
    return True


def _load_layer_results_raw() -> list:
    if not os.path.exists(LAYER_RESULTS_FILE):
        return []
    try:
        with open(LAYER_RESULTS_FILE) as fh:
            return json.load(fh)
    except Exception:
        return []
