# Evals Custom Configs & UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the owner to create fully customizable eval configs (model, prompts, sampling params) directly in the UI, view prompts used in any past run, and interact with templates via a 3-dot context menu; replace the accordion results view with a popup modal.

**Architecture:** New `evals/custom_store.py` holds CRUD for user-created configs stored as JSON files. New `evals/executor.py` builds a `run_fn` from a config dict — handling both single-pass and two-pass, injecting all sampling parameters. `evals/runner.py` merges built-in and custom configs. Results JSON gains a `_config_snapshots` top-level key so prompts are permanently recorded. `evals.html` is redesigned: Configs section (new), Templates section (3-dot menu), Run section (all configs), Results as modal.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Anthropic SDK (`temperature`, `top_p`, `top_k` supported), Jinja2, vanilla JS

---

## All Customizable Parameters When Creating a New Eval Config

| Section | Field | Type | Default | Notes |
|---------|-------|------|---------|-------|
| Basic | `name` | string | — | Slug-safe, unique, letters/numbers/underscores only |
| Basic | `description` | string | — | Human-readable label shown in tables |
| Pipeline | `pipeline` | select | `two_pass` | `two_pass` \| `single_pass` |
| Pass 1 | `pass1.model` | select | `claude-haiku-4-5-20251001` | haiku / sonnet-4-6 / opus-4-6 |
| Pass 1 | `pass1.max_tokens` | integer | `4096` | 256–16000 |
| Pass 1 | `pass1.temperature` | float | `1.0` | 0.0–1.0; slider + number |
| Pass 1 | `pass1.top_p` | float \| null | null | 0.0–1.0; leave blank = API default |
| Pass 1 | `pass1.top_k` | integer \| null | null | 1–500; leave blank = API default |
| Pass 1 | `pass1.system_prompt` | textarea | *(current production discovery prompt)* | Full system prompt text |
| Pass 1 | `pass1.user_prompt_template` | textarea | *(current production user message template)* | Supports `{language}`, `{title_hint}`, `{transcript}` placeholders; two_pass also gets `{n}`, `{stock_list}` in Pass 2 |
| Pass 2 *(two_pass only)* | `pass2.model` | select | `claude-haiku-4-5-20251001` | Same options as Pass 1 |
| Pass 2 | `pass2.max_tokens` | integer | `8192` | 256–16000 |
| Pass 2 | `pass2.temperature` | float | `1.0` | 0.0–1.0 |
| Pass 2 | `pass2.top_p` | float \| null | null | Optional |
| Pass 2 | `pass2.top_k` | integer \| null | null | Optional |
| Pass 2 | `pass2.system_prompt` | textarea | *(current production analysis system prompt)* | |
| Pass 2 | `pass2.user_prompt_template` | textarea | *(current production analysis user message template)* | |

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `evals/custom_store.py` | CRUD for user-created config dicts stored in `evals/custom_configs/*.json` |
| Create | `evals/executor.py` | `build_run_fn(config_dict)` → callable(transcript,title,language); handles sampling params |
| Modify | `evals/runner.py` | Merge built-in + custom configs; record prompt snapshots in result |
| Modify | `evals/store.py` | `save_result` accepts optional `snapshots` dict; stored under `_config_snapshots` key |
| Modify | `main.py` | New CRUD routes for custom configs; update GET /admin/evals and POST /admin/evals/run |
| Modify | `templates/evals.html` | Full redesign: Configs section, Templates 3-dot menu, Results modal |

---

## Task 1: Custom Config Storage

**Files:**
- Create: `evals/custom_configs/.gitkeep`
- Create: `evals/custom_store.py`

- [ ] **Step 1: Create the directory and gitkeep**

```bash
mkdir -p evals/custom_configs
touch evals/custom_configs/.gitkeep
```

- [ ] **Step 2: Write `evals/custom_store.py`**

