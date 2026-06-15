# Versioned Eval Config Editing — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add full version history to eval configs — every edit creates a new version; users can view, switch, and edit any version; built-in configs fork into custom configs on first edit.

**Architecture:** Migrate `custom_store.py` to a versioned file format (`versions: [...]` array per config), add an explicit edit route, update the runner to use stored version numbers directly, then update the UI with a hover popup action menu and an edit panel with a version selector.

**Tech Stack:** Python (FastAPI, plain JSON file storage), Jinja2 templates, vanilla JS.

---

## File Map

| File | Change |
|------|--------|
| `evals/custom_store.py` | Add `_migrate()`, `add_version()`, `get_version()`, `list_versions()`; update `save_config()`, `get_config()`, `list_configs()` |
| `evals/executor.py` | `config_to_runnable()`: read pass params from `versions[-1]`, expose `_version` |
| `evals/runner.py` | `run_as_task()`: use `config["_version"]` for custom configs instead of version_registry hash |
| `main.py` | Fix `create_eval_config` (add three_pass support); add `POST /admin/evals/builtin-config/{name}/fork`; add `POST /admin/evals/custom-config/{name}/edit`; update `duplicate_eval_config` to copy latest version; update `admin_evals` to pass version data to template |
| `templates/evals.html` | Replace inline action buttons with hover popup; add Edit panel version selector; add "Load defaults" per pass; add `custom-configs-json` script tag |

---

## Task 1: Migrate custom_store.py to versioned format

**Files:**
- Modify: `evals/custom_store.py`

The current flat format is `{name, description, pipeline, pass1, pass2?, pass3?, created_at}`.
The new format adds a `versions` array. All reads must transparently upgrade old files.

- [ ] **Step 1: Add `_migrate(data)` helper**

Add this to `evals/custom_store.py` right before `list_configs()`:

```python
def _migrate(data: dict) -> dict:
    """Upgrade old flat-format configs to versioned format in-place."""
    if "versions" in data:
        return data
    from datetime import datetime
    pass_data = {k: data.pop(k) for k in ("pass1", "pass2", "pass3") if k in data}
    pass_data["version"] = 1
    pass_data["created_at"] = data.get("created_at", datetime.now().isoformat())
    data["versions"] = [pass_data]
    return data
```

- [ ] **Step 2: Apply `_migrate` in `get_config()` and `list_configs()`**

Update `get_config`:
```python
def get_config(name: str) -> dict | None:
    p = _path(name)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        data = json.load(fh)
    data = _migrate(data)
    # Persist migration so we don't re-migrate every read
    with open(p, "w") as fh:
        json.dump(data, fh, indent=2)
    return data
```

Update `list_configs` — after loading each file, call `_migrate` and re-save if needed:
```python
def list_configs() -> list:
    os.makedirs(_DIR, exist_ok=True)
    files = glob.glob(os.path.join(_DIR, "*.json"))
    configs = []
    for f in files:
        if os.path.basename(f).startswith("_"):
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            if "versions" not in data:
                data = _migrate(data)
                with open(f, "w") as fh:
                    json.dump(data, fh, indent=2)
            configs.append(data)
        except Exception:
            pass
    return sorted(configs, key=lambda c: c.get("created_at", ""), reverse=True)
```

- [ ] **Step 3: Update `save_config()` to write versioned format**

New configs start with v1. `save_config` is only called for brand-new configs (create); edits use `add_version`.

```python
def save_config(data: dict) -> str:
    """Persist a NEW custom config as v1. Returns slug."""
    from datetime import datetime
    os.makedirs(_DIR, exist_ok=True)
    slug = _slug(data["name"])
    now = datetime.now().isoformat()
    pass_keys = [k for k in ("pass1", "pass2", "pass3") if k in data]
    pass_data = {k: data[k] for k in pass_keys}
    pass_data["version"] = 1
    pass_data["created_at"] = now
    versioned = {
        "name":        slug,
        "description": data.get("description", slug),
        "pipeline":    data.get("pipeline", "two_pass"),
        "created_at":  now,
        "versions":    [pass_data],
    }
    with open(_path(slug), "w") as fh:
        json.dump(versioned, fh, indent=2)
    return slug
```

- [ ] **Step 4: Add `add_version()`, `get_version()`, `list_versions()` helpers**

