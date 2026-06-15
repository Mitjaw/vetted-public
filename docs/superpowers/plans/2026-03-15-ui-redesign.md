# UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Vetted dashboard from a blue-tinted sidebar layout to a charcoal dark Linear/Vercel aesthetic with amber accent and a 48px top navigation bar.

**Architecture:** All CSS lives in `base.html`'s `<style>` block. No separate CSS file is introduced. The sidebar `<aside>` is replaced with a `<nav class="topbar">`. Page-specific JS chart color values are updated in `analyst.html` and `stock.html` only. Home filter pills are converted from inline Jinja2 styles to reusable `.pill`/`.pill.active` CSS classes.

**Tech Stack:** HTML, CSS custom properties, Jinja2 (no logic changes), Chart.js (color values only)

**Spec:** `docs/superpowers/specs/2026-03-15-ui-redesign-design.md`

> **Implementer note:** Before applying any edit, use the Read tool to confirm the exact whitespace in the target file. Indentation must match exactly for Edit tool operations to succeed.

---

## Chunk 1: base.html — Full Overhaul

### Task 1: Font — Inter → Geist

**Files:**
- Modify: `templates/base.html` (lines 7–9 and 24)

- [ ] **Step 1: Verify Geist font URL**

  Open your browser and visit:
  ```
  https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&display=swap
  ```
  If it returns CSS, use Google Fonts. If it 404s, use Bunny Fonts instead:
  ```
  https://fonts.bunny.net/css?family=geist:300,400,500,600,700&display=swap
  ```

- [ ] **Step 2: Replace Inter font link with Geist**

  Find in `templates/base.html`:
  ```html
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  ```
  Replace with (Google Fonts version — swap for Bunny URL if Step 1 failed):
  ```html
    <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  ```

---

### Task 2: CSS Custom Properties — Palette + Font Variable

**Files:**
- Modify: `templates/base.html` (`:root` block, lines 12–25)

- [ ] **Step 1: Replace the entire :root block**

  Find in `templates/base.html`:
  ```css
    :root {
      --bg:        #070d1a;
      --bg-card:   #0f1829;
      --bg-raised: #162033;
      --border:    #1e2d4a;
      --accent:    #2563eb;
      --accent-hl: #3b82f6;
      --text:      #e8eeff;
      --text-muted:#6b80a8;
      --positive:  #10b981;
      --negative:  #ef4444;
      --neutral:   #f59e0b;
      --font: 'Inter', system-ui, sans-serif;
    }
  ```
  Replace with:
  ```css
    :root {
      --bg:        #0c0c0c;
      --bg-card:   #141414;
      --bg-raised: #1c1c1c;
      --border:    #262626;
      --accent:    #d97706;
      --accent-hl: #f59e0b;
      --text:      #e5e5e5;
      --text-muted:#737373;
      --positive:  #10b981;
      --negative:  #ef4444;
      --neutral:   #d97706;
      --font: 'Geist', system-ui, sans-serif;
    }
  ```

