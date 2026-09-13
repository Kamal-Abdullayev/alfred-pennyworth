"""schedules.py — recurring jobs (the daily brief, or any question) fired by the supervisor.

A schedule says: at HH:MM on these weekdays, create a task for this role. The supervisor
calls tick() every few seconds; a schedule fires once per due slot, and late by up to
`grace_min` minutes when the laptop was asleep. "Run now" from the UI uses fire().
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta

import board
import memory

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def me_name() -> str:
    """First name for the brief's headline: settings.me_name, else git user.name, else the login."""
    n = board.get_setting("me_name")
    if n:
        return n
    try:
        full = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True, timeout=3).stdout.strip()
        if full:
            return full.split()[0]
    except Exception:
        pass
    import getpass
    return getpass.getuser().split(".")[0].capitalize()


def due_slot(s: dict, now: float) -> float | None:
    """The most recent due time (epoch) for this schedule that is still within grace; None if none."""
    try:
        hh, mm = (int(x) for x in s["at_time"].split(":"))
    except Exception:
        return None
    days = {int(d) for d in str(s["days"]).split(",") if d.strip().isdigit()}
    t = datetime.fromtimestamp(now)
    for back in range(0, 2):                      # today, and yesterday's slot if still inside grace
        day = (t - timedelta(days=back)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if day.weekday() not in days:
            continue
        due = day.timestamp()
        if due <= now <= due + int(s.get("grace_min") or 180) * 60:
            return due
    return None


def next_run(s: dict, now: float) -> float | None:
    try:
        hh, mm = (int(x) for x in s["at_time"].split(":"))
    except Exception:
        return None
    days = {int(d) for d in str(s["days"]).split(",") if d.strip().isdigit()}
    if not days:
        return None
    t = datetime.fromtimestamp(now)
    for ahead in range(0, 8):
        day = (t + timedelta(days=ahead)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if day.weekday() in days and day.timestamp() > now:
            return day.timestamp()
    return None


def brief_body(s: dict, when: datetime) -> str:
    cutoff = (when - timedelta(days=1)).strftime("%A %Y-%m-%d 07:00")
    focus = f"\nExtra focus the person asked for: {s['prompt'].strip()}\n" if (s.get("prompt") or "").strip() else ""
    return (f"DAILY BRIEF for {me_name()} — {when.strftime('%A, %B %-d %Y')}, written at {when.strftime('%H:%M')} local time.\n"
            f"Read everything since {cutoff}: today's calendar, unread mail, Teams chats and channels, Jira issues assigned to me.\n"
            f"{focus}Write the brief as the structured output. Do not send or change anything anywhere.")


def fire(s: dict, reason: str = "due") -> str:
    """Create the task for one schedule and record the run. Returns the task id."""
    when = datetime.now()
    title = f"{s['name']} · {when.strftime('%a %d %b')}"
    cid = board.create_conversation(title, s.get("project_dir"))
    if s["kind"] == "brief":
        body, question = brief_body(s, when), f"{s['name']} for {when.strftime('%A %-d %B')}"
    else:
        body, question = s["prompt"], s["prompt"]
    tid = board.create_task(role=s["role"], title=title[:80], body=body, created_by=f"schedule:{s['id']}",
                            project_dir=s.get("project_dir"), conversation_id=cid, question=question,
                            project_key=memory.project_key(s.get("project_dir")))
    board.mark_schedule_run(s["id"], tid)
    try:
        from daemon import flow
        flow(f"[schedule] {s['name']} ({reason}) → {s['role']} task {tid}")
    except Exception:
        pass
    return tid


def tick(now: float | None = None) -> list[str]:
    """Fire every enabled schedule whose slot is due and not yet run. Returns fired task ids."""
    now = now or time.time()
    fired = []
    for s in board.list_schedules():
        if not s.get("enabled"):
            continue
        due = due_slot(s, now)
        if due is None:
            continue
        if s.get("last_run_at") and s["last_run_at"] >= due:
            continue                                    # this slot already ran
        fired.append(fire(s, "due" if now - due < 120 else f"late by {int((now - due) / 60)} min"))
    return fired
