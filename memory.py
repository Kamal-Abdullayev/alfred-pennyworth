"""memory.py — two shared memories for the agent team.

1. PROJECT MEMORY: decisions, facts, conventions and open questions that outlive one chat.
   Keyed by the repository's git remote (two clones of one service share it) or "global".
   Rows are `active` (agents see them), `proposed` (an agent or a meeting-notes intake
   suggested them; the human accepts or rejects) or `retired`. Agents can propose, only the
   human activates. SQLite is the source of truth; data/memory/<key>.md is a readable export.

2. WORKING MEMORY: what everyone is doing right now — the board itself, plus one-line
   progress notes agents leave for each other (task_notes).

Both are rendered into a compact block appended to every task prompt (context_block) and
exposed to agents as in-process MCP tools (see runner.alfred_tools).
"""
from __future__ import annotations

import re
import subprocess
import time
import uuid
from pathlib import Path

import board

ROOT = Path(__file__).resolve().parent
EXPORT_DIR = ROOT / "data" / "memory"

KINDS = ("decision", "fact", "convention", "glossary", "person", "question", "todo")
STATUSES = ("active", "proposed", "retired")
GLOBAL = "global"
PROMPT_BUDGET = 7000          # chars of memory injected per run (~1.7k tokens, mostly cache reads)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL DEFAULT 'project',   -- project | global
    project_key TEXT NOT NULL,                     -- host/group/repo from the git remote, or 'global'
    kind        TEXT NOT NULL,                     -- decision | fact | convention | glossary | person | question | todo
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',          -- comma-separated
    source      TEXT NOT NULL,                     -- human | team_lead | developer | qa | meeting
    status      TEXT NOT NULL DEFAULT 'active',    -- active | proposed | retired
    chain_id    TEXT,                              -- run that produced it, if any
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    accepted_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mem_key_status ON memories(project_key, status);
CREATE TABLE IF NOT EXISTS task_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    chain_id   TEXT NOT NULL,
    role       TEXT NOT NULL,
    note       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_chain ON task_notes(chain_id);
"""
FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(title, body, tags, content='memories', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, title, body, tags) VALUES (new.rowid, new.title, new.body, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, body, tags) VALUES ('delete', old.rowid, old.title, old.body, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, body, tags) VALUES ('delete', old.rowid, old.title, old.body, old.tags);
  INSERT INTO memories_fts(rowid, title, body, tags) VALUES (new.rowid, new.title, new.body, new.tags);
END;
"""
_fts_ok: bool | None = None


def init():
    global _fts_ok
    with board.connect() as con:
        con.executescript(SCHEMA)
        try:
            con.executescript(FTS)
            _fts_ok = True
        except Exception:      # sqlite built without FTS5: fall back to LIKE search
            _fts_ok = False


# ------------------------------------------------------------ project key ----

