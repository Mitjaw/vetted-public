# Commodity / Resource Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make gold, silver, and other commodity resources discoverable, correctly stored, and distinguishable from stocks in the DB.

**Architecture:** Three-layer change — prompts, schema, plumbing. The prompts get a commodity ticker convention table and explicit instruction to leave `company_name` blank. A new `asset_type` column (`stock` / `etf` / `crypto` / `commodity`) is added to `mentions` via the existing migration pattern. `save_mention()` gains an `asset_type` param and `scanner.py` passes it through. No change to price fetching — we standardise on ETF proxies (`GLD`, `SLV`, `USO`) which Tiingo/yfinance already handle. The `custom_store.py` default prompts are updated in parallel so user-created configs inherit the same conventions.

**Tech Stack:** Python 3.11, SQLite, Anthropic Claude (brain.py)

---

## File Map

| File | Change |
|------|--------|
| `brain.py` | Pass 1 system: add commodity ticker table + `company_name = ""` rule; Pass 1/2/single output schema: add `asset_type` field; Pass 2 system: mention commodities explicitly |
| `db_manager.py` | Migration: `ALTER TABLE mentions ADD COLUMN asset_type TEXT DEFAULT 'stock'`; `save_mention()` gains `asset_type` param |
| `scanner.py` | Pass `asset_type` from mention dict to `save_mention()` |
| `evals/custom_store.py` | Update `DEFAULT_PASS1_SYSTEM`, `DEFAULT_PASS2_SYSTEM`, `DEFAULT_SINGLE_SYSTEM/USER` to match new brain.py prompts |

**Not changed:** `market_data.py` (GLD/SLV are normal ETF tickers, already priced fine), `templates/` (no UI changes needed now), `evals/configs.py` (built-in eval configs call `brain.analyze_transcript()` which already picks up the updated prompts).

---

## Commodity ticker conventions

These are the standard ETF proxies to use — they have normal exchange tickers and are priced by Tiingo/yfinance:

| Resource | Ticker | Note |
|----------|--------|------|
| Gold | `GLD` | SPDR Gold Shares |
| Silver | `SLV` | iShares Silver Trust |
| Oil (WTI) | `USO` | US Oil Fund |
| Natural Gas | `UNG` | US Natural Gas Fund |
| Copper | `CPER` | US Copper Index Fund |
| Bitcoin | `BTC-USD` | already handled as crypto |
| Ethereum | `ETH-USD` | already handled as crypto |

Raw spot/futures tickers (`XAUUSD`, `GC=F`, `XAU`) must **not** be used — they don't resolve in Tiingo.

---

## Task 1: Update `brain.py` prompts

**Files:**
- Modify: `brain.py`

### What to know
Three prompt locations:
1. `_discovery_pass()` — system prompt (line ~65) + output schema in `user_message` (line ~46)
2. `_analysis_pass()` — system prompt (line ~129) + output schema in `user_message` (line ~99)
3. `_single_pass()` — inline prompt (line ~161)

The output schema currently has no `asset_type`. We add it to all three. The AI must also know:
- Commodities use ETF proxy tickers (see table above)
- `company_name` must be `""` for commodities — do not invent a company
- `asset_type` is one of: `"stock"`, `"etf"`, `"crypto"`, `"commodity"`

- [ ] **Step 1: Update `_discovery_pass()` system prompt**

Replace the existing system string passed to `client.messages.create()`:

```python
            system=(
                "You are a specialist financial transcript scanner with deep experience reading "
                "unpunctuated auto-generated YouTube transcripts in German and English. "
                "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, and commodities. "
                "Cast a wide net. No sentiment, no judgement — discovery only. "
                "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
                "Prefer US ADR ticker where one exists; otherwise use local format (SAP.DE, P911.DE, BMW.DE). "
                "For commodities use the standard ETF proxy ticker: "
                "gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
                "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
                "Return entries in the order they first appear in the transcript. "
                "Do not invent tickers not present in the transcript."
            ),
```

- [ ] **Step 2: Update `_discovery_pass()` output schema**

In the `user_message` f-string, replace the `Required output format` block:

```python
Required output format:
{{
  "stocks": [
    {{"ticker": "AAPL",  "company_name": "Apple Inc.", "asset_type": "stock"}},
    {{"ticker": "GLD",   "company_name": "NULL",           "asset_type": "commodity"}},
    {{"ticker": "BTC-USD","company_name": "NULL",          "asset_type": "crypto"}}
  ]
}}"""
```

- [ ] **Step 3: Update `_analysis_pass()` system prompt**

Replace the existing system string:

```python
            system=(
                "You are a sharp-tongued senior financial analyst expert at cutting through vague "
                "YouTuber commentary to identify the real signal. You know the difference between "
                "genuine conviction and performative neutrality. "
                "For each investment vehicle in the provided list: return exactly one mention object. "
                "Do not skip any. Do not add tickers beyond those listed. "
                "Use the exact ticker string as provided. "
                "If a vehicle cannot be found in the transcript: "
                "is_real_stock_mention=false, confidence=0.0, mention_count=0, explain in context. "
                "For commodities (gold=GLD, silver=SLV, oil=USO, etc.): "
                "company_name must be the literal string "NULL" — do not invent a company name. "
                "Sentiment: lean toward bullish/bearish when any directional signal is present — "
                "reserve neutral for genuinely balanced or purely informational mentions. "
                "Confidence reflects clarity of sentiment expression, not certainty about the asset's prospects."
            ),
```

- [ ] **Step 4: Update `_analysis_pass()` output schema**

In the `user_message` f-string, replace the `Required output format` block:

```python
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
      "context": "Host calls Apple his highest-conviction pick for H2."
    }},
    {{
      "ticker": "GLD",
      "company_name": "NULL",
      "asset_type": "commodity",
      "is_real_stock_mention": true,
      "sentiment": "bullish",
      "confidence": 0.75,
      "recommendation": "buy",
      "mention_count": 2,
      "context": "Host recommends gold as inflation hedge."
    }}
  ]
}}"""
```

- [ ] **Step 5: Update `_single_pass()` prompt**

Replace the `Rules:` block and output format:

```python
    prompt = f"""You are analyzing a finance YouTube video transcript. Language: {lang_label}.
{title_hint}
IMPORTANT: Be THOROUGH. Find EVERY investment vehicle discussed — stocks, ETFs, crypto, and commodities.

Rules:
1. Extract ALL stocks, ETFs, crypto, or commodities mentioned as investments
2. Ignore only truly non-investment mentions: "I bought an Apple" (food), "I use Google every day" (product usage)
3. Fix transcription errors: "in Vidia" = NVIDIA, "A MD" = AMD, "Novo" alone in context = NVO
4. For commodities use ETF proxy tickers: gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. Do NOT use XAUUSD or GC=F.
5. For commodities: company_name must be "" — do not invent a company name.
6. asset_type: "stock" / "etf" / "crypto" / "commodity"
7. For sentiment: ambiguous or mildly positive = neutral + low confidence.
8. Sarcasm or hypotheticals = is_real_stock_mention: false
9. For non-US companies use US ADR where available, otherwise local ticker (P911.DE, SAP.DE)
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
      "context": "Apple is my top pick for Q2.",
      "is_real_stock_mention": true
    }},
    {{
      "ticker": "GLD",
      "company_name": "NULL",
      "asset_type": "commodity",
      "mention_count": 2,
      "sentiment": "bullish",
      "confidence": 0.8,
      "recommendation": "buy",
      "context": "Host recommends gold as inflation hedge.",
      "is_real_stock_mention": true
    }}
  ]
}}"""
```

---

## Task 2: DB migration + update `save_mention()`

**Files:**
- Modify: `db_manager.py`

### What to know
New column: `asset_type TEXT DEFAULT 'stock'`. Existing rows default to `'stock'` — no backfill needed.

`save_mention()` currently takes 10 params. We add `asset_type` as an optional keyword arg with default `"stock"` so nothing else breaks before we update the callers.

- [ ] **Step 1: Add migration in `init_db()`**

Find the block that migrates `channels` columns (around line 148). Add immediately after it:

```python
# Migrate: asset_type on mentions
try:
    cursor.execute("ALTER TABLE mentions ADD COLUMN asset_type TEXT DEFAULT 'stock'")
    conn.commit()
except sqlite3.OperationalError:
    pass  # column already exists
```

- [ ] **Step 2: Update `save_mention()` signature and INSERT**

Replace the function (lines ~381–403):

```python
def save_mention(video_id, ticker, company_name, mention_count, sentiment,
                 confidence, recommendation, context, is_real_stock_mention,
                 upload_date=None, asset_type="stock"):
    """Insert a mention row. Returns the new mention id."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mentions (
            video_id, ticker, company_name, mention_count, sentiment,
            confidence, recommendation, context, is_real_stock_mention,
            upload_date, asset_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            video_id, ticker, company_name if company_name is not None else "NULL", mention_count, sentiment,
            confidence, recommendation, context,
            1 if is_real_stock_mention else 0,
            upload_date, asset_type or "stock",
        ),
    )
    mention_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return mention_id
```

Note the `company_name if company_name is not None else "NULL"` guard — ensures NULL/None from the AI becomes an empty string, not a DB NULL.

- [ ] **Step 3: Verify column exists**

Start the server (which runs `init_db()`) then:

```bash
sqlite3 vetted.db "PRAGMA table_info(mentions);"
```

Expected: a row for `asset_type` with `dflt_value='stock'`.

---

## Task 3: Pass `asset_type` through `scanner.py`

**Files:**
- Modify: `scanner.py` (lines ~287–299)

### What to know
`mention` is a dict returned by `brain.analyze_transcript()`. After the prompt changes in Task 1, it will include `asset_type`. We just need to pass it through to `save_mention()`. Use `.get("asset_type", "stock")` as the default in case an older cached result or a model that ignores the new field returns no `asset_type`.

