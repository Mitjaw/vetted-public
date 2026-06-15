# Vetted — Consumer UI Design Spec
*Session: 2026-03-26*

---

## Product Vision

**Vetted** is a B2B finance intelligence data product for hedge funds and prop trading firms. It tracks what finance YouTubers say about stocks — objectively, over time, at scale. The value is the depth and breadth of the historical record, not prediction or signal generation.

**Core product statement:** "Here is what was said, by whom, how often, and what happened to the stock after. Draw your own conclusions."

---

## What This Is NOT

- Not a prediction engine or signal aggregator
- Not a retail investor tool
- No implied buy/sell recommendations
- Not a news ticker or short-term sentiment feed
- No "consensus picks" framing — that implies causality

---

## Business Model

- **B2B subscription** — target: hedge funds, prop trading firms
- **Primary deliverable:** scheduled data exports (CSV/JSON) + full dashboard
- **Three tiers:**

| Tier | History at signup | History over time | Exports | Watchlist/Alerts | Seats |
|------|-------------------|-------------------|---------|------------------|-------|
| Starter | 30 days | Accumulates while subscribed | Weekly | No | 1 |
| Pro | 1 year | Accumulates while subscribed | Daily + on-demand | Yes (email) | 1 |
| Enterprise | All-time | Full archive from day 1 | On-demand, any freq | Yes (email + Slack/webhook) | Multiple |

**Key principle on history:** Customers keep history they were subscribed for. After 12 months on Starter, they have 12 months. This is fair and incentivises retention without being punitive.

**Key principle on seats:** Only Enterprise gets multi-seat — because seat limits only make sense when there's meaningful per-user state (watchlists, alert preferences). Starter/Pro are single-user.

**Not yet decided:** billing/payment flow (Stripe vs manual invoicing). Deferred.

---

## Architecture

**Approach: Separate consumer app, shared database (read-only)**

- New FastAPI app (`consumer/`) alongside existing owner dashboard
- Connects to `vetted.db` in **read-only mode** (`?mode=ro`) — zero risk of consumer code mutating data
- Consumer-specific state (users, subscriptions, watchlists, export jobs) lives in a separate `consumer.db`
- Auth: session-based login (email + password, bcrypt) replacing HTTP Basic Auth
- Two processes on same server: owner dashboard port 8000, consumer app port 8001 (or nginx subdomain `app.vetted.co`)

---

## Pages / Navigation

**Navigation:** Icon sidebar (collapsed, 52px wide). Maximises content width. Standard for dense data tools.

