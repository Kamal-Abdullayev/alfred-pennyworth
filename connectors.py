# connectors.py — MCP connectors as data.
#
# A connector is a row in the board (name, kind, command/args or url, env, headers)
# plus its secrets in the Keychain. Roles are assigned to connectors; at run time
# the runner asks role_servers(role) for the mcp_servers block and the allow rules.
# Only tools that were discovered by a Test and classified non-mutating are
# allowed — unknown or mutating tools are denied by the PreToolUse gate.
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import board
import vault

ROOT = Path(__file__).parent
PYTHON = sys.executable

# ---------------------------------------------------------------- logging ----
_SECRET_RE = re.compile(r"(bearer\s+)\S+|\b(token|secret|password|passwd|api[_-]?key|authorization)(\s*[=:]\s*)\S+", re.I)


def mask(text) -> str:
    """Redact anything that looks like a credential in free text."""
    return _SECRET_RE.sub(lambda m: (m.group(1) or f"{m.group(2)}{m.group(3)}") + "••••", str(text))


def log(name: str, event: str, message: str, data=None, level: str = "info") -> None:
    board.log_connector(name, event, mask(message), json.loads(mask(json.dumps(data, default=str))) if data is not None else None, level)


def masked_config(row: dict) -> dict:
    """server_config with secret values replaced — safe to log and to show."""
    cfg = server_config(row)
    env_spec = json.loads(row.get("env") or "{}")
    if "env" in cfg:
        cfg["env"] = {k: ("••••" if env_spec.get(k, {}).get("secret") else v) for k, v in cfg["env"].items()}
        for k, spec in env_spec.items():
            if spec.get("secret") and k not in cfg["env"]:
                cfg["env"][k] = "<secret NOT set>"
    if "headers" in cfg:
        cfg["headers"] = {k: "••••" for k in cfg["headers"]}
    if "args" in cfg:
        cfg["args"] = [mask(a) for a in cfg["args"]]
    return cfg


CLAUDE_AI_KIND = "claude-ai"   # servers your Claude account injects into every run (OAuth done on claude.ai)


# ------------------------------------------------------------- templates ----
# env/headers: {VAR: {"secret": bool, "default": str|None, "help": str}}
TEMPLATES = {
    "atlassian-cloud": {
        "label": "Atlassian Cloud — Jira + Confluence (OAuth via mcp-remote)",
        "kind": "stdio", "command": "npx", "args": ["-y", "mcp-remote", "https://mcp.atlassian.com/v1/mcp"],
        "env": {}, "headers": {},
        "note": "Hosted Atlassian MCP. The first Test opens a browser login once; mcp-remote caches the token in ~/.mcp-auth, after which agents run headless. Requires Node.",
    },
    "confluence-onprem": {
        "label": "Confluence Data Center / Server (personal access token)",
        "kind": "stdio", "command": "uvx", "args": ["mcp-atlassian"],
        "env": {
            "CONFLUENCE_URL": {"secret": False, "default": "https://confluence.example.com", "help": "Base URL of your Confluence"},
            "CONFLUENCE_PERSONAL_TOKEN": {"secret": True, "default": None, "help": "PAT from Confluence → Profile → Personal Access Tokens"},
            "CONFLUENCE_SSL_VERIFY": {"secret": False, "default": "true", "help": "false only for internal CAs you cannot install"},
        }, "headers": {},
        "note": "Community mcp-atlassian server (Python, via uvx — requires uv). Read tools only are allowed to agents by default.",
    },
    "jira-onprem": {
        "label": "Jira Data Center — Bally's custom read-only server",
        "kind": "stdio", "command": str(Path.home() / "Desktop/custom_mcps/onprem_jira_mcp/.venv/bin/python"),
        "args": [str(Path.home() / "Desktop/custom_mcps/onprem_jira_mcp/server.py")],
        "env": {
            "JIRA_URL": {"secret": False, "default": None, "help": "e.g. https://jira.example.com"},
            "JIRA_TOKEN": {"secret": True, "default": None, "help": "Personal access token"},
        }, "headers": {},
        "note": "The custom server in ~/Desktop/custom_mcps/onprem_jira_mcp.",
    },
    "gitlab-onprem": {
        "label": "GitLab (self-hosted) — read-only: code, pipelines, MRs",
        "kind": "stdio", "command": PYTHON, "args": [str(ROOT / "mcp_servers/gitlab/server.py")],
        "env": {
            "GITLAB_URL": {"secret": False, "default": "https://gitlab.ballys.tech", "help": "Base URL"},
            "GITLAB_TOKEN": {"secret": True, "default": None, "help": "PAT with read_api scope"},
        }, "headers": {},
        "note": "project_x/mcp_servers/gitlab — the server the agents already use via YAML.",
    },
    "mysql": {
        "label": "MySQL (read-only user)",
        "kind": "stdio", "command": PYTHON, "args": [str(ROOT / "mcp_servers/mysql/server.py")],
        "env": {
            "MYSQL_HOST": {"secret": False, "default": "127.0.0.1", "help": ""},
            "MYSQL_PORT": {"secret": False, "default": "3306", "help": ""},
            "MYSQL_USER": {"secret": False, "default": None, "help": "Use a SELECT-only user"},
            "MYSQL_PASSWORD": {"secret": True, "default": None, "help": ""},
            "MYSQL_DATABASE": {"secret": False, "default": "", "help": "optional default schema"},
        }, "headers": {},
        "note": "execute_write/confirm_write exist on this server; they are classified mutating and never allowed to agents.",
    },
    "custom-stdio": {
        "label": "Custom — stdio command",
        "kind": "stdio", "command": "", "args": [], "env": {}, "headers": {},
        "note": "Any MCP server started as a local process.",
    },
    "custom-http": {
        "label": "Custom — HTTP endpoint",
        "kind": "http", "command": None, "args": [], "url": "https://",
        "env": {}, "headers": {"Authorization": {"secret": True, "default": None, "help": "e.g. Bearer <token>"}},
        "note": "Streamable-HTTP MCP server. OAuth-only endpoints need the mcp-remote template instead.",
    },
}

