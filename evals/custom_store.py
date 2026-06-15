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
    # Gemini — requires GEMINI_API_KEY in .env and google-generativeai installed
    "gemini-2.5-flash",
    "gemini-2.0-flash-001",
]

# Default prompts — match current brain.py production prompts exactly
DEFAULT_PASS1_SYSTEM = (
    "You are a specialist financial transcript scanner with deep experience reading "
    "unpunctuated auto-generated YouTube transcripts in German and English. "
    "Your sole job: find EVERY investment vehicle mentioned — stocks, ETFs, crypto, and commodities. "
    "Cast a wide net. No sentiment, no judgement — discovery only. "
    "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
    "Prefer US ADR ticker where one exists; otherwise use local format (e.g. SAP.DE). "
    "For commodities use the standard ETF proxy ticker: "
    "gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
    "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
    "For commodities and crypto: company_name must be the literal string 'NULL'. "
    "Do not invent tickers not present in the transcript."
)

DEFAULT_PASS1_USER = (
    "Language: {language}\n{title_hint}\nTranscript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    'Required output format:\n{{"stocks": [{{"ticker": "AAPL", "company_name": "Apple Inc.", "asset_type": "stock"}}, '
    '{{"ticker": "GLD", "company_name": "NULL", "asset_type": "commodity"}}]}}'
)

DEFAULT_PASS1B_SYSTEM = (
    "You are an exhaustive financial transcript scanner. Extract EVERY company or "
    "investment vehicle that is named or clearly implied — cast the widest possible net. "
    "Pass 2 will filter; your only job is to find. "
    "Capture: companies mentioned negatively ('I'm avoiding Pfizer', 'stay away from Intel'), "
    "companies used in comparisons ('unlike Apple...', 'better than Microsoft'), "
    "companies referred to by nickname when clear from context, "
    "and company names without explicit ticker mention. "
    "Fix transcription errors ('in Vidia' = NVIDIA, 'A MD' = AMD). "
    "Prefer US ADR ticker where one exists; otherwise use local format (e.g. SAP.DE). "
    "For commodities use the standard ETF proxy ticker: "
    "gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
    "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
    "For commodities and crypto: company_name must be the literal string 'NULL'. "
    "When in doubt, include — do not self-filter."
)

DEFAULT_PASS1B_USER = (
    "Language: {language}\n{title_hint}\nTranscript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    'Required output format:\n{{"stocks": [{{"ticker": "AAPL", "company_name": "Apple Inc.", "asset_type": "stock"}}, '
    '{{"ticker": "GLD", "company_name": "NULL", "asset_type": "commodity"}}]}}'
)

DEFAULT_PASS2_SYSTEM = (
    "You are a sharp-tongued senior financial analyst expert at cutting through vague YouTuber commentary to identify the real signal. "
    "You know the difference between genuine conviction and performative neutrality. "
    "For each investment vehicle in the provided list: return exactly one mention object. "
    "Do not skip any. Do not add tickers beyond those listed. "
    "Use the exact ticker string as provided. "
    "If a vehicle cannot be found in the transcript: is_real_stock_mention=false, confidence=0.0, mention_count=0, explain in context. "
    "Sentiment: lean toward bullish/bearish when any directional signal is present — "
    "reserve neutral for genuinely balanced or purely informational mentions. "
    "Recommendation must be exactly one of: buy / sell / hold / watch / reference. "
    "Use 'watch' when the creator expresses clear bullish interest but is explicitly waiting "
    "(for earnings, a dip, a catalyst, or more research) — not a current buy. "
    "Use 'reference' for passing mentions with no investment intent. "
    "Use 'hold' only when the creator already owns the asset and is keeping it. "
    "Confidence reflects clarity of sentiment expression, not certainty about the asset's prospects."
)

DEFAULT_PASS2_USER = (
    "Language: {language}\n{title_hint}\n"
    "Analyze exactly {n} investment vehicle{plural}:\n{stock_list}\n\n"
    "Transcript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    '{{"mentions": [{{"ticker": "AAPL", "company_name": "Apple Inc.", "asset_type": "stock", '
    '"is_real_stock_mention": true, "sentiment": "bullish", "confidence": 0.82, '
    '"recommendation": "buy", "mention_count": 4, "context": "..."}}, '
    '{{"ticker": "GLD", "company_name": "NULL", "asset_type": "commodity", '
    '"is_real_stock_mention": true, "sentiment": "bullish", "confidence": 0.75, '
    '"recommendation": "watch", "mention_count": 2, "context": "Host likes gold but waiting for a pullback."}}, '
    '{{"ticker": "TSLA", "company_name": "Tesla Inc.", "asset_type": "stock", '
    '"is_real_stock_mention": false, "sentiment": "neutral", "confidence": 0.0, '
    '"recommendation": "reference", "mention_count": 0, "context": "Not found in transcript."}}]}}'
)

