# api.py — HTTP + SSE over the board, and static hosting for the UI.
#
#   .venv/bin/uvicorn api:app --port 8787 --reload
#
# Read paths query tasks.db directly. Write paths only ever create board tasks
# (a job for the team lead, or a human finding that re-enters the fix loop) —
# the daemons do the work. Serves ui/dist at / when it has been built.
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import board
import connectors as conn
import contracts as contracts_module
import daemon
import vault

ROOT = Path(__file__).parent
app = FastAPI(title="Alfred", version="0.1")
board.init()


# ---------------------------------------------------------------- helpers ----

def _task(t: dict) -> dict:
    out = dict(t)
    s = out.get("structured")
    out["structured"] = json.loads(s) if s else None
    return out


def _kind(root: dict) -> str:
    """answer | plan — derived from the lead's structured output (older rows are bare plans)."""
    s = root.get("structured")
    if isinstance(s, str):
        s = json.loads(s) if s else None
    if not s:
        return "plan"
    return s.get("kind") or ("plan" if "subtasks" in s else "plan")


def _chain_status(tasks: list[dict]) -> str:
    root = tasks[0]
    if _kind(root) == "answer" and root["status"] == "done":
        return "answered"
    statuses = {t["status"] for t in tasks}
    if "stuck" in statuses:
        return "stuck"
    if statuses & {"open", "claimed"}:
        return "running"
    if "failed" in statuses:
        return "failed"
    qa = [t for t in tasks if t["role"] == "qa" and t["status"] == "done"]
    if qa:
        last = _task(sorted(qa, key=lambda t: t["created_at"])[-1])
        verdict = (last["structured"] or {}).get("verdict")
        return "passed" if verdict == "pass" else "failed"
    return "done"


def _chains(limit: int = 50) -> list[dict]:
    snap = board.snapshot()
    by_chain: dict[str, list[dict]] = {}
    for t in snap:
        by_chain.setdefault(t["chain_id"], []).append(t)
    with board.connect() as con:
        costs = {r["chain_id"]: r["c"] for r in con.execute(
            "SELECT chain_id, SUM(cost_usd) c FROM usage GROUP BY chain_id")}
    out = []
    for cid, tasks in by_chain.items():
        tasks.sort(key=lambda t: t["created_at"])
        root = tasks[0]
        roles: dict[str, int] = {}
        for t in tasks:
            roles[t["role"]] = roles.get(t["role"], 0) + 1
        out.append({
            "chain_id": cid, "title": root["title"], "status": _chain_status(tasks), "kind": _kind(root),
            "created_at": root["created_at"],
            "updated_at": max((t["finished_at"] or t["claimed_at"] or t["created_at"]) for t in tasks),
            "cost_usd": costs.get(cid, 0.0), "roles": roles,
            "project_dir": root.get("project_dir"), "iteration": max(t["iteration"] for t in tasks),
            "tasks": len(tasks),
        })
    out.sort(key=lambda c: c["updated_at"], reverse=True)
    return out[:limit]


def _git(repo: str, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                              check=True, timeout=20).stdout
    except Exception:
        return None


def _chain_diff(root: dict) -> tuple[str | None, str | None]:
    repo, base = root.get("project_dir"), root.get("base_sha")
    branch = f"alfred/{root['chain_id']}"
    if not (repo and base and os.path.isdir(repo)):
        return None, None
    return _git(repo, "diff", f"{base}..{branch}"), _git(repo, "log", "--oneline", f"{base}..{branch}")


def _agents() -> list[dict]:
    out = []
    for role, path in daemon.configs().items():
        cfg = yaml.safe_load(open(ROOT / path))
        out.append({
            "role": role, "name": cfg.get("name", role), "model": cfg.get("model"),
            "contract": cfg.get("contract"), "permission_mode": cfg.get("permission_mode", "default"),
            "max_minutes": cfg.get("max_minutes", 15), "max_turns": cfg.get("max_turns"),
            "builtin_tools": cfg.get("builtin_tools", []), "allowed_tools": cfg.get("allowed_tools", []),
            "mcp_servers": list((cfg.get("mcp_servers") or {}).keys()),
            "system_prompt": cfg.get("system_prompt", ""), "path": path,
        })
    return out


_SECRET_HINTS = ("bearer", "token", "secret", "password", "apikey", "api_key", "authorization")


def _cmd(server: dict) -> str:
    """Command line for display. Any arg that looks like a credential is redacted."""
    parts = [str(server.get("command", ""))]
    for a in server.get("args", []) or []:
        a = str(a)
        parts.append("<redacted>" if any(h in a.lower() for h in _SECRET_HINTS) else a[:48])
    return " ".join(p for p in parts if p)[-100:]