```python
"""
CRUD for user-created eval configs.
Stored as JSON files in evals/custom_configs/{name}.json.
Built-in configs (from evals/configs.py) are never stored here.
"""

import glob
import json
import os
import re

_DIR = os.path.join(os.path.dirname(__file__), "custom_configs")

AVAILABLE_MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]

# Default prompts — match current brain.py production prompts exactly
DEFAULT_PASS1_SYSTEM = (
    "You are a specialist financial transcript scanner with deep experience reading "
    "unpunctuated auto-generated YouTube transcripts in German and English. "
    "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, commodities. "
    "Cast a wide net. No sentiment, no judgement — discovery only. "
    "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
    "Prefer US ADR ticker where one exists; otherwise use local format (SAP.DE, P911.DE, BMW.DE). "
    "Return entries in the order they first appear in the transcript. "
    "Do not invent tickers not present in the transcript."
)

DEFAULT_PASS1_USER = (
    "Language: {language}\n{title_hint}\nTranscript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    'Required output format:\n{{"stocks": [{{"ticker": "AAPL", "company_name": "Apple Inc."}}]}}'
)

DEFAULT_PASS2_SYSTEM = (
    "You are a sharp-tongued senior financial analyst expert at cutting through vague "
    "YouTuber commentary to identify the real signal. You know the difference between "
    "genuine conviction and performative neutrality. "
    "For each stock in the provided list: return exactly one mention object. "
    "Do not skip any. Do not add tickers beyond those listed. "
    "Use the exact ticker string as provided. "
    "If a stock cannot be found in the transcript: "
    "is_real_stock_mention=false, confidence=0.0, mention_count=0, explain in context. "
    "Sentiment: lean toward bullish/bearish when any directional signal is present — "
    "reserve neutral for genuinely balanced or purely informational mentions. "
    "Confidence reflects clarity of sentiment expression, not certainty about the stock's prospects."
)

DEFAULT_PASS2_USER = (
    "Language: {language}\n{title_hint}\n"
    "Analyze exactly {n} investment vehicle{plural}:\n{stock_list}\n\n"
    "Transcript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    'Required output format:\n{{"mentions": [{{"ticker": "AAPL", "company_name": "Apple Inc.", '
    '"is_real_stock_mention": true, "sentiment": "bullish", "confidence": 0.82, '
    '"recommendation": "buy", "mention_count": 4, "context": "..."}}]}}'
)

DEFAULT_SINGLE_SYSTEM = ""  # single-pass uses no system prompt in legacy mode

DEFAULT_SINGLE_USER = (
    "You are analyzing a finance YouTube video transcript. Language: {language}.\n"
    "{title_hint}\n"
    "IMPORTANT: Be THOROUGH. Find EVERY stock discussed as an investment.\n\n"
    "Rules:\n"
    "1. Extract ALL stocks, ETFs, or crypto mentioned as investments\n"
    "2. Fix transcription errors: 'in Vidia' = NVIDIA, 'A MD' = AMD\n"
    "3. Ambiguous sentiment = neutral + low confidence\n"
    "4. Return ONLY valid JSON.\n\n"
    "Transcript:\n{transcript}\n\n"
    'Required output format:\n{{"mentions": [{{"ticker": "AAPL", "company_name": "Apple", '
    '"mention_count": 3, "sentiment": "bullish", "confidence": 0.85, '
    '"recommendation": "buy", "context": "...", "is_real_stock_mention": true}}]}}'
)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip())


def _path(name: str) -> str:
    return os.path.join(_DIR, f"{_slug(name)}.json")


def list_configs() -> list:
    """Return all custom configs, newest first (by created_at)."""
    os.makedirs(_DIR, exist_ok=True)
    files = glob.glob(os.path.join(_DIR, "*.json"))
    configs = []
    for f in files:
        if os.path.basename(f) == ".gitkeep":
            continue
        try:
            with open(f) as fh:
                configs.append(json.load(fh))
        except Exception:
            pass
    return sorted(configs, key=lambda c: c.get("created_at", ""), reverse=True)


def get_config(name: str) -> dict | None:
    p = _path(name)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def config_exists(name: str) -> bool:
    return os.path.exists(_path(name))


def save_config(data: dict) -> str:
    """Persist a custom config. Returns the slug name used."""
    from datetime import datetime
    os.makedirs(_DIR, exist_ok=True)
    slug = _slug(data["name"])
    data["name"] = slug
    if "created_at" not in data:
        data["created_at"] = datetime.now().isoformat()
    with open(_path(slug), "w") as fh:
        json.dump(data, fh, indent=2)
    return slug


def delete_config(name: str) -> bool:
    p = _path(name)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def defaults_for_pipeline(pipeline: str) -> dict:
    """Return default pass config dicts for a given pipeline type."""
    if pipeline == "two_pass":
        return {
            "pass1": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 4096,
                "temperature": 1.0,
                "top_p": None,
                "top_k": None,
                "system_prompt": DEFAULT_PASS1_SYSTEM,
                "user_prompt_template": DEFAULT_PASS1_USER,
            },
            "pass2": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 8192,
                "temperature": 1.0,
                "top_p": None,
                "top_k": None,
                "system_prompt": DEFAULT_PASS2_SYSTEM,
                "user_prompt_template": DEFAULT_PASS2_USER,
            },
        }
    # single_pass
    return {
        "pass1": {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 8192,
            "temperature": 1.0,
            "top_p": None,
            "top_k": None,
            "system_prompt": DEFAULT_SINGLE_SYSTEM,
            "user_prompt_template": DEFAULT_SINGLE_USER,
        }
    }
```

- [ ] **Step 3: Verify the file is importable**

```bash
cd /Users/mitjawilms/DeInfluencer && python -c "from evals.custom_store import list_configs; print('ok', list_configs())"
```
Expected: `ok []`

---

## Task 2: Executor — Build `run_fn` from Config Dict

**Files:**
- Create: `evals/executor.py`

- [ ] **Step 1: Write `evals/executor.py`**

```python
"""
Builds a run_fn callable from a custom config dict.

run_fn signature: (transcript, title, language) -> list[mention_dict]

Supports:
  pipeline: "two_pass" | "single_pass"
  pass params: model, max_tokens, temperature, top_p, top_k,
               system_prompt, user_prompt_template
"""

import json
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import anthropic
from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS

log = logging.getLogger(__name__)


def _api_kwargs(pass_cfg: dict) -> dict:
    """Build kwargs for client.messages.create from a pass config."""
    kw = {
        "model":      pass_cfg["model"],
        "max_tokens": int(pass_cfg.get("max_tokens", 4096)),
    }
    if pass_cfg.get("temperature") is not None:
        kw["temperature"] = float(pass_cfg["temperature"])
    if pass_cfg.get("top_p") is not None:
        kw["top_p"] = float(pass_cfg["top_p"])
    if pass_cfg.get("top_k") is not None:
        kw["top_k"] = int(pass_cfg["top_k"])
    return kw


def build_run_fn(config_dict: dict):
    """
    Returns a callable(transcript, title, language) -> list[mention_dict]
    built from a custom config dict.

    Also returns a snapshot dict of the config (minus run_fn) for recording in results.
    """
    pipeline = config_dict.get("pipeline", "two_pass")
    pass1    = config_dict["pass1"]
    pass2    = config_dict.get("pass2")  # None for single_pass

    def run_fn(transcript: str, title: str = "", language: str = "en") -> list:
        client     = anthropic.Anthropic()
        lang_label = _LANG_MAP.get(language, "English")
        title_hint = f"\nVideo title: {title}\n" if title else ""

        if pipeline == "single_pass":
            user_msg = pass1["user_prompt_template"].format(
                language=lang_label,
                title_hint=title_hint,
                transcript=transcript,
            )
            kw = _api_kwargs(pass1)
            msgs = [{"role": "user", "content": user_msg}]
            sys_p = pass1.get("system_prompt", "")
            if sys_p:
                kw["system"] = sys_p
            try:
                r = client.messages.create(**kw, messages=msgs)
                raw = _strip_markdown(r.content[0].text)
                mentions = json.loads(raw).get("mentions", [])
                return [m for m in mentions if m.get("is_real_stock_mention") in (True, "true", 1)]
            except Exception as e:
                log.error("executor single_pass failed: %s", e)
                return []

        # two_pass
        user1 = pass1["user_prompt_template"].format(
            language=lang_label,
            title_hint=title_hint,
            transcript=transcript,
        )
        kw1 = _api_kwargs(pass1)
        sys1 = pass1.get("system_prompt", "")
        if sys1:
            kw1["system"] = sys1
        try:
            r1 = client.messages.create(**kw1, messages=[{"role": "user", "content": user1}])
            discovered = json.loads(_strip_markdown(r1.content[0].text)).get("stocks", [])
        except Exception as e:
            log.error("executor two_pass pass1 failed: %s", e)
            return []

        if not discovered:
            return []
        if len(discovered) > _MAX_DISCOVERED_STOCKS:
            discovered = discovered[:_MAX_DISCOVERED_STOCKS]

        n          = len(discovered)
        stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
        user2 = pass2["user_prompt_template"].format(
            language=lang_label,
            title_hint=title_hint,
            transcript=transcript,
            n=n,
            plural="s" if n != 1 else "",
            stock_list=stock_list,
        )
        kw2 = _api_kwargs(pass2)
        sys2 = pass2.get("system_prompt", "")
        if sys2:
            kw2["system"] = sys2
        try:
            r2 = client.messages.create(**kw2, messages=[{"role": "user", "content": user2}])
            mentions = json.loads(_strip_markdown(r2.content[0].text)).get("mentions", [])
        except Exception as e:
            log.error("executor two_pass pass2 failed: %s", e)
            return []

        discovered_tickers = {s["ticker"].upper() for s in discovered}
        filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
        return [m for m in filtered if m.get("is_real_stock_mention") in (True, "true", 1)]

    return run_fn


def config_to_runnable(config_dict: dict) -> dict:
    """
    Convert a stored custom config dict into a runner-compatible config dict
    (with name, description, run_fn keys).
    """
    return {
        "name":        config_dict["name"],
        "description": config_dict.get("description", config_dict["name"]),
        "run_fn":      build_run_fn(config_dict),
        "_raw":        config_dict,  # for snapshot recording
    }
```