# Verbs that change state. Judged on the LEADING word only, so nouns such as
# merge_request or comment_thread do not trip it.
_MUTATING_VERBS = {"create", "update", "delete", "remove", "add", "transition", "write", "post", "put",
                   "patch", "set", "edit", "execute", "confirm", "assign", "move", "archive", "publish",
                   "send", "upload", "merge", "approve", "close", "reopen", "comment", "link", "unlink",
                   "insert", "drop", "truncate", "rename", "modify", "apply", "trigger", "cancel", "retry",
                   "start", "stop", "restart", "deploy", "rollback", "revoke", "grant", "reply"}


_PRODUCT_PREFIXES = {"outlook", "teams", "sharepoint", "onedrive", "gmail", "calendar", "drive", "slack", "jira",
                     "confluence", "gitlab", "github", "notion", "figma", "chat", "mail", "docs", "sheets", "compass"}


def _words(name: str) -> list[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name or "").lower()
    return [w for w in re.split(r"[^a-z0-9]+", snake) if w]


def classify(name: str, annotations) -> tuple[int, str]:
    """(mutates, source). Annotations win when present; otherwise the tool name's
    leading verb decides. Unknown verbs count as read — flip them in the UI if wrong;
    a human decision sticks across re-tests."""
    ro = getattr(annotations, "readOnlyHint", None) if annotations is not None else None
    if ro is None and isinstance(annotations, dict):
        ro = annotations.get("readOnlyHint")
    if ro is True:
        return 0, "annotation"
    if ro is False:
        return 1, "annotation"
    words = _words(name)
    # verb-first (create_issue) — or product-prefixed (outlook_delete_event, teams_send_message)
    verb = words[0] if words else ""
    if verb in _PRODUCT_PREFIXES and len(words) > 1:
        verb = words[1]
        if verb == "batch" and len(words) > 2:          # outlook_batch_delete_messages
            verb = words[2]
    return (1 if verb in _MUTATING_VERBS else 0), "heuristic"


# ---------------------------------------------------------------- config ----

def resolved_env(row: dict) -> dict:
    out = {}
    for var, spec in (json.loads(row.get("env") or "{}")).items():
        if spec.get("secret"):
            v = vault.get_secret(row["name"], var)
            if v is not None:
                out[var] = v
        elif spec.get("value") not in (None, ""):
            out[var] = str(spec["value"])
    return out