# ------------------------------------------------------------------ routes ----

@app.get("/api/health")
def health():
    return {"ok": True, "db": str(board.DB_PATH), "roles": list(daemon.configs())}


BUILTIN_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite"]
MODELS = ["opus", "sonnet", "haiku", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]
PERMISSION_MODES = ["default", "acceptEdits", "bypassPermissions", "plan", "dontAsk"]


class _Literal(str):
    """Marks strings that should be dumped as YAML block literals (the system prompt)."""


def _literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


yaml.add_representer(_Literal, _literal_representer, Dumper=yaml.SafeDumper)


def _agent_full(role: str) -> dict:
    path = daemon.configs().get(role)
    if not path:
        raise HTTPException(404, f"no role {role}")
    cfg = yaml.safe_load(open(ROOT / path)) or {}
    return {"role": role, "path": path, **cfg}


def _write_agent(role: str, cfg: dict) -> None:
    """Write agents/<role>.yaml. Comments are not preserved — the UI is the editor now."""
    out = dict(cfg)
    out["name"] = role
    if isinstance(out.get("system_prompt"), str):
        out["system_prompt"] = _Literal(out["system_prompt"].rstrip("\n") + "\n")
    (ROOT / "agents" / f"{role}.yaml").write_text(
        yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")


@app.get("/api/agents")
def agents():
    out = []
    for a in _agents():
        cfg = _agent_full(a["role"])
        out.append({**a, "account_connectors": bool(cfg.get("account_connectors")),
                    "workers": {"min": int((cfg.get("workers") or {}).get("min", 1)), "max": int((cfg.get("workers") or {}).get("max", 1))},
                    "logging": cfg.get("logging") or {}, "raw": cfg})
    return {"agents": out, "options": {"models": MODELS, "contracts": list(contracts_module.CONTRACTS), "builtin_tools": BUILTIN_TOOLS,
                                       "permission_modes": PERMISSION_MODES}}


class AgentIn(BaseModel):
    model: str
    contract: str
    system_prompt: str
    builtin_tools: list[str] = []
    allowed_tools: list[str] = []
    permission_mode: str = "default"
    max_minutes: int = 15
    max_turns: int | None = None
    account_connectors: bool = False
    workers: dict = {"min": 1, "max": 1}
    mcp_servers: dict | None = None      # inline YAML servers; None = keep what the file has


@app.put("/api/agents/{role}")
def agent_update(role: str, a: AgentIn):
    cur = _agent_full(role)
    if a.contract not in contracts_module.CONTRACTS:
        raise HTTPException(400, f"unknown contract {a.contract}; choose from {list(contracts_module.CONTRACTS)}")
    if a.permission_mode not in PERMISSION_MODES:
        raise HTTPException(400, f"unknown permission_mode {a.permission_mode}")
    bad = [t for t in a.builtin_tools if t not in BUILTIN_TOOLS]
    if bad:
        raise HTTPException(400, f"unknown built-in tools {bad}")
    wmin, wmax = int(a.workers.get("min", 1)), int(a.workers.get("max", 1))
    if wmin < 0 or wmax < max(wmin, 1):
        raise HTTPException(400, "workers.max must be >= workers.min >= 0")
    cfg = {k: v for k, v in cur.items() if k not in ("role", "path")}
    cfg.update({"model": a.model, "contract": a.contract, "system_prompt": a.system_prompt,
                "builtin_tools": a.builtin_tools, "allowed_tools": a.allowed_tools,
                "permission_mode": a.permission_mode, "max_minutes": a.max_minutes,
                "account_connectors": a.account_connectors, "workers": {"min": wmin, "max": wmax}})
    if a.max_turns:
        cfg["max_turns"] = a.max_turns
    else:
        cfg.pop("max_turns", None)
    if a.mcp_servers is not None:
        cfg["mcp_servers"] = a.mcp_servers
    cfg.setdefault("logging", {"level": "debug", "log_thinking": True, "log_tool_io": True})
    _write_agent(role, cfg)
    return _agent_full(role)


class NewAgentIn(BaseModel):
    role: str
    clone_from: str = "developer"


@app.post("/api/agents")
def agent_create(n: NewAgentIn):
    role = n.role.strip()
    if not re_name.match(role) or role in ("tasks", "flow"):
        raise HTTPException(400, "role must be letters, digits, - or _")
    if role in daemon.configs():
        raise HTTPException(409, f"role {role} already exists")
    src = _agent_full(n.clone_from)
    cfg = {k: v for k, v in src.items() if k not in ("role", "path")}
    cfg["workers"] = {"min": 0, "max": 1}     # a new specialist starts off; the supervisor spawns it on demand
    _write_agent(role, cfg)
    return _agent_full(role)


@app.delete("/api/agents/{role}")
def agent_delete(role: str):
    if role in ("team_lead", "developer", "qa"):
        raise HTTPException(400, "the three core roles cannot be deleted; disable them by setting workers to 0/0")
    path = daemon.configs().get(role)
    if not path:
        raise HTTPException(404, f"no role {role}")
    (ROOT / path).unlink()
    board.set_connector_roles  # noqa: B018 — connectors keep their assignment rows; harmless
    return {"deleted": role}


@app.get("/api/workers")
def workers_view():
    live = board.workers()
    tasks_by_id = {t["id"]: t for t in board.snapshot() if t["status"] == "claimed"}
    for w in live:
        t = tasks_by_id.get(w.get("current_task") or "")
        w["current_title"] = t["title"] if t else None
        w["current_chain"] = t["chain_id"] if t else None
    return {"workers": live, "open": board.open_count_by_role(),
            "max_workers": int(board.get_setting("max_workers", 4))}


@app.put("/api/settings/max_workers")
def set_max_workers(body: dict):
    n = int(body.get("max_workers", 4))
    if not 1 <= n <= 16:
        raise HTTPException(400, "max_workers must be between 1 and 16")
    board.set_setting("max_workers", n)
    return {"max_workers": n}


@app.get("/api/tasks")
def tasks(limit: int = 100):
    return [_task(t) for t in board.snapshot()[:limit]]


@app.get("/api/chains")
def chains(limit: int = 50):
    return _chains(limit)


@app.get("/api/chains/{chain_id}")
def chain(chain_id: str):
    tasks_ = sorted([t for t in board.snapshot() if t["chain_id"] == chain_id], key=lambda t: t["created_at"])
    if not tasks_:
        raise HTTPException(404, f"no chain {chain_id}")
    root = tasks_[0]
    diff, log = _chain_diff(root)
    with board.connect() as con:
        findings = [dict(r) for r in con.execute(
            "SELECT * FROM findings WHERE chain_id=? ORDER BY iteration, id", (chain_id,))]
        usage = [dict(r) for r in con.execute(
            "SELECT * FROM usage WHERE chain_id=? ORDER BY created_at", (chain_id,))]
    return {
        "root": _task(root), "tasks": [_task(t) for t in tasks_], "findings": findings, "usage": usage,
        "turns": board.turns_for_chain(chain_id),
        "code_links": (_code_links((_task(root)["structured"] or {}).get("answer") or {}) if _kind(root) == "answer" else []),
        "diff": diff, "log": log, "branch": f"alfred/{chain_id}", "status": _chain_status(tasks_), "kind": _kind(root),
        "cost_usd": sum(u["cost_usd"] for u in usage),
    }


class JobIn(BaseModel):
    body: str                          # the human's question / job, verbatim
    project_dir: str | None = None
    title: str | None = None
    conversation_id: str | None = None # continue this Ask conversation; None = start a new one


def _turn_answer_text(root: dict) -> str | None:
    s = root.get("structured")
    s = json.loads(s) if isinstance(s, str) and s else s
    if not s:
        return None
    if s.get("kind") == "answer" and s.get("answer"):
        return s["answer"].get("answer")
    plan = s.get("plan") if "plan" in s else (s if "subtasks" in s else None)
    return plan.get("summary") if plan else None


def _conversation_context(cid: str, max_turns: int = 3, max_chars: int = 1800) -> str:
    """Previous Q/A pairs, newest last, for the lead to read before the new question."""
    turns = [t for t in board.conversation_turns(cid) if t["status"] == "done"][-max_turns:]
    parts = []
    for t in turns:
        a = _turn_answer_text(t)
        if not a:
            continue
        parts.append(f"Q: {(t.get('question') or t['body']).strip()[:600]}\nA: {a.strip()[:max_chars]}")
    if not parts:
        return ""
    return ("\n\n--- Conversation so far (context only — answer the latest question above, "
            "do not repeat earlier answers) ---\n" + "\n\n".join(parts))


@app.post("/api/jobs")
def create_job(job: JobIn):
    question = job.body.strip()
    if not question:
        raise HTTPException(400, "empty job")
    project_dir = None
    if job.project_dir:
        p = Path(job.project_dir).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"project_dir does not exist: {p}")
        project_dir = str(p.resolve())
    cid = job.conversation_id
    if cid and not board.get_conversation(cid):
        raise HTTPException(404, f"no conversation {cid}")
    if not cid:
        cid = board.create_conversation(job.title or question, project_dir)
    body = question + _conversation_context(cid)
    task_id = board.create_task(role="team_lead", title=(job.title or question)[:80], body=body,
                                created_by="ui", project_dir=project_dir, conversation_id=cid, question=question)
    return {"task_id": task_id, "chain_id": task_id, "conversation_id": cid}


