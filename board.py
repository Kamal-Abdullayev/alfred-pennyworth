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
    status      TEXT NOT NULL DEFAULT 'open',  -- open | claimed | done | failed | stuck | closed | cancelled
    stop_requested INTEGER NOT NULL DEFAULT 0,
    result      TEXT,                   -- agent's final text
    structured  TEXT,                   -- agent's validated contract output (JSON)
    project_dir TEXT,                   -- absolute path the agent works in
    worktree    TEXT,                   -- worktree path for the chain (root task)
    base_sha    TEXT,                   -- sha the chain branched from (root task)
    conversation_id TEXT,               -- Ask-page conversation (root task)
    question    TEXT,                   -- the human's raw question (root task)
    iteration   INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    claimed_by  TEXT,
    created_at  REAL NOT NULL,
    claimed_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_role_status ON tasks(role, status);
CREATE INDEX IF NOT EXISTS idx_tasks_chain ON tasks(chain_id);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    project_dir TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
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

CREATE TABLE IF NOT EXISTS turns (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL,
    chain_id           TEXT NOT NULL,
    role               TEXT NOT NULL,
    turn_index         INTEGER NOT NULL,
    model              TEXT NOT NULL,
    message_id         TEXT,
    input_tokens       INTEGER NOT NULL,
    output_tokens      INTEGER NOT NULL,
    cache_read_tokens  INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    est_cost_usd       REAL NOT NULL,     -- pricing.py list-price estimate for this message
    tools              TEXT NOT NULL,     -- JSON list of tool names called in this message
    text_chars         INTEGER NOT NULL,
    created_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_chain ON turns(chain_id);

CREATE TABLE IF NOT EXISTS connectors (
    name             TEXT PRIMARY KEY,
    template         TEXT,
    kind             TEXT NOT NULL,      -- stdio | http | sse
    command          TEXT,
    args             TEXT,               -- JSON list
    url              TEXT,
    env              TEXT,               -- JSON {VAR: {"secret": bool, "value": str}}
    headers          TEXT,               -- JSON {Header: {"secret": bool, "value": str}}
    enabled          INTEGER NOT NULL DEFAULT 1,
    trust_writes     INTEGER NOT NULL DEFAULT 0,  -- 1 = its mutating tools are harmless (scratch canvas) and allowed
    note             TEXT,
    last_test_at     REAL,
    last_test_status TEXT,               -- ok | failed
    last_test_error  TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_tools (
    connector   TEXT NOT NULL,
    tool        TEXT NOT NULL,
    description TEXT,
    mutates     INTEGER NOT NULL,        -- 1 = never allowed to an agent
    source      TEXT NOT NULL,           -- annotation | heuristic | user
    PRIMARY KEY (connector, tool)
);
CREATE TABLE IF NOT EXISTS connector_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    connector  TEXT NOT NULL,
    level      TEXT NOT NULL,               -- info | warn | error
    event      TEXT NOT NULL,               -- saved | imported | test_start | server_stderr | test_ok | test_failed |
                                            -- roles | enabled | deleted | discovered | run_init | denied
    message    TEXT NOT NULL,               -- already masked; never contains a secret value
    data       TEXT,                        -- JSON, masked
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_connector_log ON connector_log(connector, id);
CREATE TABLE IF NOT EXISTS workers (
    pid          INTEGER PRIMARY KEY,
    role         TEXT NOT NULL,
    ephemeral    INTEGER NOT NULL DEFAULT 0,  -- 1 = spawned by the supervisor on demand; exits when idle
    started_at   REAL NOT NULL,
    last_seen    REAL NOT NULL,
    current_task TEXT,
    host         TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_roles (
    connector TEXT NOT NULL,
    role      TEXT NOT NULL,
    PRIMARY KEY (connector, role)
);
CREATE TABLE IF NOT EXISTS alerts (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL,             -- stable id from the watcher, e.g. mr:701:pipeline:failed
    severity    TEXT NOT NULL,             -- info | warn | urgent
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL,
    url         TEXT,
    source      TEXT,                      -- gitlab | jira | teams | mail | board
    chain_id    TEXT,
    status      TEXT NOT NULL DEFAULT 'new',   -- new | dismissed
    created_at  REAL NOT NULL,
    dismissed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts(key);
CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- brief | prompt
    role        TEXT NOT NULL,            -- agent role that runs it
    prompt      TEXT NOT NULL DEFAULT '', -- for kind=prompt: the question; for brief: extra focus
    project_dir TEXT,
    at_time     TEXT NOT NULL,            -- HH:MM local
    days        TEXT NOT NULL,            -- comma list of 0-6 (Mon=0)
    enabled     INTEGER NOT NULL DEFAULT 1,
    grace_min   INTEGER NOT NULL DEFAULT 180,  -- run late up to this many minutes (laptop was asleep)
    last_run_at REAL,
    last_task   TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
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
            ("conversation_id", "TEXT"),   # Ask-page chat this chain belongs to
            ("question", "TEXT"),          # the human's raw question (body may carry injected context)
            ("stop_requested", "INTEGER NOT NULL DEFAULT 0"),  # human pressed Stop while it was running
            ("project_key", "TEXT"),       # memory key (git remote) when the task has no project_dir
            ("session_id", "TEXT"),        # Claude session the run used (resumable)
            ("resumed", "INTEGER NOT NULL DEFAULT 0"),  # 1 when the run continued an earlier session
        ):
            if name not in cols:
                con.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")
        # the spec-approval gate is gone; anything left waiting becomes claimable
        con.execute("UPDATE tasks SET status='open' WHERE status='awaiting_approval'")
        # the project folder was renamed or moved: chain worktrees live under <root>/worktrees/<chain>,
        # so re-point stored absolute paths at the current root
        root = str(Path(__file__).resolve().parent)
        for r in con.execute("SELECT id, worktree, project_dir FROM tasks WHERE worktree IS NOT NULL OR project_dir LIKE '%/worktrees/%'").fetchall():
            for col in ("worktree", "project_dir"):
                v = r[col]
                if v and "/worktrees/" in v and not v.startswith(root + "/"):
                    con.execute(f"UPDATE tasks SET {col}=? WHERE id=?", (root + "/worktrees/" + v.split("/worktrees/", 1)[1], r["id"]))
        vcols = [r["name"] for r in con.execute("PRAGMA table_info(conversations)")]
        for name, ddl in (("last_session_id", "TEXT"), ("last_session_cwd", "TEXT"), ("last_session_at", "REAL"), ("session_turns", "INTEGER NOT NULL DEFAULT 0")):
            if name not in vcols:
                con.execute(f"ALTER TABLE conversations ADD COLUMN {name} {ddl}")
        scols = [r["name"] for r in con.execute("PRAGMA table_info(schedules)")]
        for name, ddl in (("every_min", "INTEGER"), ("active_from", "TEXT"), ("active_to", "TEXT")):
            if scols and name not in scols:
                con.execute(f"ALTER TABLE schedules ADD COLUMN {name} {ddl}")
        ccols = [r["name"] for r in con.execute("PRAGMA table_info(connectors)")]
        if ccols and "trust_writes" not in ccols:
            con.execute("ALTER TABLE connectors ADD COLUMN trust_writes INTEGER NOT NULL DEFAULT 0")


# ---------------------------------------------------------------- tasks ----

def create_task(role, title, body, created_by, chain_id=None, iteration=1,
                project_dir=None, parent_id=None, conversation_id=None, question=None, project_key=None):
    task_id = str(uuid.uuid4())[:8]
    chain_id = chain_id or task_id
    with connect() as con:
        con.execute(
            "INSERT INTO tasks (id, chain_id, parent_id, role, title, body, iteration, "
            "created_by, created_at, project_dir, conversation_id, question, project_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, chain_id, parent_id, role, title, body, iteration, created_by,
             time.time(), project_dir, conversation_id, question, project_key),
        )
        if conversation_id:
            con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), conversation_id))
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
    """Park a chain: queued tasks become 'stuck' so nobody picks them up. Tasks that are
    already running are left alone — they finish, record their cost and result, and the
    daemon skips their hand-off because chain_is_parked() is true."""
    with connect() as con:
        con.execute(
            "UPDATE tasks SET status='stuck', result=COALESCE(result,'') || ' | parked: ' || ? "
            "WHERE chain_id=? AND status='open'",
            (reason, chain_id),
        )
        # if nothing was queued, still leave a marker so the chain reads as parked
        n = con.execute("SELECT COUNT(*) c FROM tasks WHERE chain_id=? AND status='stuck'", (chain_id,)).fetchone()["c"]
        if n == 0:
            root = con.execute("SELECT id FROM tasks WHERE chain_id=? ORDER BY created_at LIMIT 1", (chain_id,)).fetchone()
            if root:
                con.execute("UPDATE tasks SET result=COALESCE(result,'') || ' | parked: ' || ? WHERE id=?", (reason, root["id"]))
                con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"parked:{chain_id}", json.dumps(reason)))


