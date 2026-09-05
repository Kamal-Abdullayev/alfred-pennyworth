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
import daemon

ROOT = Path(__file__).parent
app = FastAPI(title="Alfred", version="0.1")
board.init()


# ---------------------------------------------------------------- helpers ----

def _task(t: dict) -> dict:
    out = dict(t)
    s = out.get("structured")
    out["structured"] = json.loads(s) if s else None
    return out


def _chain_status(tasks: list[dict]) -> str:
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
            "chain_id": cid, "title": root["title"], "status": _chain_status(tasks),
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


@app.get("/api/agents")
def agents():
    return _agents()


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
        "diff": diff, "log": log, "branch": f"alfred/{chain_id}", "status": _chain_status(tasks_),
        "cost_usd": sum(u["cost_usd"] for u in usage),
    }


class JobIn(BaseModel):
    body: str
    project_dir: str | None = None
    title: str | None = None


@app.post("/api/jobs")
def create_job(job: JobIn):
    body = job.body.strip()
    if not body:
        raise HTTPException(400, "empty job")
    project_dir = None
    if job.project_dir:
        p = Path(job.project_dir).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"project_dir does not exist: {p}")
        project_dir = str(p.resolve())
    task_id = board.create_task(role="team_lead", title=(job.title or body)[:80], body=body,
                                created_by="ui", project_dir=project_dir)
    return {"task_id": task_id, "chain_id": task_id}


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
    return {"days": days, "by_day": by_day, "by_role": by_role, "by_model": by_model, "totals": tot,
            "note": "cost_usd is the SDK's estimate at API list price — a consumption meter on a Team seat, not a bill"}


@app.get("/api/connectors")
def connectors():
    """MCP servers the agents can reach (from agents/*.yaml) plus those registered for Claude
    Desktop/Code. Names and commands only — never env values."""
    out: dict[str, dict] = {}
    for a in _agents():
        cfg = yaml.safe_load(open(ROOT / a["path"]))
        for name, s in (cfg.get("mcp_servers") or {}).items():
            c = out.setdefault(name, {"name": name, "kind": s.get("type", "stdio"),
                                      "command": _cmd(s), "used_by": [], "registered_in": ["agents/"]})
            c["used_by"].append(a["role"])
    for label, path in (("claude-code", Path.home() / ".claude.json"),
                        ("claude-desktop", Path.home() / "Library/Application Support/Claude/claude_desktop_config.json")):
        try:
            servers = json.loads(path.read_text()).get("mcpServers", {})
        except Exception:
            continue
        for name, s in servers.items():
            c = out.setdefault(name, {"name": name, "kind": s.get("type", "stdio"),
                                      "command": _cmd(s), "used_by": [], "registered_in": []})
            c["registered_in"].append(label)
    return sorted(out.values(), key=lambda c: c["name"])


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
