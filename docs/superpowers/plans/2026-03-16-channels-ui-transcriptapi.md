# Channels UI — TranscriptAPI Integration & Cost Visibility Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface TranscriptAPI's free features in the UI, label every operation FREE or ~N credits, add @handle resolution to Add Channel, add per-channel credit cost discovery before committing to a backfill, and redesign the channels page around building a large transcript DB.

**Architecture:** Two new JSON API routes for resolve and discovery. `channels.html` redesigned with a FREE/PAID visual language. `.env` safety check. No new Python modules — all wired through existing `channel_fetcher.py` and `scanner.py`.

---

## Cost map — what everything costs

| Operation | Endpoint used | Cost | Source |
|---|---|---|---|
| Resolve @handle → UC ID | `channel/resolve` | **FREE** | TranscriptAPI |
| Check for new videos (latest 15) | `channel/latest` | **FREE** | TranscriptAPI |
| Re-analyze stored transcripts | DB read only | **FREE** | Internal |
| Fetch recent transcripts | `transcript` × N new videos | **1 credit / video** | TranscriptAPI |
| Backfill channel (discover) | YouTube Data API playlist | **FREE** | YouTube |
| Backfill channel (fetch transcripts) | `transcript` × N missing | **1 credit / video** | TranscriptAPI |

---

## File Map

| Action | Path |
|---|---|
| Verify `.env` safety | `.gitignore` — already has `.env` ✓ — no code change |
| Add 2 routes | `main.py` |
| Redesign | `templates/channels.html` |

---

## Task 1: Two new API routes in `main.py`

**Files:**
- Modify: `main.py`

### Route A — `GET /api/channel/resolve`

Used by the Add Channel form to auto-resolve any @handle, URL, or UC ID → canonical UC ID. Zero credits.

```python
@app.get("/api/channel/resolve")
def api_channel_resolve(
    input: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Resolve a YouTube channel @handle, URL, or UC ID to a canonical UC... ID. FREE."""
    channel_id = channel_fetcher.resolve_channel_id(input.strip())
    if not channel_id:
        raise HTTPException(status_code=404, detail="Could not resolve channel")
    return {"channel_id": channel_id}
```

### Route B — `GET /api/channel/discover`

Calls YouTube Data API to list all videos for a channel (free), cross-references DB, returns count of videos missing transcripts = exact credit cost of a full backfill.

```python
@app.get("/api/channel/discover")
def api_channel_discover(
    channel_id: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """
    Discover all videos for a channel via YouTube Data API (FREE — no TranscriptAPI credits).
    Returns count of videos that exist in the channel vs those already stored in DB.
    The difference = how many transcript credits a full backfill would cost.
    """
    import sqlite3
    all_videos = channel_fetcher.get_all_videos(channel_id, max_videos=2000)
    if not all_videos:
        return {"total": 0, "in_db": 0, "missing": 0, "estimated_credits": 0}

    # Check which video IDs already have a stored transcript
    conn = sqlite3.connect(db_manager.DB_NAME)
    conn.row_factory = sqlite3.Row
    ids_with_transcript = {
        row["video_id"]
        for row in conn.execute(
            "SELECT video_id FROM videos WHERE transcript IS NOT NULL"
        ).fetchall()
    }
    conn.close()

    total   = len(all_videos)
    in_db   = sum(1 for v in all_videos if v["video_id"] in ids_with_transcript)
    missing = total - in_db

    return {
        "total":             total,
        "in_db":             in_db,
        "missing":           missing,
        "estimated_credits": missing,
    }
```

- [ ] **Step 1: Add both routes to `main.py` after the existing `/api/video-info` route**

- [ ] **Step 2: Verify both routes return correct JSON**

```bash
# With server running:
curl -u :$DASHBOARD_PASSWORD "http://localhost:8000/api/channel/resolve?input=@mkbhd"
# Expected: {"channel_id": "UCBcRF18a7Qf58cCRy5xuWwQ"}

curl -u :$DASHBOARD_PASSWORD "http://localhost:8000/api/channel/discover?channel_id=UCBcRF18a7Qf58cCRy5xuWwQ"
# Expected: {"total": N, "in_db": M, "missing": K, "estimated_credits": K}
```