def request_stop(task_id):
    """Human action. Queued task: cancelled at once. Running task: flagged; the runner
    interrupts the agent within a couple of seconds and the daemon marks it cancelled."""
    with connect() as con:
        t = con.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            return None
        if t["status"] == "open":
            con.execute("UPDATE tasks SET status='cancelled', result=COALESCE(result,'') || ' | cancelled before start', finished_at=? WHERE id=?",
                        (time.time(), task_id))
            return "cancelled"
        if t["status"] == "claimed":
            con.execute("UPDATE tasks SET stop_requested=1 WHERE id=?", (task_id,))
            return "stopping"
        return t["status"]


def stop_requested(task_id):
    with connect() as con:
        r = con.execute("SELECT stop_requested FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(r and r["stop_requested"])


def cancel_task(task_id, note="stopped by human"):
    with connect() as con:
        con.execute("UPDATE tasks SET status='cancelled', result=COALESCE(result,'') || ' | ' || ?, finished_at=? WHERE id=?",
                    (note, time.time(), task_id))


def stop_chain(chain_id):
    """Cancel everything queued, flag everything running, and close the chain."""
    with connect() as con:
        rows = con.execute("SELECT id, status FROM tasks WHERE chain_id=? AND status IN ('open','claimed')", (chain_id,)).fetchall()
    outcome = {r["id"]: request_stop(r["id"]) for r in rows}
    close_chain(chain_id, "stopped by human")
    return outcome


def requeue_chain(chain_id):
    """Human action: stuck tasks become claimable again; the parked marker is cleared."""
    with connect() as con:
        n = con.execute("UPDATE tasks SET status='open', claimed_by=NULL, claimed_at=NULL, "
                        "result=REPLACE(COALESCE(result,''), ' | parked: ', ' | was parked: ') "
                        "WHERE chain_id=? AND status='stuck'", (chain_id,)).rowcount
        con.execute("DELETE FROM settings WHERE key=?", (f"parked:{chain_id}",))
        return n


def close_chain(chain_id, reason):
    """Human action: the chain needs no more attention. Queued/stuck tasks are closed;
    finished ones are untouched. Running tasks finish but hand nothing off."""
    with connect() as con:
        n = con.execute("UPDATE tasks SET status='closed', result=COALESCE(result,'') || ' | closed: ' || ? "
                        "WHERE chain_id=? AND status IN ('open','stuck')", (reason, chain_id)).rowcount
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"closed:{chain_id}", json.dumps(reason)))
        con.execute("DELETE FROM settings WHERE key=?", (f"parked:{chain_id}",))
        return n


