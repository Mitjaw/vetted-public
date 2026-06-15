"""
Builds a run_fn callable from a custom config dict.

run_fn signature: (transcript, title, language) -> list[mention_dict]

Supports:
  pipeline: "single_pass" | "two_pass" | "three_pass" | "dual_discovery"
  pass params: model, max_tokens, temperature, top_p, top_k,
               system_prompt, user_prompt_template

three_pass adds a verification step after analysis:
  Pass 3 user_prompt_template extra placeholders:
    {mentions_json}  — JSON string of the pass-2 mention list
    {n}              — number of mentions

dual_discovery runs pass1 (standard) and pass1b (aggressive) independently,
merges their discovered ticker sets, then runs pass2 on the union.
Config keys: pass1, pass1b, pass2
"""

import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import anthropic
from brain import _strip_markdown, _LANG_MAP, _MAX_DISCOVERED_STOCKS, _calc_cost


def _merge_discovered(a: list, b: list) -> list:
    """Merge two discovery lists, deduplicating by ticker (case-insensitive)."""
    seen = {}
    for item in a + b:
        key = item.get("ticker", "").upper()
        if key and key not in seen:
            seen[key] = item
    return list(seen.values())

log = logging.getLogger(__name__)


def _is_gemini(model: str) -> bool:
    return model.startswith("gemini-")


def _call_pass(pass_cfg: dict, system: str, user_msg: str) -> tuple[str, dict]:
    """
    Dispatch a single LLM call to either Anthropic or Gemini based on model name.
    Returns (response_text, usage_dict).
    """
    model = pass_cfg["model"]
    max_tokens = int(pass_cfg.get("max_tokens", 4096))
    temperature = float(pass_cfg.get("temperature", 1.0))

    if _is_gemini(model):
        try:
            from google import genai as google_genai
            from google.genai import types as google_genai_types
        except ImportError:
            raise RuntimeError(
                "google-genai not installed. Run: pip install google-genai"
            )
        client = google_genai.Client()  # reads GEMINI_API_KEY from env automatically

        _MAX_RETRIES = 3
        for attempt in range(_MAX_RETRIES):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=user_msg,
                    config=google_genai_types.GenerateContentConfig(
                        system_instruction=system or None,
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                    ),
                )
                text = resp.text
                in_tok  = resp.usage_metadata.prompt_token_count
                out_tok = resp.usage_metadata.candidates_token_count
                usage = {"input_tokens": in_tok, "output_tokens": out_tok,
                         "cost_usd": _calc_cost(model, in_tok, out_tok)}
                return text, usage
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Parse retry delay from error message if present
                    m = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", err_str, re.IGNORECASE)
                    wait = float(m.group(1)) + 2 if m else 30.0
                    if attempt < _MAX_RETRIES - 1:
                        log.warning("Gemini 429 rate limit on %s — waiting %.0fs then retrying (attempt %d/%d)",
                                    model, wait, attempt + 1, _MAX_RETRIES)
                        time.sleep(wait)
                        continue
                    else:
                        log.error("Gemini 429 rate limit on %s — exhausted %d retries. "
                                  "Free-tier Gemini 2.5 Flash has only 20 req/day; "
                                  "consider upgrading or using gemini-2.0-flash.",
                                  model, _MAX_RETRIES)
                raise  # non-429 errors or final retry: let caller handle
    else:
        client = anthropic.Anthropic()
        kw = {"model": model, "max_tokens": max_tokens, "temperature": temperature}
        top_p = pass_cfg.get("top_p")
        if top_p is not None:
            kw["top_p"] = float(top_p)
        top_k = pass_cfg.get("top_k")
        if top_k is not None:
            kw["top_k"] = int(top_k)
        if system:
            kw["system"] = system
        resp = client.messages.create(**kw, messages=[{"role": "user", "content": user_msg}])
        text = resp.content[0].text
        u = resp.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cost_usd": _calc_cost(resp.model, u.input_tokens, u.output_tokens)}
        return text, usage


def _add_usage(a: dict, b: dict) -> dict:
    return {
        "input_tokens":  a["input_tokens"]  + b["input_tokens"],
        "output_tokens": a["output_tokens"] + b["output_tokens"],
        "cost_usd":      round(a["cost_usd"] + b["cost_usd"], 6),
    }


_ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def build_run_fn(config_dict: dict):
    """Returns a callable(transcript, title, language) -> (list[mention_dict], usage_dict)."""
    pipeline = config_dict.get("pipeline", "two_pass")
    pass1    = config_dict["pass1"]
    pass1b   = config_dict.get("pass1b")
    pass2    = config_dict.get("pass2")
    pass3    = config_dict.get("pass3")

    def run_fn(transcript: str, title: str = "", language: str = "en"):
        lang_label = _LANG_MAP.get(language, "English")
        title_hint = f"\nVideo title: {title}\n" if title else ""

        if pipeline == "single_pass":
            user_msg = pass1["user_prompt_template"].format(
                language=lang_label, title_hint=title_hint, transcript=transcript,
            )
            try:
                raw, usage = _call_pass(pass1, pass1.get("system_prompt", ""), user_msg)
                mentions = json.loads(_strip_markdown(raw)).get("mentions", [])
                return mentions, usage
            except Exception as e:
                log.error("executor single_pass failed: %s", e)
                return [], _ZERO_USAGE

        # dual_discovery: run pass1 + pass1b, merge, then pass2
        if pipeline == "dual_discovery":
            if not pass1b or not pass2:
                log.error("dual_discovery requires pass1b and pass2 keys")
                return [], _ZERO_USAGE

            def _run_discovery(pass_cfg):
                user = pass_cfg["user_prompt_template"].format(
                    language=lang_label, title_hint=title_hint, transcript=transcript,
                )
                raw, u = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user)
                stocks = json.loads(_strip_markdown(raw)).get("stocks", [])
                return stocks, u

            try:
                discovered_a, usage_1a = _run_discovery(pass1)
            except Exception as e:
                log.error("dual_discovery pass1a failed: %s", e)
                return [], _ZERO_USAGE
            try:
                discovered_b, usage_1b = _run_discovery(pass1b)
            except Exception as e:
                log.warning("dual_discovery pass1b failed: %s — using pass1a only", e)
                discovered_b, usage_1b = [], _ZERO_USAGE

            discovered = _merge_discovered(discovered_a, discovered_b)
            usage = _add_usage(usage_1a, usage_1b)

            if not discovered:
                return [], usage
            if len(discovered) > _MAX_DISCOVERED_STOCKS:
                discovered = discovered[:_MAX_DISCOVERED_STOCKS]

            n          = len(discovered)
            stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
            user2 = pass2["user_prompt_template"].format(
                language=lang_label, title_hint=title_hint, transcript=transcript,
                n=n, plural="s" if n != 1 else "", stock_list=stock_list,
            )
            try:
                raw2, u2 = _call_pass(pass2, pass2.get("system_prompt", ""), user2)
                mentions = json.loads(_strip_markdown(raw2)).get("mentions", [])
                usage = _add_usage(usage, u2)
            except Exception as e:
                log.error("dual_discovery pass2 failed: %s", e)
                return [], usage

            discovered_tickers = {s["ticker"].upper() for s in discovered}
            filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]
            return filtered, usage

        # two_pass / three_pass — Pass 1
        user1 = pass1["user_prompt_template"].format(
            language=lang_label, title_hint=title_hint, transcript=transcript,
        )
        try:
            raw1, usage = _call_pass(pass1, pass1.get("system_prompt", ""), user1)
            discovered = json.loads(_strip_markdown(raw1)).get("stocks", [])
        except Exception as e:
            log.error("executor two_pass pass1 failed: %s", e)
            return [], _ZERO_USAGE

        if not discovered:
            return [], usage
        if len(discovered) > _MAX_DISCOVERED_STOCKS:
            discovered = discovered[:_MAX_DISCOVERED_STOCKS]

        n          = len(discovered)
        stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
        user2 = pass2["user_prompt_template"].format(
            language=lang_label, title_hint=title_hint, transcript=transcript,
            n=n, plural="s" if n != 1 else "", stock_list=stock_list,
        )
        try:
            raw2, u2 = _call_pass(pass2, pass2.get("system_prompt", ""), user2)
            mentions = json.loads(_strip_markdown(raw2)).get("mentions", [])
            usage = _add_usage(usage, u2)
        except Exception as e:
            log.error("executor two_pass pass2 failed: %s", e)
            return [], usage

        discovered_tickers = {s["ticker"].upper() for s in discovered}
        filtered = [m for m in mentions if m.get("ticker", "").upper() in discovered_tickers]

        if pipeline != "three_pass" or not pass3:
            return filtered, usage

        # Pass 3 — verification
        n3            = len(filtered)
        mentions_json = json.dumps(filtered, ensure_ascii=False, indent=2)
        user3 = pass3["user_prompt_template"].format(
            language=lang_label, title_hint=title_hint, transcript=transcript,
            mentions_json=mentions_json, n=n3,
        )
        try:
            raw3, u3 = _call_pass(pass3, pass3.get("system_prompt", ""), user3)
            verified = json.loads(_strip_markdown(raw3)).get("mentions", [])
            usage = _add_usage(usage, u3)
        except Exception as e:
            log.error("executor three_pass pass3 failed: %s — returning pass2 result", e)
            return filtered, usage

        v_tickers = {s["ticker"].upper() for s in discovered}
        v_filtered = [m for m in verified if m.get("ticker", "").upper() in v_tickers]
        return v_filtered, usage

    return run_fn


