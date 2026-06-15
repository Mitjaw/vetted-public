import os
import logging
import anthropic
from dotenv import load_dotenv
load_dotenv()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

import json

_log = logging.getLogger(__name__)
_MAX_DISCOVERED_STOCKS = 30

ACTIVE_CONFIG_NAME = os.getenv("VETTED_EXTRACTION_CONFIG", "three_pass_haiku_v2")

_LANG_MAP = {"en": "English", "de": "German", "es": "Spanish"}

# Pricing in USD per million tokens (input, output).
# Keys are model name prefixes — matched longest-first so versioned suffixes
# (e.g. claude-opus-4-6-20251101) still resolve correctly.
_PRICING = {
    "claude-haiku-4-5-20251001": (0.80,  4.00),
    "claude-haiku-4-5":          (1.00,  5.00),
    "claude-sonnet-4-6":         (3.00, 15.00),
    "claude-opus-4-6":           (5.00, 25.00),
    # Gemini 2.5 Flash (non-thinking mode) — verify current rates at ai.google.dev
    "gemini-2.5-flash":          (0.15,  0.60),
    "gemini-2.0-flash":          (0.10,  0.40),
    "gemini-1.5-flash":          (0.075, 0.30),
}

def _calc_cost(model, input_tokens, output_tokens):
    # Match by longest prefix so versioned model strings resolve to the right entry
    matched = next(
        (v for k, v in sorted(_PRICING.items(), key=lambda x: -len(x[0])) if model.startswith(k)),
        (3.00, 15.00),  # fallback: Sonnet pricing
    )
    price_in, price_out = matched
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