def chain_is_closed(chain_id):
    with connect() as con:
        return con.execute("SELECT 1 FROM settings WHERE key=?", (f"closed:{chain_id}",)).fetchone() is not None


def set_task_project_dir(task_id, project_dir):
    with connect() as con:
        con.execute("UPDATE tasks SET project_dir=? WHERE id=?", (project_dir, task_id))


def chain_is_parked(chain_id):
    with connect() as con:
        if con.execute("SELECT 1 FROM tasks WHERE chain_id=? AND status='stuck' LIMIT 1", (chain_id,)).fetchone():
            return True
        return con.execute("SELECT 1 FROM settings WHERE key IN (?, ?)", (f"parked:{chain_id}", f"closed:{chain_id}")).fetchone() is not None


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


# ---------------------------------------------------------------- workers ----

def worker_heartbeat(pid, role, ephemeral, current_task=None):
    import socket
    with connect() as con:
        con.execute(
            "INSERT INTO workers (pid, role, ephemeral, started_at, last_seen, current_task, host) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pid) DO UPDATE SET last_seen=excluded.last_seen, current_task=excluded.current_task",
            (pid, role, int(ephemeral), time.time(), time.time(), current_task, socket.gethostname()))


def worker_gone(pid):
    with connect() as con:
        con.execute("DELETE FROM workers WHERE pid=?", (pid,))