```python
def add_version(name: str, pass_data: dict) -> int:
    """
    Append a new version to an existing config.
    pass_data must contain pass1 (and pass2/pass3 if the pipeline uses them).
    Returns the new version number.
    """
    from datetime import datetime
    cfg = get_config(name)
    if cfg is None:
        raise ValueError(f"Config '{name}' not found")
    new_v = max(v["version"] for v in cfg["versions"]) + 1
    entry = dict(pass_data)
    entry["version"] = new_v
    entry["created_at"] = datetime.now().isoformat()
    cfg["versions"].append(entry)
    with open(_path(name), "w") as fh:
        json.dump(cfg, fh, indent=2)
    return new_v


def get_version(name: str, version_num: int) -> dict | None:
    """Return a specific version entry (includes pass1/pass2/pass3/version/created_at)."""
    cfg = get_config(name)
    if cfg is None:
        return None
    for v in cfg["versions"]:
        if v["version"] == version_num:
            return v
    return None


def list_versions(name: str) -> list:
    """Return all versions for a config, sorted ascending by version number."""
    cfg = get_config(name)
    if cfg is None:
        return []
    return sorted(cfg["versions"], key=lambda v: v["version"])


def latest_version(name: str) -> dict | None:
    """Return the highest-version entry for a config."""
    versions = list_versions(name)
    return versions[-1] if versions else None
```

- [ ] **Step 5: Manual smoke test**

Start the server (`uvicorn main:app --reload`) and navigate to `/admin/evals`.
Check terminal — no errors. The evals page should still load correctly.
If you have an existing custom config, check that it still appears in the table.

- [ ] **Step 6: Commit**

```bash
git add evals/custom_store.py
git commit -m "feat(evals): migrate custom config storage to versioned format"
```

---

## Task 2: Update executor.py to use versioned pass params

**Files:**
- Modify: `evals/executor.py`

`config_to_runnable()` currently passes the entire config dict as `_raw`. After migration, pass params live in `config["versions"][-1]`, not at the top level.

- [ ] **Step 1: Update `config_to_runnable()`**

```python
def config_to_runnable(config_dict: dict) -> dict:
    """Convert a stored versioned custom config dict into a runner-compatible config dict."""
    # Get pass params from the latest version
    latest = sorted(config_dict["versions"], key=lambda v: v["version"])[-1]
    # Build a flat config for build_run_fn (it expects pass1/pass2/pass3 at top level)
    runnable_cfg = {
        "pipeline": config_dict["pipeline"],
        "pass1":    latest.get("pass1"),
        "pass2":    latest.get("pass2"),
        "pass3":    latest.get("pass3"),
    }
    return {
        "name":        config_dict["name"],
        "description": config_dict.get("description", config_dict["name"]),
        "run_fn":      build_run_fn(runnable_cfg),
        "_raw":        runnable_cfg,       # for snapshot storage in runner
        "_version":    latest["version"],  # explicit version number for runner
    }
```

- [ ] **Step 2: Smoke test**

Navigate to `/admin/evals`, select a custom config, click "Run Selected".
Check terminal — run should complete without errors.

- [ ] **Step 3: Commit**

```bash
git add evals/executor.py
git commit -m "feat(evals): executor reads pass params from latest version"
```

---

## Task 3: Update runner.py to use explicit version numbers

**Files:**
- Modify: `evals/runner.py`

Currently `run_as_task` calls `vr.resolve_version(name, cfg_hash)` for all configs. For custom configs, the explicit version stored in the file is now authoritative — no need to hash.

- [ ] **Step 1: Update version resolution in `run_as_task()`**

Find this block in `run_as_task()`:
```python
if "_raw" in config:
    snapshots[name] = config["_raw"]
    cfg_hash = vr.compute_hash_custom(config["_raw"])
else:
    cfg_hash = vr.compute_hash_builtin(config["run_fn"])

versions[name] = vr.resolve_version(name, cfg_hash)
```

Replace with:
```python
if "_raw" in config:
    snapshots[name] = config["_raw"]
    if "_version" in config:
        # Custom config with explicit version — use it directly
        versions[name] = config["_version"]
    else:
        # Fallback: hash-based (shouldn't happen after migration)
        cfg_hash = vr.compute_hash_custom(config["_raw"])
        versions[name] = vr.resolve_version(name, cfg_hash)
else:
    # Built-in config — hash-based version tracking unchanged
    cfg_hash = vr.compute_hash_builtin(config["run_fn"])
    versions[name] = vr.resolve_version(name, cfg_hash)
```

