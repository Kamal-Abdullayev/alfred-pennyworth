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
[ -x "$PY" ] || PY="python3"

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

echo "all three daemons running — Ctrl-C to stop them all"
wait