def workers(stale_after=60):
    """Live workers: heartbeat within stale_after seconds. Stale rows are removed."""
    with connect() as con:
        con.execute("DELETE FROM workers WHERE last_seen < ?", (time.time() - stale_after,))
        return [dict(r) for r in con.execute("SELECT * FROM workers ORDER BY role, started_at")]


def open_count_by_role():
    with connect() as con:
        return {r["role"]: r["n"] for r in con.execute("SELECT role, COUNT(*) n FROM tasks WHERE status='open' GROUP BY role")}


def get_setting(key, default=None):
    with connect() as con:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default


def set_setting(key, value):
    with connect() as con:
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)))


# -------------------------------------------------------- conversations ----

def create_conversation(title, project_dir=None):
    cid = str(uuid.uuid4())[:8]
    with connect() as con:
        con.execute("INSERT INTO conversations (id, title, project_dir, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (cid, title[:120], project_dir, time.time(), time.time()))
    return cid


def set_conversation_project_dir(cid, project_dir):
    with connect() as con:
        con.execute("UPDATE conversations SET project_dir=?, updated_at=? WHERE id=? AND (project_dir IS NULL OR project_dir='')",
                    (project_dir, time.time(), cid))


def set_conversation_session(cid, session_id, cwd, resumed):
    """Remember the lead's Claude session so the next question in this chat can continue it."""
    with connect() as con:
        con.execute("UPDATE conversations SET last_session_id=?, last_session_cwd=?, last_session_at=?, "
                    "session_turns=CASE WHEN ? THEN session_turns+1 ELSE 1 END WHERE id=?",
                    (session_id, cwd, time.time(), int(bool(resumed)), cid))


def set_task_session(task_id, session_id, resumed):
    with connect() as con:
        con.execute("UPDATE tasks SET session_id=?, resumed=? WHERE id=?", (session_id, int(bool(resumed)), task_id))


def turn_answer_text(root: dict) -> str | None:
    s = root.get("structured")
    s = json.loads(s) if isinstance(s, str) and s else s
    if not s:
        return None
    if s.get("kind") == "answer" and s.get("answer"):
        return s["answer"].get("answer")
    plan = s.get("plan") if "plan" in s else (s if "subtasks" in s else None)
    return plan.get("summary") if plan else None


def conversation_context(cid: str, max_turns: int = 3, max_chars: int = 1800) -> str:
    """Previous Q/A pairs, newest last — the fallback when a chat cannot resume its session."""
    turns = [t for t in conversation_turns(cid) if t["status"] == "done"][-max_turns:]
    parts = []
    for t in turns:
        a = turn_answer_text(t)
        if a:
            parts.append(f"Q: {(t.get('question') or t['body']).strip()[:600]}\nA: {a.strip()[:max_chars]}")
    if not parts:
        return ""
    return ("\n\n--- Conversation so far (context only — answer the latest question above, "
            "do not repeat earlier answers) ---\n" + "\n\n".join(parts))


RESUME_MAX_AGE_S = 24 * 3600     # a session older than this starts fresh (context re-injected instead)
RESUME_MAX_TURNS = 12            # after this many continued turns the transcript is long: start fresh


def resumable_session(cid, cwd):
    """The session id to continue for a new question in this chat, or None."""
    c = get_conversation(cid) if cid else None
    if not c or not c.get("last_session_id"):
        return None
    if c.get("last_session_cwd") != cwd:
        return None
    if time.time() - (c.get("last_session_at") or 0) > RESUME_MAX_AGE_S:
        return None
    if (c.get("session_turns") or 0) >= RESUME_MAX_TURNS:
        return None
    return c["last_session_id"]


def get_conversation(cid):
    with connect() as con:
        r = con.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None


def list_conversations(limit=100):
    """Newest first, with turn count and total cost of their chains."""
    with connect() as con:
        rows = con.execute(
            "SELECT c.*, "
            " (SELECT COUNT(*) FROM tasks t WHERE t.conversation_id=c.id AND t.role='team_lead') AS turns, "
            " (SELECT COALESCE(SUM(u.cost_usd),0) FROM usage u WHERE u.chain_id IN "
            "   (SELECT t.chain_id FROM tasks t WHERE t.conversation_id=c.id)) AS cost_usd, "
            " (SELECT t.status FROM tasks t WHERE t.conversation_id=c.id AND t.role='team_lead' ORDER BY t.created_at DESC LIMIT 1) AS last_status, "
            " (SELECT COUNT(*) FROM tasks t WHERE t.conversation_id=c.id AND t.created_by LIKE 'schedule:%') AS scheduled "
            "FROM conversations c ORDER BY c.updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def conversation_turns(cid):
    """The root (team_lead) tasks of a conversation, oldest first."""
    with connect() as con:
        rows = con.execute("SELECT * FROM tasks WHERE conversation_id=? AND role='team_lead' ORDER BY created_at", (cid,)).fetchall()
        return [dict(r) for r in rows]


def rename_conversation(cid, title):
    with connect() as con:
        con.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?", (title[:120], time.time(), cid))


def delete_conversation(cid):
    """Forgets the conversation; its chains stay on the board."""
    with connect() as con:
        con.execute("UPDATE tasks SET conversation_id=NULL WHERE conversation_id=?", (cid,))
        con.execute("DELETE FROM conversations WHERE id=?", (cid,))


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


# ---------------------------------------------------------------- turns ----

def record_turns(task_id, chain_id, role, turns):
    if not turns:
        return
    with connect() as con:
        con.executemany(
            "INSERT INTO turns (task_id, chain_id, role, turn_index, model, message_id, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens, est_cost_usd, tools, text_chars, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(task_id, chain_id, role, t["turn_index"], t["model"], t.get("message_id"),
              t["input_tokens"], t["output_tokens"], t["cache_read_tokens"], t["cache_write_tokens"],
              t["est_cost_usd"], json.dumps(t.get("tools") or []), t.get("text_chars", 0), time.time())
             for t in turns],
        )


