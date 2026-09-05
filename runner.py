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
    AssistantMessage, SystemMessage, TextBlock, ThinkingBlock, ToolUseBlock, ResultMessage,
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


TASK_LOG_DIR = LOG_DIR / "tasks"


def make_logger(name, level, task_id=None):
    """JSONL machine log. Every event goes to logs/<role>.jsonl (the role's history) AND,
    when a task id is known, to logs/tasks/<task_id>.jsonl — one file per run, which is
    what the UI's transcript view reads. Close with close_logger() after the run."""
    LOG_DIR.mkdir(exist_ok=True)
    if task_id is None:
        log = logging.getLogger(name)
        if not log.handlers:
            handler = logging.FileHandler(LOG_DIR / f"{name}.jsonl")
            handler.setFormatter(logging.Formatter("%(message)s"))
            log.addHandler(handler)
        log.setLevel(level.upper())
        return log
    TASK_LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger(f"{name}.task.{task_id}")
    log.propagate = False
    if not log.handlers:
        for path in (LOG_DIR / f"{name}.jsonl", TASK_LOG_DIR / f"{task_id}.jsonl"):
            h = logging.FileHandler(path)
            h.setFormatter(logging.Formatter("%(message)s"))
            log.addHandler(h)
    log.setLevel(level.upper())
    return log


def close_logger(log):
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)


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


# logs/<role>.log is meant to be READ by a person, so it is not truncated unless the
# role's YAML sets logging.human_max_len. The JSONL next to it is the machine record.
_HUMAN_MAX = {"limit": 0}


def shorten(value, limit=None):
    s = str(value)
    limit = _HUMAN_MAX["limit"] if limit is None or _HUMAN_MAX["limit"] == 0 else min(limit, _HUMAN_MAX["limit"])
    if not limit or len(s) <= limit:
        return s
    return s[:limit] + f" …[+{len(s) - limit} chars]"


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
    log = make_logger(cfg["name"], cfg["logging"]["level"], task_id=meta.get("id"))
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
    _HUMAN_MAX["limit"] = int(cfg["logging"].get("human_max_len", 0))   # 0 = never trim
    denied: list[str] = []

    # --- hooks: the deterministic "what it does" trail + the allowlist gate ---
    async def pre_tool(input_data, tool_use_id, context):
        try:
            tool = input_data["tool_name"]
            if not is_allowed(tool, allowed):
                denied.append(tool)
                log_event(log, "tool_denied", {"tool_use_id": tool_use_id, "tool": tool})
                connectors.log_denied(tool, {"task_id": meta.get("id"), "role": cfg["name"]})
                human(hlog, f"  XX {tool}: DENIED (not in allowed_tools)")
                return {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Tool '{tool}' is not in {cfg['name']}'s allowed_tools.",
                }}
            if cfg["logging"].get("log_tool_io", True):
                log_event(log, "tool_call", {"tool_use_id": tool_use_id, "tool": tool,
                                             "input": input_data["tool_input"]})
                human(hlog, f"  -> {tool}: {shorten(input_data['tool_input'])}")
        except Exception as e:
            log_event(log, "hook_error", {"where": "pre_tool", "error": repr(e)})
        return {}

    def _capture_asset(tool, response):
        """Excalidraw exports become files of the run: scene JSON, PNG/SVG images."""
        if not tool.startswith("mcp__excalidraw__") or not meta.get("id"):
            return
        blocks = response if isinstance(response, list) else [response]
        out_dir = ROOT / "data" / "assets" / str(meta["id"])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "image" and b.get("data"):
                import base64
                ext = ".svg" if "svg" in str(b.get("mimeType", "")) else ".png"
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / f"canvas-{len(list(out_dir.iterdir())) + 1}{ext}"
                path.write_bytes(base64.b64decode(b["data"]))
                log_event(log, "asset", {"tool": tool, "kind": "image", "path": str(path)})
            elif tool.endswith("export_scene") and b.get("type") == "text" and '"elements"' in str(b.get("text", ""))[:200]:
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / f"scene-{len(list(out_dir.iterdir())) + 1}.excalidraw"
                path.write_text(b["text"], encoding="utf-8")
                log_event(log, "asset", {"tool": tool, "kind": "scene", "path": str(path)})

    async def post_tool(input_data, tool_use_id, context):
        try:
            _capture_asset(input_data.get("tool_name", ""), input_data.get("tool_response"))
        except Exception as e:
            log_event(log, "hook_error", {"where": "capture_asset", "error": repr(e)})
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
        # Isolation by default: nothing from ~/.claude leaks in. `account_connectors: true`
        # in the role YAML switches to ["user"], which is the only way the CLI attaches the
        # MCP servers your claude.ai account provides (Atlassian, Microsoft 365, …) — at the
        # price of also loading ~/.claude/settings.json (permissions, hooks) into the run.
        setting_sources=(["user"] if cfg.get("account_connectors") else []),
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
        "account_connectors": bool(cfg.get("account_connectors")), "db_connectors": sorted(db_servers),
    })
    human(hlog, "=" * 76)
    human(hlog, f"TASK {meta.get('id', '?')}  chain {meta.get('chain_id', '?')}  "
                f"round {meta.get('iteration', 1)}  [{cfg['name']}]")
    human(hlog, f"  goal:    {shorten(task)}")
    human(hlog, f"  workdir: {workdir}")

    try:
        out = await asyncio.wait_for(
            _run(task, options, cfg, log, hlog, max_len, meta), timeout=timeout_s)
    except asyncio.TimeoutError:
        log_event(log, "timeout", {"after_minutes": timeout_s / 60})
        human(hlog, f"  TIMEOUT after {timeout_s / 60:g} minutes — task will be marked failed")
        if meta.get("id"):
            close_logger(log)
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
            human(hlog, f"  CONTRACT INVALID: {shorten(e)}")
    elif contract:
        log_event(log, "contract_missing", {"contract": contract, "subtype": out["subtype"]})
        human(hlog, "  CONTRACT MISSING: run ended without structured output")

    result = {**out, "structured": structured, "contract": contract, "denied": denied}
    del result["structured_raw"]
    log_event(log, "response", {k: v for k, v in result.items() if k != "text"} | {"text": out["text"][:max_len]})
    human(hlog, f"  RESULT:  {shorten(out['text'])}")
    if meta.get("id"):
        close_logger(log)
    return result


