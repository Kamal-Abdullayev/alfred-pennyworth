# runner.py — loads an agent YAML, runs one task through the SDK, logs everything.
#
# Boundaries enforced here, not by prompt:
#   - anything outside the role's allowed_tools is denied in a PreToolUse hook
#   - setting_sources=[] so agents don't inherit ~/.claude settings/hooks/MCPs
#   - structured output enforced by the SDK (output_format) and re-validated
#     against the role's contract (contracts.py)
#   - wall-clock limit per run (max_minutes)
import asyncio
import json
import logging
import os
import string
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from claude_agent_sdk import (
    ClaudeSDKClient, ClaudeAgentOptions,
    AssistantMessage, TextBlock, ThinkingBlock, ToolUseBlock, ResultMessage,
    HookMatcher,
)

import connectors
import contracts
import pricing

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
WORKSPACE = ROOT / "workspace"   # sandbox for tasks with no project_dir

# Load secrets (e.g. GITLAB_TOKEN) from .env into this process's environment.
# MCP subprocesses inherit this environment, so tokens never live in the YAML.
load_dotenv(ROOT / ".env", override=True)

# SDK-internal tools that must never be gated: StructuredOutput carries the answer.
ALWAYS_ALLOWED = {"StructuredOutput"}


def _expand_env(obj, mapping):
    """Recursively expand ${VAR} placeholders in an MCP server config."""
    if isinstance(obj, dict):
        return {k: _expand_env(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v, mapping) for v in obj]
    if isinstance(obj, str):
        return string.Template(obj).safe_substitute(mapping)
    return obj


def is_allowed(tool: str, rules: list[str]) -> bool:
    """Allow rules are bare tool names or prefix wildcards like mcp__gitlab__*."""
    if tool in ALWAYS_ALLOWED:
        return True
    for r in rules:
        if r.endswith("*") and tool.startswith(r[:-1]):
            return True
        if r == tool:
            return True
    return False


def make_logger(name, level):
    LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger(name)
    if not log.handlers:  # daemon loops call this repeatedly; don't stack handlers
        handler = logging.FileHandler(LOG_DIR / f"{name}.jsonl")
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    log.setLevel(level.upper())
    return log


def log_event(log, kind, payload):
    log.info(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "data": payload,
    }, default=str))


def make_human_logger(name):
    """Second channel: logs/<name>.log — the same story, readable by a person."""
    LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger(f"{name}.human")
    if not log.handlers:
        handler = logging.FileHandler(LOG_DIR / f"{name}.log")
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    log.setLevel("INFO")
    return log


def human(hlog, text):
    hlog.info(f"{datetime.now().strftime('%H:%M:%S')}  {text}")


def shorten(value, limit=200):
    s = " ".join(str(value).split())
    return s if len(s) <= limit else s[:limit] + " ..."


def load_config(config_path: str) -> dict:
    return yaml.safe_load(open(ROOT / config_path))


def _usage_get(u, *keys, default=0):
    """model_usage entries differ by SDK version (dict vs object, camel vs snake)."""
    for k in keys:
        if isinstance(u, dict) and k in u and u[k] is not None:
            return u[k]
        if hasattr(u, k) and getattr(u, k) is not None:
            return getattr(u, k)
    return default


