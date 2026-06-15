# UI Redesign — Design Spec

**Date:** 2026-03-15
**Project:** Vetted (private finance intelligence dashboard)
**Scope:** Visual redesign only — palette, typography, navigation structure, component styling. No changes to Jinja2 template logic, routes, data, or JS behavior.

**Direction:** Linear/Vercel dark aesthetic. Neutral charcoal dark (not blue-tinted), Geist Sans font, restrained amber accent used as a signal color (not decoration), top navigation bar replacing fixed sidebar.

---

## 1. Foundation — Palette & Typography

### CSS Custom Properties (replacing all current vars in `base.html`)

| Variable | New value | Old value | Notes |
|---|---|---|---|
| `--bg` | `#0c0c0c` | `#070d1a` | Near-black, no blue tint |
| `--bg-card` | `#141414` | `#0f1829` | Card surface |
| `--bg-raised` | `#1c1c1c` | `#162033` | Hover states, table headers |
| `--border` | `#262626` | `#1e2d4a` | Warm-gray, not blue-tinted |
| `--accent` | `#d97706` | `#2563eb` | Amber-600 — restrained signal color |
| `--accent-hl` | `#f59e0b` | `#3b82f6` | Amber-400 — hover/active |
| `--text` | `#e5e5e5` | `#e8eeff` | Slightly warm white |
| `--text-muted` | `#737373` | `#6b80a8` | Neutral gray, not blue-gray |
| `--positive` | `#10b981` | `#10b981` | Unchanged |
| `--negative` | `#ef4444` | `#ef4444` | Unchanged |
| `--neutral` | `#d97706` | `#f59e0b` | Amber does double duty: brand + neutral sentiment |
| `--font` | `'Geist', system-ui, sans-serif` | `'Inter', system-ui, sans-serif` | |

### Typography

- **Font:** Geist Sans — load from Google Fonts:
  ```html
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  ```
- **Body:** 14px / 400 — unchanged
- **Card titles:** 11px / 600 / uppercase / `letter-spacing: 0.08em` — unchanged rhythm
- **Page headers (`h1`):** 24px / 600 (reduced from 26px / 700)

### Spacing
No changes to spacing, padding, or grid gap values.

---

## 2. Navigation — Sidebar → Top Bar

The fixed 220px left sidebar is removed entirely. Replaced with a 48px horizontal top bar.

### Top bar structure
```
[VETTED · Intelligence]   Dashboard  Stocks  Channels  Analyst  Export   [Scan All ↗]
```

### Styles
```css
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
```

- **Logo** (left): `VETTED` in 13px/700 white + a thin vertical rule (`1px solid var(--border)`) + `Intelligence` in 10px/500 muted. Separated from nav links by a gap.
- **Nav links:** 13px/500, `color: var(--text-muted)` default, `color: var(--text)` on hover, active state: `color: var(--text)` + `border-bottom: 2px solid var(--accent)` flush to bottom of bar
- **Scan All button** (right-aligned via `margin-left: auto`): 12px/500, `border: 1px solid var(--border)`, transparent background, amber text on hover — quiet, not a CTA
- **Active detection:** Jinja2 `{% if request.url.path == '/' %}class="active"{% endif %}` pattern — unchanged from sidebar

### Main content area
```css
main {
  margin-left: 0;        /* was 220px */
  padding-top: 72px;     /* 48px bar + 24px breathing room */
  padding-left: 30px;
  padding-right: 30px;
  padding-bottom: 30px;
}
```

### Removed
- `.sidebar`, `.sidebar-logo`, `.sidebar-nav`, `.sidebar-admin` CSS classes — deleted
- Sidebar HTML `<aside>` block in `base.html` — replaced with `.topbar`
- "Add Channel" button from sidebar admin — already exists as a form on `/channels` page; the sidebar button was redundant

---

## 3. Components

### Cards
```css
.card {
  background: var(--bg-card);      /* #141414 */
  border: 1px solid var(--border); /* #262626 */
  border-radius: 6px;              /* was 8px */
  padding: 20px;
}
```