- [ ] **Step 2: Verify executor imports cleanly**

```bash
cd /Users/mitjawilms/DeInfluencer && python -c "from evals.executor import build_run_fn, config_to_runnable; print('ok')"
```
Expected: `ok`

---

## Task 3: Update `evals/store.py` — Snapshots in Results

**Files:**
- Modify: `evals/store.py`

- [ ] **Step 1: Read current `save_result` in store.py (already done in plan)**

- [ ] **Step 2: Update `save_result` to accept optional `snapshots` param**

In `evals/store.py`, change `save_result` signature and body:

```python
def save_result(data, config_names, snapshots=None):
    """Save a result dict to evals/results/ with a timestamped filename.

    snapshots: optional dict {config_name: config_dict} — records prompts used.
    """
    from datetime import datetime
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
    fname = f"{timestamp}-{'_vs_'.join(config_names)}.json"
    path  = os.path.join(RESULTS_DIR, fname)

    def serialise(obj):
        if isinstance(obj, set):
            return sorted(obj)
        if isinstance(obj, dict):
            return {k: serialise(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [serialise(i) for i in obj]
        return obj

    payload = serialise(data)
    if snapshots:
        payload["_config_snapshots"] = serialise(snapshots)

    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path
```

- [ ] **Step 3: Verify backward compatibility — existing callers pass no `snapshots` and still work**

```bash
cd /Users/mitjawilms/DeInfluencer && python -c "from evals.store import save_result; print('ok')"
```

---

## Task 4: Update `evals/runner.py` — Merge Built-in + Custom, Record Snapshots

**Files:**
- Modify: `evals/runner.py`

- [ ] **Step 1: Update `run_as_task` to merge built-in and custom configs and record snapshots**

Replace the `run_as_task` function:

```python
def run_as_task(config_names=None):
    """
    Entry point for FastAPI background task.
    config_names: list of config name strings (built-in or custom), or None to run all.
    """
    from evals import custom_store
    from evals.executor import config_to_runnable

    # Build unified config map: built-in first, then custom
    builtin_map = {c["name"]: c for c in CONFIGS}
    custom_cfgs = {c["name"]: config_to_runnable(c) for c in custom_store.list_configs()}
    config_map  = {**builtin_map, **custom_cfgs}

    selected = (
        [config_map[n] for n in config_names if n in config_map]
        if config_names else list(config_map.values())
    )

    ground_truth = store.list_templates()
    if not ground_truth:
        log.info("Eval run: no ground truth templates found.")
        return

    log.info("Eval run starting: %d config(s) × %d video(s)", len(selected), len(ground_truth))

    all_results = {}
    snapshots   = {}
    for config in selected:
        all_results[config["name"]] = run_config(config, ground_truth)
        # Record prompt snapshot if available (custom configs carry _raw)
        if "_raw" in config:
            snapshots[config["name"]] = config["_raw"]

    path = store.save_result(
        all_results,
        [c["name"] for c in selected],
        snapshots=snapshots or None,
    )
    log.info("Eval run complete — saved to %s", path)
```

- [ ] **Step 2: Verify runner still imports cleanly**

```bash
cd /Users/mitjawilms/DeInfluencer && python -c "from evals.runner import run_as_task; print('ok')"
```

---

## Task 5: New Backend Routes in `main.py`

**Files:**
- Modify: `main.py`

The following routes need to be added. Read `main.py` first and insert these after the existing evals routes.

- [ ] **Step 1: Add GET `/admin/evals/config/defaults` — returns default prompts for a pipeline type**

```python
@app.get("/admin/evals/config/defaults")
def evals_config_defaults(
    pipeline: str = "two_pass",
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals.custom_store import defaults_for_pipeline, AVAILABLE_MODELS
    return {"defaults": defaults_for_pipeline(pipeline), "models": AVAILABLE_MODELS}
```

- [ ] **Step 2: Add GET `/admin/evals/config/{name}` — returns full config JSON (for viewing prompts)**

```python
@app.get("/admin/evals/config/{name}")
def get_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import custom_store
    from evals.configs import CONFIGS
    # Check custom first
    cfg = custom_store.get_config(name)
    if cfg:
        return cfg
    # Fall back to built-in — return a snapshot representation
    builtin = next((c for c in CONFIGS if c["name"] == name), None)
    if not builtin:
        raise HTTPException(status_code=404, detail="Config not found")
    # Return the built-in config's prompts from custom_store defaults
    defaults = custom_store.defaults_for_pipeline("two_pass" if "two_pass" in name else "single_pass")
    return {
        "name": name,
        "description": builtin["description"],
        "pipeline": "single_pass" if "single" in name else "two_pass",
        "is_builtin": True,
        **defaults,
    }
```

