#!/usr/bin/env bash
# run_all.sh — start the team_lead, developer and qa daemons and the API/UI together.
# One Ctrl-C stops all three.
#
#   ./run_all.sh
#
set -uo pipefail
cd "$(dirname "$0")"

# Prefer the project venv; fall back to whatever python3 is on PATH.
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

pids=()
cleanup() {
  echo
  echo "stopping agents..."
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM

for role in team_lead developer qa; do
  "$PY" daemon.py "$role" &
  pids+=($!)
  echo "started $role (pid $!)"
done

echo "all three daemons running — Ctrl-C to stop them all"
wait
