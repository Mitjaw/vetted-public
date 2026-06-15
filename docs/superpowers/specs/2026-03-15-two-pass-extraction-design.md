# Two-Pass Transcript Extraction Pipeline

**Date:** 2026-03-15
**Status:** Approved
**File affected:** `brain.py` only

---

## Problem

The current single-pass extraction in `brain.py` has two systematic failure modes:

1. **Missing stocks** — Haiku misses mentions even in videos titled "My 5 Stock Picks for January", because it is simultaneously discovering investment vehicles AND analysing sentiment in one prompt. The cognitive load causes recall failures.
2. **Over-classification of neutral** — Sentiment defaults to neutral too often, even when the host's language is clearly bullish or bearish.

**Root cause:** One call is doing too much. Separating discovery from analysis fixes both.

---

## Note on Model and Transcript

`brain.py` already uses `claude-haiku-4-5-20251001` via the Anthropic SDK. Both passes continue using Claude Haiku — no model change is required.

The transcript is fetched once by `scanner.py` and passed as a string to `analyze_transcript`. Both internal passes receive that same in-memory string. No additional YouTube API calls occur.

Transcripts are already persisted to the `videos` table by `scanner.py` — no schema change is needed.

---

## Solution: Two-Pass Pipeline

### Pass 1 — Discovery

**Job:** Find every investment vehicle mentioned in the transcript — stocks, ETFs, crypto, commodities. Cast a wide net. No sentiment, no judgement.

**System prompt personality:** Specialist financial transcript scanner with deep experience reading unpunctuated auto-generated YouTube transcripts in German and English.