- [ ] **Step 3: Add POST `/admin/evals/custom-config/create`**

```python
@app.post("/admin/evals/custom-config/create")
async def create_eval_config(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import custom_store
    body = await request.json()

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    import re
    slug = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if custom_store.config_exists(slug):
        raise HTTPException(status_code=409, detail=f"Config '{slug}' already exists")

    # Validate pipeline
    pipeline = body.get("pipeline", "two_pass")
    if pipeline not in ("two_pass", "single_pass"):
        raise HTTPException(status_code=400, detail="pipeline must be two_pass or single_pass")

    # Build the config dict
    cfg = {
        "name":        slug,
        "description": body.get("description", slug),
        "pipeline":    pipeline,
        "pass1":       body.get("pass1", custom_store.defaults_for_pipeline(pipeline)["pass1"]),
    }
    if pipeline == "two_pass":
        cfg["pass2"] = body.get("pass2", custom_store.defaults_for_pipeline(pipeline)["pass2"])

    saved_name = custom_store.save_config(cfg)
    return {"name": saved_name, "ok": True}
```

- [ ] **Step 4: Add POST `/admin/evals/custom-config/{name}/delete`**

```python
@app.post("/admin/evals/custom-config/{name}/delete")
def delete_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import custom_store
    if not custom_store.delete_config(name):
        raise HTTPException(status_code=404, detail="Config not found")
    return RedirectResponse(url="/admin/evals?msg=Config+deleted", status_code=303)
```

- [ ] **Step 5: Add POST `/admin/evals/custom-config/{name}/duplicate`**

```python
@app.post("/admin/evals/custom-config/{name}/duplicate")
def duplicate_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import custom_store
    import re
    cfg = custom_store.get_config(name)
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found")
    # Generate new unique name
    base = re.sub(r"_copy(_\d+)?$", "", cfg["name"])
    new_name = f"{base}_copy"
    i = 2
    while custom_store.config_exists(new_name):
        new_name = f"{base}_copy_{i}"
        i += 1
    cfg["name"] = new_name
    cfg.pop("created_at", None)
    custom_store.save_config(cfg)
    return RedirectResponse(url=f"/admin/evals?msg=Config+duplicated+as+{new_name}", status_code=303)
```

- [ ] **Step 6: Update `GET /admin/evals` to include custom configs**

In the existing `admin_evals` route, change the context passed to the template to include custom configs:

```python
# After existing template setup, add:
from evals import custom_store as cs
custom_configs = cs.list_configs()
# Pass to template:
"custom_configs": custom_configs,
"available_models": cs.AVAILABLE_MODELS,
```

- [ ] **Step 7: Update `POST /admin/evals/run` to look up custom configs too**

The existing route already passes `selected` config names to `run_as_task`. The updated `run_as_task` (Task 4) already resolves custom configs from the name. No change needed to the route itself.

- [ ] **Step 8: Restart server and verify no import errors**

```bash
cd /Users/mitjawilms/DeInfluencer && python -c "import main; print('ok')"
```

---

## Task 6: Redesign `templates/evals.html`

This is the largest task. The redesign covers four sections:

1. **Configs** — new section at top: built-in (read-only, view prompts) + custom (delete, duplicate, view prompts) + "Create New Config" button opening inline panel
2. **Templates** — existing section with 3-dot menu added
3. **Run Evals** — existing section, now lists all configs (built-in + custom)
4. **Past Results** — "View Past Results" button opens a full-screen scrollable modal

**Files:**
- Modify: `templates/evals.html`

- [ ] **Step 1: Read the full current `evals.html`**

(Already read above in plan research — use the structural summary)

- [ ] **Step 2: Replace the entire file with the redesigned version**

Key structure of new `evals.html`:

```html
{% extends "base.html" %}
{% block title %}Vetted — Evals{% endblock %}
{% block content %}

<!-- flash messages -->

<!-- ── SECTION 1: Eval Configs ── -->
<div class="card section">
  <div class="card-title" style="display:flex;align-items:center;justify-content:space-between;">
    Eval Configs
    <button onclick="showCreateConfigPanel()" class="btn-sm">+ New Config</button>
  </div>

  <!-- Create Config inline panel (hidden by default) -->
  <div id="create-config-panel" style="display:none; ...">
    <div style="display:flex;gap:16px;margin-bottom:12px;">
      <div style="flex:1">
        <label>Name (slug)</label>
        <input id="cfg-name" type="text" placeholder="my_custom_haiku">
      </div>
      <div style="flex:2">
        <label>Description</label>
        <input id="cfg-desc" type="text" placeholder="Human-readable label">
      </div>
      <div>
        <label>Pipeline</label>
        <select id="cfg-pipeline" onchange="onPipelineChange()">
          <option value="two_pass">Two-pass</option>
          <option value="single_pass">Single-pass</option>
        </select>
      </div>
    </div>

    <!-- Pass tabs -->
    <div id="cfg-pass-tabs" style="...">
      <button id="tab-pass1" class="tab-btn active" onclick="showPassTab(1)">Pass 1 — Discovery</button>
      <button id="tab-pass2" class="tab-btn" onclick="showPassTab(2)">Pass 2 — Analysis</button>
    </div>

    <!-- Pass 1 panel -->
    <div id="cfg-pass1-panel">
      <!-- Model, max_tokens, temperature, top_p, top_k row -->
      <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
        <div>
          <label>Model</label>
          <select id="cfg-p1-model">
            <option value="claude-haiku-4-5-20251001">Haiku 4.5</option>
            <option value="claude-sonnet-4-6">Sonnet 4.6</option>
            <option value="claude-opus-4-6">Opus 4.6</option>
          </select>
        </div>
        <div>
          <label>Max tokens</label>
          <input id="cfg-p1-maxtokens" type="number" value="4096" min="256" max="16000" style="width:90px">
        </div>
        <div>
          <label>Temperature <span style="color:var(--text-muted);font-size:11px;">(0–1)</span></label>
          <input id="cfg-p1-temp" type="number" value="1.0" min="0" max="1" step="0.05" style="width:70px">
        </div>
        <div>
          <label>top_p <span style="color:var(--text-muted);font-size:11px;">(optional)</span></label>
          <input id="cfg-p1-topp" type="number" placeholder="—" min="0" max="1" step="0.05" style="width:70px">
        </div>
        <div>
          <label>top_k <span style="color:var(--text-muted);font-size:11px;">(optional)</span></label>
          <input id="cfg-p1-topk" type="number" placeholder="—" min="1" max="500" style="width:70px">
        </div>
      </div>
      <div style="margin-bottom:12px;">
        <label>System prompt</label>
        <textarea id="cfg-p1-system" rows="5" style="width:100%;font-family:monospace;font-size:12px;"></textarea>
      </div>
      <div>
        <label>User message template
          <span style="color:var(--text-muted);font-size:11px;">
            Placeholders: {language} {title_hint} {transcript}
          </span>
        </label>
        <textarea id="cfg-p1-user" rows="8" style="width:100%;font-family:monospace;font-size:12px;"></textarea>
      </div>
    </div>

    <!-- Pass 2 panel (only shown if two_pass) -->
    <div id="cfg-pass2-panel" style="display:none;">
      <!-- Same fields as Pass 1, but with ids cfg-p2-* -->
      <!-- Extra placeholders available: {n} {plural} {stock_list} -->
      ...
    </div>

    <div style="display:flex;gap:8px;margin-top:16px;">
      <button onclick="saveConfig()" class="btn-primary">Save Config</button>
      <button onclick="hideCreateConfigPanel()" class="btn-ghost">Cancel</button>
    </div>
  </div>

  <!-- Config table -->
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Name</th><th>Description</th><th>Pipeline</th><th>Models</th><th></th>
        </tr>
      </thead>
      <tbody>
        <!-- Built-in configs (from CONFIGS) -->
        {% for c in configs %}
        <tr>
          <td><span style="font-family:monospace;font-size:12px;">{{ c.name }}</span>
              <span style="color:var(--text-muted);font-size:10px;margin-left:4px;">built-in</span></td>
          <td>{{ c.description }}</td>
          <td>{{ 'Two-pass' if 'two_pass' in c.name else 'Single-pass' }}</td>
          <td style="font-size:11px;color:var(--text-muted);">haiku</td>
          <td>
            <button onclick="viewConfigPrompts('{{ c.name }}')" class="btn-ghost btn-xs">View prompts</button>
          </td>
        </tr>
        {% endfor %}
        <!-- Custom configs -->
        {% for c in custom_configs %}
        <tr>
          <td><span style="font-family:monospace;font-size:12px;">{{ c.name }}</span></td>
          <td>{{ c.description }}</td>
          <td>{{ 'Two-pass' if c.pipeline == 'two_pass' else 'Single-pass' }}</td>
          <td style="font-size:11px;color:var(--text-muted);">
            {{ c.pass1.model | replace('claude-', '') }}
            {% if c.pipeline == 'two_pass' and c.pass2.model != c.pass1.model %}
              / {{ c.pass2.model | replace('claude-', '') }}
            {% endif %}
          </td>
          <td style="display:flex;gap:4px;">
            <button onclick="viewConfigPrompts('{{ c.name }}')" class="btn-ghost btn-xs">View prompts</button>
            <form method="POST" action="/admin/evals/custom-config/{{ c.name }}/duplicate" style="margin:0">
              <button type="submit" class="btn-ghost btn-xs">Duplicate</button>
            </form>
            <form method="POST" action="/admin/evals/custom-config/{{ c.name }}/delete"
                  onsubmit="return confirm('Delete config {{ c.name }}?')" style="margin:0">
              <button type="submit" class="btn-ghost btn-xs btn-danger">Delete</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<!-- ── SECTION 2: Ground Truth Templates (existing, with 3-dot menu) ── -->
<div class="card section">
  <div class="card-title">Ground Truth Templates</div>
  ...
  <tbody>
    {% for t in templates %}
    <tr>
      <td>{{ t.video_id }}</td>
      <td>{{ t.title }}</td>
      <td>{{ t.channel }}</td>
      <td>{{ t.language | upper }}</td>
      <td>{{ t.annotations | length }} ...</td>
      <td style="position:relative;">
        <!-- 3-dot menu -->
        <button class="btn-ghost btn-xs" onclick="toggleMenu('menu-{{ t.video_id }}')" style="...">⋮</button>
        <div id="menu-{{ t.video_id }}" class="context-menu" style="display:none;position:absolute;right:0;...">
          <a href="#" onclick="duplicateTemplate('{{ t.video_id }}')">Duplicate</a>
          <form method="POST" action="/admin/evals/template/{{ t.video_id }}/delete"
                onsubmit="return confirm('Delete?')">
            <button type="submit" class="menu-item-danger">Delete</button>
          </form>
        </div>
      </td>
    </tr>
    {% endfor %}
  </tbody>
  ...
  <!-- Add Template form (existing) -->
</div>

<!-- ── SECTION 3: Run Evals ── -->
<div class="card section">
  <div class="card-title">Run Evals</div>
  <form method="POST" action="/admin/evals/run">
    <!-- Built-in configs -->
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;">Built-in</div>
    {% for c in configs %}
    <label><input type="checkbox" name="configs" value="{{ c.name }}" checked> {{ c.description }}</label>
    {% endfor %}
    <!-- Custom configs -->
    {% if custom_configs %}
    <div style="font-size:11px;color:var(--text-muted);margin:10px 0 6px;">Custom</div>
    {% for c in custom_configs %}
    <label><input type="checkbox" name="configs" value="{{ c.name }}" checked> {{ c.description }}</label>
    {% endfor %}
    {% endif %}
    <div style="margin-top:12px;">
      <button type="submit" {% if not templates %}disabled{% endif %}>Run Selected Evals</button>
    </div>
  </form>
</div>

<!-- ── SECTION 4: Past Results (button + modal) ── -->
<div class="card section">
  <div style="display:flex;align-items:center;justify-content:space-between;">
    <span class="card-title">Past Results</span>
    <button onclick="document.getElementById('results-modal').style.display='flex'" class="btn-sm">
      View Results ({{ results | length }})
    </button>
  </div>
  {% if results %}
  <p style="color:var(--text-muted);font-size:13px;">{{ results | length }} run(s) on record. Click "View Results" to browse.</p>
  {% else %}
  <p style="color:var(--text-muted);">No eval runs yet.</p>
  {% endif %}
</div>

<!-- ── Results Modal ── -->
<div id="results-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);
     z-index:1000;align-items:flex-start;justify-content:center;padding:40px 20px;overflow-y:auto;">
  <div style="background:var(--bg-card);border-radius:8px;width:100%;max-width:1100px;
              max-height:calc(100vh - 80px);overflow-y:auto;padding:24px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h2 style="margin:0;">Past Eval Results</h2>
      <button onclick="document.getElementById('results-modal').style.display='none'"
              style="font-size:20px;background:none;border:none;cursor:pointer;color:var(--text-muted);">✕</button>
    </div>
    {% for r in results %}
    <!-- Compact summary row, expand to see per-video detail -->
    <div class="result-row" style="border:1px solid var(--border);border-radius:6px;margin-bottom:10px;">
      <div onclick="toggleResult({{ loop.index0 }})"
           style="padding:12px 16px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;">
        <span style="font-family:monospace;font-size:12px;">{{ r.filename }}</span>
        <span id="toggle-{{ loop.index0 }}" style="color:var(--text-muted);font-size:12px;">▼ expand</span>
      </div>
      <!-- Summary table (always visible inside card) -->
      <div style="padding:0 16px 12px;">
        <table style="font-size:12px;">
          <thead><tr>
            <th>Config</th><th>P</th><th>R</th><th>F1</th><th>Sent</th><th>Rec</th><th>Videos</th>
            <th></th>
          </tr></thead>
          <tbody>
          {% for cfg_name, agg in r.summary.items() %}
          <tr>
            <td style="font-family:monospace;">{{ cfg_name }}</td>
            <td style="color:{% if agg.precision >= 0.8 %}var(--positive){% elif agg.precision >= 0.6 %}var(--neutral){% else %}var(--negative){% endif %}">
              {{ "%.0f%%"|format(agg.precision * 100) }}</td>
            <td style="color:{% if agg.recall >= 0.8 %}var(--positive){% elif agg.recall >= 0.6 %}var(--neutral){% else %}var(--negative){% endif %}">
              {{ "%.0f%%"|format(agg.recall * 100) }}</td>
            <td><strong>{{ "%.0f%%"|format(agg.f1 * 100) }}</strong></td>
            <td>{{ "%.0f%%"|format(agg.sentiment_acc * 100) if agg.sentiment_acc is not none else "—" }}</td>
            <td>{{ "%.0f%%"|format(agg.rec_acc * 100) if agg.rec_acc is not none else "—" }}</td>
            <td>{{ agg.n_videos }}</td>
            <td>
              {% if r.raw[cfg_name] and r.raw[cfg_name][0] %}
              <!-- View prompts button (only if snapshot exists in result) -->
              {% endif %}
            </td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
      <!-- Expandable per-video detail -->
      <div id="detail-{{ loop.index0 }}" style="display:none;padding:0 16px 16px;">
        {% for cfg_name, per_video in r.raw.items() %}
        <div style="margin-bottom:12px;">
          <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px;">{{ cfg_name }}</div>
          <table style="font-size:11px;">
            <thead><tr><th>Video</th><th>P</th><th>R</th><th>F1</th><th>Found</th><th>Missed</th><th>Halluc</th></tr></thead>
            <tbody>
            {% for v in per_video %}
            <tr>
              <td style="font-family:monospace;max-width:120px;overflow:hidden;text-overflow:ellipsis;">{{ v.video_id }}</td>
              <td>{{ "%.0f%%"|format(v.metrics.precision * 100) }}</td>
              <td>{{ "%.0f%%"|format(v.metrics.recall * 100) }}</td>
              <td><strong>{{ "%.0f%%"|format(v.metrics.f1 * 100) }}</strong></td>
              <td style="font-size:10px;color:var(--positive);">{{ v.found | join(', ') or '—' }}</td>
              <td style="font-size:10px;color:var(--negative);">{{ v.missed | join(', ') or '—' }}</td>
              <td style="font-size:10px;color:var(--neutral);">{{ v.hallucinated | join(', ') or '—' }}</td>
            </tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<!-- ── Prompt Viewer Modal ── -->
<div id="prompt-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);
     z-index:1001;align-items:flex-start;justify-content:center;padding:40px 20px;overflow-y:auto;">
  <div style="background:var(--bg-card);border-radius:8px;width:100%;max-width:900px;
              max-height:calc(100vh - 80px);overflow-y:auto;padding:24px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h2 id="prompt-modal-title" style="margin:0;">Config Prompts</h2>
      <button onclick="document.getElementById('prompt-modal').style.display='none'"
              style="font-size:20px;background:none;border:none;cursor:pointer;color:var(--text-muted);">✕</button>
    </div>
    <div id="prompt-modal-body"></div>
  </div>
</div>

<script>
// ── Config panel ──
async function showCreateConfigPanel() {
  const panel = document.getElementById('create-config-panel');
  panel.style.display = 'block';
  // Load defaults for two_pass
  const resp = await fetch('/admin/evals/config/defaults?pipeline=two_pass',
    { headers: { 'Authorization': 'Basic ' + btoa(':' + (window._pass || '')) } }
  );
  if (!resp.ok) return;
  const data = await resp.json();
  prefillPassPanel('p1', data.defaults.pass1);
  if (data.defaults.pass2) prefillPassPanel('p2', data.defaults.pass2);
}

function hideCreateConfigPanel() {
  document.getElementById('create-config-panel').style.display = 'none';
}

function prefillPassPanel(pfx, cfg) {
  setValue(`cfg-${pfx}-model`,     cfg.model);
  setValue(`cfg-${pfx}-maxtokens`, cfg.max_tokens);
  setValue(`cfg-${pfx}-temp`,      cfg.temperature);
  setValue(`cfg-${pfx}-topp`,      cfg.top_p ?? '');
  setValue(`cfg-${pfx}-topk`,      cfg.top_k ?? '');
  setValue(`cfg-${pfx}-system`,    cfg.system_prompt);
  setValue(`cfg-${pfx}-user`,      cfg.user_prompt_template);
}

function setValue(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

async function onPipelineChange() {
  const pipeline = document.getElementById('cfg-pipeline').value;
  const tab2 = document.getElementById('tab-pass2');
  const pass2panel = document.getElementById('cfg-pass2-panel');
  tab2.style.display = pipeline === 'two_pass' ? '' : 'none';
  pass2panel.style.display = pipeline === 'two_pass' ? '' : 'none';
  // Reload defaults for new pipeline
  const resp = await fetch(`/admin/evals/config/defaults?pipeline=${pipeline}`,
    { headers: { 'Authorization': 'Basic ' + btoa(':' + (window._pass || '')) } }
  );
  if (!resp.ok) return;
  const data = await resp.json();
  prefillPassPanel('p1', data.defaults.pass1);
  if (data.defaults.pass2) prefillPassPanel('p2', data.defaults.pass2);
}

function showPassTab(n) {
  document.getElementById('cfg-pass1-panel').style.display = n === 1 ? '' : 'none';
  document.getElementById('cfg-pass2-panel').style.display = n === 2 ? '' : 'none';
  document.getElementById('tab-pass1').classList.toggle('active', n === 1);
  document.getElementById('tab-pass2').classList.toggle('active', n === 2);
}

function getOpt(id) {
  const val = document.getElementById(id)?.value?.trim();
  return val ? parseFloat(val) || parseInt(val) : null;
}

async function saveConfig() {
  const pipeline = document.getElementById('cfg-pipeline').value;
  const body = {
    name:        document.getElementById('cfg-name').value.trim(),
    description: document.getElementById('cfg-desc').value.trim(),
    pipeline,
    pass1: {
      model:                document.getElementById('cfg-p1-model').value,
      max_tokens:           parseInt(document.getElementById('cfg-p1-maxtokens').value),
      temperature:          parseFloat(document.getElementById('cfg-p1-temp').value),
      top_p:                getOpt('cfg-p1-topp'),
      top_k:                getOpt('cfg-p1-topk') ? parseInt(document.getElementById('cfg-p1-topk').value) : null,
      system_prompt:        document.getElementById('cfg-p1-system').value,
      user_prompt_template: document.getElementById('cfg-p1-user').value,
    },
  };
  if (pipeline === 'two_pass') {
    body.pass2 = {
      model:                document.getElementById('cfg-p2-model').value,
      max_tokens:           parseInt(document.getElementById('cfg-p2-maxtokens').value),
      temperature:          parseFloat(document.getElementById('cfg-p2-temp').value),
      top_p:                getOpt('cfg-p2-topp'),
      top_k:                getOpt('cfg-p2-topk') ? parseInt(document.getElementById('cfg-p2-topk').value) : null,
      system_prompt:        document.getElementById('cfg-p2-system').value,
      user_prompt_template: document.getElementById('cfg-p2-user').value,
    };
  }
  const resp = await fetch('/admin/evals/custom-config/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json',
               'Authorization': 'Basic ' + btoa(':' + (window._pass || '')) },
    body: JSON.stringify(body),
  });
  if (resp.ok) {
    window.location.reload();
  } else {
    const err = await resp.json();
    alert('Error: ' + (err.detail || 'unknown'));
  }
}

// ── Prompt viewer ──
async function viewConfigPrompts(name) {
  const resp = await fetch(`/admin/evals/config/${name}`,
    { headers: { 'Authorization': 'Basic ' + btoa(':' + (window._pass || '')) } }
  );
  if (!resp.ok) { alert('Config not found'); return; }
  const cfg = await resp.json();
  document.getElementById('prompt-modal-title').textContent = `Prompts — ${name}`;
  const body = document.getElementById('prompt-modal-body');
  let html = `<p><strong>Pipeline:</strong> ${cfg.pipeline || '—'} &nbsp; <strong>Built-in:</strong> ${cfg.is_builtin ? 'Yes' : 'No'}</p>`;
  for (const [pass, label] of [['pass1','Pass 1'], ['pass2','Pass 2']]) {
    if (!cfg[pass]) continue;
    const p = cfg[pass];
    html += `<h3>${label}</h3>`;
    html += `<p><strong>Model:</strong> ${p.model} &nbsp; <strong>max_tokens:</strong> ${p.max_tokens} &nbsp; <strong>temperature:</strong> ${p.temperature} &nbsp; <strong>top_p:</strong> ${p.top_p ?? '—'} &nbsp; <strong>top_k:</strong> ${p.top_k ?? '—'}</p>`;
    if (p.system_prompt) {
      html += `<p><strong>System prompt:</strong></p><pre style="background:var(--bg-raised);padding:10px;border-radius:4px;font-size:11px;white-space:pre-wrap;">${escHtml(p.system_prompt)}</pre>`;
    }
    html += `<p><strong>User message template:</strong></p><pre style="background:var(--bg-raised);padding:10px;border-radius:4px;font-size:11px;white-space:pre-wrap;">${escHtml(p.user_prompt_template)}</pre>`;
  }
  body.innerHTML = html;
  document.getElementById('prompt-modal').style.display = 'flex';
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Results modal ──
function toggleResult(idx) {
  const d = document.getElementById(`detail-${idx}`);
  const t = document.getElementById(`toggle-${idx}`);
  const show = d.style.display === 'none';
  d.style.display = show ? 'block' : 'none';
  t.textContent   = show ? '▲ collapse' : '▼ expand';
}

// ── 3-dot menu on templates ──
function toggleMenu(id) {
  document.querySelectorAll('.context-menu').forEach(m => {
    if (m.id !== id) m.style.display = 'none';
  });
  const el = document.getElementById(id);
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}
document.addEventListener('click', e => {
  if (!e.target.closest('[onclick^="toggleMenu"]') && !e.target.closest('.context-menu')) {
    document.querySelectorAll('.context-menu').forEach(m => m.style.display = 'none');
  }
});

// Duplicate template — opens Add Template form prefilled
function duplicateTemplate(videoId) {
  // Close any open menus
  document.querySelectorAll('.context-menu').forEach(m => m.style.display = 'none');
  // Scroll to the add-template form
  const form = document.getElementById('add-template-form');
  if (!form) return;
  form.scrollIntoView({ behavior: 'smooth' });
  // Prefill video_id (user will change it)
  document.getElementById('template-video-id').value = videoId + '_copy';
}

// ── Existing functions (unchanged) ──
function addAnnotationRow(data) { /* ... existing code ... */ }
// submitForm serializer — existing
// lookupVideo — existing
</script>

{% endblock %}
```