### Tables
- **Header:** `background: var(--bg-card)` + `border-bottom: 1px solid var(--border)` — no fill color change, just a bottom rule
- **Row hover:** `background: var(--bg-raised)` — unchanged behavior
- **No zebra striping** (there is none currently — confirm and keep it that way)

### Badges
- Bullish: `rgba(16, 185, 129, 0.12)` background, `var(--positive)` text — unchanged
- Bearish: `rgba(239, 68, 68, 0.12)` background, `var(--negative)` text — unchanged
- Neutral: `rgba(217, 119, 6, 0.12)` background, `var(--neutral)` text — amber replaces yellow
- Lang badge: `rgba(217, 119, 6, 0.15)` background, `var(--accent-hl)` text — amber replaces blue
- Border-radius: `3px` (was `4px`)

### Buttons
```css
.btn-primary {
  background: var(--accent);   /* #d97706 */
  color: #000;                 /* black text on amber — higher contrast */
}
.btn-primary:hover {
  background: #b45309;         /* amber-700 */
}
.btn-secondary {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
}
.btn-secondary:hover {
  border-color: #404040;
  background: var(--bg-raised);
}
```

### Stat boxes (KPI cards on home page)
- Remove `.stat-box` card border and background entirely
- Values sit as bare numbers on the page background, separated by grid spacing only
- Label and value styling unchanged

```css
.stat-box {
  padding: 18px 20px;
  /* background and border removed */
}
```

### Filter pills (home page Period / Min Channels / Lang / Sentiment toggles)
```css
/* Inactive */
.pill {
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-muted);
  border-radius: 5px;
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 500;
}
/* Active */
.pill.active {
  border-color: var(--accent);
  color: var(--accent-hl);
  background: transparent;  /* no fill — amber border + text only */
}
```

### Forms (inputs, selects)
- `background: var(--bg-raised)` — unchanged
- `border: 1px solid var(--border)` — color updates with variable
- `focus: border-color: var(--accent)` — amber focus ring instead of blue

---

## 4. Page-Specific Notes

### Home (Dashboard)
- KPI stat boxes: remove card borders (per Section 3 stat box change)
- Filter pills: apply new pill styling (per Section 3)
- Leaderboard day buttons: same pill treatment as filter pills

### Analyst & Stock pages — Chart.js color updates
In the JS chart initialization on both pages, update grid and tick colors:
```js
grid: { color: '#1c1c1c' }   // was rgba(255,255,255,0.05) or similar
ticks: { color: '#737373' }  // was rgba(255,255,255,0.4) or similar
```

### All other pages (Stocks, Channels, Channel detail, Export)
No structural or targeted changes — global CSS variable and component updates cover everything.

---

## 5. What Does NOT Change

- All Jinja2 template logic and data bindings
- All FastAPI routes and Python code
- All JavaScript behavior (sorting, filter navigation, chart data fetching)
- Page layouts, grid structures, and section ordering
- Sentiment signal colors (`--positive` green, `--negative` red)
- `--neutral` amber color value is the same as `--accent` — this is intentional double-duty
- Scrollbar styling (minor, keep as-is)

---

## 6. Implementation Scope

All CSS changes live in `base.html`'s `<style>` block. No separate CSS file is introduced. Page-specific JS chart color changes are made in `analyst.html` and `stock.html` only.

**Files touched:**
- `templates/base.html` — CSS variables, font, sidebar → topbar, component styles
- `templates/home.html` — stat box border removal, filter pill class names
- `templates/analyst.html` — Chart.js grid/tick colors
- `templates/stock.html` — Chart.js grid/tick colors

**Files not touched:** `main.py`, `db_manager.py`, `brain.py`, `scanner.py`, all other Python files, `channel.html`, `channels.html`, `stocks.html`, `export.html`

---

## 7. Success Criteria

- No Inter font loaded or rendered anywhere
- No blue-tinted background colors (`#070d1a`, `#0f1829`, `#162033` family gone)
- No blue accent (`#2563eb`, `#3b82f6` family gone)
- Top bar present on all pages, sidebar absent
- Amber accent present on: active nav link, active filter pills, primary buttons, focus rings, lang badges
- All existing functionality works: filters, table sorts, chart toggles, export downloads, scan triggers
- Pages render correctly with empty data states (no layout breaks when tables are empty)