---

## Task 2: Redesign `templates/channels.html`

**Files:**
- Modify: `templates/channels.html`

Complete replacement. Key design decisions:
- Every action has a visible **FREE** (green) or **~N credits** (amber) badge
- "Add Channel" uses JS resolve → auto-fills UC ID from @handle/URL
- Per-channel row: "Discover" button (free) → shows credit estimate inline → "Fetch Transcripts" button appears
- Global actions split into two clear sections

### New page structure

```
┌─ Add Channel ──────────────────────────────────────────────────┐
│  Enter @handle or URL → [Resolve FREE] → auto-fills Channel ID │
│  Name, Language → [Add Channel]                                 │
└─────────────────────────────────────────────────────────────────┘

┌─ Free Operations ───────────────────────────────── [FREE] ─────┐
│  Re-analyze stored transcripts  [Re-analyze FREE]              │
│  Fetch recent transcripts for all channels [Scan All ~N credits]│
└─────────────────────────────────────────────────────────────────┘

┌─ Build Transcript DB ──────────────── costs credits ───────────┐
│  Backfill All    [Backfill All]                                  │
│  Backfill Timeframe  [From] [To] [Channel] [Start]             │
└─────────────────────────────────────────────────────────────────┘

┌─ All Channels ─────────────────────────────────────────────────┐
│  Name | Lang | Videos | Transcripts | Last Scanned | Actions   │
│  ...  │  DE  │  142   │  138 / 142  │  2026-03-15  │          │
│                                                                  │
│  Actions per row:                                               │
│  [Scan Now ~N credits]  [Discover FREE→shows cost]  [Exclude]  │
└─────────────────────────────────────────────────────────────────┘
```

### Badge styles (inline CSS, no external deps)

```css
.badge-free {
  background: rgba(34,197,94,0.15);
  color: #22c55e;
  border: 1px solid rgba(34,197,94,0.3);
  font-size: 10px; font-weight: 700; padding: 2px 6px;
  border-radius: 3px; letter-spacing: .04em; white-space: nowrap;
}
.badge-credits {
  background: rgba(245,158,11,0.15);
  color: #f59e0b;
  border: 1px solid rgba(245,158,11,0.3);
  font-size: 10px; font-weight: 700; padding: 2px 6px;
  border-radius: 3px; letter-spacing: .04em; white-space: nowrap;
}
```

### JS functions needed

1. `resolveChannel()` — calls `GET /api/channel/resolve?input=VALUE`, fills `#channel-id-input`
2. `discoverChannel(channelId, rowEl)` — calls `GET /api/channel/discover?channel_id=VALUE`, updates the inline discovery result in that table row

### Full new `channels.html`