# ---------------------------------------------------------- conversations ----

@app.get("/api/conversations")
def conversations():
    return board.list_conversations()


@app.get("/api/conversations/{cid}")
def conversation(cid: str):
    c = board.get_conversation(cid)
    if not c:
        raise HTTPException(404, f"no conversation {cid}")
    turns = []
    with board.connect() as con:
        costs = {r["chain_id"]: r["c"] for r in con.execute("SELECT chain_id, SUM(cost_usd) c FROM usage GROUP BY chain_id")}
    for t in board.conversation_turns(cid):
        root = _task(t)
        turns.append({"chain_id": t["chain_id"], "question": t.get("question") or t["body"], "status": t["status"],
                      "kind": _kind(root), "created_at": t["created_at"], "finished_at": t["finished_at"],
                      "cost_usd": costs.get(t["chain_id"], 0.0), "structured": root["structured"], "result": t.get("result")})
    return {**c, "turns": turns}


@app.put("/api/conversations/{cid}")
def conversation_rename(cid: str, body: dict):
    if not board.get_conversation(cid):
        raise HTTPException(404, f"no conversation {cid}")
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    board.rename_conversation(cid, title)
    return {"id": cid, "title": title}


@app.delete("/api/conversations/{cid}")
def conversation_delete(cid: str):
    if not board.get_conversation(cid):
        raise HTTPException(404, f"no conversation {cid}")
    board.delete_conversation(cid)
    return {"deleted": cid}