def resolved_headers(row: dict) -> dict:
    out = {}
    for var, spec in (json.loads(row.get("headers") or "{}")).items():
        if spec.get("secret"):
            v = vault.get_secret(row["name"], f"header:{var}")
            if v is not None:
                out[var] = v
        elif spec.get("value"):
            out[var] = str(spec["value"])
    return out


def server_config(row: dict) -> dict:
    """The mcp_servers entry the SDK expects."""
    if row["kind"] == "stdio":
        cfg = {"type": "stdio", "command": row["command"], "args": json.loads(row.get("args") or "[]")}
        env = resolved_env(row)
        if env:
            cfg["env"] = env
        return cfg
    cfg = {"type": row["kind"], "url": row["url"]}
    headers = resolved_headers(row)
    if headers:
        cfg["headers"] = headers
    return cfg


def role_servers(role: str) -> tuple[dict, list[str]]:
    """(mcp_servers, allow_rules) for every enabled connector assigned to `role`.
    Only tools recorded as non-mutating are allowed; anything else the gate denies."""
    servers, rules = {}, []
    for row in board.connectors_for_role(role):
        if not row.get("enabled"):
            continue
        if row["kind"] != CLAUDE_AI_KIND:          # claude.ai servers are already in the session
            servers[row["name"]] = server_config(row)
        if row.get("trust_writes"):
            # a scratch tool (e.g. a drawing canvas): every tool is allowed, mutating or not
            rules.append(f"mcp__{row['name']}__*")
            continue
        for t in board.connector_tools(row["name"]):
            if not t["mutates"]:
                rules.append(f"mcp__{row['name']}__{t['tool']}")
    return servers, rules


# ------------------------------------------------------------------ test ----

async def _list_tools(row: dict, timeout: float, errlog):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    cfg = server_config(row)
    if row["kind"] == "stdio":
        params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []),
                                       env={**os.environ, **cfg.get("env", {})})
        async with stdio_client(params, errlog=errlog) as (r, w):
            async with ClientSession(r, w) as sess:
                await asyncio.wait_for(sess.initialize(), timeout)
                return (await asyncio.wait_for(sess.list_tools(), timeout)).tools
    if row["kind"] == "sse":
        from mcp.client.sse import sse_client
        async with sse_client(cfg["url"], headers=cfg.get("headers")) as (r, w):
            async with ClientSession(r, w) as sess:
                await asyncio.wait_for(sess.initialize(), timeout)
                return (await asyncio.wait_for(sess.list_tools(), timeout)).tools
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(cfg["url"], headers=cfg.get("headers")) as (r, w, _):
        async with ClientSession(r, w) as sess:
            await asyncio.wait_for(sess.initialize(), timeout)
            return (await asyncio.wait_for(sess.list_tools(), timeout)).tools


