"""
Eval runner — callable as CLI or as importable background task.

CLI usage:
  python -m evals.runner
  python -m evals.runner --configs two_pass_haiku single_pass_haiku
  python -m evals.runner --video <video_id>
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from evals.configs import CONFIGS
from evals import scorer as sc
from evals import store

log = logging.getLogger(__name__)


def _get_transcript(video_id, gt):
    """Return transcript text: from GT file if present, otherwise from DB."""
    if gt.get("transcript"):
        return gt["transcript"]
    try:
        import sqlite3
        import db_manager
        conn = sqlite3.connect(db_manager.DB_NAME)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT transcript FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        conn.close()
        if row and row["transcript"]:
            return row["transcript"]
    except Exception as e:
        log.warning("Could not load transcript for %s from DB: %s", video_id, e)
    return None


def run_config(config, ground_truth):
    """Run one config against all GT videos. Returns list of per-video result dicts."""
    results = []
    for gt in ground_truth:
        video_id   = gt["video_id"]
        transcript = _get_transcript(video_id, gt)

        if not transcript:
            log.warning("Eval: no transcript for %s — skipping", video_id)
            continue

        log.info("Eval: running %s on %s", config["name"], video_id)
        try:
            result = config["run_fn"](
                transcript=transcript,
                title=gt.get("title", ""),
                language=gt.get("language", "en"),
            )
            # run_fn may return (mentions, usage) or just mentions (legacy)
            if isinstance(result, tuple):
                mentions, usage = result
            else:
                mentions, usage = result, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        except Exception as e:
            log.error("Eval: %s failed on %s: %s", config["name"], video_id, e)
            mentions, usage = [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            results.append({
                "video_id":      video_id,
                "title":         gt.get("title", ""),
                "metrics":       {"precision": None, "recall": None, "f1": None, "f2": None,
                                  "sentiment_acc": None, "rec_acc": None, "confidence_acc": None,
                                  "quality_pts": 0, "quality_max": 0, "quality_display": "—",
                                  "missed": [], "hallucinated": [], "found": [], "ticker_details": []},
                "missed":        [],
                "hallucinated":  [],
                "found":         [],
                "ticker_details": [],
                "usage":         usage,
                "error":         str(e),
            })
            continue

        metrics = sc.score(gt["annotations"], mentions)
        results.append({
            "video_id":      video_id,
            "title":         gt.get("title", ""),
            "metrics":       metrics,
            "missed":        sorted(metrics["missed"]),
            "hallucinated":  sorted(metrics["hallucinated"]),
            "found":         sorted(metrics["found"]),
            "ticker_details": metrics.get("ticker_details", []),
            "usage":         usage,
            # Store minimal AI output so results can be re-scored when GT changes
            "raw_mentions":  [
                {k: m.get(k) for k in ("ticker", "sentiment", "recommendation", "confidence")}
                for m in mentions
            ],
        })
    return results


def run_as_task(config_names=None, video_ids=None, config_versions=None):
    """
    Entry point for FastAPI background task.
    config_names:    list of config name strings (built-in or custom), or None to run all.
    video_ids:       list of video_id strings to evaluate against, or None to use all templates.
    config_versions: optional dict {config_name: version_int} — run a specific version.
                     Custom configs not in this dict use their latest version.
    """
    from evals import custom_store
    from evals.executor import config_to_runnable

    config_versions = config_versions or {}
    builtin_map = {c["name"]: c for c in CONFIGS}
    custom_cfgs = {
        c["name"]: config_to_runnable(c, version_num=config_versions.get(c["name"]))
        for c in custom_store.list_configs()
    }
    config_map  = {**builtin_map, **custom_cfgs}

    selected = (
        [config_map[n] for n in config_names if n in config_map]
        if config_names else list(config_map.values())
    )

    ground_truth = store.list_templates()
    if video_ids:
        video_ids_set = set(video_ids)
        ground_truth  = [gt for gt in ground_truth if gt["video_id"] in video_ids_set]
    if not ground_truth:
        log.info("Eval run: no ground truth templates found.")
        return

    from evals import version_registry as vr

    log.info("Eval run starting: %d config(s) × %d video(s)", len(selected), len(ground_truth))

    all_results = {}
    snapshots   = {}
    versions    = {}

    for config in selected:
        name = config["name"]
        all_results[name] = run_config(config, ground_truth)

        if "_raw" in config:
            snapshots[name] = config["_raw"]
            if "_version" in config:
                # Custom config with explicit version — use it directly
                versions[name] = config["_version"]
            else:
                # Fallback: hash-based (legacy configs without explicit version)
                cfg_hash = vr.compute_hash_custom(config["_raw"])
                versions[name] = vr.resolve_version(name, cfg_hash)
        else:
            # Built-in config — hash-based version tracking unchanged
            cfg_hash = vr.compute_hash_builtin(config["run_fn"])
            versions[name] = vr.resolve_version(name, cfg_hash)

    path = store.save_result(
        all_results,
        [c["name"] for c in selected],
        snapshots=snapshots or None,
        versions=versions,
    )
    log.info("Eval run complete — saved to %s", path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run extraction evals")
    parser.add_argument("--configs", nargs="+", help="Config names to run (default: all)")
    parser.add_argument("--video",   help="Run against a single video_id only")
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

    ground_truth = store.list_templates()
    if args.video:
        ground_truth = [g for g in ground_truth if g["video_id"] == args.video]

    if not ground_truth:
        print("No ground truth files found. Add JSON files to evals/ground_truth/ (see EXAMPLE.json).")
        sys.exit(0)

    print(f"\nRunning {len(selected)} config(s) against {len(ground_truth)} video(s).\n")

    all_results = {}
    for config in selected:
        print(f"\n── {config['description']} ──")
        per_video = run_config(config, ground_truth)
        all_results[config["name"]] = per_video
        for v in per_video:
            m = v["metrics"]
            sent = f"{m['sentiment_acc']:.0%}" if m["sentiment_acc"] is not None else "—"
            rec  = f"{m['rec_acc']:.0%}"       if m["rec_acc"]       is not None else "—"
            print(
                f"  {v['video_id']} | P={m['precision']:.0%} R={m['recall']:.0%} "
                f"F2={m['f2']:.0%} Q={m['quality_display']} Sent={sent} Rec={rec} | "
                f"Found={','.join(v['found']) or '—'} "
                f"Missed={','.join(v['missed']) or '—'} "
                f"Halluc={','.join(v['hallucinated']) or '—'}"
            )

    # Summary table
    print("\n" + "=" * 90)
    print(f"{'Config':<30} {'P':>6} {'R':>6} {'F2':>6} {'Quality':>9} {'Sent':>7} {'Rec':>7} {'Videos':>7}")
    print("-" * 90)
    for config in selected:
        agg  = sc.aggregate([v["metrics"] for v in all_results[config["name"]]])
        sent = f"{agg['sentiment_acc']:.0%}" if agg["sentiment_acc"] is not None else "—"
        rec  = f"{agg['rec_acc']:.0%}"       if agg["rec_acc"]       is not None else "—"
        print(
            f"{config['name']:<30} "
            f"{agg['precision']:>5.0%} {agg['recall']:>6.0%} {agg['f2']:>6.0%} "
            f"{agg['quality_display']:>9} {sent:>7} {rec:>7} {agg['n_videos']:>7}"
        )
    print("=" * 90)

    store.save_result(all_results, [c["name"] for c in selected])


if __name__ == "__main__":
    main()