async def run_agent(config_path: str, task: str, project_dir: str | None = None,
                    meta: dict | None = None) -> dict:
    """Run one agent on one task. Returns a dict:
       text, structured (validated contract dict or None), contract, subtype,
       is_error, turns, duration_ms, cost_usd, model_usage, denied (list of tools),
       turn_log (per-message tokens + est cost, see pricing.py)
    """
    # The API-key trap: if this env var is set, the SDK bills API rates
    # instead of the subscription. Refuse to run rather than silently pay.
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is set. Unset it so agents use the subscription "
            "(CLAUDE_CODE_OAUTH_TOKEN / CLI login), not API billing."
        )

    meta = meta or {}
    cfg = load_config(config_path)
    log = make_logger(cfg["name"], cfg["logging"]["level"])
    hlog = make_human_logger(cfg["name"])
    allowed = list(cfg.get("allowed_tools", []))
    contract = cfg.get("contract")

    if project_dir:
        workdir = Path(project_dir).expanduser().resolve()
        if not workdir.is_dir():
            raise RuntimeError(f"project_dir does not exist: {workdir}")
    else:
        WORKSPACE.mkdir(exist_ok=True)
        workdir = WORKSPACE

    max_len = int(cfg["logging"].get("max_field_len", 100_000))
    denied: list[str] = []

    # --- hooks: the deterministic "what it does" trail + the allowlist gate ---
    async def pre_tool(input_data, tool_use_id, context):
        try:
            tool = input_data["tool_name"]
            if not is_allowed(tool, allowed):
                denied.append(tool)
                log_event(log, "tool_denied", {"tool_use_id": tool_use_id, "tool": tool})
                human(hlog, f"  XX {tool}: DENIED (not in allowed_tools)")
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Tool '{tool}' is not in {cfg['name']}'s allowed_tools.",
                }}
            if cfg["logging"].get("log_tool_io", True):
                log_event(log, "tool_call", {"tool_use_id": tool_use_id, "tool": tool,
                                             "input": input_data["tool_input"]})
                human(hlog, f"  -> {tool}: {shorten(input_data['tool_input'], 160)}")
        except Exception as e:
            log_event(log, "hook_error", {"where": "pre_tool", "error": repr(e)})
        return {}

    async def post_tool(input_data, tool_use_id, context):
        try:
            if cfg["logging"].get("log_tool_io", True):
                out = str(input_data.get("tool_response"))
                log_event(log, "tool_result", {"tool_use_id": tool_use_id,
                                               "tool": input_data["tool_name"],
                                               "output": out[:max_len]})
                human(hlog, f"  <- {input_data['tool_name']} returned {len(out)} chars")
        except Exception as e:
            log_event(log, "hook_error", {"where": "post_tool", "error": repr(e)})
        return {}

    mcp_mapping = {**os.environ, "PROJECT_ROOT": str(ROOT), "PYTHON": os.sys.executable}
    mcp_servers = _expand_env(cfg.get("mcp_servers", {}), mcp_mapping)

    # Connectors configured in the UI and assigned to this role. Only their
    # non-mutating, discovered tools become allow rules; everything else is denied.
    db_servers, db_rules = connectors.role_servers(cfg["name"])
    for name, server in db_servers.items():
        if name in mcp_servers:
            log_event(log, "connector_shadowed", {"connector": name, "by": "yaml mcp_servers"})
            continue
        mcp_servers[name] = server
    allowed = allowed + [r for r in db_rules if r not in allowed]

    opt_kwargs = dict(
        model=cfg["model"],
        system_prompt=cfg["system_prompt"],
        allowed_tools=allowed,
        mcp_servers=mcp_servers,
        permission_mode=cfg.get("permission_mode", "default"),
        cwd=str(workdir),
        setting_sources=[],          # isolation: no ~/.claude settings, hooks or MCPs leak in
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre_tool])],
            "PostToolUse": [HookMatcher(hooks=[post_tool])],
        },
    )
    if cfg.get("builtin_tools") is not None:
        opt_kwargs["tools"] = list(cfg["builtin_tools"])
    if cfg.get("max_turns"):
        opt_kwargs["max_turns"] = cfg["max_turns"]
    if contract:
        opt_kwargs["output_format"] = {"type": "json_schema", "schema": contracts.schema_for(contract)}
    options = ClaudeAgentOptions(**opt_kwargs)

    timeout_s = float(cfg.get("max_minutes", 15)) * 60

    log_event(log, "request", {
        "task": task, "task_id": meta.get("id"), "chain_id": meta.get("chain_id"),
        "iteration": meta.get("iteration"), "workdir": str(workdir), "model": cfg["model"],
        "allowed_tools": allowed, "contract": contract, "max_minutes": timeout_s / 60,
    })
    human(hlog, "=" * 76)
    human(hlog, f"TASK {meta.get('id', '?')}  chain {meta.get('chain_id', '?')}  "
                f"round {meta.get('iteration', 1)}  [{cfg['name']}]")
    human(hlog, f"  goal:    {shorten(task, 300)}")
    human(hlog, f"  workdir: {workdir}")

    try:
        out = await asyncio.wait_for(
            _run(task, options, cfg, log, hlog, max_len), timeout=timeout_s)
    except asyncio.TimeoutError:
        log_event(log, "timeout", {"after_minutes": timeout_s / 60})
        human(hlog, f"  TIMEOUT after {timeout_s / 60:g} minutes — task will be marked failed")
        raise RuntimeError(
            f"agent '{cfg['name']}' exceeded its {timeout_s / 60:g} minute limit and was terminated")

    # Re-validate the structured output against the contract. The SDK already
    # enforced the schema; this catches SDK/contract drift and gives typed access.
    structured = None
    if contract and out["structured_raw"] is not None:
        try:
            structured = contracts.parse(contract, out["structured_raw"]).model_dump()
        except Exception as e:
            log_event(log, "contract_invalid", {"contract": contract, "error": str(e)[:2000]})
            human(hlog, f"  CONTRACT INVALID: {shorten(e, 300)}")
    elif contract:
        log_event(log, "contract_missing", {"contract": contract, "subtype": out["subtype"]})
        human(hlog, "  CONTRACT MISSING: run ended without structured output")

    result = {**out, "structured": structured, "contract": contract, "denied": denied}
    del result["structured_raw"]
    log_event(log, "response", {k: v for k, v in result.items() if k != "text"} | {"text": out["text"][:max_len]})
    human(hlog, f"  RESULT:  {shorten(out['text'], 500)}")
    return result


