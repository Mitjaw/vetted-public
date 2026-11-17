# Data journal

Running log of decisions for the Vetted capstone: what was decided, why, how many rows it
touched, and what was tried and dropped. Newest phase at the bottom. Row counts are from the
final run (16 Nov 2026) and are also written to `results/cleaning_log.json`.

## Ask

| Decision | Reason |
|---|---|
| Guiding question sharpened from "how meaningful is fintuber stock advice?" to "how much signal is in it: do the calls beat the S&P 500, is the information in the call or in the stock style, do channels differ beyond chance?" | "Meaningful" cannot be measured. Excess return, timing and channel differences can. |
| Stakeholder is the Vetted product owner; viewers are secondary | The decision at stake is what the product may claim and rank. |
| Not a trading-strategy backtest | No costs, no position sizing, no claim about what anyone should buy. |

## Prepare

**What the source is.** `vetted.db` (SQLite, private): 2,548 videos, 13,530 extracted mentions
from 1,951 videos, 20 registered channels plus a tail of channels that were only scanned for a
few days in March 2026. Mentions run from 2024-03-01 to 2026-03-28; later videos exist but were
never analysed. Each mention is one ticker in one video with free-text `recommendation` (97
distinct values), `sentiment`, `confidence`, `mention_count`.

**ROCCC.** Reliable: partly (machine labels, see audit). Original: yes. Comprehensive: partly
(12 usable channels, hand-picked, one regime). Current: videos to Mar 2026, prices to
13 Nov 2026. Cited: every call maps to a public video ID.

**Problems found in the source, and what I did about them**

| Finding | Consequence |
|---|---|
| `price_daily` stops on 2026-03-27 | Re-downloaded all prices through 2026-11-13 so the last calls also get a forward window. |
| Corrupted price series (negative closes for SSNLF, all-zero for ACNV/TTCF/VGLS) and ROI rows with impossible baselines | Stored ROI tables are not used at all. Every return is recomputed from the fresh price cache. Cross-check: my 30-day returns correlate 0.91 with the production `roi_30d`; the 12 large disagreements are zeros or missing prices on the production side. |
| `is_real_stock_mention` is 1 for every row; `verified` only checks that the name appears in the transcript | Neither is used as a quality filter. In the audit, `verified=0` rows were no worse (70% genuine) than `verified=1` rows (71%); unverified mostly means auto-captions garbled the name ("Palunteer"). |
| The production app counts a buy call as "correct" when the stock went up | Not used. In a market where SPY itself returned +4.4% per 90 days this flatters everything. |

**Extraction audit (the most important Prepare step).** Seeded random sample of 140 directional
stock calls (70 buy/verified, 30 buy/unverified, 40 bearish), each re-read against transcript
windows and labelled A explicit recommendation / B clear view / T relayed third-party pick /
C not a call. Labels: `audit/extraction_audit.csv`. The labelling was done with Claude Code
(a stronger model than the extractor) and has not been independently double-coded.

* buy: 42 A, 29 B, 5 T, 24 C. Genuine positive view (A+B) 71% (95% CI 61-79%).
* bearish: 7 A, 9 B, 4 T, 20 C. Genuine negative view 40% (26-55%).
* Ticker points at the wrong company in 9 of 140. Most of those have no price series and drop
  out later; 2 of the 124 audited calls that survive cleaning are still wrong (ALF, TPR).
* Typical false positives: historical examples ("stocks like Nvidia made people rich"),
  earnings news, a competitor used as a foil, another investor's 13F.
* The project's own benchmark agrees: 40 repeated runs of the production set-up on 4
  hand-annotated videos give 83% ticker precision, 66% recall, 70% label accuracy, with recall
  on single runs ranging from 0% to 96%. Small models are still inconsistent at this task.

Decision: no second LLM pass over the data (decided with the product owner). Instead the noise
is measured, stated, and handled in three ways: (1) "buy call" is defined as "the creator spoke
positively about the stock"; (2) an `in_depth` flag marks calls where the ticker is named at
least 4 times, which were genuine in 30 of 31 audited cases, and every headline is re-run on
that subset; (3) conclusions that rest on the bearish label are marked as weak.

Caveat on (2): the threshold of 4 was chosen after looking at the audit sample, so 30/31 is an
in-sample figure and probably optimistic.

## Process

Applied in this order (`src/03_clean.py`):

