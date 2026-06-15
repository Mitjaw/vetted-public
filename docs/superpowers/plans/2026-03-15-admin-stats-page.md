# Admin Stats Page Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/admin/stats` page showing DB health, extraction quality, ROI performance, and activity timelines — all from existing tables, no schema changes.

**Architecture:** Single `get_admin_stats()` function in `db_manager.py` runs all queries and returns one dict. The route in `main.py` calls it and renders `templates/stats.html`. Nav link added to `base.html`.

**Tech Stack:** Python/SQLite (raw queries), FastAPI, Jinja2, Chart.js (already loaded in base.html)

**Note on tests:** This codebase has no test suite. Verification is done by running the server and inspecting the page.

---

## Chunk 1: DB query function

### Task 1: Add `get_admin_stats()` to `db_manager.py`

**Files:**
- Modify: `db_manager.py` (append after `get_channels_list`)

- [ ] **Step 1: Add the function**

Paste this after the `get_channels_list` function (around line 1293):

```python
def get_admin_stats():
    """
    Aggregate stats across all tables for the /admin/stats owner page.
    Returns a single dict — all queries run in one connection.
    """
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # --- Database health ---
    c.execute("SELECT COUNT(*) n FROM channels")
    total_channels = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM channels WHERE skip_backfill = 1")
    excluded_channels = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM videos")
    total_videos = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM videos WHERE transcript IS NOT NULL")
    videos_with_transcript = c.fetchone()["n"]

    c.execute(
        """
        SELECT COUNT(*) n FROM videos
        WHERE transcript IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.video_id = videos.video_id)
        """
    )
    videos_no_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM mentions")
    total_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM roi_tracking")
    total_roi_rows = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM roi_tracking WHERE roi_7d IS NOT NULL")
    roi_7d_done = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM roi_tracking WHERE roi_30d IS NOT NULL")
    roi_30d_done = c.fetchone()["n"]

    # --- Extraction quality ---
    c.execute(
        "SELECT sentiment, COUNT(*) cnt FROM mentions GROUP BY sentiment"
    )
    sentiment_rows = {r["sentiment"]: r["cnt"] for r in c.fetchall()}
    bullish_count  = sentiment_rows.get("bullish",  0)
    bearish_count  = sentiment_rows.get("bearish",  0)
    neutral_count  = sentiment_rows.get("neutral",  0)

    c.execute(
        "SELECT recommendation, COUNT(*) cnt FROM mentions GROUP BY recommendation"
    )
    rec_rows = {r["recommendation"]: r["cnt"] for r in c.fetchall()}

    c.execute("SELECT ROUND(AVG(confidence), 3) avg FROM mentions WHERE is_real_stock_mention = 1")
    avg_confidence = c.fetchone()["avg"] or 0.0

    c.execute("SELECT COUNT(*) n FROM mentions WHERE is_real_stock_mention = 1")
    real_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) n FROM mentions WHERE is_real_stock_mention = 0 OR is_real_stock_mention IS NULL")
    filtered_mentions = c.fetchone()["n"]

    c.execute("SELECT COUNT(DISTINCT UPPER(ticker)) n FROM mentions")
    distinct_tickers = c.fetchone()["n"]

    # --- ROI performance ---
    c.execute("SELECT ROUND(AVG(roi_7d), 2) avg FROM roi_tracking WHERE roi_7d IS NOT NULL")
    avg_roi_7d = c.fetchone()["avg"]

    c.execute("SELECT ROUND(AVG(roi_30d), 2) avg FROM roi_tracking WHERE roi_30d IS NOT NULL")
    avg_roi_30d = c.fetchone()["avg"]

    c.execute(
        """
        SELECT rt.ticker, m.company_name,
               ROUND(rt.roi_30d, 2) roi_30d,
               ROUND(rt.roi_7d,  2) roi_7d,
               v.title, v.upload_date,
               ch.name channel_name
        FROM roi_tracking rt
        JOIN mentions m  ON m.id       = rt.mention_id
        JOIN videos v    ON v.video_id = m.video_id
        JOIN channels ch ON ch.id      = v.channel_id
        WHERE rt.roi_30d IS NOT NULL
        ORDER BY rt.roi_30d DESC
        LIMIT 5
        """
    )
    top_picks = [dict(r) for r in c.fetchall()]

    c.execute(
        """
        SELECT rt.ticker, m.company_name,
               ROUND(rt.roi_30d, 2) roi_30d,
               ROUND(rt.roi_7d,  2) roi_7d,
               v.title, v.upload_date,
               ch.name channel_name
        FROM roi_tracking rt
        JOIN mentions m  ON m.id       = rt.mention_id
        JOIN videos v    ON v.video_id = m.video_id
        JOIN channels ch ON ch.id      = v.channel_id
        WHERE rt.roi_30d IS NOT NULL
        ORDER BY rt.roi_30d ASC
        LIMIT 5
        """
    )
    worst_picks = [dict(r) for r in c.fetchall()]

    # --- Activity timeline (last 12 months) ---
    c.execute(
        """
        SELECT strftime('%Y-%m', upload_date) month, COUNT(*) cnt
        FROM videos
        WHERE upload_date >= date('now', '-12 months')
        GROUP BY month
        ORDER BY month
        """
    )
    videos_by_month_raw = {r["month"]: r["cnt"] for r in c.fetchall()}

    c.execute(
        """
        SELECT strftime('%Y-%m', v.upload_date) month, COUNT(*) cnt
        FROM mentions m
        JOIN videos v ON v.video_id = m.video_id
        WHERE v.upload_date >= date('now', '-12 months')
        GROUP BY month
        ORDER BY month
        """
    )
    mentions_by_month_raw = {r["month"]: r["cnt"] for r in c.fetchall()}

    conn.close()

    # Fill missing months with 0 so charts have no gaps
    from datetime import date, timedelta
    months = []
    d = date.today().replace(day=1)
    for _ in range(12):
        months.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    months.reverse()

    videos_by_month   = [{"month": m, "cnt": videos_by_month_raw.get(m, 0)}   for m in months]
    mentions_by_month = [{"month": m, "cnt": mentions_by_month_raw.get(m, 0)} for m in months]

    return {
        # health
        "total_channels":       total_channels,
        "active_channels":      total_channels - excluded_channels,
        "excluded_channels":    excluded_channels,
        "total_videos":         total_videos,
        "videos_with_transcript": videos_with_transcript,
        "videos_without_transcript": total_videos - videos_with_transcript,
        "transcript_pct": round(videos_with_transcript / total_videos * 100) if total_videos else 0,
        "videos_no_mentions":   videos_no_mentions,
        "total_mentions":       total_mentions,
        "total_roi_rows":       total_roi_rows,
        "roi_7d_done":          roi_7d_done,
        "roi_30d_done":         roi_30d_done,
        # extraction quality
        "bullish_count":        bullish_count,
        "bearish_count":        bearish_count,
        "neutral_count":        neutral_count,
        "rec_buy":     rec_rows.get("buy",       0),
        "rec_sell":    rec_rows.get("sell",      0),
        "rec_hold":    rec_rows.get("hold",      0),
        "rec_reference": rec_rows.get("reference", 0),
        "avg_confidence":  avg_confidence,
        "real_mentions":   real_mentions,
        "filtered_mentions": filtered_mentions,
        "distinct_tickers": distinct_tickers,
        # roi
        "avg_roi_7d":   avg_roi_7d,
        "avg_roi_30d":  avg_roi_30d,
        "top_picks":    top_picks,
        "worst_picks":  worst_picks,
        # timeline
        "videos_by_month":   videos_by_month,
        "mentions_by_month": mentions_by_month,
    }
```