async def _run(task, options, cfg, log, hlog, max_len) -> dict:
    out = {"text": "", "structured_raw": None, "subtype": None, "is_error": False,
           "turns": 0, "duration_ms": 0, "cost_usd": 0.0, "model_usage": {}, "turn_log": []}
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task)
        # Don't return from inside this loop: bailing out early closes the
        # stream while hook callbacks may still be in flight.
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                # Per-message accounting: exact tokens from the API, cost from pricing.py.
                u = msg.usage or {}
                tools = [b.name for b in msg.content if isinstance(b, ToolUseBlock)]
                text_chars = sum(len(b.text) for b in msg.content if isinstance(b, TextBlock))
                out["turn_log"].append({
                    "turn_index": len(out["turn_log"]) + 1, "model": msg.model, "message_id": msg.message_id,
                    "input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0),
                    "cache_read_tokens": int(u.get("cache_read_input_tokens") or 0),
                    "cache_write_tokens": int(u.get("cache_creation_input_tokens") or 0),
                    "est_cost_usd": pricing.estimate(msg.model, u), "tools": tools, "text_chars": text_chars,
                })
                for block in msg.content:
                    if isinstance(block, ThinkingBlock) and cfg["logging"].get("log_thinking") and block.thinking.strip():
                        log_event(log, "thinking", {"text": block.thinking[:max_len]})
                        human(hlog, f"  THINK:   {shorten(block.thinking, 240)}")
                    elif isinstance(block, TextBlock):
                        log_event(log, "say", {"text": block.text[:max_len]})
                        human(hlog, f"  SAY:     {shorten(block.text, 240)}")
            elif isinstance(msg, ResultMessage):
                out["text"] = msg.result or ""
                out["structured_raw"] = getattr(msg, "structured_output", None)
                out["subtype"] = getattr(msg, "subtype", None)
                out["is_error"] = bool(getattr(msg, "is_error", False))
                out["turns"] = getattr(msg, "num_turns", 0) or 0
                out["duration_ms"] = getattr(msg, "duration_ms", 0) or 0
                out["cost_usd"] = float(getattr(msg, "total_cost_usd", 0) or 0)
                mu = getattr(msg, "model_usage", None) or {}
                out["model_usage"] = {
                    model: {
                        "input_tokens": _usage_get(u, "inputTokens", "input_tokens"),
                        "output_tokens": _usage_get(u, "outputTokens", "output_tokens"),
                        "cache_read_tokens": _usage_get(u, "cacheReadInputTokens", "cache_read_input_tokens"),
                        "cache_write_tokens": _usage_get(u, "cacheCreationInputTokens", "cache_creation_input_tokens"),
                        "cost_usd": float(_usage_get(u, "costUSD", "cost_usd", default=0.0)),
                    } for model, u in mu.items()
                }
                log_event(log, "done", {"turns": out["turns"], "subtype": out["subtype"],
                                        "is_error": out["is_error"], "duration_ms": out["duration_ms"],
                                        "cost_usd": out["cost_usd"], "model_usage": out["model_usage"]})
                human(hlog, f"  DONE:    {out['turns']} turns, {out['duration_ms'] / 1000:.0f}s, est≈${out['cost_usd']:.4f}")
    return out
