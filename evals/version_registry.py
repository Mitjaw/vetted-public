"""
Version registry for eval configs.

Each config name maps to a list of {version, hash, created} entries.
A new version is assigned whenever the config's content hash changes.

Hash inputs:
  - Custom configs: pipeline + pass1/pass2/pass3 params (excludes name/description/created_at)
  - Built-in configs: source code of the run_fn (auto-detects code changes)

Registry stored at evals/custom_configs/_version_registry.json
"""

import hashlib
import inspect
import json
import os
from datetime import datetime

_REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "custom_configs", "_version_registry.json"
)


def _load() -> dict:
    if not os.path.exists(_REGISTRY_PATH):
        return {}
    try:
        with open(_REGISTRY_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
    with open(_REGISTRY_PATH, "w") as fh:
        json.dump(data, fh, indent=2)


def compute_hash_custom(config_dict: dict) -> str:
    """Hash the pass params of a custom config dict."""
    relevant = {
        k: config_dict[k]
        for k in ("pipeline", "pass1", "pass2", "pass3")
        if k in config_dict
    }
    return hashlib.sha256(
        json.dumps(relevant, sort_keys=True).encode()
    ).hexdigest()[:12]


def compute_hash_builtin(run_fn) -> str:
    """Hash the source code of a built-in config's run_fn."""
    try:
        src = inspect.getsource(run_fn)
    except (OSError, TypeError):
        src = getattr(run_fn, "__name__", str(run_fn))
    return hashlib.sha256(src.encode()).hexdigest()[:12]


def resolve_version(config_name: str, config_hash: str) -> int:
    """
    Return the existing version number for this hash, or create a new version.
    Version numbers start at 1 and increment.
    """
    reg = _load()
    entries = reg.get(config_name, [])
    for entry in entries:
        if entry["hash"] == config_hash:
            return entry["version"]
    new_v = max((e["version"] for e in entries), default=0) + 1
    entries.append({
        "version": new_v,
        "hash":    config_hash,
        "created": datetime.now().isoformat(),
    })
    reg[config_name] = entries
    _save(reg)
    return new_v


def get_history(config_name: str) -> list:
    """Return [{version, hash, created}] sorted by version ascending."""
    return sorted(_load().get(config_name, []), key=lambda e: e["version"])