Note: The actual implementation should replace the placeholder comments with the real existing JS functions (`addAnnotationRow`, `lookupVideo`, `submitForm` listener). All existing template management functionality is preserved.

- [ ] **Step 3: Add CSS for new elements (inline `<style>` block in the file)**

```css
.btn-sm { padding:4px 10px; font-size:12px; border-radius:4px; cursor:pointer; }
.btn-xs { padding:2px 8px; font-size:11px; border-radius:3px; cursor:pointer; }
.btn-primary { background:var(--accent); color:#fff; border:none; }
.btn-ghost { background:transparent; border:1px solid var(--border); color:var(--text-muted); }
.btn-danger { color:var(--negative); border-color:var(--negative); }
.tab-btn { padding:6px 14px; border:1px solid var(--border); background:var(--bg-raised);
           cursor:pointer; border-radius:4px 4px 0 0; margin-right:2px; }
.tab-btn.active { background:var(--bg-card); border-bottom-color:var(--bg-card);
                  color:var(--text-primary); }
.context-menu { background:var(--bg-card); border:1px solid var(--border);
                border-radius:6px; padding:4px 0; min-width:140px; z-index:100;
                box-shadow:0 4px 12px rgba(0,0,0,0.3); }
.context-menu a, .context-menu button { display:block; width:100%; text-align:left;
  padding:6px 14px; font-size:13px; background:none; border:none; cursor:pointer;
  color:var(--text-primary); text-decoration:none; }
.context-menu a:hover, .context-menu button:hover { background:var(--bg-raised); }
.menu-item-danger { color:var(--negative) !important; }
```