DEFAULT_PASS3_SYSTEM = (
    "You are a meticulous fact-checker reviewing extracted stock mentions from a YouTube transcript. "
    "Your job: verify each mention is accurate, fix any errors in sentiment/confidence/recommendation, "
    "and remove any hallucinated stocks. Be conservative — when in doubt, keep the original. "
    "Return the corrected list using exactly the same JSON format. "
    "Do not add new tickers. Do not remove tickers unless they are clearly wrong. "
    "Valid recommendation values: buy / sell / hold / watch / reference. "
    "'watch' = bullish interest but creator is explicitly waiting. "
    "'hold' = creator already owns it and is keeping it. 'reference' = no investment intent. "
    "IMPORTANT: Cross-check every ticker against the company_name using your knowledge. "
    "Speakers sometimes state wrong ticker symbols — if the company_name clearly identifies a company "
    "whose correct ticker differs from what was extracted, correct it. "
    "Example: 'VersaMet Royalties Corp' with ticker 'VME' should be corrected to ticker 'VMET'."
)

DEFAULT_PASS3_USER = (
    "Language: {language}\n{title_hint}\n"
    "Below are {n} stock mention(s) extracted from a transcript. "
    "Review each one against the transcript and correct any errors in sentiment, confidence, recommendation, or context.\n\n"
    "Extracted mentions to verify:\n{mentions_json}\n\n"
    "Transcript:\n{transcript}\n\n"
    "Return ONLY valid JSON. No preamble, no markdown.\n\n"
    '{{"mentions": [{{"ticker": "AAPL", "company_name": "Apple Inc.", '
    '"is_real_stock_mention": true, "sentiment": "bullish", "confidence": 0.82, '
    '"recommendation": "buy", "mention_count": 4, "context": "..."}}]}}'
)

DEFAULT_SINGLE_SYSTEM = (
    "You are a specialist financial transcript analyst. "
    "Extract every investment vehicle discussed as an investment — stocks, ETFs, crypto, and commodities. "
    "For commodities use ETF proxy tickers: gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER. "
    "Do NOT use spot/futures tickers like XAUUSD or GC=F. "
    "For commodities and crypto: company_name must be the literal string 'NULL'. "
    "Fix transcription errors. Return only valid JSON. "
    "Recommendation must be one of: buy / sell / hold / watch / reference. "
    "'watch' = bullish interest but creator is explicitly waiting (for earnings, a dip, a catalyst). "
    "'hold' = creator already owns it and is keeping it. 'reference' = passing mention, no investment intent."
)

DEFAULT_SINGLE_USER = (
    "You are analyzing a finance YouTube video transcript. Language: {language}.\n"
    "{title_hint}\n"
    "IMPORTANT: Be THOROUGH. Find EVERY investment vehicle — stocks, ETFs, crypto, and commodities.\n\n"
    "Rules:\n"
    "1. Extract ALL stocks, ETFs, crypto, or commodities mentioned as investments\n"
    "2. For commodities use ETF proxy tickers: gold=GLD, silver=SLV, oil=USO, natural gas=UNG, copper=CPER\n"
    "3. For commodities and crypto: company_name must be the literal string 'NULL'\n"
    "4. asset_type: 'stock' / 'etf' / 'crypto' / 'commodity'\n"
    "5. Fix transcription errors: 'in Vidia' = NVIDIA, 'A MD' = AMD\n"
    "6. Ambiguous sentiment = neutral + low confidence\n"
    "7. Recommendation: buy / sell / hold / watch / reference\n"
    "   'watch' = bullish but explicitly waiting (earnings, dip, catalyst)\n"
    "   'hold' = already owns it and keeping it\n"
    "   'reference' = passing mention, no investment intent\n"
    "8. Return ONLY valid JSON.\n\n"
    "Transcript:\n{transcript}\n\n"
    'Required output format:\n{{"mentions": [{{"ticker": "AAPL", "company_name": "Apple", '
    '"asset_type": "stock", "mention_count": 3, "sentiment": "bullish", "confidence": 0.85, '
    '"recommendation": "buy", "context": "..."}}, '
    '{{"ticker": "GLD", "company_name": "NULL", "asset_type": "commodity", '
    '"mention_count": 2, "sentiment": "bullish", "confidence": 0.8, '
    '"recommendation": "watch", "context": "Host likes gold but waiting for a pullback."}}]}}'
)


_HIDDEN_FILE = os.path.join(_DIR, "_hidden_builtins.json")