| Route | Name | Tier |
|-------|------|------|
| `/` | Feed / Home | All |
| `/stocks` | Stocks | All |
| `/stock/:ticker` | Stock Detail | All |
| `/channels` | Channels | All |
| `/channel/:id` | Channel Detail | All |
| `/leads` | Leads | All (filtered by tier's history window) |
| `/analyst` | Analyst | All |
| `/exports` | Exports | All (frequency by tier) |
| `/account` | Account | All |
| `/account/team` | Team | Enterprise |

---

## Home Page

**Settled direction:** Activity feed + right panel. NOT database stats as the hero.

**Hierarchy:**
1. **Search bar** at top — primary interaction, jump to any ticker or channel
2. **"Since last visit" delta strip** — new mentions, new stocks, channels published. Factual delta, no spin.
3. **Activity feed (main/left)** — recent videos with mention counts extracted. Factual: "Finanzfluss published X, 9 mentions extracted across 4 stocks." Fades older items.
4. **Most covered stocks (right)** — ranked by raw all-time mention count. Sentiment dot is a descriptor, not a recommendation.
5. **Database health mini (right bottom)** — total mentions, videos, stocks, history depth. Tucked away, not the hero.

**What was rejected:**
- "Consensus picks" framing — implies causality
- "Sustained bullish" — implies prediction
- Short time windows (7d, 30d) as defaults — product is about long-horizon data, default is 1Y
- Pure database stats as the landing page — too dry, no entry point for the user

**Feedback on current state:** Homepage direction is right but content is scattered and doesn't surface correlations clearly. Needs a cleaner layout pass — more intentional use of space, clearer visual hierarchy.

**Outstanding:** Layout needs a cleaner pass. Tighter grid, better use of whitespace, stronger correlation between data points.

---

## Leads Page

**Concept:** A dedicated page surfacing stocks that were recently mentioned by channels with strong historical track records and/or high average ROI on past picks.

**Framing:** Still objective — "Channel X (74% accuracy on past picks) mentioned Stock Y for the first time on [date]." The user decides what to do with that. No recommendation language.

**Why a full page (not just a section):** Two distinct datasets justify it:
- **Fresh leads** — mentioned in last 7/30/90 days by high-accuracy channels, no ROI outcome yet
- **Validated leads** — mentioned 30+ days ago by high-accuracy channels, now have ROI data showing how it played out

**Filter controls:**
- Minimum channel accuracy % (e.g. show only channels with >60% historical accuracy)
- Time window for "new" (7d / 30d / 90d)
- Minimum number of high-accuracy channels that mentioned it
- Asset type (stock / ETF / crypto)

**Table columns (Fresh Leads):**
- Ticker + company name
- First mentioned date
- Channel(s) — with their accuracy % shown inline
- Sentiment at mention (as descriptor)
- Confidence score from extraction
- Days since mention

**Table columns (Validated Leads):**
- All of the above
- ROI at 7d and 30d post-mention
- Whether it outperformed the market in that window

**Open question:** What is the minimum accuracy threshold that makes a "lead" meaningful? And how do we handle channels with small sample sizes — a channel with 5 picks at 100% accuracy is statistically meaningless. Need a minimum pick count (e.g. >20 tracked mentions) before a channel qualifies as "high accuracy."

---

## Design Principles

1. **Objective over opinionated** — the product records and reports, it does not recommend
2. **Long-horizon by default** — 1Y is the default window, not 7D or 30D
3. **Data depth is the product** — the size and age of the database is a feature, surface it
4. **Elevated, not noisy** — B2B hedge fund audience. Dense but not cluttered. Think Bloomberg, not Robinhood.
5. **Search-first** — analysts know what they're looking for. Make search prominent.
6. **Correlations visible** — data points should be shown in relation to each other, not in isolation

---

## Visual Direction

- Dark theme (existing: `#080808` background, `#d97706` orange accent)
- Icon sidebar, 52px wide
- Default time window: 1Y
- No prediction language in any UI copy
- Sentiment shown as a factual descriptor (bull/bear/neutral), never a recommendation

---

---

## Youtubers Page (`/channels`)

**Concept:** Ranked leaderboard of tracked channels, ordered by historical pick accuracy.

**Controls bar:** ROI window toggle (`7D / 30D`) — prominently labelled, affects all return figures on the page.

**Table grid:** `28px 1fr 120px 120px 120px 28px` (rank · name+meta · success rate · bullish calls · bearish calls · arrow)

**Column: Success Rate**
- Inline legend in column header: three colored dots + `≥65% · 50–65% · <50%` + "50% = coin flip" footnote
- Number shown as `XX%` in color matching threshold (green/amber/red)
- 12 monthly dots below the number — color = threshold (green ≥65%, amber 50–65%, red <50%), hollow dot = no data that month
- Hover on dot shows `Mon YY · XX%`
- `⚠ low sample` amber badge appears under any stat with < 15 picks

**Column: Bullish Calls**
- Header sub-label: `↑ = stock rose = ✓`
- Shows `X/Y` (correct/total) + average return in green if positive
- Sign convention: positive return = stock rose = correct bullish call → green

**Column: Bearish Calls**
- Header sub-label: `↓ = stock fell = ✓`
- Shows `X/Y` (correct/total) + average return in green/red by actual price direction
- Sign convention: `−6.1%` in green = stock fell = correct bearish call; `+1.2%` in red = stock rose = wrong bearish call
- This is the actual price change, not a profit/loss figure

**Channel meta (under name):**
- Video count (e.g. `847 videos`) — this is where video count lives, NOT a separate column
- Activity signal: colored dot + "Xd ago" (green ≤7d, amber 8–30d, red >30d)

**What was rejected:**
- Standalone videos column (redundant — count is visible in meta)
- Bar-based sparkline for monthly success rate (no zero line = misleading at ≈50%; replaced with colored dots)
- `+6.1%` in green for bearish call (ambiguous — looks like a gain; replaced with actual price direction)

---

## Market Summary Page (`/stocks`)

**Sections:**

**Most Mentioned** — ranked by mention count. Each row shows ticker, name, count, and three `%` figures: green (bullish), yellow (neutral), red (bearish). No single bull/bear label — the split is shown directly.

**Narrative Shifts** — stocks where sentiment direction changed recently. Each row has a mini 8-week stacked sentiment timeline (CSS flex columns, green/gray/red proportions by week) so the shift is visible at a glance.

**Coverage Concentration** — which channels dominate coverage of each stock.

**Asset & Industry Breakdown** — horizontal bar chart. Each bar has a `breakdown-delta` column showing week-over-week change (`+3%` green, `−2%` red, `—` flat).

**Sentiment vs Outcome** — scatter of sentiment buckets vs average 30d ROI. Zero-baseline chart: positive bars extend upward, negative bars extend downward from a `1px #333` zero line. Each bucket labelled as `X periods` (not `n=X`). Footnote: "Each period = one calendar month · correlation across 36 months of data." Current period marked `◂ now`.

---

## Stats Page (`/stats`)

**Concept:** Full database overview — all channels, all picks, all time.

**Summary strip (4 cards):**
- Total Mentions (amber accent number)
- Videos Analyzed
- Channels Tracked (sub: `X English · Y German`)
- Settled ROI Points
- Each card shows week-over-week delta (`+X this week`)

**ROI histogram:**
- 6 buckets covering negative to positive returns
- Red bars for negative buckets, green for positive
- Amber median line overlay with label (`median +2.1%`)

**Two-column timeline (12 months):**
- Left: mention volume bar chart (amber bars, current month highlighted)
- Right: stacked sentiment proportions (bull/neutral/bear) per month

**Best/Worst picks tables (side by side):**
- Columns: ticker, channel, date, 30d return
- Best picks in green returns, worst picks in red

---

## Exports Page (`/exports`)

**Two tabs:** `Analyst` (default) and `Scheduled Exports`.

**Analyst tab layout:** Left filter panel (300px fixed) + right data preview.

**Filter panel:**
- Ticker search input
- Channel checkboxes
- Date range (from/to)
- Sentiment pills (Bullish / Neutral / Bearish)
- Asset type pills (Stock / ETF / Crypto)
- Confidence threshold slider (static at 0.70 in mockup)
- Result count in amber: `4,821 rows match`
- CSV and JSON download buttons

**Data preview:**
- Sticky-header table, 8 sample rows
- Pending ROI shows `—`
- Columns: ticker, channel, date, sentiment, confidence, 7d ROI, 30d ROI

**Scheduled Exports tab:**
- Cards showing existing scheduled exports with frequency, format, last sent
- Hover reveals edit/delete actions
- Dashed `+ Add scheduled export` CTA card

---

## SPA Implementation Notes

**File:** `vetted-spa.html` (combined, ~176KB)

**Navigation:** Shared 52px icon sidebar. JS `nav(pageId)` swaps `.active` on both `.page` divs and `[data-page]` nav icons. Home is active by default.

**CSS strategy:** All page-specific CSS scoped with `#page-{id}` prefix to prevent class name conflicts across pages. Shared sidebar CSS defined once globally. `switchTab(name, el)` handles Exports tab switching.

**Scroll model:** Each page div is `display: flex; flex-direction: column; overflow: hidden`. Pages that need to scroll (Home, Stats) have their `.main` wrapper set to `overflow-y: auto`. Individual sub-sections do not have their own scroll.

---

## Open Decisions

- [ ] Billing flow: Stripe self-serve vs manual B2B invoicing
- [ ] Leads page: minimum channel sample size threshold before qualifying as "high accuracy" (suggested: >20 tracked mentions)
- [ ] Leads page: UI copy — "Leads" is internal framing; consider "Notable Coverage" or "High-Accuracy Mentions" for the product
- [ ] Email alert delivery for watchlist — infrastructure not yet designed
- [ ] Slack/webhook delivery for Enterprise alerts — not yet designed
