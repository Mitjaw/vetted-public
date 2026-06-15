"""
Pure scoring functions — no I/O, no side effects.

Matching rule: ticker.upper() exact match.

Primary metrics:
  f2             — recall-weighted F score (β=2); penalises misses more than hallucinations
  quality_display— partial-credit score "X/Y" where:
                     +6 pts  ticker found
                     +2 pts  sentiment correct (skipped for reference-annotated tickers)
                     +2 pts  recommendation correct
                   max per ticker = 10 (8 for reference tickers where sentiment pts are skipped)
Secondary (diagnostic):
  sentiment_acc  — % correct sentiment on matched non-reference tickers
  rec_acc        — % correct recommendation on matched tickers
  confidence_acc — AI confidence within annotated range (kept for reference, not used in ranking)
"""


def score(gt_annotations, model_mentions):
    """
    Score model output against ground truth annotations.

    Returns dict with:
      precision       — % of returned tickers that were expected (anti-hallucination)
      recall          — % of expected tickers that were returned (anti-miss)
      f1              — harmonic mean (kept for reference)
      f2              — recall-weighted F score (β=2, primary ranking metric)
      sentiment_acc   — % correct sentiment on matched non-reference tickers
      rec_acc         — % correct recommendation on matched tickers
      confidence_acc  — AI confidence within annotated range (diagnostic only)
      quality_pts     — partial-credit score earned (integer, ×10 scale)
      quality_max     — maximum achievable score for this video (integer, ×10 scale)
      quality_display — "quality_pts/quality_max" string
      found           — set of tickers correctly returned
      missed          — set of expected tickers not returned
      hallucinated    — set of returned tickers not in ground truth
      n_expected      — count of expected mentions
      n_returned      — count of model-returned mentions
    """
    expected = {a["ticker"].upper(): a for a in gt_annotations}
    returned = {m["ticker"].upper(): m for m in model_mentions}

    found        = set(expected) & set(returned)
    missed       = set(expected) - set(returned)
    hallucinated = set(returned) - set(expected)

    precision = len(found) / len(returned) if returned else 1.0
    recall    = len(found) / len(expected) if expected else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    # F2: β=2, recall counts twice as much as precision
    f2 = (5 * precision * recall / (4 * precision + recall)) if (4 * precision + recall) else 0.0

    # Sentiment accuracy: skip reference tickers (no directional signal to judge)
    sentiment_eligible = [
        t for t in found
        if expected[t].get("recommendation") != "reference"
        and expected[t].get("sentiment") not in (None, "", "unknown")
    ]
    sentiment_matches = sum(
        1 for t in sentiment_eligible
        if returned[t].get("sentiment") == expected[t].get("sentiment")
    )

    # Rec accuracy: references included — calling a reference a "buy" is a real failure
    rec_matches = sum(
        1 for t in found
        if returned[t].get("recommendation") == expected[t].get("recommendation")
    )

    # Confidence accuracy: kept as diagnostic metric, not used for ranking
    conf_eligible = [
        t for t in found
        if expected[t].get("confidence_min") is not None
        or expected[t].get("confidence_max") is not None
    ]
    conf_matches = 0
    for t in conf_eligible:
        model_conf = returned[t].get("confidence")
        if model_conf is None:
            continue
        lo = expected[t].get("confidence_min") if expected[t].get("confidence_min") is not None else 0.0
        hi = expected[t].get("confidence_max") if expected[t].get("confidence_max") is not None else 1.0
        if lo <= float(model_conf) <= hi:
            conf_matches += 1

    sentiment_acc  = sentiment_matches / len(sentiment_eligible) if sentiment_eligible else None
    rec_acc        = rec_matches       / len(found)              if found              else None
    confidence_acc = conf_matches      / len(conf_eligible)      if conf_eligible      else None

    # Quality score: partial credit per expected ticker (×10 integer scale)
    #   +6  ticker found
    #   +2  sentiment correct (skipped for reference-annotated tickers)
    #   +2  recommendation correct
    # quality_max is computed dynamically: reference tickers cap at 8, others at 10.
    quality_pts = 0
    quality_max = 0
    ticker_details = []

    for t in expected:
        is_reference = expected[t].get("recommendation") == "reference"
        ticker_max   = 8 if is_reference else 10  # no sentiment pts for references
        quality_max += ticker_max

        pts = 0
        sentiment_match = None
        rec_match       = None

        if t in found:
            pts += 6
            if not is_reference:
                sentiment_match = (
                    returned[t].get("sentiment") == expected[t].get("sentiment")
                )
                if sentiment_match:
                    pts += 2
            rec_match = (
                returned[t].get("recommendation") == expected[t].get("recommendation")
            )
            if rec_match:
                pts += 2

        quality_pts += pts

        # Per-ticker details (for all expected tickers, found or not)
        model_conf = returned[t].get("confidence") if t in found else None
        lo = expected[t].get("confidence_min")
        hi = expected[t].get("confidence_max")
        if lo is None and hi is None:
            in_range = None
        elif model_conf is None:
            in_range = None
        else:
            lo_val = float(lo) if lo is not None else 0.0
            hi_val = float(hi) if hi is not None else 1.0
            in_range = lo_val <= float(model_conf) <= hi_val

        ticker_details.append({
            "ticker":          t,
            "found":           t in found,
            "quality_pts":     pts,
            "quality_max":     ticker_max,
            "sentiment_match": sentiment_match,
            "rec_match":       rec_match,
            "ai_confidence":   round(float(model_conf), 3) if model_conf is not None else None,
            "in_range":        in_range,
            "conf_min":        lo,
            "conf_max":        hi,
        })

    ticker_details.sort(key=lambda d: d["ticker"])

    quality_display = f"{quality_pts}/{quality_max}" if quality_max else "—"

    return {
        "precision":       round(precision, 3),
        "recall":          round(recall, 3),
        "f1":              round(f1, 3),
        "f2":              round(f2, 3),
        "sentiment_acc":   round(sentiment_acc, 3)  if sentiment_acc  is not None else None,
        "rec_acc":         round(rec_acc, 3)         if rec_acc        is not None else None,
        "confidence_acc":  round(confidence_acc, 3)  if confidence_acc is not None else None,
        "quality_pts":     quality_pts,
        "quality_max":     quality_max,
        "quality_display": quality_display,
        "found":           found,
        "missed":          missed,
        "hallucinated":    hallucinated,
        "n_expected":      len(expected),
        "n_returned":      len(returned),
        "ticker_details":  ticker_details,
    }


def aggregate(per_video_scores):
    """Average metrics across multiple videos. None values are excluded from averages.
    Quality pts/max are summed (not averaged) so the ratio stays meaningful."""
    scalar_keys = ["precision", "recall", "f1", "f2", "sentiment_acc", "rec_acc", "confidence_acc"]
    result = {}
    for k in scalar_keys:
        vals = [s[k] for s in per_video_scores if s.get(k) is not None]
        result[k] = round(sum(vals) / len(vals), 3) if vals else None

    total_pts = sum(s.get("quality_pts", 0) for s in per_video_scores)
    total_max = sum(s.get("quality_max", 0) for s in per_video_scores)
    result["quality_pts"]     = total_pts
    result["quality_max"]     = total_max
    result["quality_display"] = f"{total_pts}/{total_max}" if total_max else "—"

    result["n_videos"] = len(per_video_scores)
    return result