```html
{% extends "base.html" %}
{% block title %}Vetted — Channels{% endblock %}
{% block content %}

<style>
.badge-free    { background:rgba(34,197,94,.15);color:#22c55e;border:1px solid rgba(34,197,94,.3);font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.04em;white-space:nowrap;vertical-align:middle; }
.badge-credits { background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.3);font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.04em;white-space:nowrap;vertical-align:middle; }
.op-row        { display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border); }
.op-row:last-child { border-bottom:none; }
.op-label      { font-weight:600;font-size:14px; }
.op-desc       { color:var(--text-muted);font-size:12px;margin-top:2px; }
</style>

<div class="page-header">
  <h1>Channels</h1>
  <p>Manage tracked YouTube channels and build your transcript database.</p>
</div>

<!-- ══ Add Channel ══════════════════════════════════════════════ -->
<div class="card section">
  <div class="card-title">Add Channel</div>
  <form method="POST" action="/admin/channels/add">
    <div class="form-row" style="align-items:flex-end;gap:10px;flex-wrap:wrap;">

      <div class="form-group">
        <label>YouTube URL or @handle</label>
        <div style="display:flex;gap:6px;">
          <input type="text" id="channel-url-input" placeholder="@finanzfluss or youtube.com/..." style="width:240px;">
          <button type="button" onclick="resolveChannel()"
                  class="btn btn-secondary" style="font-size:12px;padding:7px 10px;white-space:nowrap;">
            Resolve &nbsp;<span class="badge-free">FREE</span>
          </button>
        </div>
        <span id="resolve-status" style="font-size:11px;color:var(--text-muted);display:block;margin-top:3px;"></span>
      </div>

      <div class="form-group">
        <label>Channel ID <span style="color:var(--text-muted);font-size:11px;">(UC…)</span></label>
        <input type="text" id="channel-id-input" name="channel_id" placeholder="UCxxxxxxxxxxxxxxxx" style="width:200px;">
      </div>

      <div class="form-group">
        <label>Display Name</label>
        <input type="text" name="name" id="channel-name-input" placeholder="Channel name" style="width:160px;">
      </div>

      <div class="form-group">
        <label>Language</label>
        <select name="language" style="width:90px;">
          <option value="de">DE</option>
          <option value="en">EN</option>
          <option value="es">ES</option>
        </select>
      </div>

      <div class="form-group">
        <label>&nbsp;</label>
        <button type="submit" class="btn btn-primary">Add Channel</button>
      </div>
    </div>
  </form>
</div>


<!-- ══ Operations ═══════════════════════════════════════════════ -->
<div class="card section">
  <div class="card-title">Operations</div>

  <!-- Re-analyze -->
  <div class="op-row">
    <div>
      <div class="op-label">Re-analyze Stored Transcripts &nbsp;<span class="badge-free">FREE</span></div>
      <div class="op-desc">Run the two-pass pipeline on videos with transcript but no mentions — reads DB only, zero API calls</div>
    </div>
    <form method="POST" action="/admin/reanalyze">
      <button type="submit" class="btn btn-secondary">Re-analyze</button>
    </form>
  </div>

  <!-- Fetch recent -->
  <div class="op-row">
    <div>
      <div class="op-label">Fetch Recent Transcripts &nbsp;<span class="badge-credits">1 credit / new video</span></div>
      <div class="op-desc">Check all channels for videos in the last 48h via RSS (free), then fetch their transcripts. Runs analysis automatically after.</div>
    </div>
    <form method="POST" action="/admin/scan/all" class="scan-form">
      <button type="submit" class="btn btn-primary">Scan All Channels</button>
    </form>
  </div>

  <!-- Backfill All -->
  <div class="op-row">
    <div>
      <div class="op-label">Backfill All Channels &nbsp;<span class="badge-credits">1 credit / missing transcript</span></div>
      <div class="op-desc">Fetch full upload history for every tracked channel. Use per-channel Discover to see exact cost before running.</div>
    </div>
    <form method="POST" action="/admin/backfill/all" class="scan-form">
      <button type="submit" class="btn btn-secondary" style="border-color:var(--accent);color:var(--accent);">Backfill All</button>
    </form>
  </div>

  <!-- Backfill Timeframe -->
  <div class="op-row" style="flex-wrap:wrap;gap:12px;">
    <div style="flex:1;min-width:200px;">
      <div class="op-label">Backfill Timeframe &nbsp;<span class="badge-credits">1 credit / missing transcript</span></div>
      <div class="op-desc">Fetch transcripts for a specific date range and channel.</div>
    </div>
    <form method="POST" action="/admin/backfill/timeframe" class="scan-form"
          style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;">
      <div>
        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">Channel</label>
        <select name="channel_id" style="padding:6px 9px;border-radius:4px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:13px;">
          <option value="all">All Channels</option>
          {% for ch in channels %}
          <option value="{{ ch.id }}">{{ ch.name }}</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">From</label>
        <input type="date" name="date_from" required
          style="padding:6px 9px;border-radius:4px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:13px;">
      </div>
      <div>
        <label style="font-size:11px;color:var(--text-muted);display:block;margin-bottom:3px;">To</label>
        <input type="date" name="date_to" required
          style="padding:6px 9px;border-radius:4px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:13px;">
      </div>
      <button type="submit" class="btn btn-secondary" style="border-color:var(--accent);color:var(--accent);">Start Backfill</button>
    </form>
  </div>
</div>


<!-- ══ Channels table ════════════════════════════════════════════ -->
<div class="card section">
  <div class="card-title">All Channels</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Language</th>
          <th>Videos in DB</th>
          <th>Transcript Health</th>
          <th>Last Scanned</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {% for ch in channels %}
        <tr id="ch-row-{{ ch.id }}" {% if ch.skip_backfill %}style="background:rgba(239,68,68,.05);"{% endif %}>
          <td>
            <a href="/channel/{{ ch.id }}">{{ ch.name }}</a>
            {% if ch.skip_backfill %}
              <span style="margin-left:6px;font-size:10px;font-weight:600;color:#ef4444;background:rgba(239,68,68,.12);padding:2px 6px;border-radius:3px;">EXCLUDED</span>
            {% endif %}
          </td>
          <td><span class="lang-badge">{{ ch.language | upper }}</span></td>
          <td>{{ ch.video_count }}</td>
          <td>
            {% if ch.failure_rate is none %}
              <span style="font-size:12px;color:var(--text-muted);">—</span>
            {% elif ch.failure_rate <= 20 %}
              <span style="font-size:12px;font-weight:600;color:#22c55e;">{{ ch.failure_rate }}% fail</span>
            {% elif ch.failure_rate <= 50 %}
              <span style="font-size:12px;font-weight:600;color:#f59e0b;">{{ ch.failure_rate }}% fail</span>
            {% else %}
              <span style="font-size:12px;font-weight:600;color:#ef4444;">{{ ch.failure_rate }}% fail</span>
            {% endif %}
            <span style="font-size:10px;color:var(--text-muted);margin-left:3px;">({{ ch.transcript_attempts or 0 }} tried)</span>
          </td>
          <td style="color:var(--text-muted);font-size:12px;">{{ ch.last_scanned or '—' }}</td>
          <td>
            <div style="display:flex;gap:5px;align-items:center;flex-wrap:wrap;">

              <!-- Scan Now -->
              <form method="POST" action="/admin/scan/{{ ch.id }}" class="scan-form" style="margin:0;">
                <button type="submit" class="btn btn-secondary" style="font-size:11px;padding:4px 9px;">
                  Scan Now &nbsp;<span class="badge-credits">credits</span>
                </button>
              </form>

              <!-- Discover (free) + inline result -->
              {% if not ch.skip_backfill %}
              <button onclick="discoverChannel('{{ ch.id }}')"
                      id="discover-btn-{{ ch.id }}"
                      class="btn btn-secondary" style="font-size:11px;padding:4px 9px;">
                Discover &nbsp;<span class="badge-free">FREE</span>
              </button>
              <span id="discover-result-{{ ch.id }}"
                    style="font-size:11px;color:var(--text-muted);display:none;"></span>
              <form id="backfill-form-{{ ch.id }}" method="POST" action="/admin/backfill/{{ ch.id }}"
                    class="scan-form" style="margin:0;display:none;">
                <button type="submit" class="btn btn-secondary"
                        style="font-size:11px;padding:4px 9px;border-color:var(--accent);color:var(--accent);">
                  Fetch Transcripts &nbsp;<span id="backfill-cost-{{ ch.id }}" class="badge-credits"></span>
                </button>
              </form>
              {% endif %}

              <!-- Exclude / Re-enable -->
              <form method="POST" action="/admin/channels/{{ ch.id }}/toggle-skip-backfill" style="margin:0;">
                <button type="submit" class="btn btn-secondary"
                        style="font-size:11px;padding:4px 9px;{% if ch.skip_backfill %}border-color:#22c55e;color:#22c55e;{% else %}border-color:#ef4444;color:#ef4444;{% endif %}">
                  {% if ch.skip_backfill %}Re-enable{% else %}Exclude{% endif %}
                </button>
              </form>
            </div>
          </td>
        </tr>
        {% else %}
        <tr>
          <td colspan="6" style="color:var(--text-muted);text-align:center;padding:24px;">
            No channels added yet.
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

{% endblock %}

{% block scripts %}
<script>
// ── Resolve @handle / URL → UC ID ────────────────────────────────
function resolveChannel() {
  const input  = document.getElementById('channel-url-input').value.trim();
  const status = document.getElementById('resolve-status');
  if (!input) return;
  status.textContent = 'Resolving…';
  status.style.color = 'var(--text-muted)';
  fetch('/api/channel/resolve?input=' + encodeURIComponent(input))
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(data => {
      document.getElementById('channel-id-input').value = data.channel_id;
      status.textContent = '✓ Resolved: ' + data.channel_id;
      status.style.color = 'var(--positive)';
    })
    .catch(() => {
      status.textContent = '✗ Could not resolve — try a UC… ID directly';
      status.style.color = 'var(--negative)';
    });
}

// ── Discover videos + credit estimate ────────────────────────────
function discoverChannel(channelId) {
  const btn    = document.getElementById('discover-btn-' + channelId);
  const result = document.getElementById('discover-result-' + channelId);
  const form   = document.getElementById('backfill-form-' + channelId);
  const cost   = document.getElementById('backfill-cost-' + channelId);

  btn.disabled    = true;
  btn.textContent = 'Discovering…';
  result.style.display = 'inline';
  result.style.color   = 'var(--text-muted)';
  result.textContent   = 'querying YouTube…';

  fetch('/api/channel/discover?channel_id=' + encodeURIComponent(channelId))
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(data => {
      btn.textContent = 'Discover ';
      btn.appendChild(Object.assign(document.createElement('span'), {
        className: 'badge-free', textContent: 'FREE'
      }));
      btn.disabled = false;

      if (data.missing === 0) {
        result.textContent = '✓ All ' + data.total + ' videos already have transcripts';
        result.style.color = 'var(--positive)';
      } else {
        result.textContent = data.total + ' videos total · ' + data.in_db + ' stored · ';
        result.style.color = 'var(--text-muted)';
        cost.textContent   = '~' + data.missing + ' credits';
        form.style.display = 'inline';
      }
    })
    .catch(() => {
      btn.disabled    = false;
      btn.textContent = 'Discover ';
      result.textContent   = '✗ Discovery failed';
      result.style.color   = 'var(--negative)';
    });
}

// ── Disable all action buttons while a job is running ───────────
(function () {
  document.querySelectorAll('.scan-form').forEach(function (form) {
    form.addEventListener('submit', function () {
      var clicked = form.querySelector('button[type="submit"]');
      document.querySelectorAll('.scan-form button[type="submit"]').forEach(function (btn) {
        btn.disabled = true;
        btn.style.opacity = '0.5';
        if (btn === clicked) btn.textContent = 'Running…';
      });
    });
  });
})();
</script>
{% endblock %}
```

- [ ] **Step 1: Replace `templates/channels.html` with the new version**

- [ ] **Step 2: Verify page loads and table renders at `/channels`**

- [ ] **Step 3: Test Resolve button with a real @handle**

- [ ] **Step 4: Test Discover button on a channel — should show video count and credit estimate, then reveal the Fetch Transcripts button**

- [ ] **Step 5: Verify scan/backfill disable-all behaviour still works (`.scan-form` class)**

---

## Task 3: `.env` — add the key

**No code change.** `.env` is already in `.gitignore`. The only action needed is for the owner to add their key:

```bash
# In /Users/mitjawilms/DeInfluencer/.env
TRANSCRIPTAPI_KEY=your_key_here
```

The key is already in `.env.example` as a placeholder. The server reads it via `os.getenv("TRANSCRIPTAPI_KEY")` in `extract.py` and `channel_fetcher.py`.

---

## Commit

```bash
git add main.py templates/channels.html
git commit -m "feat: TranscriptAPI UI — FREE/credit labels, @handle resolve, per-channel cost discovery"
```
