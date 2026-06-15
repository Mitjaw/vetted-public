# Code Quality Refactor — Design Spec

**Date:** 2026-03-14
**Project:** Vetted (private finance intelligence dashboard)
**Scope:** Moderate refactor — dead code removal, SQL extraction into db_manager, standardised error handling. No behaviour changes.

---

## 1. Dead Code & Duplicates

### Remove `extract_all_buys()` from `brain.py`
- Lines 14–64 in `brain.py`
- Legacy function never called from `scanner.py` or anywhere else in the codebase
- Safe to delete with zero impact

### Remove `_get_conn()` from `main.py`
- Lines 53–56 in `main.py`
- Identical duplicate of `_get_conn()` in `db_manager.py`
- Becomes unnecessary once all routes delegate DB work to `db_manager`

---

## 2. New db_manager Functions

Six functions to add to `db_manager.py`. Each replaces inline SQL currently living inside a route handler.

| Function | Returns | Used by |
|---|---|---|
| `get_home_stats()` | `{recent_scans: list, channels_total: int}` | `GET /` |
| `get_stock_detail(ticker, days)` | `{mentions: list, sentiment_summary: dict, roi_data: list}` | `GET /stock/{ticker}` |
| `get_channel_detail(channel_id)` | `{channel: dict, videos: list, pick_accuracy: float\|None, top_stocks: list, channel_mentions: list}` | `GET /channel/{channel_id}` |
| `get_channels_list()` | `list[dict]` — see column spec below | `GET /channels`, `GET /export`, `GET /analyst` |
| `get_stocks_list(days)` | `list[dict]` — tickers aggregated with sentiment counts | `GET /stocks` |
| `get_export_rows(ticker, channel_id, date_from, date_to, sentiment)` | `list[dict]` — raw export rows | `GET /export/csv`, `GET /export/json` |

### `get_channels_list()` column specification

The three routes currently fetch channels with different column sets and sort orders. `get_channels_list()` returns a single superset that satisfies all three:

```
id, name, url, language, added_at, last_scanned, video_count
ORDER BY language ASC, name ASC
```

- `/channels` uses: all columns + `video_count`
- `/export` uses: `id`, `name` (was previously ordered by name only — ordering by language then name is a safe, compatible change)
- `/analyst` uses: `id`, `name`, `language`

Each caller uses only the columns it needs; extra columns are silently ignored by the template.

### `get_channel_detail()` specification

Absorbs four queries from the `GET /channel/{channel_id}` route plus the separate `db_manager.get_channel_mentions()` call. Returns:

```python
{
    "channel": dict,           # channel row
    "videos": list[dict],      # videos with mention_count
    "pick_accuracy": float | None,
    "top_stocks": list[dict],
    "channel_mentions": list[dict],  # key matches template variable
}
```

`channel_mentions` query **must preserve** `WHERE m.is_real_stock_mention = 1` — this filter is present in the existing `get_channel_mentions()` function and is required for correct template rendering.

### `get_export_rows()` specification

Direct move of `_export_rows()` from `main.py` into `db_manager.py`. Same SQL, same parameters, same return type. The `try/except Exception: return []` block inside `_export_rows()` is retired along with the function deletion — it is **not** patched in the error-handling step (Step 5). The calling routes (`/export/csv`, `/export/json`) will gain standardised error handling at the route level instead.

---

## 3. Route Cleanup

After the db_manager functions exist, each route becomes a thin wrapper.

**Routes affected:** `/`, `/stock/{ticker}`, `/channel/{channel_id}`, `/channels`, `/stocks`, `/export`, `/analyst`

**Routes left untouched:** all `/api/*` endpoints, all `POST /admin/*` routes

`_export_rows()` helper is deleted from `main.py` once `get_export_rows()` exists in `db_manager.py`.
`_get_conn()` is deleted from `main.py` once all routes no longer call it.
`CSV_COLUMNS` constant stays in `main.py`.

---

## 4. Error Handling

Replace `except Exception: pass` blocks with a consistent three-step pattern:

1. **Log** — `logger.exception(...)` captures full traceback to server log
2. **Default data** — empty-but-safe fallback so templates don't crash on missing keys
3. **Flash message** — pass `msg = "Data temporarily unavailable — check server logs."` to template

`msg` is already wired into `base.html` as a flash message — no template changes required.

A module-level logger is added to `main.py`:
```python
import logging
logger = logging.getLogger(__name__)
```

**Scope of Step 5 (error handling pass):**
- 7 `except Exception: pass` blocks in route handlers
- The 8th block (inside `_export_rows()`) is retired by deletion in Step 4, not patched here
- Background task internals in `scanner.py` — excluded, already have their own error handling
- `db_manager.py` internals — excluded, errors bubble up to route handlers

---

## Implementation Order

1. Delete `extract_all_buys()` from `brain.py`
2. Add all six functions to `db_manager.py` (no route changes yet — old inline SQL still works in parallel)
3. Update routes one by one to call db_manager, verify each page in browser before moving to next
4. Delete `_export_rows()`, `_get_conn()`, and unused imports from `main.py` once all routes are migrated
5. Replace the remaining 7 `except Exception: pass` blocks in route handlers with the standardised pattern
6. Smoke-test all pages end-to-end

---

## Success Criteria

- All pages render identically to before — no visible behaviour change
- No `except: pass` remaining in any route handler in `main.py`
- No raw SQL in any route handler in `main.py` (SQL lives only in `db_manager.py` and the `_export_rows` helper which moves there)
- `brain.py` contains only `analyze_transcript()`
- `_get_conn()` removed from `main.py`
- Server log shows structured error messages when DB queries fail
- `get_channel_detail()` returns `channel_mentions` key with `is_real_stock_mention = 1` filter intact
