# Do finance YouTubers' stock picks beat the market?

Capstone case study for the Google Data Analytics Certificate (Track B, own dataset), built on
the data that the Vetted pipeline in this repository collects.

Vetted reads the transcripts of finance-YouTube videos and logs every stock a creator
recommends or warns about. This folder cleans that data and checks the calls against simply
holding the S&P 500.

**Short answer: mostly no.** Fewer than half of the buy recommendations beat the S&P 500, and
buying right after a video worked no better than buying the same stocks on any other day.

| | |
|---|---|
| Dataset | [`data/public/vetted_calls.csv`](data/public/vetted_calls.csv), 5,461 calls. [Column descriptions](data/public/README.md) |
| Notebook | [`notebook/vetted_capstone.ipynb`](notebook/vetted_capstone.ipynb), recalculates the main results from the dataset |
| Data journal | [`JOURNAL.md`](JOURNAL.md), every decision and what was tried and dropped |
| Results | [`results/results.json`](results/results.json), every number shown on the case study page |

## Findings

4,110 buy calls and 1,351 warnings on 763 US stocks, from 1,295 videos on 12 channels,
March 2024 to March 2026, with prices up to 13 November 2026. Every result compares a stock
with the S&P 500 (SPY) over the same days, in percentage points (pp).

1. **More than half of the buy calls did worse than the S&P 500.** After 90 days 45% had
   beaten the index, and the middle call was 1.9 pp behind. The same holds after 30, 180 and
   365 days. As a statement about such calls in general this is less firm: the 95% interval
   for the share that beats the index is 40% to 51%.
2. **On average the calls were ahead, thanks to a few stocks that were recommended over and
   over.** The average call was 7.3 pp ahead after 180 days (95% interval +1.0 to +13.6).
   More than half of that comes from three stocks (PLTR, AMD, MU). Against the Nasdaq-100,
   adjusted for risk, or with each idea counted once (+1.6 pp, −1.8 to +4.9), the lead can no
   longer be told apart from zero.
3. **It made no difference when a call was made, or whether it was a recommendation or a
   warning.** Moved to other dates, the same calls did better in 85% of 500 alternative
   calendars. Stocks the creators warned about did about as well as the ones they recommended
   (difference after 90 days +0.8 pp, −2.3 to +3.8).
4. **No channel was clearly better than the others.** One channel is in front (+10.1 pp after
   90 days), with a fifth of its calls on a single stock. After correcting for comparing ten
   channels, none differs from zero.

**How good is the data?** A language model writes the labels. In a manual check of 140 rows,
71% of the "buy" labels and 40% of the "avoid/sell" labels were a real opinion of the creator.

## What to take from this

- **If you watch finance YouTube:** treat a recommendation as an idea to look into, and take
  your time. The days right after a video were no better a time to buy than any other.
- **If you rank creators or analysts:** compare with the index, count each idea once (61% of
  the buy calls here were repeats), show the uncertainty, and leave out anyone with only a few
  dozen calls.
- **If you extract data with a language model:** read a sample yourself before you trust it.

## Limits

One rising, tech-led market. About three in ten "buy" rows are not real recommendations. Twelve
channels chosen by hand. No fees or taxes. The manual check was done with AI assistance and
not repeated by a second reader. This is an analysis of public videos, not investment advice.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd src
python 01_extract.py     # private database    -> data/private/ (not in this repository)
python 02_prices.py      # share prices        -> data/private/
python 03_clean.py       # cleaning steps      -> data/public/vetted_calls.csv
python 04_analyze.py     # all statistics      -> results/results.json
cd .. && python site/build.py   # the page     -> site/dist/
```

Steps 1 and 2 need the private Vetted database. Everything from the public dataset onwards
can be rerun with the notebook, which downloads prices itself.

```
src/           the four pipeline steps and the sampling script for the manual check
audit/         labels from the manual check of 140 rows (no transcript text)
data/public/   the dataset
results/       results.json and cleaning_log.json
notebook/      reproduction notebook
site/          page template, styles, chart code and build script
```

Transcript text and the price cache are never published.

## How AI was used

Claude Haiku does the extraction inside the Vetted pipeline. The cleaning and analysis code,
the page and the manual check of the 140 sampled rows were done together with Claude Code.