def _unwrap(e: BaseException) -> BaseException:
    """anyio wraps failures in ExceptionGroups; the first leaf is the real story."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def _explain(e: BaseException, row: dict) -> str:
    leaf = _unwrap(e)
    msg = f"{type(leaf).__name__}: {str(leaf)[:400]}".strip()
    low = msg.lower()
    if row["kind"] != "stdio" and ("401" in low or "unauthorized" in low or "403" in low):
        msg += (" — this endpoint needs an interactive OAuth login, which the agent runtime cannot do. "
                "Use the 'Atlassian Cloud (OAuth via mcp-remote)' template instead: mcp-remote does the browser login "
                "once and caches the token in ~/.mcp-auth.")
    elif isinstance(leaf, (FileNotFoundError, PermissionError)):
        msg += " — the command was not found or is not executable; check the path."
    elif isinstance(leaf, asyncio.TimeoutError):
        msg += " — the server did not answer in time; see server_stderr below for what it printed."
    return msg


def _merge_tools(name: str, discovered) -> list[dict]:
    """Classify discovered tools; a human decision (source=user) survives re-discovery."""
    existing = {t["tool"]: t for t in board.connector_tools(name)}
    recorded = []
    for t in discovered:
        tname = t if isinstance(t, str) else t.name
        desc = "" if isinstance(t, str) else (t.description or "")
        ann = None if isinstance(t, str) else getattr(t, "annotations", None)
        mut, src = classify(tname, ann)
        prev = existing.get(tname)
        if prev and prev["source"] == "user":
            mut, src = prev["mutates"], "user"
        recorded.append({"tool": tname, "description": desc[:400], "mutates": mut, "source": src})
    board.set_connector_tools(name, recorded)
    return recorded


async def test_connection(name: str, timeout: float = 45.0) -> dict:
    """Connect, list tools, classify, store. Everything that happened lands in the connector log."""
    import tempfile
    row = board.get_connector(name)
    if not row:
        return {"ok": False, "error": f"no connector {name}"}
    if row["kind"] == CLAUDE_AI_KIND:
        return {"ok": False, "error": "Provided by your Claude account — it cannot be tested outside an agent run. Its tools refresh on every run."}
    log(name, "test_start", f"testing {row['kind']} connector", {"config": masked_config(row), "timeout_s": timeout})
    err_path = Path(tempfile.mkstemp(prefix=f"alfred-{name}-", suffix=".stderr")[1])
    try:
        with open(err_path, "w", encoding="utf-8") as errlog:
            tools = await asyncio.wait_for(_list_tools(row, timeout, errlog), timeout + 15)
        ok, error, tools_out = True, None, tools
    except BaseException as e:  # noqa: BLE001 — surfaced to the UI verbatim (masked)
        ok, error, tools_out = False, _explain(e, row), []
    finally:
        stderr = err_path.read_text(encoding="utf-8", errors="replace") if err_path.exists() else ""
        err_path.unlink(missing_ok=True)
    if stderr.strip():
        tail = stderr.strip().splitlines()[-40:]
        log(name, "server_stderr", f"{len(tail)} line(s) from the server process", {"stderr": tail}, level="warn" if not ok else "info")
    if not ok:
        board.set_connector_test(name, "failed", error)
        log(name, "test_failed", error, level="error")
        return {"ok": False, "error": error}
    recorded = _merge_tools(name, tools_out)
    board.set_connector_test(name, "ok", None)
    log(name, "test_ok", f"{len(recorded)} tools discovered, {sum(r['mutates'] for r in recorded)} classified mutating",
        {"tools": [{"tool": r["tool"], "mutates": r["mutates"], "source": r["source"]} for r in recorded]})
    return {"ok": True, "tools": recorded, "mutating": sum(r["mutates"] for r in recorded), "error": None}


# ------------------------------------------------------------- run time ----

def discover_from_init(tools: list[str], mcp_servers: list[dict], ctx: dict) -> None:
    """Called by the runner on the SDK's init message. Records each configured connector's
    status for this run, and registers servers your Claude account injected (mcp__claude_ai_*)
    as discovered connectors so they can be assigned to roles."""
    by_server: dict[str, list[str]] = {}
    for t in tools or []:
        if t.startswith("mcp__"):
            parts = t.split("__", 2)
            if len(parts) == 3:
                by_server.setdefault(parts[1], []).append(parts[2])
    # status entries use display names ("claude.ai Atlassian"); tool prefixes use the
    # normalised form (claude_ai_Atlassian) — index both.
    status = {}
    for s_ in (mcp_servers or []):
        n = s_.get("name") or ""
        status[n] = s_.get("status")
        status[re.sub(r"\W", "_", n)] = s_.get("status")
    known = {c["name"]: c for c in board.list_connectors()}
    for server, names in by_server.items():
        if server in known:
            st = status.get(server, "connected")
            log(server, "run_init", f"{ctx.get('role')} run {ctx.get('task_id')}: status={st}, {len(names)} tools visible",
                {"status": st, "tools": names, "task_id": ctx.get("task_id"), "role": ctx.get("role")},
                level="warn" if st in ("failed", "needs-auth") else "info")
        elif server.startswith("claude_ai_"):
            board.upsert_connector({"name": server, "template": CLAUDE_AI_KIND, "kind": CLAUDE_AI_KIND, "command": None,
                                    "args": "[]", "url": None, "env": "{}", "headers": "{}",
                                    "note": "Provided by your Claude account (claude.ai connector, OAuth already done there). "
                                            "Discovered during an agent run; tick the roles that may use its read tools. "
                                            "The role needs `account_connectors: true` in its YAML to see these servers."})
            recorded = _merge_tools(server, names)
            board.set_connector_test(server, "ok", None)
            log(server, "discovered", f"discovered from {ctx.get('role')} run {ctx.get('task_id')}: {len(names)} tools, "
                f"{sum(r['mutates'] for r in recorded)} mutating", {"tools": names}, level="info")
    for server, st in status.items():
        if server in known and server not in by_server and st in ("failed", "needs-auth", "pending"):
            log(server, "run_init", f"{ctx.get('role')} run {ctx.get('task_id')}: status={st}, no tools visible",
                {"status": st, "task_id": ctx.get("task_id")}, level="warn")


async def probe_account_connectors() -> dict:
    """Start one minimal SDK session (haiku, one turn, no built-in tools) just to read
    its init message: that lists every MCP server your Claude account injects and
    their tools. Registers them as discovered connectors. Costs about a cent."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, SystemMessage
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is set; Alfred runs on the Claude seat login. Unset it.")
    found = {"tools": [], "mcp_servers": []}
    # ["user"] is what makes the CLI attach claude.ai connectors (see runner.py); the probe
    # mirrors that so what it discovers is what an opted-in role will actually see.
    opts = ClaudeAgentOptions(model="haiku", max_turns=1, tools=[], allowed_tools=[], setting_sources=["user"],
                              system_prompt="Reply with the single word OK.", permission_mode="default", cwd=str(ROOT))
    async with ClaudeSDKClient(options=opts) as client:
        await client.query("Reply OK.")
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                found["tools"] = list(msg.data.get("tools") or [])
                found["mcp_servers"] = list(msg.data.get("mcp_servers") or [])
    discover_from_init(found["tools"], found["mcp_servers"], {"task_id": "probe", "role": "probe"})
    account = sorted({t.split("__", 2)[1] for t in found["tools"] if t.startswith("mcp__claude_ai_")})
    return {"account_servers": account,
            "tools": {srv: sorted(t.split("__", 2)[2] for t in found["tools"] if t.startswith(f"mcp__{srv}__")) for srv in account},
            "mcp_servers": found["mcp_servers"]}