- [ ] **Step 4: Handle auth header for JS fetch calls**

The JS fetch calls to `/admin/evals/config/defaults` and `/admin/evals/config/{name}` need Basic Auth. The server uses HTTP Basic Auth. The cleanest solution is to add a hidden field in the page with the encoded credentials, or to use a cookie/session. The simplest approach for a single-owner tool: use the browser's cached credentials (the browser sends them automatically for same-origin requests to Basic Auth protected endpoints when the user is already authenticated). Replace the `btoa(':' + ...)` approach with simply including credentials in the fetch:

```javascript
async function authFetch(url, opts = {}) {
  return fetch(url, { ...opts, credentials: 'include' });
}
```

Then replace all `fetch(url, { headers: ... })` calls in the JS with `authFetch(url, ...)`.

Actually — for HTTP Basic Auth, `credentials: 'include'` alone doesn't work because Basic Auth is not cookie-based. The simplest approach: the browser will prompt the Basic Auth dialog automatically on 401. Use `fetch(url)` with no special headers — the browser will attach the cached credentials. This works for same-origin requests in modern browsers when the user has already authenticated.

Replace all fetch calls with plain `fetch(url, opts)`.

- [ ] **Step 5: Test the full UI in browser**

With the server running:
1. Navigate to `/admin/evals`
2. Verify the 4 sections render without errors
3. Click "+ New Config" — panel should open with prefilled prompts
4. Create a config → should appear in table, page reloads
5. Click "View prompts" on a built-in config → modal should show system + user prompts
6. Click "⋮" on a template → context menu should appear with Duplicate / Delete
7. Click "View Results (N)" → modal should open and be scrollable
8. Expand a result → per-video table should appear