- [ ] **Step 2: Smoke test**

Run an eval. Check the saved result file in `evals/results/` — the `_meta.versions` dict should show the correct version number for the config you ran.

- [ ] **Step 3: Commit**

```bash
git add evals/runner.py
git commit -m "feat(evals): runner uses explicit version number from custom config"
```

---

## Task 4: Add fork + edit routes to main.py

**Files:**
- Modify: `main.py`

Four changes:
1. Fix `create_eval_config` to accept `three_pass`
2. Add `POST /admin/evals/builtin-config/{name}/fork` — creates a custom config from a built-in's defaults
3. Add `POST /admin/evals/custom-config/{name}/edit` — appends a new version
4. Fix `duplicate_eval_config` to carry the full versions array

- [ ] **Step 1: Fix `create_eval_config` — add three_pass and pass3**

Find the validation line:
```python
if pipeline not in ("two_pass", "single_pass"):
    raise HTTPException(status_code=400, detail="pipeline must be two_pass or single_pass")
```

Replace with:
```python
if pipeline not in ("two_pass", "single_pass", "three_pass"):
    raise HTTPException(status_code=400, detail="pipeline must be two_pass, single_pass, or three_pass")
```

And after the `pass2` block, add:
```python
if pipeline == "three_pass":
    cfg["pass3"] = body.get("pass3", defaults.get("pass3"))
```

- [ ] **Step 2: Add fork route**

Add after the `delete_builtin_eval_config` route:

```python
@app.post("/admin/evals/builtin-config/{name}/fork")
async def fork_builtin_eval_config(
    name: str,
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Fork a built-in config into a custom config (v1 = defaults for its pipeline)."""
    from evals import custom_store as cs
    from evals.configs import CONFIGS
    builtin = next((c for c in CONFIGS if c["name"] == name), None)
    if not builtin:
        raise HTTPException(status_code=404, detail="Built-in config not found")
    if cs.config_exists(name):
        # Already forked — return existing config
        return {"name": name, "ok": True, "already_existed": True}
    pipeline = "single_pass" if "single" in name else "two_pass"
    defaults = cs.defaults_for_pipeline(pipeline)
    cfg = {
        "name":        name,
        "description": builtin["description"],
        "pipeline":    pipeline,
        **defaults,
    }
    saved = cs.save_config(cfg)
    # Hide the built-in so only the custom version shows
    cs.hide_builtin(name)
    return {"name": saved, "ok": True, "already_existed": False}
```

- [ ] **Step 3: Add edit route**

Add after the fork route:

```python
@app.post("/admin/evals/custom-config/{name}/edit")
async def edit_eval_config(
    name: str,
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Save a new version of an existing custom config."""
    from evals import custom_store as cs
    if not cs.config_exists(name):
        raise HTTPException(status_code=404, detail="Config not found")
    body = await request.json()
    pipeline = cs.get_config(name)["pipeline"]
    pass_data = {"pass1": body["pass1"]}
    if pipeline in ("two_pass", "three_pass"):
        pass_data["pass2"] = body["pass2"]
    if pipeline == "three_pass":
        pass_data["pass3"] = body["pass3"]
    # Update top-level description if provided
    if "description" in body:
        cfg = cs.get_config(name)
        cfg["description"] = body["description"]
        import os, json as _json
        p = cs._path(name)
        with open(p, "w") as fh:
            _json.dump(cfg, fh, indent=2)
    new_v = cs.add_version(name, pass_data)
    return {"name": name, "version": new_v, "ok": True}
```

- [ ] **Step 4: Fix `duplicate_eval_config` for versioned format**

The current duplicate route does `cfg = cs.get_config(name)` then `cs.save_config(cfg)`. After migration, `cfg` has `versions` but `save_config` expects flat pass keys. Fix by passing the latest version's pass params:

```python
@app.post("/admin/evals/custom-config/{name}/duplicate")
def duplicate_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    import re as _re
    from evals import custom_store as cs
    cfg = cs.get_config(name)
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found")
    base = _re.sub(r"_copy(_\d+)?$", "", cfg["name"])
    new_name = f"{base}_copy"
    i = 2
    while cs.config_exists(new_name):
        new_name = f"{base}_copy_{i}"
        i += 1
    latest = cs.latest_version(name)
    new_cfg = {
        "name":        new_name,
        "description": cfg.get("description", new_name),
        "pipeline":    cfg["pipeline"],
        **{k: latest[k] for k in ("pass1", "pass2", "pass3") if k in latest},
    }
    cs.save_config(new_cfg)
    return RedirectResponse(url=f"/admin/evals?msg=Config+duplicated+as+{new_name}", status_code=303)
```

- [ ] **Step 5: Update `admin_evals` to expose version data for JS**

The template JS needs version history to populate the version selector. Add `custom_configs_versioned` to the template context (it's the same list, just clarifying the template can now access `c.versions`):

```python
# In admin_evals():
custom_cfgs = cs.list_configs()
return templates.TemplateResponse("evals.html", {
    "request":          request,
    "templates":        templates_list,
    "configs":          visible_configs,
    "custom_configs":   custom_cfgs,   # now has versions array
    "available_models": cs.AVAILABLE_MODELS,
    "results_grouped":  results_grouped,
    "msg":              msg,
})
```

(No structural change — just ensuring `list_configs()` is called after migration is in place.)

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(evals): add fork, edit, three_pass support routes"
```

---

## Task 5: UI — Hover popup action menu

**Files:**
- Modify: `templates/evals.html`

Replace the inline action button cells in the configs table with a single `⋯` button that reveals a popup with all actions. Same `position:fixed` + `getBoundingClientRect()` pattern already used for the template context menu.

- [ ] **Step 1: Add CSS for the config action popup**

The `.ev-context-menu` style already exists and covers this. No new CSS needed — the popup reuses it.

- [ ] **Step 2: Replace built-in config action cells**

Find:
```html
<td style="display:flex;gap:4px;flex-wrap:wrap;">
  <button type="button" onclick="viewConfigPrompts('{{ c.name }}')" class="ev-btn-xs ev-btn-ghost">View prompts</button>
  <button type="button" class="ev-btn-xs ev-btn-ghost ev-btn-danger"
          onclick="submitPost('/admin/evals/builtin-config/{{ c.name }}/delete', 'Remove built-in config {{ c.name }}?\n\nIt will reappear after a server restart.')">Delete</button>
</td>
```

Replace with:
```html
<td>
  <button type="button" class="ev-btn-xs ev-btn-ghost"
          onclick="toggleCtxMenu(event,'cfg-ctx-{{ c.name }}')"
          style="font-size:15px;padding:2px 8px;">⋯</button>
  <div id="cfg-ctx-{{ c.name }}" class="ev-context-menu" style="display:none;">
    <button type="button" onclick="forkAndEdit('{{ c.name }}');toggleCtxMenu(null,'cfg-ctx-{{ c.name }}')">Edit</button>
    <button type="button" onclick="viewConfigPrompts('{{ c.name }}');toggleCtxMenu(null,'cfg-ctx-{{ c.name }}')">View prompts</button>
    <button type="button" style="color:#ef4444;"
            onclick="submitPost('/admin/evals/builtin-config/{{ c.name }}/delete','Remove built-in config {{ c.name }}?\n\nIt will reappear after a server restart.');toggleCtxMenu(null,'cfg-ctx-{{ c.name }}')">Delete</button>
  </div>
</td>
```

- [ ] **Step 3: Replace custom config action cells**

Find:
```html
<td style="display:flex;gap:4px;flex-wrap:wrap;">
  <button type="button" onclick="viewConfigPrompts('{{ c.name }}')" class="ev-btn-xs ev-btn-ghost">View prompts</button>
  <button type="button" class="ev-btn-xs ev-btn-ghost"
          onclick="submitPost('/admin/evals/custom-config/{{ c.name }}/duplicate')">Duplicate</button>
  <button type="button" class="ev-btn-xs ev-btn-ghost ev-btn-danger"
          onclick="submitPost('/admin/evals/custom-config/{{ c.name }}/delete', 'Delete config {{ c.name }}?')">Delete</button>
</td>
```

Replace with:
```html
<td>
  <button type="button" class="ev-btn-xs ev-btn-ghost"
          onclick="toggleCtxMenu(event,'cfg-ctx-{{ c.name }}')"
          style="font-size:15px;padding:2px 8px;">⋯</button>
  <div id="cfg-ctx-{{ c.name }}" class="ev-context-menu" style="display:none;">
    <button type="button" onclick="openEditPanel('{{ c.name }}');toggleCtxMenu(null,'cfg-ctx-{{ c.name }}')">Edit</button>
    <button type="button" onclick="submitPost('/admin/evals/custom-config/{{ c.name }}/duplicate');toggleCtxMenu(null,'cfg-ctx-{{ c.name }}')">Duplicate</button>
    <button type="button" onclick="viewConfigPrompts('{{ c.name }}');toggleCtxMenu(null,'cfg-ctx-{{ c.name }}')">View prompts</button>
    <button type="button" style="color:#ef4444;"
            onclick="submitPost('/admin/evals/custom-config/{{ c.name }}/delete','Delete config {{ c.name }}?');toggleCtxMenu(null,'cfg-ctx-{{ c.name }}')">Delete</button>
  </div>
</td>
```

- [ ] **Step 4: Add version badge to custom config name cell**

Find the custom config name cell:
```html
<td style="font-family:monospace;font-size:12px;">{{ c.name }}</td>
```

Replace:
```html
<td style="font-family:monospace;font-size:12px;">
  {{ c.name }}
  {% if c.versions %}
  <span style="font-size:10px;color:var(--text-muted);background:var(--bg-raised);
               border:1px solid var(--border);border-radius:3px;padding:1px 5px;margin-left:4px;">
    v{{ c.versions | map(attribute='version') | max }}
  </span>
  {% endif %}
</td>
```

- [ ] **Step 5: Expose custom configs as JSON for JS**

Directly after the `<script id="templates-json">` tag at the top of `{% block content %}`, add:
```html
<script id="custom-configs-json" type="application/json">{{ custom_configs | tojson }}</script>
```

- [ ] **Step 6: Smoke test the popup**

Load `/admin/evals`, hover/click `⋯` on a config row. Popup should appear with all options. Click outside to dismiss.

- [ ] **Step 7: Commit**

```bash
git add templates/evals.html
git commit -m "feat(evals): replace inline config buttons with hover popup menu"
```

---

## Task 6: UI — Edit panel with version selector and Load defaults

**Files:**
- Modify: `templates/evals.html`

The existing "Create Config" panel is reused for editing. When editing:
- A version strip appears at the top showing `v1 · 2026-03-16 | v2 · 2026-03-17` etc.
- Clicking a version pill pre-fills the form with that version's params
- "Load defaults" button appears per pass tab
- Save calls `/admin/evals/custom-config/{name}/edit` instead of `/admin/evals/custom-config/create`
- Panel title changes to "Edit Config — {name}"

The panel already has all the fields: model, max_tokens, temperature, top_p, top_k, system_prompt, user_prompt_template per pass.

- [ ] **Step 1: Add hidden state fields to the create panel**

Inside the create-panel `<div>`, before the name/description row, add:
```html
<input type="hidden" id="edit-config-name" value="">
<input type="hidden" id="edit-mode" value="create">
```

- [ ] **Step 2: Add version selector strip to the panel**

After the `<div class="ev-section-label">New Eval Config</div>` line, add:
```html
<div id="edit-version-strip" style="display:none;margin-bottom:14px;">
  <div class="ev-section-label" style="margin-bottom:6px;">Version history</div>
  <div id="edit-version-pills" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
</div>
```

- [ ] **Step 3: Add "Load defaults" button to each pass tab**

In each pass panel (`#panel-p1`, `#panel-p2`, `#panel-p3`), add a small button at the top:
```html
<div style="display:flex;justify-content:flex-end;margin-bottom:8px;">
  <button type="button" onclick="loadDefaultsForPass('p1')"  <!-- p2 / p3 for other passes -->
          class="ev-btn-xs ev-btn-ghost" style="font-size:11px;">↺ Load defaults</button>
</div>
```

Also update the Save Config button label dynamically — show "Save as new version" when in edit mode.

- [ ] **Step 4: Update the panel title and save-button label on edit vs create**

In `showCreatePanel()`:
```javascript
async function showCreatePanel() {
  document.getElementById('edit-mode').value = 'create';
  document.getElementById('edit-config-name').value = '';
  document.getElementById('create-panel-title').textContent = 'New Eval Config';
  document.getElementById('save-config-btn').textContent = 'Save Config';
  document.getElementById('edit-version-strip').style.display = 'none';
  const panel = document.getElementById('create-panel');
  panel.style.display = 'block';
  panel.scrollIntoView({ behavior:'smooth', block:'start' });
  await loadDefaults('two_pass');
}
```

Add `id="create-panel-title"` to the `<div class="ev-section-label">New Eval Config</div>` element, and `id="save-config-btn"` to the Save Config button.

- [ ] **Step 5: Add `openEditPanel(name)` JS function**

```javascript
async function openEditPanel(name) {
  const allCfgs = JSON.parse(document.getElementById('custom-configs-json').textContent);
  const cfg = allCfgs.find(c => c.name === name);
  if (!cfg) return;

  document.getElementById('edit-mode').value = 'edit';
  document.getElementById('edit-config-name').value = name;
  document.getElementById('create-panel-title').textContent = 'Edit Config — ' + name;
  document.getElementById('save-config-btn').textContent = 'Save as new version';

  // Populate version selector
  const strip = document.getElementById('edit-version-strip');
  const pills = document.getElementById('edit-version-pills');
  pills.innerHTML = '';
  const versions = [...cfg.versions].sort((a,b) => a.version - b.version);
  versions.forEach(v => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ev-btn-xs ev-btn-ghost';
    btn.style.fontFamily = 'monospace';
    btn.textContent = `v${v.version} · ${v.created_at.slice(0,10)}`;
    btn.onclick = () => prefillFromVersion(v, cfg.pipeline);
    pills.appendChild(btn);
  });
  strip.style.display = 'block';

  // Pre-fill from latest version
  const latest = versions[versions.length - 1];
  prefillFromVersion(latest, cfg.pipeline);

  // Set pipeline (read-only in edit mode — pipeline can't change)
  document.getElementById('cfg-pipeline').value = cfg.pipeline;
  document.getElementById('cfg-pipeline').disabled = true;
  document.getElementById('cfg-name').value = cfg.name;
  document.getElementById('cfg-name').readOnly = true;
  document.getElementById('cfg-desc').value = cfg.description || '';

  // Show/hide pass tabs
  const isMulti = cfg.pipeline !== 'single_pass';
  const isThree = cfg.pipeline === 'three_pass';
  document.getElementById('tab-p2').style.display   = isMulti ? '' : 'none';
  document.getElementById('panel-p2').style.display = 'none';
  document.getElementById('tab-p3').style.display   = isThree ? '' : 'none';
  document.getElementById('panel-p3').style.display = 'none';
  showPassTab(1);

  const panel = document.getElementById('create-panel');
  panel.style.display = 'block';
  panel.scrollIntoView({ behavior:'smooth', block:'start' });
}

function prefillFromVersion(v, pipeline) {
  if (v.pass1) fillPass('p1', v.pass1);
  if (v.pass2) fillPass('p2', v.pass2);
  if (v.pass3) fillPass('p3', v.pass3);
}
```

- [ ] **Step 6: Add `forkAndEdit(name)` JS function (for built-in configs)**

```javascript
async function forkAndEdit(name) {
  try {
    const r = await fetch(`/admin/evals/builtin-config/${encodeURIComponent(name)}/fork`, {
      method: 'POST'
    });
    if (!r.ok) { alert('Fork failed'); return; }
    // Reload to pick up the new custom config, then open its edit panel
    // Use a redirect with a flag so we auto-open the edit panel
    window.location.href = `/admin/evals?msg=Built-in+forked+as+custom+config+${name}`;
  } catch(e) { alert(String(e)); }
}
```

Note: after forking, the page reloads and the user sees the new custom config with v1. They can then click Edit on it. This keeps the flow simple and avoids needing the forked config data in the current page's JS context.

- [ ] **Step 7: Add `loadDefaultsForPass(pfx)` JS function**

```javascript
async function loadDefaultsForPass(pfx) {
  const pipeline = document.getElementById('cfg-pipeline').value;
  try {
    const r = await fetch('/admin/evals/config/defaults?pipeline=' + pipeline);
    if (!r.ok) return;
    const data = await r.json();
    const passKey = pfx === 'p1' ? 'pass1' : pfx === 'p2' ? 'pass2' : 'pass3';
    if (data.defaults[passKey]) fillPass(pfx, data.defaults[passKey]);
  } catch(_) {}
}
```

- [ ] **Step 8: Update `saveConfig()` to branch on edit vs create**

```javascript
async function saveConfig() {
  const errEl = document.getElementById('create-error');
  errEl.style.display = 'none';
  const mode     = document.getElementById('edit-mode').value;
  const editName = document.getElementById('edit-config-name').value;
  const pipeline = document.getElementById('cfg-pipeline').value;

  const pass1 = buildPassObj('p1');
  const pass2 = (pipeline !== 'single_pass') ? buildPassObj('p2') : undefined;
  const pass3 = (pipeline === 'three_pass')  ? buildPassObj('p3') : undefined;

  const body = {
    name:        document.getElementById('cfg-name').value.trim(),
    description: document.getElementById('cfg-desc').value.trim(),
    pipeline,
    pass1,
    ...(pass2 ? { pass2 } : {}),
    ...(pass3 ? { pass3 } : {}),
  };

  if (!body.name) { showErr('Name is required'); return; }

  const url = mode === 'edit'
    ? `/admin/evals/custom-config/${encodeURIComponent(editName)}/edit`
    : '/admin/evals/custom-config/create';

  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      window.location.reload();
    } else {
      const err = await r.json();
      showErr(err.detail || 'Unknown error');
    }
  } catch(e) { showErr(String(e)); }
}
```

Extract the pass object builder (currently inline in `saveConfig`):
```javascript
function buildPassObj(pfx) {
  return {
    model:                document.getElementById(`cfg-${pfx}-model`).value,
    max_tokens:           parseInt(document.getElementById(`cfg-${pfx}-maxtokens`).value),
    temperature:          parseFloat(document.getElementById(`cfg-${pfx}-temp`).value),
    top_p:                getOptNum(`cfg-${pfx}-topp`),
    top_k:                getOptNum(`cfg-${pfx}-topk`) ? parseInt(document.getElementById(`cfg-${pfx}-topk`).value) : null,
    system_prompt:        document.getElementById(`cfg-${pfx}-system`).value,
    user_prompt_template: document.getElementById(`cfg-${pfx}-user`).value,
  };
}
```

- [ ] **Step 9: Reset panel state on close**

Update `hideCreatePanel()` to restore disabled fields:
```javascript
function hideCreatePanel() {
  document.getElementById('create-panel').style.display = 'none';
  document.getElementById('edit-mode').value = 'create';
  document.getElementById('edit-config-name').value = '';
  document.getElementById('cfg-pipeline').disabled = false;
  document.getElementById('cfg-name').readOnly = false;
  document.getElementById('edit-version-strip').style.display = 'none';
  document.getElementById('save-config-btn').textContent = 'Save Config';
  document.getElementById('create-panel-title').textContent = 'New Eval Config';
}
```

- [ ] **Step 10: Full end-to-end smoke test**

1. Create a new custom config → check it appears in the table with `v1` badge
2. Click ⋯ → Edit → change a system prompt → Save as new version → `v2` badge appears
3. Click ⋯ → Edit → click `v1` pill → form fills with original params → save → `v3` badge
4. Click ⋯ on a built-in → Edit → page reloads showing forked custom config → edit it
5. Run the edited config → check result file has correct version number
6. Click ⋯ → Duplicate → new `_copy` config appears with `v1`

- [ ] **Step 11: Commit**

```bash
git add templates/evals.html
git commit -m "feat(evals): edit panel with version selector, load defaults, fork built-ins"
```

---

## Notes

- **Pipeline is immutable after creation.** The pipeline type (`single_pass` / `two_pass` / `three_pass`) cannot change between versions — changing it would invalidate the version comparison. The selector is disabled when editing.
- **Built-in configs after fork.** Once forked, the original built-in is hidden and the custom config shadows it. The custom config starts at v1 with the standard defaults (not the hardcoded function body — those are equivalent to the defaults already stored in `custom_store.py`). Running the forked config uses executor.py, so all params (temperature, top_p, top_k) actually reach the API.
- **Version registry.** `version_registry.py` remains unchanged and still handles built-in configs. For custom configs, the explicit `_version` field in the runnable dict takes over, so the registry is bypassed for them.
- **Existing result files.** Old result files have version numbers that were hash-derived. They remain valid — `list_results_grouped` reads version numbers from `_meta.versions` in the result file, not from the config file.