def log_denied(tool: str, ctx: dict) -> None:
    if not tool.startswith("mcp__"):
        return
    parts = tool.split("__", 2)
    if len(parts) == 3 and board.get_connector(parts[1]):
        log(parts[1], "denied", f"{ctx.get('role')} run {ctx.get('task_id')} tried {parts[2]} — not allowed for this role "
            "(tool is classified mutating, undiscovered, or the connector is not assigned to the role)", {"tool": tool}, level="warn")


# ---------------------------------------------------------------- import ----

def import_from_config(source: str, server_name: str) -> dict:
    """Bring a server registered for Claude Desktop/Code onto the board. Values that
    look like credentials go to the Keychain; the row keeps only the key names."""
    paths = {"claude-code": Path.home() / ".claude.json",
             "claude-settings": Path.home() / ".claude/settings.json",
             "claude-desktop": Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"}
    cfg = json.loads(paths[source].read_text())
    servers = dict(cfg.get("mcpServers", {}))
    for proj in (cfg.get("projects") or {}).values():         # project-scoped entries too
        servers.update(proj.get("mcpServers") or {})
    s = servers.get(server_name)
    if not s:
        raise KeyError(server_name)
    kind = s.get("type") or ("stdio" if s.get("command") else "http")
    env, headers = {}, {}
    for var, val in (s.get("env") or {}).items():
        if re.search(r"token|secret|password|key|auth", var, re.I):
            vault.set_secret(server_name, var, str(val)); env[var] = {"secret": True}
        else:
            env[var] = {"secret": False, "value": val}
    for h, val in (s.get("headers") or {}).items():
        vault.set_secret(server_name, f"header:{h}", str(val)); headers[h] = {"secret": True}
    args = list(s.get("args") or [])
    # a bearer token passed as a CLI arg (e.g. mcp-remote --header) moves to the vault too
    for i, a in enumerate(args):
        if re.search(r"bearer\s+\S+", str(a), re.I):
            vault.set_secret(server_name, "header:Authorization", str(a).split(":", 1)[-1].strip())
            args[i] = "Authorization: ${ALFRED_SECRET:header:Authorization}"
    row = {"name": server_name, "template": f"import:{source}", "kind": kind, "command": s.get("command"),
           "args": json.dumps(args), "url": s.get("url"), "env": json.dumps(env), "headers": json.dumps(headers),
           "note": f"imported from {source}"}
    board.upsert_connector(row)
    log(server_name, "imported", f"imported from {source} as {kind}",
        {"secrets_moved_to_keychain": [k for k, v in env.items() if v.get("secret")] + [f"header:{h}" for h in headers],
         "config": masked_config(board.get_connector(server_name))})
    if kind in ("http", "sse") and not headers:
        log(server_name, "hint", "HTTP endpoint imported without any auth header. If it needs OAuth (e.g. mcp.atlassian.com), "
            "agents cannot log in; use the 'Atlassian Cloud (OAuth via mcp-remote)' template instead.", level="warn")
    return row


# ------------------------------------------------------------ excalidraw ----

async def excalidraw_call(tool: str, args: dict, timeout: float = 90.0):
    """One call against the excalidraw connector (its own short-lived MCP session).
    Returns the list of content blocks as plain dicts."""
    row = board.get_connector("excalidraw")
    if not row:
        raise RuntimeError("no 'excalidraw' connector on the board")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    cfg = server_config(row)
    params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []), env={**os.environ, **cfg.get("env", {})})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), timeout)
            res = await asyncio.wait_for(s.call_tool(tool, args), timeout)
            out = []
            for c in res.content:
                d = {"type": c.type}
                if c.type == "text":
                    d["text"] = c.text
                elif c.type == "image":
                    d["data"], d["mimeType"] = c.data, c.mimeType
                out.append(d)
            return out