def config_to_runnable(config_dict: dict, version_num: int = None) -> dict:
    """Convert a stored versioned custom config dict into a runner-compatible config dict.

    version_num: if given, use that specific version; otherwise use the latest.
    Supports both legacy (pass1/pass2/pass3) and graph (passes/connections) version formats.
    """
    all_versions = sorted(config_dict["versions"], key=lambda v: v["version"])
    if version_num is not None:
        v = next((v for v in all_versions if v["version"] == version_num), None)
        if v is None:
            v = all_versions[-1]  # fallback to latest if requested version not found
    else:
        v = all_versions[-1]

    if "passes" in v:
        # Graph format — capture version snapshot in closure
        _v = v
        def run_fn(transcript: str, title: str = "", language: str = "en"):
            return execute_graph(_v, transcript, title, language)
        raw_cfg = {
            "pipeline":    "graph",
            "passes":      v.get("passes"),
            "connections": v.get("connections", []),
        }
    else:
        # Legacy flat format
        raw_cfg = {
            "pipeline": config_dict["pipeline"],
            "pass1":    v.get("pass1"),
            "pass1b":   v.get("pass1b"),
            "pass2":    v.get("pass2"),
            "pass3":    v.get("pass3"),
        }
        run_fn = build_run_fn(raw_cfg)

    return {
        "name":        config_dict["name"],
        "description": config_dict.get("description", config_dict["name"]),
        "run_fn":      run_fn,
        "_raw":        raw_cfg,
        "_version":    v["version"],
    }


# ---------------------------------------------------------------------------
# Graph pipeline execution
# ---------------------------------------------------------------------------

def _topo_sort(passes: dict, connections: list) -> list:
    """Topological sort of pass IDs. Disconnected nodes are appended at end."""
    from collections import defaultdict, deque
    in_degree = {pid: 0 for pid in passes}
    adj = defaultdict(list)
    for conn in connections:
        frm, to = conn.get("from_pass"), conn.get("to_pass")
        if frm in passes and to in passes:
            adj[frm].append(to)
            in_degree[to] += 1
    queue = deque(pid for pid in passes if in_degree[pid] == 0)
    order = []
    while queue:
        pid = queue.popleft()
        order.append(pid)
        for nxt in adj[pid]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    for pid in passes:
        if pid not in order:
            order.append(pid)
    return order


