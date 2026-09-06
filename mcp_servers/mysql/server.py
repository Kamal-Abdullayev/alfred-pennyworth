#!/usr/bin/env python3
"""Read-mostly, multi-schema MySQL MCP server for Claude Desktop.

Connects to a MySQL server (not pinned to one database) and exposes
schema-inspection and query tools across every schema the connecting user can
see. Reads (SELECT/SHOW/DESCRIBE/EXPLAIN) run freely. Writes are GATED behind
MYSQL_ALLOW_WRITES and a two-step dry-run-then-confirm flow -- and if you
connect with a read-only DB user (recommended), writes are impossible at the
database level regardless.

No third-party MCP code in the path. Uses PyMySQL (pure Python).

Environment variables:
  MYSQL_HOST          default 127.0.0.1
  MYSQL_PORT          default 3306
  MYSQL_USER          required
  MYSQL_PASSWORD      required
  MYSQL_DATABASE      optional default schema; if unset, tools span all schemas
                      and you pass `database=` per call (or fully-qualify in SQL)
  MYSQL_ALLOW_WRITES  set to 1/true to permit INSERT/UPDATE/DELETE/REPLACE
  MYSQL_ALLOW_DDL     set to 1/true to ALSO permit CREATE/DROP/ALTER/TRUNCATE
  MYSQL_LOG_DIR       default <project>/logs/mcp/
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler

import pymysql
import pymysql.cursors
from mcp.server.fastmcp import FastMCP

HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
PORT = int(os.environ.get("MYSQL_PORT", "3306"))
USER = os.environ.get("MYSQL_USER")
PASSWORD = os.environ.get("MYSQL_PASSWORD")
DEFAULT_DB = os.environ.get("MYSQL_DATABASE") or None
ALLOW_WRITES = os.environ.get("MYSQL_ALLOW_WRITES", "").lower() in ("1", "true", "yes")
ALLOW_DDL = os.environ.get("MYSQL_ALLOW_DDL", "").lower() in ("1", "true", "yes")
LOG_DIR = os.environ.get("MYSQL_LOG_DIR", str(__import__("pathlib").Path(__file__).resolve().parents[2] / "logs" / "mcp"))

# Transport: "stdio" (Claude Desktop launches it) or "http" (you run it; Claude connects).
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio").lower()
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

MAX_LOG_BYTES = 100 * 1024 * 1024
CONFIRM_TTL_SECONDS = 300

for nm, val in (("MYSQL_USER", USER), ("MYSQL_PASSWORD", PASSWORD)):
    if not val:
        raise RuntimeError(f"{nm} environment variable is required")

READ_PREFIXES = ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")
DML_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE")
DDL_PREFIXES = ("CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME")
DESTRUCTIVE = ("DROP", "TRUNCATE", "DELETE", "ALTER")
SYSTEM_SCHEMAS = ("information_schema", "mysql", "performance_schema", "sys")


class DatedRotatingFileHandler(RotatingFileHandler):
    def __init__(self, log_dir, prefix="mysql-mcp", max_bytes=MAX_LOG_BYTES, encoding="utf-8"):
        self.log_dir = log_dir
        self.prefix = prefix
        os.makedirs(log_dir, exist_ok=True)
        super().__init__(self._new_filename(), maxBytes=max_bytes, backupCount=0, encoding=encoding)

    def _new_filename(self):
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        return os.path.join(self.log_dir, f"{self.prefix}_{ts}.log")

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = os.path.abspath(self._new_filename())
        self.stream = self._open()


_handlers = [logging.StreamHandler()]
try:
    _handlers.append(DatedRotatingFileHandler(LOG_DIR))
except Exception as exc:
    logging.getLogger().warning("file logging unavailable in %s: %s", LOG_DIR, exc)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("mysql-mcp")

mcp = FastMCP("mysql-local", host=MCP_HOST, port=MCP_PORT)

_pending: dict[str, tuple[str, float]] = {}


def _connect(autocommit: bool = True) -> pymysql.connections.Connection:
    kwargs = dict(
        host=HOST, port=PORT, user=USER, password=PASSWORD,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=autocommit,
    )
    if DEFAULT_DB:
        kwargs["database"] = DEFAULT_DB
    return pymysql.connect(**kwargs)


def _bt(ident: str) -> str:
    return "`" + ident.replace("`", "``") + "`"


def _resolve_schema(database: str) -> str | None:
    return database or DEFAULT_DB


def _resolve_table(cur, schema: str, table: str):
    """Find the real stored table name for `table` within `schema`.

    Returns (real_name, candidates). Tries an exact match first, then a
    case-insensitive match (so 'member' resolves to 'MEMBER'). `candidates` is
    a list when the case-insensitive match is ambiguous (multiple tables differ
    only by case), and an empty list when nothing matches.
    """
    cur.execute(
        "SELECT table_name AS t FROM information_schema.tables WHERE table_schema = %s",
        (schema,),
    )
    names = [r["t"] for r in cur.fetchall()]
    if table in names:
        return table, None
    ci = [n for n in names if n.lower() == table.lower()]
    if len(ci) == 1:
        return ci[0], None
    if len(ci) > 1:
        return None, ci
    return None, []


def _first_keyword(sql: str) -> str:
    s = sql.strip().rstrip(";").lstrip()
    return s.split(None, 1)[0].upper() if s else ""


def _single_statement(sql: str) -> bool:
    return ";" not in sql.strip().rstrip(";")


def _lc(rows: list) -> list:
    """Lowercase the keys of each result row. MySQL 8's information_schema
    returns column labels in UPPERCASE (COLUMN_NAME, not column_name); this
    normalizes them so key access is predictable regardless of server version.
    """
    return [{k.lower(): v for k, v in r.items()} for r in rows]


def _rows_to_text(rows: list, max_rows: int) -> str:
    if not rows:
        return "(0 rows)"
    shown = rows[:max_rows]
    body = "\n".join(json.dumps(r, default=str, ensure_ascii=False) for r in shown)
    suffix = "" if len(rows) <= max_rows else f"\n... ({len(rows) - max_rows} more rows not shown)"
    return f"({len(rows)} row(s))\n{body}{suffix}"


@mcp.tool()
def list_databases(include_system: bool = False) -> str:
    """List the schemas (databases) the connected user can see.

    System schemas (information_schema, mysql, performance_schema, sys) are
    hidden unless include_system=True.
    """
    log.info("TOOL list_databases(include_system=%s)", include_system)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SHOW DATABASES")
        names = [list(r.values())[0] for r in cur.fetchall()]
    if not include_system:
        names = [n for n in names if n not in SYSTEM_SCHEMAS]
    if not names:
        return "No schemas visible to this user."
    return f"{len(names)} schema(s):\n" + "\n".join(f"- {n}" for n in names)


@mcp.tool()
def list_tables(database: str = "") -> str:
    """List tables in a schema, with row-count estimates.

    `database` selects the schema; if omitted, the server default is used. If
    neither is set, call list_databases first and pass one here.
    """
    log.info("TOOL list_tables(database=%r)", database)
    schema = _resolve_schema(database)
    if not schema:
        return "No schema selected. Call list_databases, then pass database='<schema>'."
    sql = (
        "SELECT table_name, table_rows, "
        "ROUND((data_length + index_length)/1024/1024, 2) AS size_mb "
        "FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name"
    )
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (schema,))
        rows = _lc(cur.fetchall())
    if not rows:
        return f"No tables in schema '{schema}'."
    return f"Tables in '{schema}':\n" + "\n".join(
        f"- {r['table_name']}  (~{r['table_rows']} rows, {r['size_mb']} MB)" for r in rows
    )


@mcp.tool()
def describe_table(table: str, database: str = "") -> str:
    """Show a table's design: columns, types, keys, indexes, and foreign keys.

    `database` selects the schema (defaults to the server default). The table
    is validated against the schema to prevent injection.
    """
    log.info("TOOL describe_table(table=%r, database=%r)", table, database)
    schema = _resolve_schema(database)
    if not schema:
        return "No schema selected. Pass database='<schema>' (see list_databases)."
    with _connect() as conn, conn.cursor() as cur:
        real, candidates = _resolve_table(cur, schema, table)
        if real is None:
            if candidates:
                return (f"'{table}' is ambiguous in schema '{schema}' (the server treats "
                        f"table names case-sensitively). Did you mean: {', '.join(candidates)}?")
            return f"Table '{table}' not found in schema '{schema}'."
        if real != table:
            log.info("   resolved table %r -> %r (case-insensitive)", table, real)
        table = real  # use the true stored name from here on
        qualified = f"{_bt(schema)}.{_bt(table)}"

        cur.execute(
            "SELECT column_name, column_type, is_nullable, column_key, "
            "column_default, extra FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema, table),
        )
        cols = _lc(cur.fetchall())

        cur.execute(f"SHOW INDEX FROM {qualified}")
        idx = _lc(cur.fetchall())

        cur.execute(
            "SELECT column_name, referenced_table_schema, referenced_table_name, "
            "referenced_column_name FROM information_schema.key_column_usage "
            "WHERE table_schema = %s AND table_name = %s "
            "AND referenced_table_name IS NOT NULL",
            (schema, table),
        )
        fks = _lc(cur.fetchall())

    out = [f"Table: {schema}.{table}", "", "Columns:"]
    for c in cols:
        bits = [c["column_name"], c["column_type"]]
        if c["column_key"]:
            bits.append({"PRI": "PK", "UNI": "UNIQUE", "MUL": "INDEX"}.get(c["column_key"], c["column_key"]))
        if c["is_nullable"] == "NO":
            bits.append("NOT NULL")
        if c["extra"]:
            bits.append(c["extra"])
        if c["column_default"] is not None:
            bits.append(f"default={c['column_default']}")
        out.append("  - " + "  ".join(str(b) for b in bits))

    if idx:
        out += ["", "Indexes:"]
        seen = {}
        for i in idx:
            seen.setdefault(i["key_name"], []).append(i["column_name"])
        for nm, c in seen.items():
            out.append(f"  - {nm} ({', '.join(c)})")

    if fks:
        out += ["", "Foreign keys:"]
        for f in fks:
            tgt_schema = f.get("referenced_table_schema")
            prefix = f"{tgt_schema}." if tgt_schema and tgt_schema != schema else ""
            out.append(
                f"  - {f['column_name']} -> {prefix}{f['referenced_table_name']}.{f['referenced_column_name']}"
            )

    return "\n".join(out)


@mcp.tool()
def run_select(sql: str, max_rows: int = 200) -> str:
    """Run a READ-ONLY query and return rows.

    Only SELECT / WITH / SHOW / DESCRIBE / EXPLAIN, single statement only.
    Since the connection isn't pinned to one schema, fully-qualify tables that
    aren't in the default schema, e.g. SELECT * FROM player.account LIMIT 5.
    """
    log.info("TOOL run_select(sql=%r, max_rows=%d)", sql, max_rows)
    if not _single_statement(sql):
        return "Refused: only a single statement is allowed (no ';'-separated statements)."
    kw = _first_keyword(sql)
    if kw not in READ_PREFIXES:
        return f"Refused: '{kw}' is not a read-only statement. Use execute_write for changes."
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() or []
    return _rows_to_text(rows, max_rows)


@mcp.tool()
def execute_write(sql: str) -> str:
    """Step 1 of a gated write: validates, DRY-RUNS inside a rolled-back
    transaction, reports rows it WOULD affect, and returns a confirmation
    token. Nothing persists here. Disabled unless MYSQL_ALLOW_WRITES is set
    (and impossible anyway with a read-only DB user). Fully-qualify tables.
    """
    log.info("TOOL execute_write(sql=%r)", sql)
    if not ALLOW_WRITES:
        return ("Refused: writes are disabled. Set MYSQL_ALLOW_WRITES=1 (and use a "
                "DB user that has write grants) to permit them.")
    if not _single_statement(sql):
        return "Refused: only a single statement is allowed."
    kw = _first_keyword(sql)
    if kw in READ_PREFIXES:
        return "That's a read query -- use run_select."
    if kw in DDL_PREFIXES and not ALLOW_DDL:
        return f"Refused: '{kw}' is DDL. Set MYSQL_ALLOW_DDL=1 to permit schema changes."
    if kw not in DML_PREFIXES and kw not in DDL_PREFIXES:
        return f"Refused: unsupported statement '{kw}'."

    try:
        conn = _connect(autocommit=False)
        try:
            with conn.cursor() as cur:
                affected = cur.execute(sql)
        finally:
            conn.rollback()
            conn.close()
    except Exception as exc:
        log.exception("execute_write dry-run failed")
        return f"Statement failed during dry-run (nothing changed): {exc}"

    token = uuid.uuid4().hex[:8]
    _pending[token] = (sql, time.time() + CONFIRM_TTL_SECONDS)
    warn = "  *** DESTRUCTIVE ***" if kw in DESTRUCTIVE else ""
    log.info("   dry-run ok: %s would affect %d row(s); token=%s", kw, affected, token)
    return (
        f"CONFIRMATION REQUIRED{warn}\n"
        f"Statement: {sql}\n"
        f"This would affect approximately {affected} row(s). Nothing has been changed yet.\n"
        f"To apply, the user must explicitly approve, then call: confirm_write(token=\"{token}\")\n"
        f"(token expires in {CONFIRM_TTL_SECONDS // 60} minutes)"
    )


@mcp.tool()
def confirm_write(token: str) -> str:
    """Step 2 of a gated write: executes and COMMITS the statement staged by
    execute_write. Only call after the user approved the exact SQL shown.
    """
    log.info("TOOL confirm_write(token=%r)", token)
    entry = _pending.pop(token, None)
    if not entry:
        return "No such pending statement (token unknown or already used)."
    sql, expiry = entry
    if time.time() > expiry:
        return "Token expired -- re-run execute_write to stage the statement again."
    try:
        conn = _connect(autocommit=False)
        try:
            with conn.cursor() as cur:
                affected = cur.execute(sql)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:
        log.exception("confirm_write failed")
        return f"Execution failed (rolled back): {exc}"
    log.info("   COMMITTED: %d row(s) affected", affected)
    return f"Done. {affected} row(s) affected and committed."


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("mysql-local MCP server starting")
    log.info("db_host=%s db_port=%s default_db=%s writes=%s ddl=%s",
             HOST, PORT, DEFAULT_DB or "(none / all schemas)", ALLOW_WRITES, ALLOW_DDL)
    log.info("transport=%s", MCP_TRANSPORT)
    try:
        if MCP_TRANSPORT in ("http", "streamable-http"):
            log.info("serving HTTP at http://%s:%s/mcp", MCP_HOST, MCP_PORT)
            mcp.run(transport="streamable-http")
        else:
            mcp.run()  # stdio
    except Exception:
        log.exception("server crashed")
        raise
    finally:
        log.info("mysql-local MCP server stopped")