async def _run(task, options, cfg, log, hlog, max_len, meta) -> dict:
    out = {"text": "", "structured_raw": None, "subtype": None, "is_error": False,
           "turns": 0, "duration_ms": 0, "cost_usd": 0.0, "model_usage": {}, "turn_log": []}
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task)
        # Don't return from inside this loop: bailing out early closes the
        # stream while hook callbacks may still be in flight.
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage):
                if msg.subtype == "init":
                    d = msg.data or {}
                    servers = d.get("mcp_servers") or []
                    tools = d.get("tools") or []
                    log_event(log, "init", {"model": d.get("model"), "mcp_servers": servers, "tools": tools,
                                            "permission_mode": d.get("permissionMode")})
                    human(hlog, "  INIT:    " + ", ".join(f"{s_.get('name')}={s_.get('status')}" for s_ in servers) if servers else "  INIT:    no MCP servers")
                    try:
                        connectors.discover_from_init(tools, servers, {"task_id": meta.get("id"), "role": cfg["name"]})
                    except Exception as e:  # never let bookkeeping take the run down
                        log_event(log, "hook_error", {"where": "discover_from_init", "error": repr(e)})
                elif not str(msg.subtype).startswith("hook_"):
                    # hook_started / hook_response fire for every hook call — noise, not signal
                    log_event(log, "system", {"subtype": msg.subtype, "data": msg.data})
            elif isinstance(msg, AssistantMessage):
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
                        human(hlog, f"  THINK:   {shorten(block.thinking)}")
                    elif isinstance(block, TextBlock):
                        log_event(log, "say", {"text": block.text[:max_len]})
                        human(hlog, f"  SAY:     {shorten(block.text)}")
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