def execute_graph(graph_version: dict, transcript: str, title: str, language: str):
    """Execute a graph-format pipeline version. Returns (mentions, usage)."""
    passes = {p["id"]: p for p in graph_version.get("passes", [])}
    connections = graph_version.get("connections", [])
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""

    order = _topo_sort(passes, connections)

    # incoming[pass_id] = list of (from_pass_id, port_type)
    incoming = {pid: [] for pid in passes}
    for conn in connections:
        frm, to, port = conn.get("from_pass"), conn.get("to_pass"), conn.get("port", "")
        if to in incoming:
            incoming[to].append((frm, port))

    output_store = {}  # pass_id -> {"type": "stock_list"|"mentions", "data": [...]}
    total_usage = dict(_ZERO_USAGE)

    for pass_id in order:
        pass_cfg = passes[pass_id]
        role = pass_cfg.get("role", "extraction")

        if role == "discovery":
            user_msg = pass_cfg["user_prompt_template"].format(
                language=lang_label, title_hint=title_hint, transcript=transcript,
            )
            try:
                raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
                stocks = json.loads(_strip_markdown(raw)).get("stocks", [])
            except Exception as e:
                log.error("graph pass %s (discovery) failed: %s", pass_id, e)
                stocks, usage = [], dict(_ZERO_USAGE)
            output_store[pass_id] = {"type": "stock_list", "data": stocks}
            total_usage = _add_usage(total_usage, usage)

        elif role == "analysis":
            all_stocks = []
            for (frm, port) in incoming[pass_id]:
                if frm in output_store:
                    upstream = output_store[frm]
                    if upstream["type"] == "stock_list" or port == "stock_list":
                        all_stocks.extend(upstream["data"])
            discovered = _merge_discovered(all_stocks, [])
            if len(discovered) > _MAX_DISCOVERED_STOCKS:
                discovered = discovered[:_MAX_DISCOVERED_STOCKS]
            if not discovered:
                output_store[pass_id] = {"type": "mentions", "data": []}
                continue
            n = len(discovered)
            stock_list = "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
            user_msg = pass_cfg["user_prompt_template"].format(
                language=lang_label, title_hint=title_hint, transcript=transcript,
                n=n, plural="s" if n != 1 else "", stock_list=stock_list,
            )
            try:
                raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
                mentions = json.loads(_strip_markdown(raw)).get("mentions", [])
                disc_tickers = {s["ticker"].upper() for s in discovered}
                mentions = [m for m in mentions if m.get("ticker", "").upper() in disc_tickers]
            except Exception as e:
                log.error("graph pass %s (analysis) failed: %s", pass_id, e)
                mentions, usage = [], dict(_ZERO_USAGE)
            output_store[pass_id] = {"type": "mentions", "data": mentions}
            total_usage = _add_usage(total_usage, usage)

        elif role == "verification":
            prev = []
            for (frm, port) in incoming[pass_id]:
                if frm in output_store:
                    upstream = output_store[frm]
                    if upstream["type"] == "mentions" or port == "mentions":
                        prev = upstream["data"]
                        break
            if not prev:
                output_store[pass_id] = {"type": "mentions", "data": []}
                continue
            n = len(prev)
            mentions_json_str = json.dumps(prev, ensure_ascii=False, indent=2)
            user_msg = pass_cfg["user_prompt_template"].format(
                language=lang_label, title_hint=title_hint, transcript=transcript,
                mentions_json=mentions_json_str, n=n,
            )
            try:
                raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
                verified = json.loads(_strip_markdown(raw)).get("mentions", [])
            except Exception as e:
                log.error("graph pass %s (verification) failed: %s — using upstream result", pass_id, e)
                verified, usage = prev, dict(_ZERO_USAGE)
            output_store[pass_id] = {"type": "mentions", "data": verified}
            total_usage = _add_usage(total_usage, usage)

        elif role == "extraction":
            user_msg = pass_cfg["user_prompt_template"].format(
                language=lang_label, title_hint=title_hint, transcript=transcript,
            )
            try:
                raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
                mentions = json.loads(_strip_markdown(raw)).get("mentions", [])
            except Exception as e:
                log.error("graph pass %s (extraction) failed: %s", pass_id, e)
                mentions, usage = [], dict(_ZERO_USAGE)
            output_store[pass_id] = {"type": "mentions", "data": mentions}
            total_usage = _add_usage(total_usage, usage)

    # Find final output: last mention-producing node in topological order
    final_mentions = []
    for pid in reversed(order):
        if output_store.get(pid, {}).get("type") == "mentions":
            final_mentions = output_store[pid]["data"]
            break

    return final_mentions, total_usage