class FindingIn(BaseModel):
    file: str
    line: int | None = None
    severity: str = "important"
    claim: str
    evidence: str = "reported by human reviewer in the UI"


@app.post("/api/chains/{chain_id}/findings")
def add_human_finding(chain_id: str, f: FindingIn):
    """A human review comment becomes a finding and re-enters the developer loop."""
    root = board.chain_root(chain_id)
    if not root:
        raise HTTPException(404, f"no chain {chain_id}")
    devs = sorted([t for t in board.snapshot() if t["chain_id"] == chain_id and t["role"] == "developer"],
                  key=lambda t: t["created_at"])
    if not devs:
        raise HTTPException(400, "chain has no developer task to send feedback to")
    last_dev = devs[-1]
    iteration = board.chain_iteration(chain_id) + 1
    board.add_findings(chain_id, root["id"], iteration, "human", [f.model_dump()])
    original = last_dev["body"].split("\n\n--- Fix round ")[0]
    new_id = board.create_task(
        role="developer", title=f"fix (round {iteration}): {root['title']}"[:80],
        body=daemon.fix_body(original, iteration, board.open_findings(chain_id)),
        created_by="human", chain_id=chain_id, iteration=iteration,
        project_dir=last_dev.get("project_dir"), parent_id=last_dev["id"],
    )
    return {"task_id": new_id, "iteration": iteration}


@app.get("/api/findings")
def findings(chain_id: str | None = None, status: str | None = None):
    q, args = "SELECT * FROM findings WHERE 1=1", []
    if chain_id:
        q += " AND chain_id=?"; args.append(chain_id)
    if status:
        q += " AND status=?"; args.append(status)
    with board.connect() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY created_at DESC LIMIT 500", args)]


