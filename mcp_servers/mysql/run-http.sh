#!/usr/bin/env bash
# Launch the MySQL MCP server as a local HTTP service you control.
# Edit the credentials below, then:  ./run-http.sh
set -euo pipefail
cd "$(dirname "$0")"

# --- connection (read-only user) ---
export MYSQL_HOST="127.0.0.1"
export MYSQL_PORT="3306"
export MYSQL_USER="mcp_readonly"
export MYSQL_PASSWORD="readonly"
# export MYSQL_DATABASE="player"   # optional default schema; omit to span all

# --- writes stay OFF (read-only user can't write anyway) ---
# export MYSQL_ALLOW_WRITES="1"
# export MYSQL_ALLOW_DDL="1"

# --- transport: HTTP on loopback only ---
export MCP_TRANSPORT="http"
export MCP_HOST="127.0.0.1"     # NEVER 0.0.0.0 — that exposes it to your network
export MCP_PORT="8000"

exec arch -arm64 .venv/bin/python server.py