| # | Filter | Removed | Left | Why |
|---|---|---:|---:|---|
| 0 | all mentions | | 13,530 | |
| 1 | directional label | 6,251 | 7,279 | `reference`, `hold`, `watch` and conditional labels (`buy_on_dip`, `avoid_at_current_price` ...) are not calls a viewer can act on at the video date. The 97 labels are mapped by an explicit list in `src/common.py`. |
| 2 | label agrees with sentiment | 75 | 7,204 | `buy` with bearish/neutral sentiment and vice versa: the model contradicts itself. |
| 3 | single stocks | 1,209 | 5,995 | ETFs, funds, crypto, commodities, indices. Asset type is taken as the majority label per ticker because the row-level label is inconsistent (SPY is "stock" in 40 rows). |
| 4 | US listing | 98 | 5,897 | Exchange-suffixed tickers trade in other currencies; comparing with SPY would add FX. |
| 5 | price series found | 276 | 5,621 | Ticker resolves nowhere: garbled or invented symbols (NVIDIA, FORTNET, PALO, LVMH). |
| 6 | core channels | 76 | 5,545 | Channels with fewer than 50 analysed videos: a March 2026 test batch and rows without channel metadata. |
| 7 | one call per channel/ticker/day | 10 | 5,535 | Two same-day videos repeating one call. |
| 8 | entry price available | 63 | 5,472 | No close within 5 days after the video: not listed yet, already delisted, or the symbol belonged to another company then. |
| 9 | entry price at least $1 | 11 | 5,461 | Added after an outlier check, see below. |

Result: **5,461 calls** (4,110 buy, 1,351 bearish), 12 channels, 1,295 videos, 763 tickers.

**Other processing decisions**

* **Entry = first close after the upload date.** Only the date is stored. Same-day close would
  credit after-hours videos with a move they could not have caught. Costs the calls the
  overnight reaction, which is the honest direction to err in.
* **Horizons 30/90/180/365 calendar days**, end price = last close on or before the target
  date, SPY and QQQ over exactly the same dates. 90 days is the main horizon for channel
  comparisons: long enough to be more than noise, short enough that all 5,461 calls have it
  (365 days exists for 4,208 calls).
* **Survivorship.** Yahoo drops delisted tickers. Order of sources: Yahoo, Yahoo under the
  current symbol for renames (SQ→XYZ, FI→FISV, NYCB→FLG, CEIX→CNR, HHC→HHH), Tiingo for
  delisted names (WBA, NKLA, X, WBD ...), old `price_daily` as last resort. If a series ends
  inside a return window, the last traded price is used and the proceeds are assumed to earn
  the benchmark until the horizon. 70 calls on 29 tickers are affected.
  Limit: Tiingo's free tier allows about 50 symbols an hour, so only the 65 most-mentioned
  missing symbols were tried; the rest have at most 2 mentions each and are mostly not real tickers.
* **Repeat calls are flagged, not removed** (`is_repeat_90d`: same channel, same stock, same
  direction within 90 days). 61% of buy calls are repeats. Deleting them would misdescribe
  what a viewer sees; keeping them unflagged would overstate the evidence.
* **Outlier check that changed the pipeline.** After adding Tiingo data the bearish 30-day mean
  jumped from +0.4 to +7.5 pp. Cause: TTCF, a bankrupt shell quoted at $0.0001-0.0003, with a
  "+9,900%" return from tick noise. Fix: filter 9 (entry price ≥ $1), 11 rows.
* **Public export** (`data/public/vetted_calls.csv`): call_id, video_id, channel, language,
  video_date, ticker, company_name, direction, llm_label, llm_confidence, mention_count,
  in_depth, is_repeat_90d. No transcript text (the `context` column holds verbatim quotes and
  stays private), no prices, no returns.

## Analyze

| Decision | Reason |
|---|---|
| Metric: excess return = stock return − SPY return | Raw returns mostly measure the bull market (mean raw 90-day return +6.5%, SPY +4.4%). |
| Report mean, median and share beating SPY together | Single-stock returns are right-skewed (skew 2.3); the mean alone hides that the typical call loses. |
| Standard errors clustered two ways, by ticker and by month | Calls are not independent. Naive SE at 365 days is 1.4 pp, two-way clustered 6.4 pp. |
| Second benchmark QQQ, plus beta adjustment | Picks are tech-heavy and high-beta (median beta 1.25, estimated on the 250 trading days before each call, risk-free rate from 13-week T-bills). |
| "Each idea once", "without top 3 stocks", equal weight per channel / per month | Checks whether the average is carried by repetition, by a few tickers, by one channel or by one period. |
| Timing test by shifting all calls by a common number of trading days (500 shifts, circular) | Keeps the clustering of the real data intact, unlike drawing an independent random date per call. |
| Channels: minimum 50 buy calls, Holm correction, split-half persistence | With 10 channels, 0.5 false positives are expected at 5%. |

**Tried and dropped**

* *Month-block bootstrap for the confidence intervals* (first draft). It ignored that one
  stock is called over and over, and gave intervals up to 3× too narrow at long horizons
  (365 days: ±4.4 pp instead of ±13 pp). Replaced by two-way clustering.
* *Independent random-date placebo per call.* Distribution too narrow for the same reason.
  Replaced by the common-shift version.
