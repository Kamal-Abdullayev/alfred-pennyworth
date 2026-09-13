#!/usr/bin/env bash
# setup.sh — one-time setup for a fresh clone. Safe to re-run.
#
#   ./setup.sh            # venv + Python deps, UI build, then a preflight report
#   ./setup.sh --no-ui    # skip the npm build (e.g. no Node yet)
#
# What it does NOT do: log you into Claude (run `claude` once and /login with your
# Team account) or start Docker for the optional Excalidraw canvas
# (docker compose -f excalidraw/docker-compose.yml up -d).
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# --- Python ------------------------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Python 3.11+ is required (found: $(python3 --version 2>&1 || echo none))"; exit 1; }

if [ -f .venv/pyvenv.cfg ] && ! grep -q "^command = .* $(pwd)/.venv" .venv/pyvenv.cfg 2>/dev/null && ! grep -q "$(pwd)" .venv/pyvenv.cfg 2>/dev/null; then
  say "The .venv was created under a different path (folder renamed or moved) — recreating it"
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  say "Creating .venv with $PY"
  "$PY" -m venv .venv
fi
say "Installing Python dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# --- UI ------------------------------------------------------------------------
if [ "${1:-}" != "--no-ui" ]; then
  if command -v npm >/dev/null 2>&1; then
    say "Building the UI"
    (cd ui && npm install --no-fund --no-audit --silent && npm run build --silent)
  else
    echo "npm not found — install Node 20+ and run: (cd ui && npm install && npm run build)"
  fi
fi

# --- Folders the runtime expects ---------------------------------------------
mkdir -p logs/tasks logs/mcp data/assets data/memory workspace worktrees

# --- Preflight ---------------------------------------------------------------
say "Preflight"
.venv/bin/python doctor.py || true

cat <<'EOF'

Next:
  1. If "claude login" is not OK above:  claude        (then /login with your Team account, exit)
  2. ./run_all.sh                      → http://127.0.0.1:8787
  3. Connectors page → add Jira/GitLab/Confluence (or "Discover account connectors" for the
     ones your claude.ai account already has), Test, tick the roles.
  4. Optional canvas: docker compose -f excalidraw/docker-compose.yml up -d, then add the
     "excalidraw" connector from its template.
EOF