@app.get("/api/usage/summary")
def usage_summary(days: int = 30):
    since = time.time() - days * 86400
    with board.connect() as con:
        by_day = [dict(r) for r in con.execute(
            "SELECT date(created_at,'unixepoch','localtime') day, role, model, SUM(cost_usd) cost, "
            "SUM(input_tokens+cache_read_tokens+cache_write_tokens) in_tok, SUM(output_tokens) out_tok, "
            "COUNT(DISTINCT task_id) runs FROM usage WHERE created_at>=? GROUP BY day, role, model ORDER BY day", (since,))]
        by_role = [dict(r) for r in con.execute(
            "SELECT role, SUM(cost_usd) cost, COUNT(DISTINCT task_id) runs, SUM(input_tokens+cache_read_tokens+cache_write_tokens) in_tok, "
            "SUM(output_tokens) out_tok FROM usage WHERE created_at>=? GROUP BY role ORDER BY cost DESC", (since,))]
        by_model = [dict(r) for r in con.execute(
            "SELECT model, SUM(cost_usd) cost, COUNT(DISTINCT task_id) runs FROM usage WHERE created_at>=? GROUP BY model ORDER BY cost DESC", (since,))]
        tot = dict(con.execute(
            "SELECT COALESCE(SUM(cost_usd),0) cost, COUNT(DISTINCT task_id) runs, COALESCE(SUM(input_tokens+cache_read_tokens+cache_write_tokens),0) in_tok, "
            "COALESCE(SUM(output_tokens),0) out_tok FROM usage WHERE created_at>=?", (since,)).fetchone())
        by_chain = [dict(r) for r in con.execute(
            "SELECT u.chain_id, t.title, SUM(u.cost_usd) cost, COUNT(DISTINCT u.task_id) runs, "
            "SUM(u.input_tokens+u.cache_read_tokens+u.cache_write_tokens) in_tok, SUM(u.output_tokens) out_tok, MIN(u.created_at) started "
            "FROM usage u JOIN tasks t ON t.id = u.chain_id WHERE u.created_at>=? GROUP BY u.chain_id ORDER BY started DESC LIMIT 100", (since,))]
    return {"days": days, "by_day": by_day, "by_role": by_role, "by_model": by_model, "by_chain": by_chain, "totals": tot,
            "note": "cost_usd is the SDK's estimate at API list price — a consumption meter on a Team seat, not a bill"}


def _registered() -> list[dict]:
    """Servers registered for Claude Desktop/Code — import candidates. Names and redacted commands only."""
    out = []
    for label, path in (("claude-code", Path.home() / ".claude.json"),
                        ("claude-settings", Path.home() / ".claude/settings.json"),
                        ("claude-desktop", Path.home() / "Library/Application Support/Claude/claude_desktop_config.json")):
        try:
            cfg = json.loads(path.read_text())
        except Exception:
            continue
        servers = dict(cfg.get("mcpServers", {}))
        for proj in (cfg.get("projects") or {}).values():
            servers.update(proj.get("mcpServers") or {})
        for name, s in servers.items():
            out.append({"name": name, "source": label, "kind": s.get("type") or ("stdio" if s.get("command") else "http"),
                        "command": _cmd(s) if s.get("command") else (s.get("url") or "")})
    return out


def _connector_view(row: dict) -> dict:
    env = json.loads(row.get("env") or "{}")
    headers = json.loads(row.get("headers") or "{}")
    tools = board.connector_tools(row["name"])
    return {
        **row, "args": json.loads(row.get("args") or "[]"),
        "env": {k: {"secret": bool(v.get("secret")), "set": (vault.has_secret(row["name"], k) if v.get("secret") else bool(v.get("value"))),
                    "value": None if v.get("secret") else v.get("value")} for k, v in env.items()},
        "headers": {k: {"secret": bool(v.get("secret")), "set": (vault.has_secret(row["name"], f"header:{k}") if v.get("secret") else bool(v.get("value")))}
                    for k, v in headers.items()},
        "roles": board.connector_roles(row["name"]),
        "provider": "claude-account" if row["kind"] == conn.CLAUDE_AI_KIND else "configured",
        "masked_config": (conn.masked_config(row) if row["kind"] != conn.CLAUDE_AI_KIND else None),
        "tools": tools, "tool_count": len(tools), "mutating": sum(t["mutates"] for t in tools),
        "yaml_used_by": [a["role"] for a in _agents() if row["name"] in a["mcp_servers"]],
    }


@app.get("/api/connectors")
def connectors_list():
    return {"connectors": [_connector_view(r) for r in board.list_connectors()],
            "registered": _registered(),
            "roles": list(daemon.configs()),
            "yaml": [{"name": n, "used_by": [a["role"] for a in _agents() if n in a["mcp_servers"]]}
                     for n in sorted({m for a in _agents() for m in a["mcp_servers"]})]}


@app.get("/api/connectors/templates")
def connector_templates():
    return {k: {kk: vv for kk, vv in v.items()} for k, v in conn.TEMPLATES.items()}


class ConnectorIn(BaseModel):
    name: str
    template: str | None = None
    kind: str = "stdio"
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    env: dict[str, dict] = {}       # {VAR: {"secret": bool, "value": str|None}}  value of a secret is stored, never returned
    headers: dict[str, dict] = {}
    note: str | None = None
    enabled: bool = True