- [ ] **Step 2: Verify it runs without error**

Start the server (`uvicorn main:app --reload`) and in a Python shell:
```python
import db_manager
s = db_manager.get_admin_stats()
print(s["total_videos"], s["distinct_tickers"], s["top_picks"])
```
Expected: numbers print without exception.

- [ ] **Step 3: Commit**
```bash
git add db_manager.py
git commit -m "feat: add get_admin_stats() for owner stats page"
```

---

## Chunk 2: Route, template, nav link

### Task 2: Add `/admin/stats` route to `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add the route**

Add after the `/admin/reanalyze` route:

```python
@app.get("/admin/stats")
def admin_stats(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    stats = db_manager.get_admin_stats()
    return templates.TemplateResponse("stats.html", {"request": request, "stats": stats})
```

---

### Task 3: Create `templates/stats.html`

**Files:**
- Create: `templates/stats.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}Vetted — Stats{% endblock %}
{% block content %}

<div class="page-header">
  <h1>Database Stats</h1>
  <p>Everything in the DB, at a glance.</p>
</div>

<!-- ── Database Health ── -->
<div class="card-title">Database Health</div>
<div class="stat-row stat-row-4 section">
  <div class="card stat-box">
    <div class="stat-value">{{ stats.total_channels }}</div>
    <div class="stat-label">Channels tracked</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{{ stats.active_channels }} active · {{ stats.excluded_channels }} excluded</div>
  </div>
  <div class="card stat-box">
    <div class="stat-value">{{ stats.total_videos }}</div>
    <div class="stat-label">Videos in DB</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{{ stats.videos_with_transcript }} with transcript ({{ stats.transcript_pct }}%)</div>
  </div>
  <div class="card stat-box">
    <div class="stat-value">{{ stats.total_mentions }}</div>
    <div class="stat-label">Mention rows</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{{ stats.distinct_tickers }} distinct tickers</div>
  </div>
  <div class="card stat-box">
    <div class="stat-value">{{ stats.total_roi_rows }}</div>
    <div class="stat-label">ROI rows</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{{ stats.roi_7d_done }} with 7d · {{ stats.roi_30d_done }} with 30d</div>
  </div>
</div>

<div class="stat-row stat-row-3 section">
  <div class="card stat-box">
    <div class="stat-value" style="color:var(--text-muted);">{{ stats.videos_no_mentions }}</div>
    <div class="stat-label">Transcripts, 0 mentions</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Candidates for re-analysis</div>
  </div>
  <div class="card stat-box">
    <div class="stat-value" style="color:var(--text-muted);">{{ stats.videos_without_transcript }}</div>
    <div class="stat-label">Videos without transcript</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Subtitles disabled or unavailable</div>
  </div>
  <div class="card stat-box">
    <div class="stat-value">{{ stats.avg_confidence }}</div>
    <div class="stat-label">Avg confidence</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Across real mentions only</div>
  </div>
</div>

<!-- ── Extraction Quality ── -->
<div class="card-title">Extraction Quality</div>
<div class="two-col two-col-50-50 section">

  <div class="card">
    <div class="card-title">Sentiment Breakdown</div>
    {% set sent_total = stats.bullish_count + stats.bearish_count + stats.neutral_count %}
    <table>
      <thead><tr><th>Sentiment</th><th>Count</th><th>Share</th><th>Bar</th></tr></thead>
      <tbody>
        <tr>
          <td><span class="badge bullish">Bullish</span></td>
          <td style="font-weight:600;color:var(--positive);">{{ stats.bullish_count }}</td>
          <td style="color:var(--text-muted);">{% if sent_total %}{{ (stats.bullish_count / sent_total * 100)|round|int }}%{% else %}—{% endif %}</td>
          <td>
            {% if sent_total %}
            <div style="height:6px;border-radius:3px;background:var(--positive);width:{{ (stats.bullish_count / sent_total * 100)|round|int }}%;min-width:2px;"></div>
            {% endif %}
          </td>
        </tr>
        <tr>
          <td><span class="badge bearish">Bearish</span></td>
          <td style="font-weight:600;color:var(--negative);">{{ stats.bearish_count }}</td>
          <td style="color:var(--text-muted);">{% if sent_total %}{{ (stats.bearish_count / sent_total * 100)|round|int }}%{% else %}—{% endif %}</td>
          <td>
            {% if sent_total %}
            <div style="height:6px;border-radius:3px;background:var(--negative);width:{{ (stats.bearish_count / sent_total * 100)|round|int }}%;min-width:2px;"></div>
            {% endif %}
          </td>
        </tr>
        <tr>
          <td><span class="badge neutral">Neutral</span></td>
          <td style="font-weight:600;color:var(--neutral);">{{ stats.neutral_count }}</td>
          <td style="color:var(--text-muted);">{% if sent_total %}{{ (stats.neutral_count / sent_total * 100)|round|int }}%{% else %}—{% endif %}</td>
          <td>
            {% if sent_total %}
            <div style="height:6px;border-radius:3px;background:var(--neutral);width:{{ (stats.neutral_count / sent_total * 100)|round|int }}%;min-width:2px;"></div>
            {% endif %}
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-title">Recommendation Breakdown</div>
    {% set rec_total = stats.rec_buy + stats.rec_sell + stats.rec_hold + stats.rec_reference %}
    <table>
      <thead><tr><th>Recommendation</th><th>Count</th><th>Share</th></tr></thead>
      <tbody>
        <tr><td style="font-weight:600;color:var(--positive);">Buy</td><td>{{ stats.rec_buy }}</td><td style="color:var(--text-muted);">{% if rec_total %}{{ (stats.rec_buy / rec_total * 100)|round|int }}%{% else %}—{% endif %}</td></tr>
        <tr><td style="font-weight:600;color:var(--negative);">Sell</td><td>{{ stats.rec_sell }}</td><td style="color:var(--text-muted);">{% if rec_total %}{{ (stats.rec_sell / rec_total * 100)|round|int }}%{% else %}—{% endif %}</td></tr>
        <tr><td style="font-weight:600;color:var(--neutral);">Hold</td><td>{{ stats.rec_hold }}</td><td style="color:var(--text-muted);">{% if rec_total %}{{ (stats.rec_hold / rec_total * 100)|round|int }}%{% else %}—{% endif %}</td></tr>
        <tr><td style="color:var(--text-muted);">Reference</td><td>{{ stats.rec_reference }}</td><td style="color:var(--text-muted);">{% if rec_total %}{{ (stats.rec_reference / rec_total * 100)|round|int }}%{% else %}—{% endif %}</td></tr>
      </tbody>
    </table>
    <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:24px;font-size:12px;">
      <span>Real mentions: <strong style="color:var(--text);">{{ stats.real_mentions }}</strong></span>
      <span style="color:var(--text-muted);">Filtered out: {{ stats.filtered_mentions }}</span>
    </div>
  </div>

</div>

<!-- ── ROI Performance ── -->
<div class="card-title">ROI Performance</div>
<div class="stat-row stat-row-4 section">
  <div class="card stat-box">
    <div class="stat-value {% if stats.avg_roi_7d is not none %}{% if stats.avg_roi_7d >= 0 %}roi-positive{% else %}roi-negative{% endif %}{% endif %}">
      {% if stats.avg_roi_7d is not none %}{{ stats.avg_roi_7d }}%{% else %}—{% endif %}
    </div>
    <div class="stat-label">Avg ROI at 7 days</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{{ stats.roi_7d_done }} picks tracked</div>
  </div>
  <div class="card stat-box">
    <div class="stat-value {% if stats.avg_roi_30d is not none %}{% if stats.avg_roi_30d >= 0 %}roi-positive{% else %}roi-negative{% endif %}{% endif %}">
      {% if stats.avg_roi_30d is not none %}{{ stats.avg_roi_30d }}%{% else %}—{% endif %}
    </div>
    <div class="stat-label">Avg ROI at 30 days</div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">{{ stats.roi_30d_done }} picks tracked</div>
  </div>
</div>

<div class="two-col two-col-50-50 section">
  <div class="card">
    <div class="card-title">Top 5 Picks (30d ROI)</div>
    {% if stats.top_picks %}
    <table>
      <thead><tr><th>Ticker</th><th>Channel</th><th>Date</th><th>7d</th><th>30d</th></tr></thead>
      <tbody>
        {% for p in stats.top_picks %}
        <tr>
          <td><a href="/stock/{{ p.ticker }}" style="font-weight:600;">{{ p.ticker }}</a><div style="font-size:11px;color:var(--text-muted);">{{ p.company_name or '' }}</div></td>
          <td style="color:var(--text-muted);font-size:12px;">{{ p.channel_name }}</td>
          <td style="color:var(--text-muted);font-size:12px;">{{ p.upload_date }}</td>
          <td class="{% if p.roi_7d is not none %}{% if p.roi_7d >= 0 %}roi-positive{% else %}roi-negative{% endif %}{% else %}roi-na{% endif %}">
            {% if p.roi_7d is not none %}{{ p.roi_7d }}%{% else %}—{% endif %}
          </td>
          <td class="{% if p.roi_30d >= 0 %}roi-positive{% else %}roi-negative{% endif %}">{{ p.roi_30d }}%</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}<p style="color:var(--text-muted);font-size:13px;">No 30d data yet.</p>{% endif %}
  </div>

  <div class="card">
    <div class="card-title">Worst 5 Picks (30d ROI)</div>
    {% if stats.worst_picks %}
    <table>
      <thead><tr><th>Ticker</th><th>Channel</th><th>Date</th><th>7d</th><th>30d</th></tr></thead>
      <tbody>
        {% for p in stats.worst_picks %}
        <tr>
          <td><a href="/stock/{{ p.ticker }}" style="font-weight:600;">{{ p.ticker }}</a><div style="font-size:11px;color:var(--text-muted);">{{ p.company_name or '' }}</div></td>
          <td style="color:var(--text-muted);font-size:12px;">{{ p.channel_name }}</td>
          <td style="color:var(--text-muted);font-size:12px;">{{ p.upload_date }}</td>
          <td class="{% if p.roi_7d is not none %}{% if p.roi_7d >= 0 %}roi-positive{% else %}roi-negative{% endif %}{% else %}roi-na{% endif %}">
            {% if p.roi_7d is not none %}{{ p.roi_7d }}%{% else %}—{% endif %}
          </td>
          <td class="{% if p.roi_30d >= 0 %}roi-positive{% else %}roi-negative{% endif %}">{{ p.roi_30d }}%</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}<p style="color:var(--text-muted);font-size:13px;">No 30d data yet.</p>{% endif %}
  </div>
</div>

<!-- ── Activity Timeline ── -->
<div class="card-title">Activity (Last 12 Months)</div>
<div class="two-col two-col-50-50 section">
  <div class="card">
    <div class="card-title">Videos Analyzed</div>
    <canvas id="videos-chart" style="max-height:180px;"></canvas>
  </div>
  <div class="card">
    <div class="card-title">Mentions Extracted</div>
    <canvas id="mentions-chart" style="max-height:180px;"></canvas>
  </div>
</div>

{% endblock %}

{% block scripts %}
<script>
(function() {
  const barOpts = function(color) {
    return {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#6b80a8', font: { size: 10 } }, grid: { color: '#1e2d4a' } },
        y: { ticks: { color: '#6b80a8', stepSize: 1 }, grid: { color: '#1e2d4a' }, beginAtZero: true }
      }
    };
  };

  const videoData = {{ stats.videos_by_month | tojson }};
  const mentionData = {{ stats.mentions_by_month | tojson }};

  new Chart(document.getElementById('videos-chart'), {
    type: 'bar',
    data: {
      labels: videoData.map(d => d.month),
      datasets: [{ data: videoData.map(d => d.cnt), backgroundColor: 'rgba(217,119,6,0.7)', borderRadius: 3 }]
    },
    options: barOpts('rgba(217,119,6,0.7)')
  });

  new Chart(document.getElementById('mentions-chart'), {
    type: 'bar',
    data: {
      labels: mentionData.map(d => d.month),
      datasets: [{ data: mentionData.map(d => d.cnt), backgroundColor: 'rgba(59,130,246,0.7)', borderRadius: 3 }]
    },
    options: barOpts('rgba(59,130,246,0.7)')
  });
})();
</script>
{% endblock %}
```

---

### Task 4: Add Stats link to nav in `base.html`

**Files:**
- Modify: `templates/base.html`

- [ ] **Step 1: Add nav link after Export**

Find this line:
```html
<a href="/export" {% if request.url.path == '/export' %}class="active"{% endif %}>Export</a>
```

Replace with:
```html
<a href="/export" {% if request.url.path == '/export' %}class="active"{% endif %}>Export</a>
<a href="/admin/stats" {% if request.url.path == '/admin/stats' %}class="active"{% endif %}>Stats</a>
```

- [ ] **Step 2: Verify in browser**

Navigate to `/admin/stats`. Confirm:
- All stat boxes show numbers (not errors)
- Sentiment and recommendation tables render
- Top/worst picks tables show data or "No 30d data yet"
- Both bar charts render without JS errors in console

- [ ] **Step 3: Commit**
```bash
git add main.py templates/stats.html templates/base.html
git commit -m "feat: add /admin/stats owner overview page"
```
