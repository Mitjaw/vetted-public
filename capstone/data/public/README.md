# Vetted: finance-YouTube stock calls (2024-2026)

5,461 directional stock calls extracted from 1,295 videos of 12 finance-YouTube channels,
March 2024 to March 2026. One row = one channel taking a positive ("buy") or negative
("bearish") stance on one US-listed stock in one video.

The data was produced by my own pipeline (Vetted): video transcripts are read by a language
model (Claude Haiku), which lists the tickers discussed and classifies the creator's stance.
This file is the cleaned subset used in my Google Data Analytics capstone.

**Not included, on purpose:** transcript text and market prices. To compute returns, download
prices yourself (the accompanying notebook does this with `yfinance`).

## Columns

| Column | Type | Description |
|---|---|---|
| `call_id` | int | Row id |
| `video_id` | str | Public YouTube video id (`https://youtu.be/<video_id>`) |
| `channel` | str | Channel name |
| `language` | str | Channel language (`en`, `de`) |
| `video_date` | date | Upload date of the video (no time of day available) |
| `ticker` | str | Ticker in Yahoo Finance notation (`BRK-B`) |
| `company_name` | str | Company name as written by the model (not normalised) |
| `direction` | str | `buy` or `bearish`, mapped from `llm_label` |
| `llm_label` | str | Original free-text label from the model (`buy`, `avoid`, `sell`, ...) |
| `llm_confidence` | float | Model's self-reported confidence, 0-1. Did not predict correctness in the audit |
| `mention_count` | int | How often the stock is named in the video, as counted by the model |
| `in_depth` | bool | `mention_count >= 4`. The most reliable subset |
| `is_repeat_90d` | bool | Same channel made the same call on the same stock in the previous 90 days |

## Known limitations

* **Label noise.** In a manual audit of 140 calls, 71% of `buy` labels (95% CI 61-79%) and 40%
  of `bearish` labels (26-55%) were a genuine view of the creator. The rest are historical
  examples, news items, comparisons or relayed third-party picks. `in_depth` buy calls were
  genuine in 30 of 31 audited cases.
* **Wrong tickers.** About 2% of rows point at the wrong company (a garbled name resolved to
  another valid symbol).
* **Selection.** The 12 channels were chosen by hand and are mostly English-speaking and
  growth/tech-oriented. They are not a sample of finance YouTube.
* **Repetition.** 61% of buy calls repeat an earlier call by the same channel on the same
  stock. Do not treat rows as independent observations.
* **Period.** One market regime.

## How it was cleaned

13,530 raw mentions → 5,461 calls: directional labels only, label and sentiment agree, single
stocks (no ETFs, crypto, commodities), US listings, ticker has a price history, channels with
at least 50 analysed videos, one call per channel/stock/day, tradeable on the day after the
video, entry price of at least $1. Details in the project's data journal.

## Licence and use

Derived from public videos for research and education. Channel names identify public
creators; the dataset makes no claim about any creator's skill, and the label noise above
applies to every row. Not investment advice. Suggested licence: CC BY-NC-SA 4.0.