@app.post("/api/connectors")
def connector_save(c: ConnectorIn):
    name = c.name.strip()
    if not name or not re_name.match(name):
        raise HTTPException(400, "name must be letters, digits, - or _")
    env, headers = {}, {}
    for var, spec in c.env.items():
        if spec.get("secret"):
            if spec.get("value"):
                vault.set_secret(name, var, str(spec["value"]))
            env[var] = {"secret": True}
        else:
            env[var] = {"secret": False, "value": spec.get("value")}
    for h, spec in c.headers.items():
        if spec.get("secret"):
            if spec.get("value"):
                vault.set_secret(name, f"header:{h}", str(spec["value"]))
            headers[h] = {"secret": True}
        else:
            headers[h] = {"secret": False, "value": spec.get("value")}
    existed = board.get_connector(name) is not None
    board.upsert_connector({"name": name, "template": c.template, "kind": c.kind, "command": c.command,
                            "args": json.dumps(c.args), "url": c.url, "env": json.dumps(env),
                            "headers": json.dumps(headers), "note": c.note, "enabled": int(c.enabled)})
    row = board.get_connector(name)
    conn.log(name, "saved", f"{'updated' if existed else 'created'} from template {c.template or 'custom'}",
             {"config": conn.masked_config(row), "secrets_provided": [k for k, v in c.env.items() if v.get("secret") and v.get("value")]
              + [f"header:{h}" for h, v in c.headers.items() if v.get("secret") and v.get("value")]})
    return _connector_view(row)


import re as _re
re_name = _re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@app.post("/api/connectors/import")
def connector_import(body: dict):
    source, name = body.get("source"), body.get("name")
    if source not in ("claude-code", "claude-settings", "claude-desktop") or not name:
        raise HTTPException(400, "source and name required")
    try:
        conn.import_from_config(source, name)
    except KeyError:
        raise HTTPException(404, f"{name} not registered in {source}")
    return _connector_view(board.get_connector(name))


@app.post("/api/connectors/discover")
async def connectors_discover():
    """Find the MCP servers your Claude account injects into agent runs (claude.ai connectors)."""
    try:
        result = await conn.probe_account_connectors()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, conn.mask(f"{type(e).__name__}: {e}"))
    return {**result, "connectors": [_connector_view(r) for r in board.list_connectors()]}


@app.post("/api/connectors/{name}/test")
async def connector_test(name: str):
    if not board.get_connector(name):
        raise HTTPException(404, f"no connector {name}")
    result = await conn.test_connection(name)
    return {**result, "connector": _connector_view(board.get_connector(name))}


@app.put("/api/connectors/{name}/roles")
def connector_roles(name: str, body: dict):
    if not board.get_connector(name):
        raise HTTPException(404, f"no connector {name}")
    roles = [r for r in body.get("roles", []) if r in daemon.configs()]
    board.set_connector_roles(name, roles)
    allowed = [t["tool"] for t in board.connector_tools(name) if not t["mutates"]]
    conn.log(name, "roles", f"roles set to {roles or 'none'}; {len(allowed)} read tools will be allowed on their next run",
             {"roles": roles, "allowed_tools": allowed})
    return {"roles": roles}


@app.put("/api/connectors/{name}/tools/{tool}")
def connector_tool_mutates(name: str, tool: str, body: dict):
    board.set_tool_mutates(name, tool, bool(body.get("mutates")))
    conn.log(name, "tool_access", f"{tool} set to {'mutates (denied)' if body.get('mutates') else 'read (allowed)'} by user")
    return {"tool": tool, "mutates": bool(body.get("mutates")), "source": "user"}


@app.put("/api/connectors/{name}/enabled")
def connector_enabled(name: str, body: dict):
    board.set_connector_enabled(name, bool(body.get("enabled")))
    conn.log(name, "enabled", "enabled" if body.get("enabled") else "disabled")
    return {"enabled": bool(body.get("enabled"))}


@app.get("/api/connectors/{name}/log")
def connector_log(name: str, limit: int = 100):
    if not board.get_connector(name):
        raise HTTPException(404, f"no connector {name}")
    return board.connector_log(name, limit)


@app.delete("/api/connectors/{name}")
def connector_delete(name: str):
    row = board.get_connector(name)
    if not row:
        raise HTTPException(404, f"no connector {name}")
    for var, spec in json.loads(row.get("env") or "{}").items():
        if spec.get("secret"):
            vault.delete_secret(name, var)
    for h, spec in json.loads(row.get("headers") or "{}").items():
        if spec.get("secret"):
            vault.delete_secret(name, f"header:{h}")
    board.delete_connector(name)
    return {"deleted": name}