def _load_hidden() -> set:
    if not os.path.exists(_HIDDEN_FILE):
        return set()
    try:
        with open(_HIDDEN_FILE) as fh:
            return set(json.load(fh))
    except Exception:
        return set()


def _save_hidden(names: set) -> None:
    os.makedirs(_DIR, exist_ok=True)
    with open(_HIDDEN_FILE, "w") as fh:
        json.dump(sorted(names), fh)


def hide_builtin(name: str) -> None:
    """Mark a built-in config as hidden (deleted from the UI)."""
    h = _load_hidden()
    h.add(name)
    _save_hidden(h)


def is_builtin_hidden(name: str) -> bool:
    return name in _load_hidden()


def hidden_builtins() -> set:
    return _load_hidden()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip())


def _path(name: str) -> str:
    return os.path.join(_DIR, f"{_slug(name)}.json")


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


def list_configs() -> list:
    """Return all custom configs, newest first (by created_at)."""
    os.makedirs(_DIR, exist_ok=True)
    files = glob.glob(os.path.join(_DIR, "*.json"))
    configs = []
    for f in files:
        if os.path.basename(f).startswith("_"):
            continue  # skip meta files (_hidden_builtins.json, _version_registry.json)
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


def get_config(name: str) -> dict | None:
    p = _path(name)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        data = json.load(fh)
    if "versions" not in data:
        data = _migrate(data)
        with open(p, "w") as fh:
            json.dump(data, fh, indent=2)
    return data


def config_exists(name: str) -> bool:
    return os.path.exists(_path(name))


def save_config(data: dict) -> str:
    """Persist a NEW custom config as v1. Returns the slug name used."""
    from datetime import datetime
    os.makedirs(_DIR, exist_ok=True)
    slug = _slug(data["name"])
    now = datetime.now().isoformat()
    pipeline = data.get("pipeline", "two_pass")
    if pipeline == "graph":
        pass_data = {
            "passes":      data.get("passes", []),
            "connections": data.get("connections", []),
        }
    else:
        pass_keys = [k for k in ("pass1", "pass1b", "pass2", "pass3") if k in data]
        pass_data = {k: data[k] for k in pass_keys}
    pass_data["version"] = 1
    pass_data["created_at"] = now
    versioned = {
        "name":        slug,
        "description": data.get("description", slug),
        "pipeline":    pipeline,
        "created_at":  now,
        "versions":    [pass_data],
    }
    with open(_path(slug), "w") as fh:
        json.dump(versioned, fh, indent=2)
    return slug


def add_version(name: str, pass_data: dict) -> int:
    """Append a new version to an existing config. Returns the new version number."""
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
    """Return a specific version entry."""
    cfg = get_config(name)
    if cfg is None:
        return None
    for v in cfg["versions"]:
        if v["version"] == version_num:
            return v
    return None


def list_versions(name: str) -> list:
    """Return all versions for a config, sorted ascending."""
    cfg = get_config(name)
    if cfg is None:
        return []
    return sorted(cfg["versions"], key=lambda v: v["version"])


def latest_version(name: str) -> dict | None:
    """Return the highest-version entry for a config."""
    versions = list_versions(name)
    return versions[-1] if versions else None


def delete_config(name: str) -> bool:
    p = _path(name)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def defaults_for_pipeline(pipeline: str) -> dict:
    """Return default pass config dicts for a given pipeline type."""
    two_pass_base = {
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
    if pipeline == "two_pass":
        return two_pass_base
    if pipeline == "three_pass":
        return {
            **two_pass_base,
            "pass3": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 8192,
                "temperature": 1.0,
                "top_p": None,
                "top_k": None,
                "system_prompt": DEFAULT_PASS3_SYSTEM,
                "user_prompt_template": DEFAULT_PASS3_USER,
            },
        }
    if pipeline == "graph":
        return {
            "passes": [
                {
                    "id": "p1", "role": "discovery", "label": "Discovery",
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 4096, "temperature": 1.0,
                    "system_prompt": DEFAULT_PASS1_SYSTEM,
                    "user_prompt_template": DEFAULT_PASS1_USER,
                    "position": {"x": 60, "y": 100},
                },
                {
                    "id": "p2", "role": "analysis", "label": "Analysis",
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 8192, "temperature": 1.0,
                    "system_prompt": DEFAULT_PASS2_SYSTEM,
                    "user_prompt_template": DEFAULT_PASS2_USER,
                    "position": {"x": 380, "y": 100},
                },
            ],
            "connections": [
                {"from_pass": "p1", "to_pass": "p2", "port": "stock_list"}
            ],
        }
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