---

## Task 7: Integration Test — Run a Custom Config Through the Full Pipeline

- [ ] **Step 1: Create a test custom config via the UI** (or via curl)

```bash
curl -u :$DASHBOARD_PASSWORD -X POST http://localhost:8000/admin/evals/custom-config/create \
  -H "Content-Type: application/json" \
  -d '{"name":"test_sonnet","description":"Test Sonnet","pipeline":"two_pass",
       "pass1":{"model":"claude-sonnet-4-6","max_tokens":4096,"temperature":0.8,
                "top_p":null,"top_k":null,
                "system_prompt":"Find every investment vehicle.",
                "user_prompt_template":"Language: {language}\n{title_hint}\nTranscript:\n{transcript}\n\nReturn JSON: {\"stocks\":[{\"ticker\":\"AAPL\",\"company_name\":\"Apple\"}]}"},
       "pass2":{"model":"claude-sonnet-4-6","max_tokens":8192,"temperature":0.8,
                "top_p":null,"top_k":null,
                "system_prompt":"Analyze each stock.",
                "user_prompt_template":"Language: {language}\n{title_hint}\nAnalyze {n} stock{plural}:\n{stock_list}\n\nTranscript:\n{transcript}\n\nReturn JSON: {\"mentions\":[{\"ticker\":\"AAPL\",\"company_name\":\"Apple\",\"is_real_stock_mention\":true,\"sentiment\":\"bullish\",\"confidence\":0.8,\"recommendation\":\"buy\",\"mention_count\":2,\"context\":\"...\"}]}"}}'
```

Expected: `{"name":"test_sonnet","ok":true}`

- [ ] **Step 2: Verify config appears on the evals page**

Navigate to `/admin/evals` → custom config `test_sonnet` should appear in the configs table.

- [ ] **Step 3: Delete the test config**

```bash
curl -u :$DASHBOARD_PASSWORD -X POST http://localhost:8000/admin/evals/custom-config/test_sonnet/delete
```

- [ ] **Step 4: Commit**

```bash
cd /Users/mitjawilms/DeInfluencer
git add evals/custom_store.py evals/executor.py evals/custom_configs/.gitkeep \
        evals/runner.py evals/store.py main.py templates/evals.html
git commit -m "feat: eval custom configs with model/prompt/sampling params, results modal, prompt viewer"
```