* *Permutation test for "channels differ more than chance"* (shuffling channel labels within
  months). It rejected strongly, but only because it also ignores ticker repetition. Dropped
  rather than reported with a misleading p-value.
* *Using `confidence` or `verified` as quality filters.* Neither separated genuine from
  non-genuine calls in the audit.
* *Sector analysis.* The `tickers` metadata table is empty.
* *Re-labelling all calls with a stronger model.* Would improve the data, but was ruled out
  for this study; the noise is reported instead.

**What the data says** (details and intervals in `results/results.json`)

1. Median buy call trails SPY at every horizon (−0.7 to −2.1 pp); 45-48% beat it.
2. Mean excess is +0.2 / +2.1 / +7.3 / +11.1 pp at 30/90/180/365 days. Only the 180-day
   interval excludes zero (1.0 to 13.6), and it stops doing so against QQQ, after beta
   adjustment, without the top three tickers, or with each idea counted once.
3. Shifted to other dates the same calls do as well or better (real calendar at the 15th
   percentile for 90 days). Buy minus bearish at 90 days: +0.8 pp (−2.3 to +3.8).
4. Tom Nash +10.1 pp at 90 days (p = 0.009 raw, 0.089 after Holm), with 21% of calls on PLTR
   and beta 2.0; beta-adjusted +6.8. Nobody is significant after correction.

**What surprised me**

* How much of the "result" was dependence: the first-draft intervals made the 180- and
  365-day edge look solid.
* The stocks creators warned about went on to beat the index by about as much as the ones
  they recommended.
* 61% of buy calls are repeats.
* Calls from Q4 2025 are the worst cohort (−6 pp), Q2 2025 the best (+8 pp): the cohort a
  call falls into matters more than who made it.

## Share

* Static single-file page built by `site/build.py`; every number is read from
  `results/results.json` at build time, none is typed into the HTML.
* Charts: one message per chart, stated in its title. Histogram for the distribution (the
  point is its shape), dot-and-interval for comparisons of estimates, lines with bands for
  the path over time. Two series colours (blue/rust) checked for colour-blind separation.
* Hero background: the 90-day excess-return histogram of all buy calls, blurred until the
  reader asks for it.
* Notebook (`notebook/vetted_capstone.ipynb`) reproduces findings 1-4 from the public CSV
  with Yahoo prices only. Its numbers differ slightly from the page because delisted
  companies are missing there.

## Act

Three recommendations, each tied to a result: do not sell the feed as a signal; rebuild the
leaderboard on excess return with intervals and minimum sample sizes; fix label quality
before building further. See README.

## Final checks (16 Nov 2026)

* All six phases are sections on the page and in the notebook.
* Page numbers come from `results.json`; spot-checked against the console output of
  `04_analyze.py`.
* Public files searched for transcript text and price columns: none.

## Polish pass (17 Nov 2026)

No finding and no number changed in this pass (`results.json` before and after differ only by two
added audit counts). What changed is how the page reads and looks.

| Decision | Reason |
|---|---|
| The answer is stated in one sentence at the top; a full conclusion closes the page | A reader with 30 seconds should leave with the answer. |
| Each finding is its own block with a large one-sentence statement, and all four are listed at the start of Analyze | A fast scroller should still catch all four. |
| All text rewritten in short plain sentences; statistics moved into foldable sections; "how to read this chart" notes added | The first version read like a methods paper. |
| Recommendations rewritten for viewers, for people who rank creators, and for people who extract data with a language model | The first version addressed the product owner, which is me. |
| Finding 1 reworded from "the typical call loses" / "evidence: strong" to "more than half did worse", with an explicit caveat | Read as a statistician: the 95% interval for the share beating SPY at 90 days is 40-51%, so an even split is not ruled out. The sample fact is exact; the general claim is not. |
| Confidence intervals no longer described as "the true value lies here with 95% probability" | That is not what a confidence interval means. |
| "Buying on the day of the video" changed to "right after a video" | Entry is the first close after the upload date. |
| One typeface (IBM Plex Sans), neutral background, one accent colour used only for data, grey for the comparison series; removed badges, pills, cards, uppercase labels, scroll animations | The first design looked like a template. |
| Real dashboard screenshot and four real dataset rows added to Prepare | Shows where the data actually comes from. |
| Every hover interaction also works by tap; charts redraw with their own layout below 600 px | Most readers will be on a phone. Checked with an emulated iPhone: every chart responds, no horizontal overflow, smallest tap target 37 px. |
| Analysis code published as files on the site; pipeline linked on GitHub | The capstone repository is not on GitHub yet, and the page should not link to something that does not exist. |

Dropped: "Evidence: strong / moderate / weak" badges (replaced by a sentence that says what is
and is not certain), the count-up numbers, the numbered phase cards.