async def excalidraw_snapshot_async(task_id: str) -> list[dict]:
    """Save what is on the canvas right now as assets of a run: the scene JSON always,
    a PNG when a browser has the canvas open (the UI's embedded canvas counts).
    Async because the runner calls it from inside its own event loop."""
    import base64
    out_dir = ROOT / "data" / "assets" / str(task_id)
    saved = []
    try:
        blocks = await excalidraw_call("export_scene", {})
    except Exception as e:  # noqa: BLE001
        log("excalidraw", "snapshot_failed", f"export_scene failed for run {task_id}: {type(e).__name__}: {str(e)[:200]}", level="warn")
        return saved
    for b in blocks:
        txt = b.get("text") or ""
        if b.get("type") == "text" and '"elements"' in txt[:400]:
            out_dir.mkdir(parents=True, exist_ok=True)
            n = len([p for p in out_dir.iterdir() if p.suffix == ".excalidraw"]) + 1
            path = out_dir / f"scene-{n}.excalidraw"
            path.write_text(txt, encoding="utf-8")
            saved.append({"kind": "scene", "path": str(path)})
    try:
        blocks = await excalidraw_call("export_to_image", {"format": "png"})
        for b in blocks:
            if b.get("type") == "image" and b.get("data"):
                out_dir.mkdir(parents=True, exist_ok=True)
                n = len([p for p in out_dir.iterdir() if p.suffix == ".png"]) + 1
                path = out_dir / f"canvas-{n}.png"
                path.write_bytes(base64.b64decode(b["data"]))
                saved.append({"kind": "image", "path": str(path)})
    except Exception:
        pass   # needs a browser on the canvas; the scene JSON is the durable copy
    log("excalidraw", "snapshot", f"run {task_id}: saved {[s_['kind'] for s_ in saved]}", {"files": [s_["path"] for s_ in saved]})
    return saved


def excalidraw_snapshot(task_id: str) -> list[dict]:
    """Sync wrapper for callers without a running loop (API endpoints, scripts)."""
    return asyncio.run(excalidraw_snapshot_async(task_id))


def excalidraw_show(task_id: str, name: str) -> str:
    """Put a saved scene back on the canvas (clears it first)."""
    path = (ROOT / "data" / "assets" / task_id / name).resolve()
    if (ROOT / "data" / "assets").resolve() not in path.parents or not path.is_file():
        raise FileNotFoundError(name)
    scene = path.read_text(encoding="utf-8")
    blocks = asyncio.run(excalidraw_call("import_scene", {"data": scene, "mode": "replace"}))
    try:
        asyncio.run(excalidraw_call("set_viewport", {"scrollToContent": True}))
    except Exception:
        pass
    msg = " ".join((b.get("text") or "") for b in blocks)[:300]
    log("excalidraw", "show", f"restored {name} from run {task_id}: {msg}")
    return msg
