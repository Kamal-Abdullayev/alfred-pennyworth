#!/usr/bin/env python3
"""doctor.py — preflight for a fresh clone. Run it any time something feels off.

    python doctor.py            # human-readable table, exit 1 if a required check fails
    python doctor.py --json     # for the API / UI

Required checks must pass for Alfred to run at all; optional ones only cost a feature
(canvas drawings, IntelliJ links, Keychain-backed secrets).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PY = (3, 11)
MIN_NODE = 20


def _run(cmd, timeout=15) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def check(name, ok, detail, required=True, fix=None):
    return {"name": name, "ok": bool(ok), "required": required, "detail": detail, "fix": fix}


def run_checks() -> list[dict]:
    out = []

    # --- Python -----------------------------------------------------------
    v = sys.version_info
    out.append(check("python", v >= MIN_PY, f"{v.major}.{v.minor}.{v.micro} at {sys.executable}",
                     fix=f"install Python {MIN_PY[0]}.{MIN_PY[1]}+ and recreate .venv"))
    venv = ROOT / ".venv"
    in_venv = Path(sys.prefix).resolve() == venv.resolve()
    out.append(check("venv", venv.is_dir(), "using .venv" if in_venv else ("exists" if venv.is_dir() else "missing"),
                     fix="./setup.sh  (creates .venv and installs requirements.txt)"))
    missing = []
    for mod in ("claude_agent_sdk", "fastapi", "uvicorn", "pydantic", "yaml", "dotenv"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    out.append(check("python deps", not missing, "all importable" if not missing else f"missing: {', '.join(missing)}",
                     fix=".venv/bin/pip install -r requirements.txt"))
    try:
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        out.append(check("sqlite FTS5", True, "memory search uses full-text index", required=False))
    except Exception:
        out.append(check("sqlite FTS5", False, "not available — memory search falls back to LIKE", required=False))

    # --- Claude login (Team seat, never an API key) ------------------------
    key = os.environ.get("ANTHROPIC_API_KEY")
    out.append(check("no ANTHROPIC_API_KEY", not key, "unset" if not key else "SET — runs would bill API rates, runner refuses to start",
                     fix="unset ANTHROPIC_API_KEY (and remove it from your shell profile / .env)"))
    claude = shutil.which("claude")
    if claude:
        code, txt = _run([claude, "auth", "status"])
        logged = False
        method = ""
        try:
            j = json.loads(txt)
            logged, method = bool(j.get("loggedIn")), j.get("authMethod", "")
        except Exception:
            logged = code == 0 and "logged in" in txt.lower()
        out.append(check("claude login", logged, f"{claude} — {'logged in via ' + method if logged else 'NOT logged in'}",
                         fix="run `claude` once and /login with your Team account, then exit"))
    else:
        out.append(check("claude login", False, "claude CLI not on PATH (the SDK bundles a CLI, but login state comes from `claude /login`)",
                         fix="npm install -g @anthropic-ai/claude-code && claude   # then /login"))

    # --- Node / UI --------------------------------------------------------
    code, node = _run(["node", "--version"])
    major = int(node.lstrip("v").split(".")[0]) if code == 0 and node.startswith("v") else 0
    out.append(check("node", major >= MIN_NODE, node if code == 0 else "not found", fix=f"install Node {MIN_NODE}+ (https://nodejs.org)"))
    dist = ROOT / "ui" / "dist" / "index.html"
    out.append(check("ui built", dist.is_file(), "ui/dist present" if dist.is_file() else "ui/dist missing — API would serve no UI",
                     fix="(cd ui && npm install && npm run build)"))

    # --- git ----------------------------------------------------------------
    code, gitv = _run(["git", "--version"])
    out.append(check("git", code == 0, gitv if code == 0 else "not found", fix="install git"))

    # --- Ports --------------------------------------------------------------
    def port_free(p):
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", p)) != 0
    out.append(check("port 8787", True, "free" if port_free(8787) else "in use (Alfred API already running, or another process)", required=False,
                     fix="pkill -f 'uvicorn api:app'  if it is a stale Alfred API"))

    # --- Secrets store ------------------------------------------------------
    if sys.platform == "darwin" and shutil.which("security"):
        out.append(check("secrets store", True, "macOS Keychain (service alfred-mcp)", required=False))
    else:
        out.append(check("secrets store", True, "file vault data/secrets.json (0600) — Keychain unavailable on this OS", required=False))

    # --- Optional: Excalidraw canvas ------------------------------------------
    code, _ = _run(["docker", "info"], timeout=10)
    if code != 0:
        out.append(check("docker", False, "not running or not installed — canvas drawings unavailable, everything else works", required=False,
                         fix="install Docker Desktop, then: docker compose -f excalidraw/docker-compose.yml up -d"))
    else:
        canvas_up = not port_free(3000)
        out.append(check("excalidraw canvas", canvas_up, "http://localhost:3000 answering" if canvas_up else "nothing on :3000 — the lead cannot draw on the shared canvas",
                         required=False, fix="docker compose -f excalidraw/docker-compose.yml up -d"))

    # --- Optional: IntelliJ links -------------------------------------------
    idea = any(Path(p).exists() for p in ("/Applications/IntelliJ IDEA.app", "/Applications/IntelliJ IDEA CE.app")) or bool(shutil.which("idea"))
    out.append(check("intellij", idea, "found — 'Open in IntelliJ' links work" if idea else "not found — code links fall back to GitLab", required=False))
    return out


def main():
    checks = run_checks()
    if "--json" in sys.argv:
        print(json.dumps({"ok": all(c["ok"] for c in checks if c["required"]), "checks": checks}, indent=2))
        return
    width = max(len(c["name"]) for c in checks)
    bad = False
    for c in checks:
        mark = "OK  " if c["ok"] else ("FAIL" if c["required"] else "warn")
        print(f"  {mark}  {c['name']:<{width}}  {c['detail']}")
        if not c["ok"] and c.get("fix"):
            print(f"        {'':<{width}}  → {c['fix']}")
        bad |= (not c["ok"]) and c["required"]
    print()
    print("Alfred cannot start until the FAIL lines are fixed." if bad else "Ready. ./run_all.sh starts the workers and the UI on http://127.0.0.1:8787")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
