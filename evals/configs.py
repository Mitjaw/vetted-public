"""
Eval configurations — one entry per model/prompt variant to benchmark.

Each config:
  name        — short identifier, used in filenames and table headers
  description — human-readable label shown in the UI
  run_fn      — callable(transcript, title, language) -> list[mention_dict]
                Returns same shape as brain.analyze_transcript().

Add new configs here to benchmark them. Runner and UI pick up CONFIGS automatically.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import brain

_ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def _two_pass_haiku(transcript, title, language):
    """Current production pipeline: two-pass with claude-haiku-4-5-20251001."""
    return brain.analyze_transcript(transcript, title=title, language=language)


def _single_pass_haiku(transcript, title, language):
    """Legacy single-pass pipeline (the fallback path in brain.py)."""
    return brain._single_pass(transcript, title, language)


def _two_pass_sonnet(transcript, title, language):
    """Two-pass pipeline using claude-sonnet-4-6 for both passes."""
    import anthropic
    import json
    from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS

    MODEL      = "claude-sonnet-4-6"
    client     = anthropic.Anthropic()
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""

    user1 = (
        f"Language: {lang_label}\n{title_hint}\nTranscript:\n{transcript}\n\n"
        "Return ONLY valid JSON.\n\n"
        'Required output format:\n{"stocks": [{"ticker": "AAPL", "company_name": "Apple Inc."}]}'
    )
    try:
        r1 = client.messages.create(
            model=MODEL, max_tokens=4096,
            system=(
                "You are a specialist financial transcript scanner with deep experience reading "
                "unpunctuated auto-generated YouTube transcripts in German and English. "
                "Find EVERY investment vehicle — stocks, ETFs, crypto, commodities. "
                "Fix transcription errors. Prefer US ADR ticker; use local format (SAP.DE) if no ADR. "
                "Return entries in transcript order. Do not invent tickers."
            ),
            messages=[{"role": "user", "content": user1}],
        )
        discovered = json.loads(_strip_markdown(r1.content[0].text)).get("stocks", [])
    except Exception as e:
        print(f"  [sonnet discovery failed: {e}]")
        return [], _ZERO_USAGE

    if not discovered:
        return [], _ZERO_USAGE
    if len(discovered) > _MAX_DISCOVERED_STOCKS:
        discovered = discovered[:_MAX_DISCOVERED_STOCKS]

    n          = len(discovered)
    stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
    user2 = (
        f"Language: {lang_label}\n{title_hint}\n"
        f"Analyze exactly {n} investment vehicle{'s' if n != 1 else ''}:\n{stock_list}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return ONLY valid JSON.\n\n"
        'Required output format:\n{"mentions": [{"ticker": "AAPL", "company_name": "Apple Inc.", '
        '"sentiment": "bullish", "confidence": 0.82, '
        '"recommendation": "buy", "mention_count": 4, "context": "..."}]}'
    )
    try:
        r2 = client.messages.create(
            model=MODEL, max_tokens=8192,
            system=(
                "You know the difference between genuine conviction and performative neutrality. "
                "Return exactly one mention object per stock. "
                "Do not skip any. Do not add new ones. Use exact ticker string provided. "
                "Lean bullish/bearish when directional signal present."
            ),
            messages=[{"role": "user", "content": user2}],
        )
        mentions = json.loads(_strip_markdown(r2.content[0].text)).get("mentions", [])
    except Exception as e:
        print(f"  [sonnet analysis failed: {e}]")
        return [], _ZERO_USAGE

    discovered_tickers = {s["ticker"].upper() for s in discovered}
    filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
    return filtered, _ZERO_USAGE


def _haiku_discover_sonnet_analyze(transcript, title, language):
    """
    Pass 1 uses Haiku for cheap broad discovery.
    Pass 2 uses Sonnet for sharper sentiment/confidence/context analysis.
    Idea: Haiku is good at extraction, Sonnet is better at interpretation.
    """
    import anthropic
    import json
    from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS

    client     = anthropic.Anthropic()
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = (
        f"\nVideo title: {title}\n"
        "Use the title as a count anchor — if it says '5 picks' or 'top buys' "
        "(or German/Spanish equivalent), expect to find that many.\n"
    ) if title else ""

    user1 = (
        f"Language: {lang_label}\n{title_hint}\nTranscript:\n{transcript}\n\n"
        "Return ONLY valid JSON. No preamble, no markdown.\n\n"
        'Required output format:\n{"stocks": [{"ticker": "AAPL", "company_name": "Apple Inc."}]}'
    )
    try:
        r1 = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=4096,
            system=(
                "You are a specialist financial transcript scanner with deep experience reading "
                "unpunctuated auto-generated YouTube transcripts in German and English. "
                "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, commodities. "
                "Cast a wide net. No sentiment, no judgement — discovery only. "
                "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
                "Prefer US ADR ticker where one exists; otherwise use local format (SAP.DE, P911.DE, BMW.DE). "
                "Return entries in the order they first appear in the transcript. "
                "Do not invent tickers not present in the transcript."
            ),
            messages=[{"role": "user", "content": user1}],
        )
        discovered = json.loads(_strip_markdown(r1.content[0].text)).get("stocks", [])
    except Exception as e:
        print(f"  [haiku discovery failed: {e}]")
        return [], _ZERO_USAGE

    if not discovered:
        return [], _ZERO_USAGE
    if len(discovered) > _MAX_DISCOVERED_STOCKS:
        discovered = discovered[:_MAX_DISCOVERED_STOCKS]

    n          = len(discovered)
    stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
    user2 = (
        f"Language: {lang_label}\n{title_hint}"
        f"Analyze exactly {n} investment vehicle{'s' if n != 1 else ''}:\n{stock_list}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return ONLY valid JSON. No preamble, no markdown.\n\n"
        '{"mentions": [{"ticker": "AAPL", "company_name": "Apple Inc.", '
        '"sentiment": "bullish", "confidence": 0.82, '
        '"recommendation": "buy", "mention_count": 4, "context": "..."}]}'
    )
    try:
        r2 = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=8192,
            system=(
                "You know the difference between genuine conviction and performative neutrality. "
                "For each stock in the provided list: return exactly one mention object. "
                "Do not skip any. Do not add tickers beyond those listed. "
                "Use the exact ticker string as provided. "
                "If a stock genuinely cannot be found in the transcript: "
                "set confidence=0.0, mention_count=0, and explain in context. "
                "Sentiment: lean toward bullish/bearish when any directional signal is present — "
                "reserve neutral for genuinely balanced or purely informational mentions."
            ),
            messages=[{"role": "user", "content": user2}],
        )
        mentions = json.loads(_strip_markdown(r2.content[0].text)).get("mentions", [])
    except Exception as e:
        print(f"  [sonnet analysis failed: {e}]")
        return [], _ZERO_USAGE

    discovered_tickers = {s["ticker"].upper() for s in discovered}
    filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
    return filtered, _ZERO_USAGE


def _two_pass_haiku_calibrated(transcript, title, language):
    """
    Two-pass Haiku with explicit confidence calibration examples in Pass 2 system prompt.
    Idea: models are overconfident without anchors — show them what 0.9 / 0.7 / 0.4 looks like.
    """
    import anthropic
    import json
    from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS, _discovery_pass

    client     = anthropic.Anthropic()
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""

    discovered, _ = _discovery_pass(transcript, title, language)
    if not discovered:
        return [], _ZERO_USAGE
    if len(discovered) > _MAX_DISCOVERED_STOCKS:
        discovered = discovered[:_MAX_DISCOVERED_STOCKS]

    n          = len(discovered)
    stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
    user2 = (
        f"Language: {lang_label}\n{title_hint}"
        f"Analyze exactly {n} investment vehicle{'s' if n != 1 else ''}:\n{stock_list}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return ONLY valid JSON. No preamble, no markdown.\n\n"
        '{"mentions": [{"ticker": "AAPL", "company_name": "Apple Inc.", '
        '"sentiment": "bullish", "confidence": 0.82, '
        '"recommendation": "buy", "mention_count": 4, "context": "..."}]}'
    )
    calibration = (
        "CONFIDENCE CALIBRATION GUIDE — use this scale strictly:\n"
        "  0.9–1.0 : Host says 'this is my top pick', 'I'm buying more', 'strong conviction', explicitly recommends action\n"
        "  0.7–0.89: Clear directional view expressed ('I like this stock', 'bullish on X'), no hedge\n"
        "  0.5–0.69: Mild lean with hedging ('could be interesting', 'watching closely', 'potential upside')\n"
        "  0.3–0.49: Ambiguous — balanced pros/cons, 'I'm not sure', market consensus recap without personal view\n"
        "  0.0–0.29: Purely informational, passing mention, no investment signal whatsoever\n"
        "When in doubt, score lower. Overconfident scores make eval metrics meaningless.\n\n"
    )
    try:
        r2 = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=8192,
            system=(
                calibration +
                "You know the difference between genuine conviction and performative neutrality. "
                "For each stock in the provided list: return exactly one mention object. "
                "Do not skip any. Do not add tickers beyond those listed. "
                "Use the exact ticker string as provided. "
                "If a stock genuinely cannot be found in the transcript: "
                "set confidence=0.0, mention_count=0, and explain in context. "
                "Sentiment: lean toward bullish/bearish when any directional signal is present — "
                "reserve neutral for genuinely balanced or purely informational mentions."
            ),
            messages=[{"role": "user", "content": user2}],
        )
        mentions = json.loads(_strip_markdown(r2.content[0].text)).get("mentions", [])
    except Exception as e:
        print(f"  [calibrated analysis failed: {e}]")
        return [], _ZERO_USAGE

    discovered_tickers = {s["ticker"].upper() for s in discovered}
    filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
    return filtered, _ZERO_USAGE


def _two_pass_haiku_temp_zero(transcript, title, language):
    """
    Two-pass Haiku at temperature=0 (greedy/deterministic).
    Idea: consistent, repeatable results for debugging and baseline comparison.
    """
    import anthropic
    import json
    from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS

    client     = anthropic.Anthropic()
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = (
        f"\nVideo title: {title}\n"
        "Use the title as a count anchor — if it says '5 picks' or 'top buys' "
        "(or German/Spanish equivalent), expect to find that many.\n"
    ) if title else ""

    user1 = (
        f"Language: {lang_label}\n{title_hint}\nTranscript:\n{transcript}\n\n"
        "Return ONLY valid JSON. No preamble, no markdown.\n\n"
        'Required output format:\n{"stocks": [{"ticker": "AAPL", "company_name": "Apple Inc."}]}'
    )
    try:
        r1 = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=4096, temperature=0,
            system=(
                "You are a specialist financial transcript scanner. "
                "Find EVERY investment vehicle mentioned — stocks, ETFs, crypto, commodities. "
                "Fix transcription errors. Prefer US ADR ticker; use local format if no ADR. "
                "Return entries in transcript order. Do not invent tickers."
            ),
            messages=[{"role": "user", "content": user1}],
        )
        discovered = json.loads(_strip_markdown(r1.content[0].text)).get("stocks", [])
    except Exception as e:
        print(f"  [temp-zero discovery failed: {e}]")
        return [], _ZERO_USAGE

    if not discovered:
        return [], _ZERO_USAGE
    if len(discovered) > _MAX_DISCOVERED_STOCKS:
        discovered = discovered[:_MAX_DISCOVERED_STOCKS]

    n          = len(discovered)
    stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
    user2 = (
        f"Language: {lang_label}\n{title_hint}"
        f"Analyze exactly {n} investment vehicle{'s' if n != 1 else ''}:\n{stock_list}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Return ONLY valid JSON. No preamble, no markdown.\n\n"
        '{"mentions": [{"ticker": "AAPL", "company_name": "Apple Inc.", '
        '"sentiment": "bullish", "confidence": 0.82, '
        '"recommendation": "buy", "mention_count": 4, "context": "..."}]}'
    )
    try:
        r2 = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=8192, temperature=0,
            system=(
                "You know the difference between genuine conviction and performative neutrality. "
                "Return exactly one mention object per stock. "
                "Do not skip any. Do not add new ones. Use exact ticker string provided. "
                "Lean bullish/bearish when directional signal present."
            ),
            messages=[{"role": "user", "content": user2}],
        )
        mentions = json.loads(_strip_markdown(r2.content[0].text)).get("mentions", [])
    except Exception as e:
        print(f"  [temp-zero analysis failed: {e}]")
        return [], _ZERO_USAGE

    discovered_tickers = {s["ticker"].upper() for s in discovered}
    filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
    return filtered, _ZERO_USAGE


def _two_pass_haiku_structured_context(transcript, title, language):
    """
    Two-pass Haiku with structured context: asks for a verbatim quote + interpretation.
    Idea: richer, verifiable context makes eval review much easier.
    """
    import anthropic
    import json
    from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS, _discovery_pass

    client     = anthropic.Anthropic()
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""

    discovered, _ = _discovery_pass(transcript, title, language)
    if not discovered:
        return [], _ZERO_USAGE
    if len(discovered) > _MAX_DISCOVERED_STOCKS:
        discovered = discovered[:_MAX_DISCOVERED_STOCKS]

    n          = len(discovered)
    stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
    user2 = (
        f"Language: {lang_label}\n{title_hint}"
        f"Analyze exactly {n} investment vehicle{'s' if n != 1 else ''}:\n{stock_list}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "For the context field: provide a JSON string with this format: "
        '"QUOTE: <verbatim sentence from transcript> | SIGNAL: <what it means for the investment thesis>"\n\n'
        "Return ONLY valid JSON. No preamble, no markdown.\n\n"
        '{"mentions": [{"ticker": "AAPL", "company_name": "Apple Inc.", '
        '"sentiment": "bullish", "confidence": 0.82, '
        '"recommendation": "buy", "mention_count": 4, '
        '"context": "QUOTE: Apple bleibt mein top pick für das Jahr | SIGNAL: Strong buy conviction, highest-ranked position"}]}'
    )
    try:
        r2 = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=8192,
            system=(
                "You know the difference between genuine conviction and performative neutrality. "
                "For each stock, find the most revealing quote in the transcript "
                "and explain what it signals about the host's investment view. "
                "Return exactly one mention object per stock. Do not skip any. "
                "Do not add tickers beyond those listed. Use exact ticker string provided. "
                "Lean bullish/bearish when directional signal present."
            ),
            messages=[{"role": "user", "content": user2}],
        )
        mentions = json.loads(_strip_markdown(r2.content[0].text)).get("mentions", [])
    except Exception as e:
        print(f"  [structured-context analysis failed: {e}]")
        return [], _ZERO_USAGE

    discovered_tickers = {s["ticker"].upper() for s in discovered}
    filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
    return filtered, _ZERO_USAGE


def _dual_discovery_haiku(transcript, title, language):
    """
    Dual discovery: Pass 1a (standard) + Pass 1b (aggressive) → merged → Pass 2 analysis.
    Three Haiku calls total. Maximises recall.
    """
    return brain.analyze_transcript(transcript, title=title, language=language)


# ── Add new configs below — runner and UI pick up everything in this list ──
CONFIGS = [
    {
        "name":        "dual_discovery_haiku",
        "description": "Dual discovery · 3× Haiku (max recall)",
        "run_fn":      _dual_discovery_haiku,
    },
    {
        "name":        "two_pass_haiku",
        "description": "Two-pass · claude-haiku-4-5-20251001 (baseline)",
        "run_fn":      _two_pass_haiku,
    },
    {
        "name":        "single_pass_haiku",
        "description": "Single-pass · claude-haiku-4-5-20251001 (legacy)",
        "run_fn":      _single_pass_haiku,
    },
    {
        "name":        "two_pass_sonnet",
        "description": "Two-pass · claude-sonnet-4-6",
        "run_fn":      _two_pass_sonnet,
    },
    {
        "name":        "haiku_discover_sonnet_analyze",
        "description": "Haiku discovery · Sonnet analysis (best of both)",
        "run_fn":      _haiku_discover_sonnet_analyze,
    },
    {
        "name":        "haiku_calibrated",
        "description": "Two-pass Haiku · calibrated confidence scale",
        "run_fn":      _two_pass_haiku_calibrated,
    },
    {
        "name":        "haiku_temp_zero",
        "description": "Two-pass Haiku · temperature=0 (deterministic baseline)",
        "run_fn":      _two_pass_haiku_temp_zero,
    },
    {
        "name":        "haiku_structured_context",
        "description": "Two-pass Haiku · structured context (quote + signal)",
        "run_fn":      _two_pass_haiku_structured_context,
    },
]
