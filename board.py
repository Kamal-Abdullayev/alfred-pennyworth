# board.py — shared SQLite task board for the agent team.
#
# Every agent process talks to the same tasks.db file. SQLite transactions make
# claim_next atomic, so two daemons can never grab the same task. No server
# needed; inspect it any time with: sqlite3 tasks.db "select * from tasks"
#
# Tables:
#   tasks     one row per unit of work for one role; grouped by chain_id
#   findings  typed review findings, fed back to developers as data
#   usage     per-run token/cost ledger (SDK estimate at API list price)

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# ALFRED_DB overrides the board location — handy for tests and for running a
# second API instance against a scratch board.
DB_PATH = Path(os.environ.get("ALFRED_DB") or (Path(__file__).parent / "tasks.db"))

# A claimed task older than this (seconds) is considered abandoned
# (agent crashed mid-task) and gets requeued.
LEASE_SECONDS = 15 * 60

# Hard stop for dev->qa->dev ping-pong. A chain that fails QA this many times
# gets parked as 'stuck' for a human to look at.
MAX_ITERATIONS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    chain_id    TEXT NOT NULL,          -- groups a job across roles and rounds
    parent_id   TEXT,                   -- task this one was created from
    role        TEXT NOT NULL,          -- who should pick this up (agents/<role>.yaml)
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,          -- full instructions for the agent
    status      TEXT NOT NULL DEFAULT 'open',  -- open | claimed | done | failed | stuck
    result      TEXT,                   -- agent's final text
    structured  TEXT,                   -- agent's validated contract output (JSON)
    project_dir TEXT,                   -- absolute path the agent works in
    worktree    TEXT,                   -- worktree path for the chain (root task)
    base_sha    TEXT,                   -- sha the chain branched from (root task)
    iteration   INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    claimed_by  TEXT,
    created_at  REAL NOT NULL,
    claimed_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_role_status ON tasks(role, status);
CREATE INDEX IF NOT EXISTS idx_tasks_chain ON tasks(chain_id);

CREATE TABLE IF NOT EXISTS findings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id   TEXT NOT NULL,
    task_id    TEXT NOT NULL,           -- the review task that produced it
    iteration  INTEGER NOT NULL,
    source     TEXT NOT NULL,           -- qa | human | ci | mr_review
    file       TEXT NOT NULL,
    line       INTEGER,
    severity   TEXT NOT NULL,
    claim      TEXT NOT NULL,
    evidence   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',   -- open | addressed | wontfix
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_chain ON findings(chain_id, status);

CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           TEXT NOT NULL,
    chain_id          TEXT NOT NULL,
    role              TEXT NOT NULL,
    model             TEXT NOT NULL,
    input_tokens      INTEGER NOT NULL,
    output_tokens     INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost_usd          REAL NOT NULL,    -- SDK estimate at API list price, not a bill
    turns             INTEGER,
    duration_ms       INTEGER,
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_chain ON usage(chain_id);
"""


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        # additive migrations for boards created before these columns existed
        cols = [r["name"] for r in con.execute("PRAGMA table_info(tasks)")]
        for name, ddl in (
            ("project_dir", "TEXT"),
            ("parent_id", "TEXT"),
            ("structured", "TEXT"),
            ("worktree", "TEXT"),
            ("base_sha", "TEXT"),
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")
        # the spec-approval gate is gone; anything left waiting becomes claimable
        con.execute("UPDATE tasks SET status='open' WHERE status='awaiting_approval'")


# ---------------------------------------------------------------- tasks ----

def create_task(role, title, body, created_by, chain_id=None, iteration=1,
                project_dir=None, parent_id=None):
    task_id = str(uuid.uuid4())[:8]
    chain_id = chain_id or task_id
    with connect() as con:
        con.execute(
            "INSERT INTO tasks (id, chain_id, parent_id, role, title, body, iteration, "
            "created_by, created_at, project_dir) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, chain_id, parent_id, role, title, body, iteration, created_by,
             time.time(), project_dir),
        )
    return task_id


def get_task(task_id):
    with connect() as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def claim_next(role, agent_name):
    """Atomically claim the oldest open task for a role. Returns a dict or None."""
    _requeue_stale()
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")  # take the write lock up front
        row = con.execute(
            "SELECT * FROM tasks WHERE role=? AND status='open' ORDER BY created_at LIMIT 1",
            (role,),
        ).fetchone()
        if row is None:
            return None
        con.execute(
            "UPDATE tasks SET status='claimed', claimed_by=?, claimed_at=? WHERE id=?",
            (agent_name, time.time(), row["id"]),
        )
        return dict(row)


def complete(task_id, result, structured=None):
    _finish(task_id, "done", result, structured)


def fail(task_id, result, structured=None):
    _finish(task_id, "failed", result, structured)


def _finish(task_id, status, result, structured):
    with connect() as con:
        con.execute(
            "UPDATE tasks SET status=?, result=?, structured=?, finished_at=? WHERE id=?",
            (status, result, json.dumps(structured) if structured is not None else None,
             time.time(), task_id),
        )


def park_chain(chain_id, reason):
    """Mark a whole chain as stuck. Nothing will pick it up again."""
    with connect() as con:
        con.execute(
            "UPDATE tasks SET status='stuck', result=COALESCE(result,'') || ' | parked: ' || ? "
            "WHERE chain_id=? AND status IN ('open','claimed')",
            (reason, chain_id),
        )


def chain_root(chain_id):
    """The first task of a chain — carries the job text, worktree and base sha."""
    with connect() as con:
        row = con.execute(
            "SELECT * FROM tasks WHERE chain_id=? ORDER BY created_at LIMIT 1", (chain_id,)
        ).fetchone()
        return dict(row) if row else None


def chain_title(chain_id):
    root = chain_root(chain_id)
    return root["title"] if root else ""


def chain_iteration(chain_id):
    with connect() as con:
        row = con.execute(
            "SELECT MAX(iteration) AS it FROM tasks WHERE chain_id=?", (chain_id,)
        ).fetchone()
        return row["it"] or 1


def set_chain_worktree(chain_id, worktree, base_sha):
    root = chain_root(chain_id)
    with connect() as con:
        con.execute("UPDATE tasks SET worktree=?, base_sha=? WHERE id=?",
                    (worktree, base_sha, root["id"]))


def chain_open_count(chain_id, role=None):
    with connect() as con:
        if role:
            row = con.execute("SELECT COUNT(*) c FROM tasks WHERE chain_id=? AND role=? "
                              "AND status IN ('open','claimed')", (chain_id, role)).fetchone()
        else:
            row = con.execute("SELECT COUNT(*) c FROM tasks WHERE chain_id=? "
                              "AND status IN ('open','claimed')", (chain_id,)).fetchone()
        return row["c"]


def _requeue_stale():
    cutoff = time.time() - LEASE_SECONDS
    with connect() as con:
        con.execute(
            "UPDATE tasks SET status='open', claimed_by=NULL, claimed_at=NULL "
            "WHERE status='claimed' AND claimed_at < ?",
            (cutoff,),
        )


def snapshot():
    """Everything on the board, newest first. Used by monitor.py."""
    with connect() as con:
        rows = con.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------- findings ----

def add_findings(chain_id, task_id, iteration, source, findings):
    """findings: list of dicts with file, line, severity, claim, evidence."""
    with connect() as con:
        con.executemany(
            "INSERT INTO findings (chain_id, task_id, iteration, source, file, line, severity, "
            "claim, evidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(chain_id, task_id, iteration, source, f["file"], f.get("line"), f["severity"],
              f["claim"], f["evidence"], time.time()) for f in findings],
        )


def open_findings(chain_id):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM findings WHERE chain_id=? AND status='open' ORDER BY iteration, id",
            (chain_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def resolve_findings(chain_id, status="addressed"):
    with connect() as con:
        con.execute("UPDATE findings SET status=? WHERE chain_id=? AND status='open'",
                    (status, chain_id))


# ---------------------------------------------------------------- usage ----

def record_usage(task_id, chain_id, role, model, input_tokens, output_tokens,
                 cache_read_tokens, cache_write_tokens, cost_usd, turns=None, duration_ms=None):
    with connect() as con:
        con.execute(
            "INSERT INTO usage (task_id, chain_id, role, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cost_usd, turns, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, chain_id, role, model, input_tokens, output_tokens, cache_read_tokens,
             cache_write_tokens, cost_usd, turns, duration_ms, time.time()),
        )


def chain_cost(chain_id):
    with connect() as con:
        row = con.execute("SELECT COALESCE(SUM(cost_usd),0) c FROM usage WHERE chain_id=?",
                          (chain_id,)).fetchone()
        return row["c"]


if __name__ == "__main__":
    init()
    print(f"board initialised at {DB_PATH}")