# ------------------------------------------------------------- code links ----

def _gitlab_base() -> str:
    """Web base URL for GitLab deep links: the gitlab-onprem connector's GITLAB_URL, else env, else default."""
    row = board.get_connector("gitlab-onprem")
    if row:
        env = json.loads(row.get("env") or "{}")
        v = (env.get("GITLAB_URL") or {}).get("value")
        if v:
            return str(v).rstrip("/")
    return os.environ.get("GITLAB_URL", "https://gitlab.ballys.tech").rstrip("/")


def _code_links(answer: dict) -> list[dict]:
    """For each CodeRef: where it can actually be opened. IntelliJ only when the file exists on
    this machine; GitLab blob link when the lead read it remotely; always the path to copy."""
    src = answer.get("source") or {}
    out = []
    for ref in answer.get("code") or []:
        rel = (ref.get("path") or "").lstrip("/")
        start = ref.get("start_line") or 1
        end = ref.get("end_line") or start
        entry = {"path": rel, "abs": None, "exists": False, "idea": None, "web": None}
        repo = src.get("repo_path")
        if repo and os.path.isdir(repo):
            abs_ = os.path.join(repo, rel)
            entry["abs"] = abs_
            entry["exists"] = os.path.isfile(abs_)
            if entry["exists"]:
                from urllib.parse import quote
                entry["idea"] = f"idea://open?file={quote(abs_, safe='/')}&line={start}"
        proj = src.get("gitlab_project")
        if proj:
            ref_name = src.get("commit") or src.get("branch") or "HEAD"
            entry["web"] = f"{_gitlab_base()}/{proj.strip('/')}/-/blob/{ref_name}/{rel}#L{start}-{end}"
        out.append(entry)
    return out


# ------------------------------------------------------------ transcripts ----

LOGS = ROOT / "logs"
_FIELD_CAP = 20_000   # chars per string field in the JSON view; ?raw=1 returns the untouched JSONL


def _cap(v, n=_FIELD_CAP):
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f"…[+{len(v) - n} chars]"
    if isinstance(v, dict):
        return {k: _cap(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [_cap(x, n) for x in v]
    return v


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if isinstance(e, dict) and e.get("kind"):
            out.append(e)
    return out


def _task_events(task_id: str) -> tuple[list[dict], str | None]:
    """Per-task file when it exists; otherwise the matching segment of the role log
    (runs from before per-task files existed)."""
    p = LOGS / "tasks" / f"{task_id}.jsonl"
    if p.is_file():
        return _read_jsonl(p), str(p)
    t = board.get_task(task_id)
    if not t:
        return [], None
    role_log = LOGS / f"{t['role']}.jsonl"
    if not role_log.is_file():
        return [], None
    evs = _read_jsonl(role_log)
    start = next((i for i, e in enumerate(evs) if e["kind"] == "request" and e.get("data", {}).get("task_id") == task_id), None)
    if start is None:
        return [], None
    seg = evs[start:]
    end = next((i for i, e in enumerate(seg) if e["kind"] == "response"), len(seg) - 1)
    return seg[:end + 1], str(role_log)


@app.get("/api/tasks/{task_id}/transcript")
def task_transcript(task_id: str, raw: int = 0):
    evs, source = _task_events(task_id)
    if raw:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("\n".join(json.dumps(e, default=str) for e in evs), media_type="application/x-ndjson")
    kinds: dict[str, int] = {}
    for e in evs:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return {"task_id": task_id, "source": source, "count": len(evs), "kinds": kinds, "events": _cap(evs)}


@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: str):
    """SSE: live view of one run. Replays what is already in logs/tasks/<task>.jsonl,
    then tails it until the task reaches a terminal status. Events:
      status {status, role}     board status changes
      log    <jsonl event>      thinking / say / tool_call / tool_result / init / done / …
      end    <chain detail>     the finished chain (kind, structured output, code_links)"""
    async def gen():
        path = LOGS / "tasks" / f"{task_id}.jsonl"
        pos = 0
        last_status = None
        waited = 0.0

        def read_new():
            nonlocal pos
            if not path.exists():
                return []
            with open(path, encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            out = []
            for line in chunk.splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if isinstance(e, dict) and e.get("kind"):
                    out.append(e)
            return out

        while True:
            t = board.get_task(task_id)
            status = t["status"] if t else None
            if status != last_status:
                last_status = status
                yield f"event: status\ndata: {json.dumps({'status': status, 'role': t['role'] if t else None, 'chain_id': t['chain_id'] if t else None})}\n\n"
            for e in read_new():
                yield f"event: log\ndata: {json.dumps(_cap(e, 6000), default=str)}\n\n"
            if status in ("done", "failed", "stuck") or (t is None and waited > 5):
                await asyncio.sleep(0.4)          # let the daemon finish its last writes
                for e in read_new():
                    yield f"event: log\ndata: {json.dumps(_cap(e, 6000), default=str)}\n\n"
                detail = chain(t["chain_id"]) if t else {"error": "no such task"}
                yield f"event: end\ndata: {json.dumps(detail, default=str)}\n\n"
                return
            yield ": ping\n\n"
            await asyncio.sleep(0.7)
            waited += 0.7
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/logs")
def logs_list():
    files = []
    if LOGS.is_dir():
        for p in sorted(LOGS.rglob("*")):
            if p.is_file():
                files.append({"name": str(p.relative_to(LOGS)), "bytes": p.stat().st_size, "mtime": p.stat().st_mtime})
    return {"dir": str(LOGS), "files": files}


@app.get("/api/logs/file")
def logs_file(name: str, tail: int = 500):
    p = (LOGS / name).resolve()
    if LOGS.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "no such log")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"name": name, "total_lines": len(lines), "lines": lines[-tail:] if tail else lines}


