#!/usr/bin/env bash
# run_all.sh — start the worker supervisor and the API/UI together.
# One Ctrl-C stops all three.
#
#   ./run_all.sh
#
set -uo pipefail
cd "$(dirname "$0")"

# Prefer the project venv; fall back to whatever python3 is on PATH.
PY=".venv/bin/python"
[ -x "$PY" ] || { echo "no .venv — run ./setup.sh first"; exit 1; }

# Preflight: refuse to start on a broken setup (no login, API key set, missing deps…).
"$PY" doctor.py --json >/dev/null 2>&1 || { "$PY" doctor.py; exit 1; }

# The API serves ui/dist; build it if the UI was never built (or is older than its sources).
if [ ! -f ui/dist/index.html ] || [ -n "$(find ui/src -newer ui/dist/index.html -type f 2>/dev/null | head -1)" ]; then
  if command -v npm >/dev/null 2>&1; then echo "building the UI…"; (cd ui && npm install --no-fund --no-audit --silent && npm run build --silent); fi
fi

# Workers are owned by the supervisor. Stop any daemon.py started by hand first, so
# the same role is not run twice against the shared rate limit.
if pgrep -f "daemon.py" >/dev/null 2>&1; then
  echo "stopping stray daemon.py workers…"; pkill -f "daemon.py" || true; sleep 1
fi

pids=()
cleanup() {
  echo
  echo "stopping agents..."
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM

# One supervisor keeps every role's workers alive and scales them with the queue
# (per-role workers.min/max in agents/*.yaml, global cap: settings max_workers).
"$PY" supervisor.py &
pids+=($!)
echo "started supervisor (pid $!)"

# The API + UI. If something already listens on 8787 (a stale API), stop it first so the
# UI you open is the code you just started.
if lsof -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port 8787 busy — stopping the previous API"; pkill -f "uvicorn api:app" || true; sleep 1
fi
.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8787 --log-level warning &
pids+=($!)
echo "started API (pid $!) — http://127.0.0.1:8787"

echo "supervisor + workers + API running — Ctrl-C stops them all"
wait