def execute_single_pass_test(
    graph_version: dict,
    pass_id: str,
    transcript: str,
    title: str,
    language: str,
    gt_tickers: list = None,
) -> tuple:
    """Run one pass in isolation, providing contextually appropriate inputs.

    For discovery/extraction: feeds transcript directly.
    For analysis: auto-runs upstream discovery passes first; falls back to
        gt_tickers if upstream produces nothing.
    For verification: recursively runs the upstream analysis chain first.

    Returns (raw_text, parsed_dict, usage, output_type).
    output_type is 'stock_list' or 'mentions'.
    """
    passes = {p["id"]: p for p in graph_version.get("passes", [])}
    connections = graph_version.get("connections", [])

    if pass_id not in passes:
        raise ValueError(f"Pass '{pass_id}' not found in graph")

    pass_cfg = passes[pass_id]
    role = pass_cfg.get("role", "extraction")
    lang_label = _LANG_MAP.get(language, "English")
    title_hint = f"\nVideo title: {title}\n" if title else ""
    total_usage = dict(_ZERO_USAGE)

    if role == "discovery":
        user_msg = pass_cfg["user_prompt_template"].format(
            language=lang_label, title_hint=title_hint, transcript=transcript,
        )
        raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
        parsed = json.loads(_strip_markdown(raw))
        return raw, parsed, usage, "stock_list"

    if role == "extraction":
        user_msg = pass_cfg["user_prompt_template"].format(
            language=lang_label, title_hint=title_hint, transcript=transcript,
        )
        raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
        parsed = json.loads(_strip_markdown(raw))
        return raw, parsed, usage, "mentions"

    if role == "analysis":
        upstream_ids = [
            c["from_pass"] for c in connections
            if c.get("to_pass") == pass_id
        ]
        discovered = []
        for uid in upstream_ids:
            if uid in passes:
                disc_cfg = passes[uid]
                disc_user = disc_cfg["user_prompt_template"].format(
                    language=lang_label, title_hint=title_hint, transcript=transcript,
                )
                try:
                    disc_raw, disc_usage = _call_pass(disc_cfg, disc_cfg.get("system_prompt", ""), disc_user)
                    discovered.extend(json.loads(_strip_markdown(disc_raw)).get("stocks", []))
                    total_usage = _add_usage(total_usage, disc_usage)
                except Exception as e:
                    log.warning("single_pass_test: upstream discovery %s failed: %s", uid, e)
        if not discovered and gt_tickers:
            discovered = [{"ticker": t, "company_name": t, "asset_type": "stock"} for t in gt_tickers]
        discovered = _merge_discovered(discovered, [])
        n = len(discovered)
        stock_list = (
            "\n".join(f"- {s['ticker']} ({s['company_name']})" for s in discovered)
            if discovered else "(no stocks discovered)"
        )
        user_msg = pass_cfg["user_prompt_template"].format(
            language=lang_label, title_hint=title_hint, transcript=transcript,
            n=n, plural="s" if n != 1 else "", stock_list=stock_list,
        )
        raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
        total_usage = _add_usage(total_usage, usage)
        parsed = json.loads(_strip_markdown(raw))
        return raw, parsed, total_usage, "mentions"

    if role == "verification":
        upstream_ids = [
            c["from_pass"] for c in connections
            if c.get("to_pass") == pass_id
        ]
        prev_mentions = []
        for uid in upstream_ids:
            if uid in passes:
                try:
                    _, up_parsed, up_usage, _ = execute_single_pass_test(
                        graph_version, uid, transcript, title, language, gt_tickers
                    )
                    prev_mentions = up_parsed.get("mentions", [])
                    total_usage = _add_usage(total_usage, up_usage)
                    break
                except Exception as e:
                    log.warning("single_pass_test: upstream %s failed: %s", uid, e)
        if not prev_mentions:
            raise ValueError("No upstream mentions available for verification pass test")
        n = len(prev_mentions)
        mentions_json_str = json.dumps(prev_mentions, ensure_ascii=False, indent=2)
        user_msg = pass_cfg["user_prompt_template"].format(
            language=lang_label, title_hint=title_hint, transcript=transcript,
            mentions_json=mentions_json_str, n=n,
        )
        raw, usage = _call_pass(pass_cfg, pass_cfg.get("system_prompt", ""), user_msg)
        total_usage = _add_usage(total_usage, usage)
        parsed = json.loads(_strip_markdown(raw))
        return raw, parsed, total_usage, "mentions"

    raise ValueError(f"Unknown role: {role}")