def _strip_markdown(text):
    """Strip ```json / ``` fences, find the opening brace, and trim any trailing
    content after the JSON object ends.  Gemini models with thinking enabled
    sometimes append commentary after the closing brace — raw_decode stops at
    the first complete object so trailing text is silently ignored.
    Returns a clean JSON string for compatibility with callers using json.loads."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
    # Re-serialise through raw_decode so trailing junk is stripped
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
        return json.dumps(obj)
    except Exception:
        return text


def _discovery_pass(transcript, title, language):
    """
    Pass 1 — Discovery: find every investment vehicle in the transcript.
    Returns list[{ticker, company_name}], or [] on json/API error.
    Other exceptions propagate to analyze_transcript.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = (
        f"\nVideo title: {title}\n"
        "Use the title as a count anchor — if it says '5 picks' or 'top buys' "
        "(or German/Spanish equivalent), expect to find that many.\n"
    ) if title else ""

    user_message = f"""Language: {lang_label}
{title_hint}
Transcript:
{transcript}

Return ONLY valid JSON. No preamble, no markdown.

Required output format:
{{"stocks": [{{"ticker": "AAPL", "company_name": "Apple Inc.", "asset_type": "stock"}}, {{"ticker": "GLD", "company_name": "NULL", "asset_type": "commodity"}}]}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            temperature=1,
            system=(
                "You are a specialist financial transcript scanner with deep experience reading "
                "unpunctuated auto-generated YouTube transcripts in German and English. "
                "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, and commodities. "
                "Cast a wide net. No sentiment, no judgement — discovery only. "
                "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
                "Prefer US ADR ticker where one exists; otherwise use the exchange-suffix format: "
                "XETRA/Germany → .DE (SAP.DE, P911.DE, BMW.DE, XDWD.DE), "
                "London → .L (HSBA.L), Paris → .PA (AIR.PA), Amsterdam → .AS (ASML.AS), "
                "Singapore → .SI (D05.SI). Never output bare ETF brand names like 'XTRACKERS' — "
                "always look up the actual exchange ticker. "
                "Do not prefix tickers with '$' — strip it if present in the transcript. "
                "For commodities use the standard ETF proxy ticker: "
                "gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
                "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
                "For commodities and crypto: company_name must be the literal string 'NULL'. "
                "asset_type: 'stock', 'etf', 'crypto', or 'commodity'. "
                "Return entries in the order they first appear in the transcript. "
                "Do not invent tickers not present in the transcript."
            ),
            messages=[{"role": "user", "content": user_message}],
        )
        u = response.usage
        _log.info("[tokens] discovery: in=%d out=%d", u.input_tokens, u.output_tokens)
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cost_usd": _calc_cost("claude-haiku-4-5-20251001", u.input_tokens, u.output_tokens)}
        raw = _strip_markdown(response.content[0].text)
        return json.loads(raw).get("stocks", []), usage
    except (json.JSONDecodeError, anthropic.APIError) as e:
        _log.warning("_discovery_pass failed: %s", e)
        return [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}



def _analysis_pass(transcript, title, language, discovered_stocks):
    """
    Pass 2 — Analysis: for each stock from Pass 1, extract sentiment and metadata.
    Returns list of mention dicts, or [] on json/API error.
    Other exceptions propagate to analyze_transcript.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""

    n = len(discovered_stocks)
    stock_list = "\n".join(
        f"- {s['ticker']} ({s['company_name']})" for s in discovered_stocks
    )

    user_message = f"""Language: {lang_label}
{title_hint}
Analyze exactly {n} investment vehicle{"s" if n != 1 else ""}:
{stock_list}

Transcript:
{transcript}

Return ONLY valid JSON. No preamble, no markdown.

Required output format:
{{
  "mentions": [
    {{
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "asset_type": "stock",
      "is_real_stock_mention": true,
      "sentiment": "bullish",
      "confidence": 0.82,
      "recommendation": "buy",
      "mention_count": 4,
      "context": "apple is my highest conviction pick for h2 the services margin expansion really justifies the valuation here"
    }},
    {{
      "ticker": "GLD",
      "company_name": "NULL",
      "asset_type": "commodity",
      "is_real_stock_mention": true,
      "sentiment": "bullish",
      "confidence": 0.75,
      "recommendation": "watch",
      "mention_count": 2,
      "context": "i like gold as an inflation hedge but i want to wait for a pullback before i enter"
    }},
    {{
      "ticker": "TSLA",
      "company_name": "Tesla Inc.",
      "asset_type": "stock",
      "is_real_stock_mention": false,
      "sentiment": "neutral",
      "confidence": 0.0,
      "recommendation": "reference",
      "mention_count": 0,
      "context": "Not found in transcript."
    }}
  ]
}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            temperature=0.7,
            system=(
                "You are a sharp-tongued senior financial analyst expert at cutting through vague YouTuber commentary to identify the real signal. "
                "You know the difference between genuine conviction and performative neutrality. "
                "For each investment vehicle in the provided list: return exactly one mention object. "
                "Do not skip any. Do not add tickers beyond those listed. "
                "Use the exact ticker string as provided. "
                "If a vehicle cannot be found in the transcript: is_real_stock_mention=false, confidence=0.0, mention_count=0, context='Not found in transcript.' "
                "For commodities (gold=GLD, silver=SLV, oil=USO, etc.) and crypto: company_name must be the literal string 'NULL' — do not invent a company name. "
                "Sentiment: lean toward bullish/bearish when any directional signal is present — "
                "reserve neutral for genuinely balanced or purely informational mentions. "
                "Confidence reflects clarity of sentiment expression, not certainty about the asset's prospects. "
                "context: copy the most relevant verbatim sentence(s) from the transcript where this stock is discussed — do not paraphrase or summarise. "
                "Preserve original wording including lack of punctuation. Max ~300 chars. If multiple strong quotes exist, pick the one that best explains the sentiment."
            ),
            messages=[{"role": "user", "content": user_message}],
        )
        u = response.usage
        _log.info("[tokens] analysis: in=%d out=%d", u.input_tokens, u.output_tokens)
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cost_usd": _calc_cost("claude-haiku-4-5-20251001", u.input_tokens, u.output_tokens)}
        raw = _strip_markdown(response.content[0].text)
        return json.loads(raw).get("mentions", []), usage
    except (json.JSONDecodeError, anthropic.APIError) as e:
        _log.warning("_analysis_pass failed: %s", e)
        return [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def _verification_pass(transcript, title, language, mentions):
    """
    Pass 3 — Verification: fact-check extracted mentions against the transcript.
    Fixes errors in sentiment/confidence/recommendation, removes hallucinations.
    Returns corrected list of mention dicts, or the original list on error.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""
    n = len(mentions)
    mentions_json = json.dumps(mentions, indent=2)

    user_message = f"""Language: {lang_label}
{title_hint}
Below are {n} stock mention(s) extracted from a transcript. Review each one against the transcript and correct any errors in sentiment, confidence, recommendation, or context.

Extracted mentions to verify:
{mentions_json}

Transcript:
{transcript}

Return ONLY valid JSON. No preamble, no markdown.

{{"mentions": [{{"ticker": "AAPL", "company_name": "Apple Inc.", "is_real_stock_mention": true, "sentiment": "bullish", "confidence": 0.82, "recommendation": "buy", "mention_count": 4, "context": "..."}}]}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            temperature=0.5,
            system=(
                "You are a meticulous fact-checker reviewing extracted stock mentions from a YouTube transcript. "
                "Your job: verify each mention is accurate, fix any errors in sentiment/confidence/recommendation, "
                "and remove any hallucinated stocks. "
                "Be conservative — when in doubt, keep the original. "
                "Return the corrected list using exactly the same JSON format. "
                "Do not add new tickers. Do not remove tickers unless they are clearly wrong. "
                "Change tickers and the correlating company, when the context clearly suggests another company "
                "with a similar name or ticker. "
                "context must be a verbatim quote from the transcript — do not paraphrase. "
                "If the existing context is already a verbatim quote, keep it. Only replace it if it is clearly a paraphrase."
            ),
            messages=[{"role": "user", "content": user_message}],
        )
        u = response.usage
        _log.info("[tokens] verification: in=%d out=%d", u.input_tokens, u.output_tokens)
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cost_usd": _calc_cost("claude-haiku-4-5-20251001", u.input_tokens, u.output_tokens)}
        raw = _strip_markdown(response.content[0].text)
        return json.loads(raw).get("mentions", mentions), usage
    except (json.JSONDecodeError, anthropic.APIError) as e:
        _log.warning("_verification_pass failed (%s) — keeping Pass 2 output", e)
        return mentions, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def _single_pass(transcript, title, language):
    """Original single-pass extraction — used as fallback by analyze_transcript."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = (
        f"\nVideo title: {title}\n"
        "Use the title as a strong hint — if it says '5 buys' or 'top picks' "
        "(or Spanish/German equivalent), make sure to find all of them.\n"
    ) if title else ""

    prompt = f"""You are analyzing a finance YouTube video transcript. Language: {lang_label}.
{title_hint}
IMPORTANT: Be THOROUGH. Find EVERY investment vehicle discussed — stocks, ETFs, crypto, and commodities.

Rules:
1. Extract ALL stocks, ETFs, crypto, or commodities mentioned as investments
2. Ignore only truly non-investment mentions: "I bought an Apple" (food), "I use Google every day" (product usage)
3. Fix transcription errors: "in Vidia" = NVIDIA, "A MD" = AMD, "Novo" alone in context = NVO
4. For commodities use ETF proxy tickers: gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. Do NOT use XAUUSD or GC=F.
5. For commodities and crypto: company_name must be the literal string "NULL" — do not invent a company name.
6. asset_type: "stock" / "etf" / "crypto" / "commodity"
7. For sentiment: ambiguous or mildly positive = neutral + low confidence.
8. For non-US companies use US ADR where available, otherwise exchange-suffix format: .DE (XETRA), .L (London), .PA (Paris), .AS (Amsterdam), .SI (Singapore). Never output bare ETF brand names — use the actual exchange ticker. Never prefix tickers with '$'.
9. Recommendation must be one of: buy / sell / hold / watch / reference.
    "watch" = bullish interest but creator is explicitly waiting (for earnings, a dip, a catalyst).
    "hold" = creator already owns it and is keeping it. "reference" = passing mention, no investment intent.
10. Return ONLY valid JSON. No preamble, no explanation, no markdown.

Transcript:
{transcript}

Required output format:
{{
  "mentions": [
    {{
      "ticker": "AAPL",
      "company_name": "Apple",
      "asset_type": "stock",
      "mention_count": 3,
      "sentiment": "bullish",
      "confidence": 0.85,
      "recommendation": "buy",
      "context": "apple is my top pick for q2 the valuation really makes sense here"
    }},
    {{
      "ticker": "GLD",
      "company_name": "NULL",
      "asset_type": "commodity",
      "mention_count": 2,
      "sentiment": "bullish",
      "confidence": 0.8,
      "recommendation": "watch",
      "context": "i like gold as an inflation hedge but i want to wait for a pullback before i enter"
    }}
  ]
}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    u = response.usage
    _log.info("[tokens] single-pass: in=%d out=%d", u.input_tokens, u.output_tokens)
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "cost_usd": _calc_cost("claude-haiku-4-5-20251001", u.input_tokens, u.output_tokens)}
    raw = _strip_markdown(response.content[0].text)
    _log.info("single-pass raw response (first 300): %s", raw[:300])
    parsed = json.loads(raw)
    mentions = parsed.get("mentions", [])
    return mentions, usage


def analyze_transcript(transcript, title="", language="en"):
    _zero_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def _add_usage(a, b):
        return {
            "input_tokens":  a["input_tokens"]  + b["input_tokens"],
            "output_tokens": a["output_tokens"] + b["output_tokens"],
            "cost_usd":      round(a["cost_usd"] + b["cost_usd"], 6),
        }

    try:
        # Pass 1 — Discovery
        discovered, usage1 = _discovery_pass(transcript, title, language)

        if not discovered:
            _log.info("[brain] Discovery: 0 stocks found")
            return [], usage1

        if len(discovered) > _MAX_DISCOVERED_STOCKS:
            _log.warning(
                "[brain] Discovery: truncated to %d (was %d)",
                _MAX_DISCOVERED_STOCKS, len(discovered),
            )
            discovered = discovered[:_MAX_DISCOVERED_STOCKS]

        _log.info("[brain] Discovery: %d stock(s) found", len(discovered))

        # Pass 2 — Analysis
        mentions, usage2 = _analysis_pass(transcript, title, language, discovered)
        total_usage = _add_usage(usage1, usage2)

        if not mentions:
            _log.info("[brain] Analysis: 0 mentions returned")
            return [], total_usage

        # Filter to only tickers that were in the discovered list
        discovered_tickers = {s["ticker"].upper() for s in discovered}
        filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]

        if len(filtered) < len(discovered):
            _log.warning(
                "[brain] Analysis: %d stock(s) missing from response (expected %d)",
                len(discovered) - len(filtered), len(discovered),
            )

        # Pass 3 — Verification: fact-checks mentions, removes hallucinations, corrects wrong tickers
        verified, usage3 = _verification_pass(transcript, title, language, filtered)
        total_usage = _add_usage(total_usage, usage3)

        _log.info("[brain] Analysis complete: %d final mentions", len(verified))
        return verified, total_usage

    except Exception as e:
        _log.error("analyze_transcript three-pass failed (%s) — falling back to single-pass", e)
        try:
            return _single_pass(transcript, title, language)
        except Exception as e2:
            _log.error("single-pass fallback also failed: %s", e2)
            return [], _zero_usage