**Language hint:** The `language` parameter (already in `analyze_transcript`'s signature: `analyze_transcript(transcript, title="", language="en")`) injects `"Language: German / English"` into the user message. The video `title` is also injected as a count anchor hint.

**Key instructions:**
- Fix transcription errors ("in Vidia" = NVIDIA, "A MD" = AMD)
- Include all investment vehicles mentioned even briefly — stocks, ETFs, crypto, commodities
- Prefer US ADR ticker where one exists; **otherwise use local format** (SAP.DE, P911.DE, BMW.DE)
- Use title as a count anchor ("5 picks" → expect 5)
- Return entries in the order they first appear in the transcript
- Do not invent tickers not present in the transcript

**Output schema:**
```json
{
  "stocks": [
    { "ticker": "AAPL", "company_name": "Apple Inc." },
    { "ticker": "SAP.DE", "company_name": "SAP SE" },
    { "ticker": "BTC", "company_name": "Bitcoin" }
  ]
}
```

**Token budget:** `max_tokens=4096`. Conservative cap for output predictability; actual output is compact (~450 tokens at the 30-entry ceiling).

**Hallucination guard:** Constant `_MAX_DISCOVERED_STOCKS = 30` (module level in `brain.py`). If Pass 1 returns more than 30 entries, log `[brain] Discovery: truncated to 30 (was N)` and truncate to the first 30 entries in the order returned. The post-truncation list is sent to Pass 2.

---

### Pass 2 — Analysis

**Job:** For each confirmed investment vehicle from Pass 1, extract full sentiment and metadata. Focus on quality, not discovery.

**System prompt personality:** Sharp-tongued senior financial analyst expert at cutting through vague YouTuber commentary to identify the real signal — knows the difference between genuine conviction and performative neutrality.

**Note on sentiment calibration:** The "lean toward bullish/bearish" instruction is an intentional override of the conservative default in CLAUDE.md. Pass 2 applies this only when a directional signal is actually present in the transcript.

**Language hint:** Same as Pass 1. The video `title` is also injected for reference context.

**Pass 2 input format:**
```
Analyze exactly 3 investment vehicles:
- AAPL (Apple Inc.)
- SAP.DE (SAP SE)
- BTC (Bitcoin)
```

**Transcript:** The full transcript (up to 80,000 chars) is passed again. This is intentional — Pass 2 needs full context for accurate sentiment, confidence, and context quotes.

**Key instructions:**
- Return exactly one mention object per stock in the list — do not skip any, do not add new ones
- Use the exact ticker string as provided in the input list
- If a stock cannot be located in the transcript: `is_real_stock_mention: false`, `confidence: 0.0`, `mention_count: 0`, note in `context`
- `is_real_stock_mention`: false only for pure product/brand mentions or stocks that could not be found
- `sentiment`: lean toward bullish/bearish when directional signal is present
- `confidence`: clarity of sentiment expression (not certainty about the stock's prospects)
- `recommendation`: buy / sell / hold / reference
- `mention_count`: count of times the stock appears in the transcript (0 if not found)
- `context`: 1–2 sentences capturing the host's actual thesis — quote directly where possible

**Output schema:**
```json
{
  "mentions": [
    {
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "is_real_stock_mention": true,
      "sentiment": "bullish",
      "confidence": 0.82,
      "recommendation": "buy",
      "mention_count": 4,
      "context": "Host calls Apple his highest-conviction pick for H2, citing services margin expansion."
    }
  ]
}
```

**Token budget:** `max_tokens=8192`

---

## Architecture

### `brain.py` — 3 functions

```
_discovery_pass(transcript, title, language)
  → list[{ticker: str, company_name: str}]
  → internally catches json.JSONDecodeError and anthropic.APIError → returns []
  → all other exceptions propagate to analyze_transcript

_analysis_pass(transcript, title, language, discovered_stocks)
  → list[mention dict]  (unfiltered — includes is_real_stock_mention=false items)
  → internally catches json.JSONDecodeError and anthropic.APIError → returns []
  → all other exceptions propagate to analyze_transcript

analyze_transcript(transcript, title, language)   ← public interface, unchanged signature
  Outer try/except catches only exceptions not consumed by the internal handlers
  (e.g. AttributeError, KeyError, TypeError from unexpected response structure).
  json.JSONDecodeError and anthropic.APIError are consumed internally and never
  reach the outer handler — the two categories are mutually exclusive.
  On outer exception → log error → run single-pass fallback → return its result,
  or [] if the fallback itself raises.

  Flow:
  1. call _discovery_pass
  2. if result is empty → log "[brain] Discovery: 0 stocks found" → return []
  3. [if hallucination guard fired, already logged "[brain] Discovery: truncated to 30 (was N)"]
  4. call _analysis_pass with post-truncation stock list
  5. if _analysis_pass returned [] →
       log "[brain] Analysis: 0 mentions returned" → return []
  6. filter mentions: keep only items whose ticker matches a ticker in discovered_stocks
     (case-insensitive; ticker casing and company_name in the returned mention are taken
     from the Pass 2 response — Pass 2 is instructed to use the exact ticker string
     provided, so casing should be correct; the filter is a safety net for unexpected
     model behaviour, not a normalisation step)
  7. if filtered list count < discovered_stocks count →
       log "[brain] Analysis: N stock(s) missing from response (expected M)"
  8. split filtered list:
       real = items with is_real_stock_mention=true
       K    = count with is_real_stock_mention=false
     if real is empty →
       log "[brain] Analysis: all N mentions filtered (is_real_stock_mention=false)"
     else →
       log "[brain] Discovery: D stock(s) found | Analysis: M mentions, K filtered"
       (D = post-truncation count)
  9. return real  ← scanner.py deduplication only ever sees this filtered-real list
```

### Error handling

| Error type | Caught where | Behaviour |
|---|---|---|
| `json.JSONDecodeError` in Pass 1 | `_discovery_pass` | Returns `[]`; step 2 exits with `[]` |
| `json.JSONDecodeError` in Pass 2 | `_analysis_pass` | Returns `[]`; step 5 exits with `[]` |
| `anthropic.APIError` in either pass | Inside that pass | Same as JSON parse error above |
| Any other exception | `analyze_transcript` outer handler | Fallback to single-pass; return `[]` if fallback also raises |

**Fallback:** Re-runs the existing single-pass prompt from scratch. Pass 1 results are discarded.

### Deduplication

`analyze_transcript` returns only the `real` list (step 9). `_deduplicate_mentions` in `scanner.py` never sees `is_real_stock_mention=false` entries. No change to `scanner.py` needed.

---

## Observability

| Situation | Log message |
|---|---|
| Normal | `[brain] Discovery: D stock(s) found \| Analysis: M mentions, K filtered` |
| Pass 1 empty | `[brain] Discovery: 0 stocks found` |
| Pass 2 empty | `[brain] Analysis: 0 mentions returned` |
| All filtered | `[brain] Analysis: all N mentions filtered (is_real_stock_mention=false)` |
| Hallucination guard | `[brain] Discovery: truncated to 30 (was N)` — emitted during step 3, before Pass 2 |
| Missing from Pass 2 | `[brain] Analysis: N stock(s) missing from response (expected M)` — emitted at step 7, before the normal log |

K = count of `is_real_stock_mention=false` items after the ticker filter.

---

## What Does Not Change

- `scanner.py`, `main.py`, DB schema, `_deduplicate_mentions` — zero changes
- `analyze_transcript` public signature — unchanged

---

## Cost Impact

| | Before | After |
|---|---|---|
| API calls per video | 1 | 2 |
| Pass 1 cost | — | ~$0.0003 |
| Pass 2 cost | ~$0.001 | ~$0.001 |
| Total per video | ~$0.001 | ~$0.0013 |

~30% cost increase per video in exchange for significantly higher recall and sentiment accuracy.