def project_key(project_dir: str | None) -> str:
    """host/group/repo from the git remote, so two clones share one memory. Falls back to the
    repo folder name; no repository → 'global'."""
    if not project_dir:
        return GLOBAL
    p = Path(project_dir).expanduser()
    if not p.exists():
        return GLOBAL
    try:
        url = subprocess.run(["git", "-C", str(p), "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except Exception:
        url = ""
    if url:
        return normalise_remote(url)
    try:
        root = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        if root:
            return Path(root).name.lower()
    except Exception:
        pass
    # a workspace folder holding several clones (no git remote of its own): key by folder name
    return safe_name(p.resolve().name) or GLOBAL


def normalise_remote(url: str) -> str:
    """git@gitlab.x.com:group/repo.git | https://user@gitlab.x.com/group/repo.git → gitlab.x.com/group/repo"""
    u = url.strip()
    u = re.sub(r"^[a-z+]+://", "", u)           # scheme
    u = re.sub(r"^[^@/]+@", "", u)               # user@
    u = re.sub(r"^([^/:]+):\d+/", r"\1/", u)     # host:port/
    u = u.replace(":", "/", 1) if re.match(r"^[^/]+:[^/]", u) else u   # scp-style host:path
    u = re.sub(r"\.git/?$", "", u)
    return u.strip("/").lower()


def safe_name(key: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "__", key.lower()).strip("_") or GLOBAL


# ------------------------------------------------------------------ CRUD ----

def _row(r) -> dict:
    d = dict(r)
    d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
    return d


def add(project_key_: str, kind: str, title: str, body: str, source: str, *, tags=None,
        status: str = "active", chain_id: str | None = None) -> dict:
    kind = kind if kind in KINDS else "fact"
    status = status if status in STATUSES else "proposed"
    scope = "global" if project_key_ == GLOBAL else "project"
    mid = str(uuid.uuid4())[:8]
    now = time.time()
    tags_s = ",".join(sorted({t.strip().lower() for t in (tags or []) if t and t.strip()}))
    with board.connect() as con:
        con.execute(
            "INSERT INTO memories (id, scope, project_key, kind, title, body, tags, source, status, chain_id, "
            "created_at, updated_at, accepted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, scope, project_key_, kind, title.strip()[:200], body.strip(), tags_s, source, status, chain_id,
             now, now, now if status == "active" else None))
    export(project_key_)
    return get(mid)


def get(mid: str) -> dict | None:
    with board.connect() as con:
        r = con.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    return _row(r) if r else None


def update(mid: str, **fields) -> dict | None:
    allowed = {"kind", "title", "body", "tags", "status", "project_key"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "tags":
            v = ",".join(sorted({t.strip().lower() for t in v if t and t.strip()})) if isinstance(v, list) else v
        if k == "kind" and v not in KINDS:
            continue
        if k == "status" and v not in STATUSES:
            continue
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get(mid)
    if fields.get("status") == "active":
        sets.append("accepted_at=?")
        vals.append(time.time())
    if "project_key" in fields and fields["project_key"]:
        sets.append("scope=?")
        vals.append("global" if fields["project_key"] == GLOBAL else "project")
    sets.append("updated_at=?")
    vals.append(time.time())
    with board.connect() as con:
        con.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", (*vals, mid))
    m = get(mid)
    if m:
        export(m["project_key"])
    return m


def delete(mid: str) -> bool:
    m = get(mid)
    if not m:
        return False
    with board.connect() as con:
        con.execute("DELETE FROM memories WHERE id=?", (mid,))
    export(m["project_key"])
    return True


def list_(project_key_: str | None = None, status: str | None = None, q: str | None = None,
          chain_id: str | None = None, limit: int = 500) -> list[dict]:
    where, vals = [], []
    if project_key_:
        where.append("project_key=?")
        vals.append(project_key_)
    if status:
        where.append("status=?")
        vals.append(status)
    if chain_id:
        where.append("chain_id=?")
        vals.append(chain_id)
    sql = "SELECT * FROM memories"
    if q and q.strip():
        ids = search_ids(q, limit=limit)
        if not ids:
            return []
        where.append(f"id IN ({','.join('?' * len(ids))})")
        vals.extend(ids)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE status WHEN 'proposed' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, updated_at DESC LIMIT ?"
    with board.connect() as con:
        return [_row(r) for r in con.execute(sql, (*vals, limit))]


def search_ids(q: str, limit: int = 50) -> list[str]:
    q = q.strip()
    if not q:
        return []
    with board.connect() as con:
        if _fts_ok:
            try:
                # quote every term so user text can't break the FTS grammar; prefix-match the last one
                terms = [f'"{t.replace(chr(34), "")}"' for t in re.findall(r"\w+", q)]
                if terms:
                    terms[-1] += "*"
                    rows = con.execute(
                        "SELECT m.id FROM memories_fts f JOIN memories m ON m.rowid=f.rowid WHERE memories_fts MATCH ? "
                        "ORDER BY bm25(memories_fts) LIMIT ?", (" ".join(terms), limit)).fetchall()
                    return [r["id"] for r in rows]
            except Exception:
                pass
        like = f"%{q}%"
        rows = con.execute("SELECT id FROM memories WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? LIMIT ?",
                           (like, like, like, limit)).fetchall()
        return [r["id"] for r in rows]


def projects() -> list[dict]:
    with board.connect() as con:
        rows = con.execute(
            "SELECT project_key, SUM(status='active') active, SUM(status='proposed') proposed, "
            "SUM(status='retired') retired, MAX(updated_at) updated_at FROM memories GROUP BY project_key "
            "ORDER BY project_key='global' DESC, updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def store_proposals(project_key_: str, proposals, source: str, chain_id: str | None) -> list[dict]:
    """Proposals from an agent's structured output. Skips near-duplicates of existing titles."""
    out = []
    if not proposals:
        return out
    existing = {m["title"].strip().lower() for m in list_(project_key_, limit=2000)}
    for p in proposals:
        p = dict(p) if not isinstance(p, dict) else p
        title = (p.get("title") or "").strip()
        if not title or title.lower() in existing:
            continue
        out.append(add(project_key_, p.get("kind") or "fact", title, p.get("body") or "", source,
                       tags=p.get("tags") or [], status="proposed", chain_id=chain_id))
        existing.add(title.lower())
    return out


def rekey_chain(chain_id: str, new_key: str) -> int:
    """Entries proposed while the run had no repository land under 'global'; once the lead's
    answer names the checkout it read, move them to that project."""
    if not new_key or new_key == GLOBAL:
        return 0
    with board.connect() as con:
        n = con.execute("UPDATE memories SET project_key=?, scope='project', updated_at=? WHERE chain_id=? AND project_key=?",
                        (new_key, time.time(), chain_id, GLOBAL)).rowcount
    if n:
        export(GLOBAL)
        export(new_key)
    return n


# ----------------------------------------------------------------- notes ----

def add_note(task_id: str, chain_id: str, role: str, note: str) -> dict:
    now = time.time()
    with board.connect() as con:
        cur = con.execute("INSERT INTO task_notes (task_id, chain_id, role, note, created_at) VALUES (?,?,?,?,?)",
                          (task_id, chain_id, role, note.strip()[:500], now))
        nid = cur.lastrowid
    return {"id": nid, "task_id": task_id, "chain_id": chain_id, "role": role, "note": note.strip()[:500], "created_at": now}


def notes_for_chain(chain_id: str, limit: int = 50) -> list[dict]:
    with board.connect() as con:
        rows = con.execute("SELECT * FROM task_notes WHERE chain_id=? ORDER BY id DESC LIMIT ?", (chain_id, limit)).fetchall()
    return [dict(r) for r in rows][::-1]


# ---------------------------------------------------------- prompt blocks ----

_KIND_ORDER = {k: i for i, k in enumerate(("decision", "convention", "fact", "glossary", "person", "question", "todo"))}


def memory_block(project_key_: str, budget: int = PROMPT_BUDGET) -> str:
    rows = list_(project_key_, status="active", limit=400)
    if project_key_ != GLOBAL:
        rows += list_(GLOBAL, status="active", limit=200)
    if not rows:
        return (f"\n\n--- PROJECT MEMORY ({project_key_}) ---\n"
                "(empty) Nothing has been recorded for this project yet. If the human states a decision, "
                "constraint or fact that is not visible in the code, propose it as memory.\n")
    rows.sort(key=lambda m: (_KIND_ORDER.get(m["kind"], 9), -m["updated_at"]))
    lines, used, skipped = [], 0, 0
    for m in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(m["accepted_at"] or m["updated_at"]))
        scope = "" if m["project_key"] == project_key_ else " (global)"
        body = " ".join(m["body"].split())
        line = f"- [{m['kind']}{scope} {day} #{m['id']}] {m['title']}: {body}"
        if used + len(line) > budget:
            skipped += 1
            continue
        lines.append(line)
        used += len(line) + 1
    more = f"\n({skipped} more entries not shown — use memory_search)" if skipped else ""
    return (f"\n\n--- PROJECT MEMORY ({project_key_}) — decisions and facts the human recorded; treat as "
            f"authoritative for intent, but say so if the code contradicts them ---\n" + "\n".join(lines) + more + "\n")


def board_block(task: dict) -> str:
    """Other live work in the same chain and project, plus the notes agents left."""
    chain = task["chain_id"]
    with board.connect() as con:
        same_chain = [dict(r) for r in con.execute(
            "SELECT id, role, title, status, iteration, result FROM tasks WHERE chain_id=? AND id<>? ORDER BY created_at",
            (chain, task["id"]))]
        others = []
        if task.get("project_dir"):
            others = [dict(r) for r in con.execute(
                "SELECT id, chain_id, role, title, status FROM tasks WHERE project_dir=? AND chain_id<>? AND status IN ('open','claimed') "
                "ORDER BY created_at DESC LIMIT 8", (task["project_dir"], chain))]
    notes = notes_for_chain(chain, limit=20)
    if not same_chain and not others and not notes:
        return ""
    out = ["\n\n--- BOARD — what the rest of the team is doing right now ---"]
    if same_chain:
        out.append("This chain:")
        for t in same_chain:
            res = (t.get("result") or "").strip().replace("\n", " ")
            res = f" — {res[:140]}" if res and t["status"] in ("done", "failed", "stuck") else ""
            out.append(f"  • {t['role']} · {t['status']} · round {t['iteration']} · {t['title'][:90]}{res}")
    if others:
        out.append("Same repository, other chains:")
        for t in others:
            out.append(f"  • {t['role']} · {t['status']} · chain {t['chain_id']} · {t['title'][:90]}")
    if notes:
        out.append("Progress notes left by teammates (newest last):")
        for n in notes:
            hhmm = time.strftime("%H:%M", time.localtime(n["created_at"]))
            out.append(f"  {hhmm} {n['role']} ({n['task_id']}): {n['note']}")
    out.append("Use board_peek for the latest state and note_progress to leave a one-line note for the others.")
    return "\n".join(out) + "\n"


def context_block(task: dict, role: str) -> str:
    key = task.get("project_key") or project_key(task.get("project_dir"))
    return memory_block(key) + board_block(task)


# ---------------------------------------------------------------- export ----

def export(project_key_: str) -> Path:
    """Readable Markdown mirror of one project's memory (git-friendly, greppable)."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{safe_name(project_key_)}.md"
    rows = list_(project_key_, limit=5000)
    lines = [f"# Project memory — {project_key_}", "", f"_exported {time.strftime('%Y-%m-%d %H:%M')} by Alfred; "
             f"the SQLite board is the source of truth — edit on the Memory page, not here._", ""]
    for status in ("active", "proposed", "retired"):
        sub = [m for m in rows if m["status"] == status]
        if not sub:
            continue
        lines += [f"## {status.capitalize()} ({len(sub)})", ""]
        for m in sorted(sub, key=lambda m: (_KIND_ORDER.get(m["kind"], 9), -m["updated_at"])):
            day = time.strftime("%Y-%m-%d", time.localtime(m["updated_at"]))
            tags = f"  `{'` `'.join(m['tags'])}`" if m["tags"] else ""
            lines += [f"- **{m['title']}** ({m['kind']}, {m['source']}, {day}, #{m['id']}){tags}  ", f"  {m['body']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ------------------------------------------------------- meeting intake ----

def intake_body(notes: str, project_key_: str) -> str:
    return (
        "MEETING NOTES INTAKE — do not investigate code for this request.\n\n"
        f"The human pasted notes from a meeting about project `{project_key_}`. Distil them into project memory:\n"
        "- every DECISION taken (what was decided, by whom if stated, and why)\n"
        "- every FACT or constraint that a future agent must respect (deadlines, environments, owners, scope limits)\n"
        "- open QUESTIONs and TODOs with an owner where given\n"
        "- GLOSSARY entries for internal names or acronyms that appear\n"
        "Put each as one entry in `memory_proposals` (kind, one-line title, self-contained body, tags). "
        "Do not propose anything already present in the PROJECT MEMORY block. Skip small talk and status "
        "chatter that will be stale tomorrow.\n"
        "Answer with kind: answer — a short Markdown summary of what you extracted, grouped by kind, so the "
        "human can review the proposals on the Memory page. No code, no diagrams, source fields null.\n\n"
        f"--- Meeting notes ---\n{notes.strip()}\n"
    )