- [ ] **Step 1: Add `asset_type` to the `save_mention()` call**

Find the `db_manager.save_mention(...)` call in `reanalyze_stored_transcripts()` and add the new kwarg:

```python
            mention_id = db_manager.save_mention(
                video_id=video_id,
                ticker=mention["ticker"],
                company_name=mention.get("company_name", "NULL"),
                mention_count=mention["mention_count"],
                sentiment=mention["sentiment"],
                confidence=mention["confidence"],
                recommendation=mention.get("recommendation", "reference"),
                context=mention.get("context", ""),
                is_real_stock_mention=mention.get("is_real_stock_mention", True),
                upload_date=upload_date,
                asset_type=mention.get("asset_type", "stock"),
            )
```

Note: also changed `mention["company_name"]` → `mention.get("company_name", "NULL")` to handle commodities that return an empty string or missing key.

---

## Task 4: Update `custom_store.py` default prompts

**Files:**
- Modify: `evals/custom_store.py`

### What to know
`custom_store.py` holds the default system/user prompts that pre-fill the Create Config form. They must stay in sync with `brain.py` so user-created eval configs test the same conventions. Update `DEFAULT_PASS1_SYSTEM`, `DEFAULT_PASS2_SYSTEM`, and `DEFAULT_SINGLE_SYSTEM` / `DEFAULT_SINGLE_USER` to match what was written in Task 1.

- [ ] **Step 1: Update `DEFAULT_PASS1_SYSTEM`**

```python
DEFAULT_PASS1_SYSTEM = (
    "You are a specialist financial transcript scanner with deep experience reading "
    "unpunctuated auto-generated YouTube transcripts in German and English. "
    "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, and commodities. "
    "Cast a wide net. No sentiment, no judgement — discovery only. "
    "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
    "Prefer US ADR ticker where one exists; otherwise use local format (SAP.DE, P911.DE, BMW.DE). "
    "For commodities use the standard ETF proxy ticker: "
    "gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
    "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
    "Return entries in the order they first appear in the transcript. "
    "Do not invent tickers not present in the transcript."
)
```

- [ ] **Step 2: Update `DEFAULT_PASS1_USER`**

Add `asset_type` to the output format example:

```python
DEFAULT_PASS1_USER = (
    "Language: {language}\n{title_hint}\nTranscript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    'Required output format:\n{{"stocks": [{{"ticker": "AAPL", "company_name": "Apple Inc.", "asset_type": "stock"}}, '
    '{{"ticker": "GLD", "company_name": "NULL", "asset_type": "commodity"}}]}}'
)
```

- [ ] **Step 3: Update `DEFAULT_PASS2_SYSTEM`**

```python
DEFAULT_PASS2_SYSTEM = (
    "You are a sharp-tongued senior financial analyst expert at cutting through vague "
    "YouTuber commentary to identify the real signal. You know the difference between "
    "genuine conviction and performative neutrality. "
    "For each investment vehicle in the provided list: return exactly one mention object. "
    "Do not skip any. Do not add tickers beyond those listed. "
    "Use the exact ticker string as provided. "
    "If a vehicle cannot be found in the transcript: "
    "is_real_stock_mention=false, confidence=0.0, mention_count=0, explain in context. "
    "For commodities (gold=GLD, silver=SLV, oil=USO, etc.): "
    "company_name must be the literal string "NULL" — do not invent a company name. "
    "Sentiment: lean toward bullish/bearish when any directional signal is present — "
    "reserve neutral for genuinely balanced or purely informational mentions. "
    "Confidence reflects clarity of sentiment expression, not certainty about the asset's prospects."
)
```

- [ ] **Step 4: Update `DEFAULT_PASS2_USER`**

Add `asset_type` to the output format example:

```python
DEFAULT_PASS2_USER = (
    "Language: {language}\n{title_hint}\n"
    "Analyze exactly {n} investment vehicle{plural}:\n{stock_list}\n\n"
    "Transcript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    '{{\"mentions\": [{{\"ticker\": \"AAPL\", \"company_name\": \"Apple Inc.\", \"asset_type\": \"stock\", '
    '\"is_real_stock_mention\": true, \"sentiment\": \"bullish\", \"confidence\": 0.82, '
    '\"recommendation\": \"buy\", \"mention_count\": 4, \"context\": \"...\"}}]}}'
)
```

- [ ] **Step 5: Update `DEFAULT_SINGLE_SYSTEM` and `DEFAULT_SINGLE_USER`**

Match the rules and output format from Task 1 Step 5.

---

## Quick smoke test

After all tasks, restart the server and run:

```bash
# Check column exists
sqlite3 vetted.db "PRAGMA table_info(mentions);"

# Trigger re-analysis on a video that discusses gold/silver if one exists
# Check the log for: "[brain] Discovery: X stock(s) found"
# Then verify the saved mention:
sqlite3 vetted.db "SELECT ticker, company_name, asset_type FROM mentions ORDER BY id DESC LIMIT 10;"
```

Expected for a gold mention: `GLD | (empty) | commodity`