def turns_for_chain(chain_id):
    with connect() as con:
        rows = con.execute("SELECT * FROM turns WHERE chain_id=? ORDER BY task_id, turn_index",
                           (chain_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r); d["tools"] = json.loads(d["tools"] or "[]"); out.append(d)
        return out


# ----------------------------------------------------------- connectors ----

def upsert_connector(row):
    now = time.time()
    with connect() as con:
        con.execute(
            "INSERT INTO connectors (name, template, kind, command, args, url, env, headers, enabled, note, created_at, updated_at) "
            "VALUES (:name, :template, :kind, :command, :args, :url, :env, :headers, :enabled, :note, :now, :now) "
            "ON CONFLICT(name) DO UPDATE SET template=excluded.template, kind=excluded.kind, command=excluded.command, "
            "args=excluded.args, url=excluded.url, env=excluded.env, headers=excluded.headers, enabled=excluded.enabled, "
            "note=excluded.note, updated_at=excluded.updated_at",
            {"name": row["name"], "template": row.get("template"), "kind": row["kind"], "command": row.get("command"),
             "args": row.get("args") or "[]", "url": row.get("url"), "env": row.get("env") or "{}",
             "headers": row.get("headers") or "{}", "enabled": int(row.get("enabled", 1)), "note": row.get("note"), "now": now},
        )


def get_connector(name):
    with connect() as con:
        r = con.execute("SELECT * FROM connectors WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None


def list_connectors():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM connectors ORDER BY name")]


def delete_connector(name):
    with connect() as con:
        con.execute("DELETE FROM connector_tools WHERE connector=?", (name,))
        con.execute("DELETE FROM connector_roles WHERE connector=?", (name,))
        con.execute("DELETE FROM connector_log WHERE connector=?", (name,))
        con.execute("DELETE FROM connectors WHERE name=?", (name,))


def set_connector_trust_writes(name, trust):
    with connect() as con:
        con.execute("UPDATE connectors SET trust_writes=?, updated_at=? WHERE name=?", (int(trust), time.time(), name))


def set_connector_enabled(name, enabled):
    with connect() as con:
        con.execute("UPDATE connectors SET enabled=?, updated_at=? WHERE name=?", (int(enabled), time.time(), name))


def set_connector_test(name, status, error):
    with connect() as con:
        con.execute("UPDATE connectors SET last_test_at=?, last_test_status=?, last_test_error=? WHERE name=?",
                    (time.time(), status, error, name))


def set_connector_tools(name, tools):
    with connect() as con:
        con.execute("DELETE FROM connector_tools WHERE connector=?", (name,))
        con.executemany("INSERT INTO connector_tools (connector, tool, description, mutates, source) VALUES (?, ?, ?, ?, ?)",
                        [(name, t["tool"], t.get("description"), int(t["mutates"]), t["source"]) for t in tools])


def connector_tools(name):
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM connector_tools WHERE connector=? ORDER BY tool", (name,))]


def set_tool_mutates(name, tool, mutates):
    with connect() as con:
        con.execute("UPDATE connector_tools SET mutates=?, source='user' WHERE connector=? AND tool=?",
                    (int(mutates), name, tool))


def set_connector_roles(name, roles):
    with connect() as con:
        con.execute("DELETE FROM connector_roles WHERE connector=?", (name,))
        con.executemany("INSERT INTO connector_roles (connector, role) VALUES (?, ?)", [(name, r) for r in roles])


def connector_roles(name):
    with connect() as con:
        return [r["role"] for r in con.execute("SELECT role FROM connector_roles WHERE connector=? ORDER BY role", (name,))]


def log_connector(connector, event, message, data=None, level="info"):
    with connect() as con:
        con.execute("INSERT INTO connector_log (connector, level, event, message, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (connector, level, event, message, json.dumps(data, default=str) if data is not None else None, time.time()))
        # keep the log bounded per connector
        con.execute("DELETE FROM connector_log WHERE connector=? AND id NOT IN "
                    "(SELECT id FROM connector_log WHERE connector=? ORDER BY id DESC LIMIT 500)", (connector, connector))


def connector_log(connector, limit=100):
    with connect() as con:
        rows = con.execute("SELECT * FROM connector_log WHERE connector=? ORDER BY id DESC LIMIT ?", (connector, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r); d["data"] = json.loads(d["data"]) if d["data"] else None; out.append(d)
        return out


def connectors_for_role(role):
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT c.* FROM connectors c JOIN connector_roles cr ON cr.connector=c.name WHERE cr.role=? ORDER BY c.name",
            (role,))]


if __name__ == "__main__":
    init()
    print(f"board initialised at {DB_PATH}")


# ------------------------------------------------------------ schedules ----

def list_schedules():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM schedules ORDER BY at_time, name")]


def get_schedule(sid):
    with connect() as con:
        r = con.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None


def create_schedule(name, kind, role, at_time, days, prompt="", project_dir=None, enabled=True, grace_min=180,
                    every_min=None, active_from=None, active_to=None):
    sid = str(uuid.uuid4())[:8]
    now = time.time()
    with connect() as con:
        con.execute("INSERT INTO schedules (id, name, kind, role, prompt, project_dir, at_time, days, enabled, grace_min, every_min, active_from, active_to, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid, name, kind, role, prompt or "", project_dir, at_time, days, int(enabled), grace_min, every_min, active_from, active_to, now, now))
    return get_schedule(sid)


def update_schedule(sid, **fields):
    allowed = {"name", "kind", "role", "prompt", "project_dir", "at_time", "days", "enabled", "grace_min", "every_min", "active_from", "active_to"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    vals = [int(v) if k == "enabled" else v for k, v in fields.items() if k in allowed]
    if not sets:
        return get_schedule(sid)
    with connect() as con:
        con.execute(f"UPDATE schedules SET {', '.join(sets)}, updated_at=? WHERE id=?", (*vals, time.time(), sid))
    return get_schedule(sid)


def delete_schedule(sid):
    with connect() as con:
        return con.execute("DELETE FROM schedules WHERE id=?", (sid,)).rowcount > 0


def mark_schedule_run(sid, task_id):
    with connect() as con:
        con.execute("UPDATE schedules SET last_run_at=?, last_task=? WHERE id=?", (time.time(), task_id, sid))


def schedule_runs(sid, limit=20):
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, chain_id, conversation_id, status, created_at, finished_at, role FROM tasks WHERE created_by=? ORDER BY created_at DESC LIMIT ?",
            (f"schedule:{sid}", limit))]


# ---------------------------------------------------------------- alerts ----

def add_alerts(items, chain_id=None, source_default="watch"):
    """Store watcher findings. A key already open (status new) or dismissed in the last 7 days is
    skipped, so the same red pipeline is not reported every half hour. Returns the new rows."""
    new = []
    now = time.time()
    with connect() as con:
        for it in items or []:
            key = (it.get("key") or "").strip()
            if not key:
                continue
            dup = con.execute("SELECT 1 FROM alerts WHERE key=? AND (status='new' OR dismissed_at > ?)", (key, now - 7 * 86400)).fetchone()
            if dup:
                continue
            aid = str(uuid.uuid4())[:8]
            con.execute("INSERT INTO alerts (id, key, severity, title, detail, url, source, chain_id, status, created_at) VALUES (?,?,?,?,?,?,?,?,'new',?)",
                        (aid, key, it.get("severity") or "info", (it.get("title") or "")[:200], it.get("detail") or "", it.get("url"),
                         it.get("source") or source_default, chain_id, now))
            new.append({"id": aid, **it})
    return new


def list_alerts(status="new", limit=50):
    with connect() as con:
        if status == "all":
            rows = con.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM alerts WHERE status=? ORDER BY CASE severity WHEN 'urgent' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, created_at DESC LIMIT ?", (status, limit)).fetchall()
        return [dict(r) for r in rows]


def dismiss_alert(aid=None, all_=False):
    with connect() as con:
        if all_:
            return con.execute("UPDATE alerts SET status='dismissed', dismissed_at=? WHERE status='new'", (time.time(),)).rowcount
        return con.execute("UPDATE alerts SET status='dismissed', dismissed_at=? WHERE id=?", (time.time(), aid)).rowcount


def recent_alert_keys(limit=200):
    with connect() as con:
        return [r["key"] for r in con.execute("SELECT key FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,))]