_FLOW_RE = _re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+\[(\w+)\]\s+(CLAIMED|FINISHED|FAILED|NEW JOB)\s+(\S+)\s*(.*)$", _re.S)


@app.get("/api/logs/flow")
def logs_flow(tail: int = 300):
    """flow.log parsed into rows: ts, role, action, task, detail."""
    p = LOGS / "flow.log"
    if not p.is_file():
        return {"rows": []}
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]:
        m = _FLOW_RE.match(line)
        if m:
            ts, role, action, task, detail = m.groups()
            rows.append({"ts": ts, "role": role, "action": action.replace(" ", "_"), "task": task, "detail": " ".join(detail.split())})
        else:
            rows.append({"ts": "", "role": "", "action": "", "task": "", "detail": line})
    return {"rows": rows}


@app.get("/api/logs/events")
def logs_events(name: str, tail: int = 400, kind: str | None = None):
    """A role JSONL parsed into events, each tagged with the task it belonged to."""
    p = (LOGS / name).resolve()
    if LOGS.resolve() not in p.parents or not p.is_file() or not p.name.endswith(".jsonl"):
        raise HTTPException(404, "no such jsonl log")
    evs = _read_jsonl(p)
    task = None
    for e in evs:
        if e["kind"] == "request":
            task = (e.get("data") or {}).get("task_id")
        e["task_id"] = task
    if kind:
        evs = [e for e in evs if e["kind"] == kind or (kind == "tools" and e["kind"].startswith("tool_"))]
    kinds: dict[str, int] = {}
    for e in evs:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    tasks_ = {}
    for e in evs:
        if e.get("task_id"):
            tasks_.setdefault(e["task_id"], 0)
            tasks_[e["task_id"]] += 1
    return {"name": name, "total": len(evs), "kinds": kinds, "tasks": tasks_, "events": _cap(evs[-tail:])}


@app.get("/api/events")
async def events():
    """SSE: `tasks` whenever the board changes, `flow` for each new flow.log line."""
    async def stream():
        last_sig = None
        flow_path = ROOT / "logs" / "flow.log"
        pos = flow_path.stat().st_size if flow_path.exists() else 0
        # replay the last few flow lines so a fresh page has context
        if flow_path.exists():
            tail = flow_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            for line in tail:
                yield f"event: flow\ndata: {json.dumps(line)}\n\n"
        while True:
            snap = board.snapshot()
            sig = json.dumps([(t["id"], t["status"], t["finished_at"], t["claimed_at"]) for t in snap], default=str)
            if sig != last_sig:
                last_sig = sig
                yield f"event: tasks\ndata: {json.dumps([_task(t) for t in snap[:100]], default=str)}\n\n"
                yield f"event: chains\ndata: {json.dumps(_chains(50), default=str)}\n\n"
            if flow_path.exists():
                size = flow_path.stat().st_size
                if size < pos:
                    pos = 0
                if size > pos:
                    with open(flow_path, encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        new = f.read()
                        pos = f.tell()
                    for line in new.splitlines():
                        yield f"event: flow\ndata: {json.dumps(line)}\n\n"
            yield ": ping\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Static UI (after the API routes so /api/* wins). Build with: cd ui && npm run build
_dist = ROOT / "ui" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """Single-page app: any non-API path serves index.html and the router takes over."""
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = _dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
