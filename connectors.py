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
    verb = words[0] if words else ""
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
        servers[row["name"]] = server_config(row)
        for t in board.connector_tools(row["name"]):
            if not t["mutates"]:
                rules.append(f"mcp__{row['name']}__{t['tool']}")
    return servers, rules


# ------------------------------------------------------------------ test ----

async def _list_tools(row: dict, timeout: float):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    cfg = server_config(row)
    if row["kind"] == "stdio":
        params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []),
                                       env={**os.environ, **cfg.get("env", {})})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await asyncio.wait_for(s.initialize(), timeout)
                return (await asyncio.wait_for(s.list_tools(), timeout)).tools
    if row["kind"] == "sse":
        from mcp.client.sse import sse_client
        async with sse_client(cfg["url"], headers=cfg.get("headers")) as (r, w):
            async with ClientSession(r, w) as s:
                await asyncio.wait_for(s.initialize(), timeout)
                return (await asyncio.wait_for(s.list_tools(), timeout)).tools
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(cfg["url"], headers=cfg.get("headers")) as (r, w, _):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), timeout)
            return (await asyncio.wait_for(s.list_tools(), timeout)).tools


async def test_connection(name: str, timeout: float = 45.0) -> dict:
    """Connect, list tools, classify, store. Returns {ok, tools, mutating, error}."""
    row = board.get_connector(name)
    if not row:
        return {"ok": False, "error": f"no connector {name}"}
    try:
        tools = await asyncio.wait_for(_list_tools(row, timeout), timeout + 15)
    except Exception as e:  # noqa: BLE001 — surfaced to the UI verbatim
        msg = f"{type(e).__name__}: {str(e)[:500]}"
        board.set_connector_test(name, "failed", msg)
        return {"ok": False, "error": msg}
    existing = {t["tool"]: t for t in board.connector_tools(name)}
    recorded = []
    for t in tools:
        mut, src = classify(t.name, getattr(t, "annotations", None))
        prev = existing.get(t.name)
        if prev and prev["source"] == "user":      # a human decision sticks
            mut, src = prev["mutates"], "user"
        recorded.append({"tool": t.name, "description": (t.description or "")[:400], "mutates": mut, "source": src})
    board.set_connector_tools(name, recorded)
    board.set_connector_test(name, "ok", None)
    return {"ok": True, "tools": recorded, "mutating": sum(r["mutates"] for r in recorded), "error": None}


# ---------------------------------------------------------------- import ----

def import_from_config(source: str, server_name: str) -> dict:
    """Bring a server registered for Claude Desktop/Code onto the board. Values that
    look like credentials go to the Keychain; the row keeps only the key names."""
    paths = {"claude-code": Path.home() / ".claude.json",
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
    return row