- [ ] **Step 2: Verify in browser**

  Start server: `cd /Users/mitjawilms/DeInfluencer && uvicorn main:app --reload`
  Open `http://localhost:8000`. Background should be near-black charcoal (#0c0c0c). No blue tint anywhere.

---

### Task 3: Sidebar CSS → Topbar CSS

**Files:**
- Modify: `templates/base.html` (sidebar CSS section, lines 66–193; main block, lines 195–199)

Remove all sidebar-related CSS classes, update `main` layout, and add topbar CSS.

- [ ] **Step 1: Remove sidebar CSS and update main block**

  Find in `templates/base.html` (the comment through the main block — verify exact whitespace with Read tool first):
  ```css
    /* Sidebar */
    .sidebar {
      position: fixed;
      top: 0;
      left: 0;
      width: 220px;
      height: 100vh;
      background: var(--bg-card);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      z-index: 100;
      overflow-y: auto;
    }

    .sidebar-logo {
      padding: 24px 20px 20px;
      border-bottom: 1px solid var(--border);
    }

    .sidebar-logo .logo-name {
      font-size: 18px;
      font-weight: 700;
      color: #ffffff;
      letter-spacing: 0.08em;
      display: block;
    }

    .sidebar-logo .logo-sub {
      font-size: 11px;
      color: var(--text-muted);
      letter-spacing: 0.06em;
      text-transform: uppercase;
      margin-top: 2px;
      display: block;
    }

    .sidebar-nav {
      flex: 1;
      padding: 12px 0;
    }

    .nav-section-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.1em;
      padding: 12px 20px 6px;
    }

    .sidebar-nav a {
      display: block;
      width: 100%;
      padding: 10px 20px;
      color: var(--text-muted);
      text-decoration: none;
      font-size: 14px;
      font-weight: 500;
      transition: background 0.15s, color 0.15s;
      border-left: 3px solid transparent;
    }

    .sidebar-nav a:hover {
      background: var(--bg-raised);
      color: var(--text);
      text-decoration: none;
    }

    .sidebar-nav a.active {
      border-left: 3px solid var(--accent);
      background: var(--bg-raised);
      color: var(--text);
    }

    .sidebar-admin {
      padding: 16px 20px;
      border-top: 1px solid var(--border);
    }

    .sidebar-admin .admin-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.1em;
      margin-bottom: 10px;
      display: block;
    }

    .sidebar-admin a,
    .sidebar-admin button {
      display: block;
      width: 100%;
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 500;
      border-radius: 6px;
      cursor: pointer;
      text-align: left;
      margin-bottom: 6px;
      transition: background 0.15s, color 0.15s;
    }

    .sidebar-admin a {
      color: var(--text-muted);
      text-decoration: none;
      background: transparent;
      border: 1px solid var(--border);
    }

    .sidebar-admin a:hover {
      background: var(--bg-raised);
      color: var(--text);
      text-decoration: none;
    }

    .sidebar-admin button {
      background: var(--accent);
      color: #fff;
      border: none;
      font-family: var(--font);
    }

    .sidebar-admin button:hover {
      background: var(--accent-hl);
    }

    /* Main content */
    main {
      margin-left: 220px;
      padding: 30px;
      min-height: 100vh;
    }
  ```
  Replace with:
  ```css
    /* Top bar */
    .topbar {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      height: 48px;
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      padding: 0 24px;
      gap: 0;
      z-index: 100;
    }

    .topbar-logo {
      display: flex;
      align-items: center;
      margin-right: 32px;
      flex-shrink: 0;
    }

    .topbar a {
      display: flex;
      align-items: center;
      height: 48px;
      padding: 0 14px;
      font-size: 13px;
      font-weight: 500;
      color: var(--text-muted);
      text-decoration: none;
      border-bottom: 2px solid transparent;
      white-space: nowrap;
      transition: color 0.15s;
    }

    .topbar a:hover {
      color: var(--text);
      text-decoration: none;
    }

    .topbar a.active {
      color: var(--text);
      border-bottom-color: var(--accent);
    }

    .topbar-scan-btn {
      font-size: 12px;
      font-weight: 500;
      color: var(--text-muted);
      background: transparent;
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 6px 12px;
      cursor: pointer;
      font-family: var(--font);
      transition: color 0.15s, border-color 0.15s;
      white-space: nowrap;
    }

    .topbar-scan-btn:hover {
      color: var(--accent-hl);
      border-color: var(--accent);
    }

    /* Main content */
    main {
      margin-left: 0;
      padding-top: 72px;
      padding-left: 30px;
      padding-right: 30px;
      padding-bottom: 30px;
      min-height: 100vh;
    }
  ```

---

### Task 4: Sidebar HTML → Topbar HTML

**Files:**
- Modify: `templates/base.html` (`<aside>` block, lines 485–509)

> **Before editing:** Run `Read templates/base.html` lines 483–511 to confirm exact indentation (2 spaces for `<aside>`, 4 for children).

- [ ] **Step 1: Replace the sidebar HTML with topbar HTML**

  > **Critical:** This step replaces a 25-line `<aside>` block. Use `Read` on `templates/base.html` lines 485–509 first, copy the exact content as the find-string, then replace it with the topbar HTML below.

  The find-string is the exact content of lines 485–509 (2-space indent for `<aside>`, 4-space for children, 6-space for grandchildren):
  ```
  (Read lines 485-509 from templates/base.html and use that as your find-string)
  ```

  Replace the entire `<aside>` block with this topbar (preserve the same leading 2-space indent on `<nav>` as `<aside>` had):
  ```html
    <nav class="topbar">
      <div class="topbar-logo">
        <span style="font-size:13px;font-weight:700;color:#ffffff;letter-spacing:0.05em;">VETTED</span>
        <span style="width:1px;height:16px;background:var(--border);margin:0 12px;display:inline-block;"></span>
        <span style="font-size:10px;font-weight:500;color:var(--text-muted);">Intelligence</span>
      </div>
      <a href="/" {% if request.url.path == '/' %}class="active"{% endif %}>Dashboard</a>
      <a href="/stocks" {% if request.url.path == '/stocks' %}class="active"{% endif %}>Stocks</a>
      <a href="/channels" {% if request.url.path.startswith('/channels') or request.url.path.startswith('/channel/') %}class="active"{% endif %}>Channels</a>
      <a href="/analyst" {% if request.url.path == '/analyst' %}class="active"{% endif %}>Analyst</a>
      <a href="/export" {% if request.url.path == '/export' %}class="active"{% endif %}>Export</a>
      <form method="POST" action="/admin/scan/all" style="margin-left:auto;margin-right:0;">
        <button type="submit" class="topbar-scan-btn">Scan All ↗</button>
      </form>
    </nav>
  ```

- [ ] **Step 2: Verify topbar in browser**

  Reload `http://localhost:8000`. Expected:
  - 48px horizontal top bar: VETTED · Intelligence on left, nav links, Scan All button on right
  - No sidebar. Main content has 72px top padding.
  - Dashboard link shows amber bottom underline (active state).

---

### Task 5: Component Styles Update

**Files:**
- Modify: `templates/base.html` (card, h1, table header, badges, lang badge, buttons, stat box, add pill CSS)

- [ ] **Step 1: Update card border-radius (8px → 6px)**

  Find:
  ```css
    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px;
    }
  ```
  Replace with:
  ```css
    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 20px;
    }
  ```

- [ ] **Step 2: Update page h1 size and weight**

  Find:
  ```css
    .page-header h1 {
      font-size: 26px;
      font-weight: 700;
      margin: 0 0 6px 0;
      color: #ffffff;
    }
  ```
  Replace with:
  ```css
    .page-header h1 {
      font-size: 24px;
      font-weight: 600;
      margin: 0 0 6px 0;
      color: #ffffff;
    }
  ```

- [ ] **Step 3: Update table header — bg-raised → bg-card + border-bottom**

  Find:
  ```css
    thead th {
      background: var(--bg-raised);
      color: var(--text-muted);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      padding: 10px 14px;
      text-align: left;
      white-space: nowrap;
    }
  ```
  Replace with:
  ```css
    thead th {
      background: var(--bg-card);
      border-bottom: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      padding: 10px 14px;
      text-align: left;
      white-space: nowrap;
    }
  ```

- [ ] **Step 4: Update badge border-radius and background alphas**

  Find:
  ```css
    .badge {
      display: inline-block;
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      border-radius: 4px;
      letter-spacing: 0.04em;
    }

    .bullish {
      background: rgba(16, 185, 129, 0.15);
      color: var(--positive);
    }

    .bearish {
      background: rgba(239, 68, 68, 0.15);
      color: var(--negative);
    }

    .neutral {
      background: rgba(245, 158, 11, 0.15);
      color: var(--neutral);
    }
  ```
  Replace with:
  ```css
    .badge {
      display: inline-block;
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      border-radius: 3px;
      letter-spacing: 0.04em;
    }

    .bullish {
      background: rgba(16, 185, 129, 0.12);
      color: var(--positive);
    }

    .bearish {
      background: rgba(239, 68, 68, 0.12);
      color: var(--negative);
    }

    .neutral {
      background: rgba(217, 119, 6, 0.12);
      color: var(--neutral);
    }
  ```

- [ ] **Step 5: Update language badge — blue → amber**

  Find:
  ```css
    /* Language badge */
    .lang-badge {
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: 700;
      border-radius: 4px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      background: rgba(37, 99, 235, 0.2);
      color: var(--accent-hl);
      border: 1px solid rgba(37, 99, 235, 0.3);
    }
  ```
  Replace with:
  ```css
    /* Language badge */
    .lang-badge {
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: 700;
      border-radius: 3px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      background: rgba(217, 119, 6, 0.15);
      color: var(--accent-hl);
    }
  ```

- [ ] **Step 6: Update primary button — amber background, black text**

  Find:
  ```css
    .btn-primary {
      background: var(--accent);
      color: #fff;
    }

    .btn-primary:hover {
      background: var(--accent-hl);
      text-decoration: none;
      color: #fff;
    }
  ```
  Replace with:
  ```css
    .btn-primary {
      background: var(--accent);
      color: #000;
    }

    .btn-primary:hover {
      background: #b45309;
      text-decoration: none;
      color: #000;
    }
  ```

- [ ] **Step 7: Update secondary button — transparent background**

  Find:
  ```css
    .btn-secondary {
      background: var(--bg-raised);
      color: var(--text);
      border: 1px solid var(--border);
    }

    .btn-secondary:hover {
      background: var(--border);
      text-decoration: none;
      color: var(--text);
    }
  ```
  Replace with:
  ```css
    .btn-secondary {
      background: transparent;
      color: var(--text);
      border: 1px solid var(--border);
    }

    .btn-secondary:hover {
      border-color: #404040;
      background: var(--bg-raised);
      text-decoration: none;
      color: var(--text);
    }
  ```

- [ ] **Step 8: Add pill CSS immediately after .btn-secondary:hover block**

  Find (the closing of btn-secondary:hover, using surrounding context as anchor):
  ```css
    .btn-secondary:hover {
      border-color: #404040;
      background: var(--bg-raised);
      text-decoration: none;
      color: var(--text);
    }

    /* Forms */
  ```
  Replace with:
  ```css
    .btn-secondary:hover {
      border-color: #404040;
      background: var(--bg-raised);
      text-decoration: none;
      color: var(--text);
    }

    /* Filter pills */
    .pill {
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text-muted);
      border-radius: 5px;
      padding: 5px 12px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      font-family: var(--font);
      transition: border-color 0.15s, color 0.15s;
    }

    .pill.active {
      border-color: var(--accent);
      color: var(--accent-hl);
      background: transparent;
    }

    /* Forms */
  ```

- [ ] **Step 9: Remove stat box background and border**

  Find:
  ```css
    .stat-box {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px 20px;
    }
  ```
  Replace with:
  ```css
    .stat-box {
      padding: 18px 20px;
    }
  ```

- [ ] **Step 10: Verify components in browser**

  Reload `http://localhost:8000`. Check:
  - KPI stat boxes: bare numbers, no card border/background around them
  - Badges: amber neutral, green bullish, red bearish, smaller border-radius
  - Lang badges: amber tint, no blue
  - Navigate to `/channels` to see a primary button with `.btn-primary`: amber background, black text
  - Filter pills appear in Task 6 (home.html) — check after that task

- [ ] **Step 11: Commit**

  ```bash
  cd /Users/mitjawilms/DeInfluencer
  git add templates/base.html
  git commit -m "redesign: base.html — Geist font, charcoal palette, topbar nav, updated components"
  ```

---

## Chunk 2: home.html, analyst.html, stock.html

### Task 6: home.html — Filter Pills

**Files:**
- Modify: `templates/home.html` (filter bar buttons, lines 73–133; leaderboard buttons, lines 239–247; leaderboard JS, lines 370–374)

Convert four filter groups and leaderboard day buttons from inline Jinja2 styles to `.pill` / `.pill.active` CSS classes defined in base.html.

- [ ] **Step 1: Update Period filter buttons**

  Find in `templates/home.html`:
  ```jinja
        {% for label, val in [('7d', 7), ('30d', 30), ('90d', 90), ('1y', 365), ('All', 0)] %}
        <button onclick="setParam('days', {{ val }})"
          style="padding:4px 10px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--font);
                 {% if days == val %}background:var(--accent);color:#fff;border:1px solid var(--accent);
                 {% else %}background:var(--bg-raised);color:var(--text-muted);border:1px solid var(--border);{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
  ```
  Replace with:
  ```jinja
        {% for label, val in [('7d', 7), ('30d', 30), ('90d', 90), ('1y', 365), ('All', 0)] %}
        <button onclick="setParam('days', {{ val }})"
          class="pill {% if days == val %}active{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
  ```

- [ ] **Step 2: Update Min Channels filter buttons**

  Find in `templates/home.html`:
  ```jinja
        {% for val in [1, 2, 3, 4, 5] %}
        <button onclick="setParam('min_channels', {{ val }})"
          style="padding:4px 10px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--font);
                 {% if min_channels == val %}background:var(--accent);color:#fff;border:1px solid var(--accent);
                 {% else %}background:var(--bg-raised);color:var(--text-muted);border:1px solid var(--border);{% endif %}">
          {{ val }}{% if val == 5 %}+{% endif %}
        </button>
        {% endfor %}
  ```
  Replace with:
  ```jinja
        {% for val in [1, 2, 3, 4, 5] %}
        <button onclick="setParam('min_channels', {{ val }})"
          class="pill {% if min_channels == val %}active{% endif %}">
          {{ val }}{% if val == 5 %}+{% endif %}
        </button>
        {% endfor %}
  ```

- [ ] **Step 3: Update Language filter buttons**

  Find in `templates/home.html`:
  ```jinja
        {% for label, val in [('All', 'all'), ('EN', 'en'), ('DE', 'de'), ('ES', 'es')] %}
        <button onclick="setParam('lang', '{{ val }}')"
          style="padding:4px 10px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--font);
                 {% if lang == val %}background:var(--accent);color:#fff;border:1px solid var(--accent);
                 {% else %}background:var(--bg-raised);color:var(--text-muted);border:1px solid var(--border);{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
  ```
  Replace with:
  ```jinja
        {% for label, val in [('All', 'all'), ('EN', 'en'), ('DE', 'de'), ('ES', 'es')] %}
        <button onclick="setParam('lang', '{{ val }}')"
          class="pill {% if lang == val %}active{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
  ```

- [ ] **Step 4: Update Sentiment filter buttons**

  Find in `templates/home.html`:
  ```jinja
        {% for label, val in [('All', 'all'), ('Bullish', 'bullish'), ('Bearish', 'bearish'), ('Neutral', 'neutral')] %}
        <button onclick="setParam('sentiment', '{{ val }}')"
          style="padding:4px 10px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;font-family:var(--font);
                 {% if sentiment == val %}background:var(--accent);color:#fff;border:1px solid var(--accent);
                 {% else %}background:var(--bg-raised);color:var(--text-muted);border:1px solid var(--border);{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
  ```
  Replace with:
  ```jinja
        {% for label, val in [('All', 'all'), ('Bullish', 'bullish'), ('Bearish', 'bearish'), ('Neutral', 'neutral')] %}
        <button onclick="setParam('sentiment', '{{ val }}')"
          class="pill {% if sentiment == val %}active{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
  ```

- [ ] **Step 5: Update Leaderboard day buttons (HTML)**

  Find in `templates/home.html` (read lines 238–245 first to confirm exact whitespace):
  ```jinja
        {% for label, val in [('7d', 7), ('30d', 30), ('90d', 90), ('All', 0)] %}
        <button class="lb-day-btn" data-days="{{ val }}" onclick="loadLeaderboard({{ val }})"
          style="padding:3px 9px;border-radius:4px;font-size:11px;font-weight:600;cursor:pointer;font-family:var(--font);
                 {% if days == val %}background:var(--accent);color:#fff;border:1px solid var(--accent);
                 {% else %}background:var(--bg-raised);color:var(--text-muted);border:1px solid var(--border);{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
  ```
  Replace with:
  ```jinja
        {% for label, val in [('7d', 7), ('30d', 30), ('90d', 90), ('All', 0)] %}
        <button class="lb-day-btn pill {% if days == val %}active{% endif %}" data-days="{{ val }}" onclick="loadLeaderboard({{ val }})">
          {{ label }}
        </button>
        {% endfor %}
  ```

- [ ] **Step 6: Update Leaderboard JS to toggle .active class**

  Find in `templates/home.html`:
  ```js
    document.querySelectorAll('.lb-day-btn').forEach(function(btn) {
      const active = parseInt(btn.dataset.days) === days;
      btn.style.background = active ? 'var(--accent)' : 'var(--bg-raised)';
      btn.style.color = active ? '#fff' : 'var(--text-muted)';
      btn.style.borderColor = active ? 'var(--accent)' : 'var(--border)';
    });
  ```
  Replace with:
  ```js
    document.querySelectorAll('.lb-day-btn').forEach(function(btn) {
      btn.classList.toggle('active', parseInt(btn.dataset.days) === days);
    });
  ```

- [ ] **Step 7: Verify pills in browser**

  Reload `http://localhost:8000`. Check filter bar:
  - Active pill: amber border + amber text, transparent background, no fill
  - Inactive pill: gray border, gray text, transparent background
  - Click a different period — active state follows
  - Leaderboard day buttons: same pill appearance, clicking updates active state via JS

- [ ] **Step 8: Commit**

  ```bash
  cd /Users/mitjawilms/DeInfluencer
  git add templates/home.html
  git commit -m "redesign: home.html — pill classes for filter and leaderboard buttons"
  ```

---

### Task 7: analyst.html — Chart.js Color Updates

**Files:**
- Modify: `templates/analyst.html` (chart scales and legend, lines 391–439)

Current hardcoded chart colors: ticks `#6b80a8`, grid `#1e2d4a`, legend text `#e8eeff`.
Target: ticks `#737373`, grid `#1c1c1c`, legend text `#e5e5e5`.

> **Note:** The analyst page filter toggles (Sentiment, Recommendation, Language radio/checkbox controls at lines 53–96) use inline `style=` attributes. Their active state uses `background:var(--accent)` which automatically becomes amber via the CSS variable update in Task 2. No code changes needed for those controls — they inherit amber correctly.

- [ ] **Step 1: Update legend label color**

  Find in `templates/analyst.html`:
  ```js
      plugins: { legend: { labels: { color: '#e8eeff' } } },
  ```
  Replace with:
  ```js
      plugins: { legend: { labels: { color: '#e5e5e5' } } },
  ```

- [ ] **Step 2: Update opts scales block (ticks and grid colors)**

  Find in `templates/analyst.html`:
  ```js
        x: { ticks: { color: '#6b80a8', maxRotation: 60, autoSkip: true }, grid: { color: '#1e2d4a' } },
        y: { ticks: { color: '#6b80a8' }, grid: { color: '#1e2d4a' }, beginAtZero: true }
  ```
  Replace with:
  ```js
        x: { ticks: { color: '#737373', maxRotation: 60, autoSkip: true }, grid: { color: '#1c1c1c' } },
        y: { ticks: { color: '#737373' }, grid: { color: '#1c1c1c' }, beginAtZero: true }
  ```

- [ ] **Step 3: Update scatter axis title colors**

  Find in `templates/analyst.html`:
  ```js
          scales: { x: Object.assign({}, opts.scales.x, { title: { display: true, text: 'Confidence %', color: '#6b80a8' } }),
                    y: Object.assign({}, opts.scales.y, { title: { display: true, text: '30d ROI %', color: '#6b80a8' } }) }
  ```
  Replace with:
  ```js
          scales: { x: Object.assign({}, opts.scales.x, { title: { display: true, text: 'Confidence %', color: '#737373' } }),
                    y: Object.assign({}, opts.scales.y, { title: { display: true, text: '30d ROI %', color: '#737373' } }) }
  ```

- [ ] **Step 4: Verify analyst charts in browser**

  Navigate to `http://localhost:8000/analyst`.
  - Timeline chart (default): dark grid lines (#1c1c1c), neutral gray tick labels (#737373)
  - Switch chart mode to "ROI Scatter": axis title labels also use #737373
  - Sentiment/Recommendation/Language filter controls: amber fill on active (inherited via CSS variable)

- [ ] **Step 5: Commit**

  ```bash
  cd /Users/mitjawilms/DeInfluencer
  git add templates/analyst.html
  git commit -m "redesign: analyst.html — Chart.js grid, tick, and legend color updates"
  ```

---

### Task 8: stock.html — Chart.js Color Updates

**Files:**
- Modify: `templates/stock.html` (ROI chart scales lines 117–118; mention chart baseOptions lines 189–198; legend line 115 and 186)

Current colors: ticks `#6b80a8`, grid `#1e2d4a`, legend `#e8eeff`.
Target: ticks `#737373`, grid `#1c1c1c`, legend `#e5e5e5`.

> **Out of scope:** The time-range filter links at lines 27–35 use inline Jinja2 styles with `var(--accent)` / `var(--bg-card)` / `var(--border)` — these inherit amber automatically via the CSS variable update in Task 2. No code change is needed for those controls.

- [ ] **Step 1: Update ROI chart legend label color**

  Find in `templates/stock.html` (inside the `roiChart` script block):
  ```js
          plugins: { legend: { labels: { color: '#e8eeff' } } },
          scales: {
            x: { ticks: { color: '#6b80a8' }, grid: { color: '#1e2d4a' } },
            y: { ticks: { color: '#6b80a8', callback: v => v + '%' }, grid: { color: '#1e2d4a' } }
  ```
  Replace with:
  ```js
          plugins: { legend: { labels: { color: '#e5e5e5' } } },
          scales: {
            x: { ticks: { color: '#737373' }, grid: { color: '#1c1c1c' } },
            y: { ticks: { color: '#737373', callback: v => v + '%' }, grid: { color: '#1c1c1c' } }
  ```

- [ ] **Step 2: Update mention history chart legend and scales**

  Find in `templates/stock.html` (inside the `baseOptions` object for the mention chart):
  ```js
      plugins: { legend: { labels: { color: '#e8eeff' } } },
      scales: {
        x: {
          ticks: {
            color: '#6b80a8',
            maxRotation: 75,
            minRotation: 45,
            autoSkip: false,
            callback: function(value) { return fmtDate(this.getLabelForValue(value)); }
          },
          grid: { color: '#1e2d4a' }
        },
        y: { ticks: { color: '#6b80a8', stepSize: 1 }, grid: { color: '#1e2d4a' }, beginAtZero: true }
  ```
  Replace with:
  ```js
      plugins: { legend: { labels: { color: '#e5e5e5' } } },
      scales: {
        x: {
          ticks: {
            color: '#737373',
            maxRotation: 75,
            minRotation: 45,
            autoSkip: false,
            callback: function(value) { return fmtDate(this.getLabelForValue(value)); }
          },
          grid: { color: '#1c1c1c' }
        },
        y: { ticks: { color: '#737373', stepSize: 1 }, grid: { color: '#1c1c1c' }, beginAtZero: true }
  ```

- [ ] **Step 3: Verify charts in browser**

  Navigate to any stock page with data, e.g. `http://localhost:8000/stock/NVDA`.
  Expected:
  - ROI chart and mention history chart: dark grid lines, neutral gray ticks
  - Time-range buttons (30d / 90d / 1y / All): amber active state (via CSS variable inheritance, no explicit change)

- [ ] **Step 4: Commit**

  ```bash
  cd /Users/mitjawilms/DeInfluencer
  git add templates/stock.html
  git commit -m "redesign: stock.html — Chart.js grid, tick, and legend color updates"
  ```

---

### Task 9: Smoke Test — All Pages

No code changes. Manual browser verification only.

- [ ] **Step 1: Start server**

  ```bash
  cd /Users/mitjawilms/DeInfluencer
  uvicorn main:app --host 0.0.0.0 --port 8000
  ```
  If port 8000 is busy: `lsof -ti :8000 | xargs kill -9 && uvicorn main:app --host 0.0.0.0 --port 8000`

- [ ] **Step 2: Dashboard (`/`)**

  Expected:
  - Top bar visible, no sidebar
  - Body background near-black charcoal, no blue tint
  - Geist font (or system-ui fallback) — no Inter
  - KPI stat boxes: bare numbers, no card border/background
  - Filter pills: amber border+text on active, transparent inactive
  - Consensus table renders, sorting works
  - Leaderboard renders, day toggle works (amber active pill state)

- [ ] **Step 3: Stocks (`/stocks`)**

  Expected:
  - Stocks link in topbar shows amber underline
  - Table renders with updated header style
  - Lang badges: amber, not blue

- [ ] **Step 4: Channels (`/channels`)**

  Expected:
  - Channels link in topbar shows amber underline
  - Lang badges: amber
  - Primary button (Add Channel form) visible: amber background, black text

- [ ] **Step 5: Stock detail (any tracked ticker)**

  Navigate to a stock with data. Expected:
  - ROI and mention history charts: dark grid lines, gray tick labels
  - Time-range buttons: amber active state
  - Context modal opens and closes (click a context snippet)
  - Badge colors correct (green bullish, red bearish, amber neutral, 3px radius)

- [ ] **Step 6: Channel detail (any tracked channel)**

  Navigate to `/channel/<any-id>`. Expected:
  - Channels link active in topbar
  - Page renders without errors

- [ ] **Step 7: Analyst (`/analyst`)**

  Expected:
  - Analyst link active in topbar
  - Timeline chart: dark grid, gray ticks
  - Switch to ROI Scatter chart mode: axis title labels visible in gray
  - Sentiment/Recommendation filter controls: active option shows amber fill (inherited via CSS var)
  - Language checkboxes: amber on checked
  - Table renders for all four table modes (By Ticker, By Channel, By Date, Raw Mentions)

- [ ] **Step 8: Export (`/export`)**

  Expected:
  - Export link active in topbar
  - Page renders without errors

- [ ] **Step 9: Scan All button**

  From any page, click the "Scan All ↗" button in the top-right of the topbar.
  Expected: redirects back to `/` with no error page.

- [ ] **Step 10: Empty data states**

  Apply filters that produce no results (e.g., dashboard with Min Channels = 5, Period = 7d).
  Expected: empty state message renders correctly, no layout breaks, table doesn't collapse or overflow.

- [ ] **Step 11: Verify spec success criteria**

  Walk through spec section 7:
  - [ ] No Inter font loaded or rendered
  - [ ] No blue-tinted backgrounds (`#070d1a`, `#0f1829`, `#162033` family gone)
  - [ ] No blue accent (`#2563eb`, `#3b82f6` family gone)
  - [ ] Top bar present on all pages, sidebar absent
  - [ ] Amber accent on: active nav link, active filter pills, primary buttons, focus rings, lang badges
  - [ ] All existing functionality works: filters, table sorts, chart toggles, export downloads, scan trigger
