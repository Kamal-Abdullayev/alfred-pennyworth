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
    if s.get("every_min"):
        last = s.get("last_run_at") or 0
        cand = max(now, last + int(s["every_min"]) * 60)
        for _ in range(0, 14 * 24 * 60, 5):           # walk forward until inside an active window
            t = datetime.fromtimestamp(cand)
            days = {int(d) for d in str(s["days"]).split(",") if d.strip().isdigit()}
            cur = t.hour * 60 + t.minute
            if t.weekday() in days and _hhmm(s.get("active_from"), "08:00") <= cur < _hhmm(s.get("active_to"), "19:00"):
                return cand
            cand += 300
        return None
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


def local_tz() -> dict:
    """Name, abbreviation and UTC offset of the machine's time zone — the agents get UTC from Outlook."""
    import os
    now = datetime.now().astimezone()
    name = None
    try:
        link = os.readlink("/etc/localtime")
        name = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else None
    except Exception:
        pass
    off = now.strftime("%z")
    return {"name": name or now.tzname(), "abbr": now.tzname(), "offset": f"UTC{off[:3]}:{off[3:]}", "now": now.strftime("%Y-%m-%d %H:%M")}


def brief_body(s: dict, when: datetime) -> str:
    cutoff = (when - timedelta(days=1)).strftime("%A %Y-%m-%d 07:00")
    focus = f"\nExtra focus the person asked for: {s['prompt'].strip()}\n" if (s.get("prompt") or "").strip() else ""
    tz = local_tz()
    return (f"DAILY BRIEF for {me_name()} — {when.strftime('%A, %B %-d %Y')}, written at {when.strftime('%H:%M')} local time.\n"
            f"TIME ZONE: the person is in {tz['name']} ({tz['abbr']}, {tz['offset']}). Calendar and mail tools return times in UTC "
            f"(ISO strings ending in Z or +00:00) — CONVERT every time to {tz['abbr']} before writing it. A meeting at 08:00Z is "
            f"08:00 plus the offset in local time; do the arithmetic with the offset above for every meeting and mail.\n"
            f"Read everything since {cutoff} local: today's calendar, unread mail, Teams chats and channels, Jira issues assigned to me.\n"
            f"{focus}Write the brief as the structured output. Do not send or change anything anywhere.")


def _hhmm(v, default):
    try:
        h, m = (int(x) for x in str(v or default).split(":"))
        return h * 60 + m
    except Exception:
        h, m = (int(x) for x in default.split(":"))
        return h * 60 + m


def interval_due(s: dict, now: float) -> bool:
    """Interval schedules (watchers): every N minutes inside the active window on the chosen days."""
    every = int(s.get("every_min") or 0)
    if every <= 0:
        return False
    t = datetime.fromtimestamp(now)
    days = {int(d) for d in str(s["days"]).split(",") if d.strip().isdigit()}
    if t.weekday() not in days:
        return False
    cur = t.hour * 60 + t.minute
    if not (_hhmm(s.get("active_from"), "08:00") <= cur < _hhmm(s.get("active_to"), "19:00")):
        return False
    last = s.get("last_run_at") or 0
    return now - last >= every * 60


def watch_body(s: dict, when: datetime) -> str:
    since = datetime.fromtimestamp(s["last_run_at"]) if s.get("last_run_at") else when - timedelta(hours=12)
    tz = local_tz()
    gl_user = board.get_setting("gitlab_username") or ""
    known = board.recent_alert_keys(150)
    focus = f"\nExtra focus: {s['prompt'].strip()}\n" if (s.get("prompt") or "").strip() else ""
    return (f"WATCH for {me_name()} — now {when.strftime('%A %Y-%m-%d %H:%M')} {tz['abbr']} ({tz['offset']}); report only what changed since "
            f"{since.strftime('%Y-%m-%d %H:%M')} local. Tools return UTC — convert.\n"
            f"GitLab username: {gl_user or 'unknown — find my MRs by my name ' + me_name()}.\n"
            f"Check, quickly and in this order: (1) my open merge requests: pipeline turned red or green, new review comments or approvals, "
            f"merge conflicts; (2) Jira issues assigned to me or reported by me: status changes, new comments, blockers; "
            f"(3) Teams chats and channels: messages that mention me or my tickets and ask for something; (4) unread mail that needs an action "
            f"(not alerts, not newsletters). Already reported — do NOT repeat these keys: {', '.join(known) if known else '(none)'}.\n"
            f"{focus}Answer with the structured output. No items is the normal result; never invent one.")


def fire(s: dict, reason: str = "due") -> str:
    """Create the task for one schedule and record the run. Returns the task id."""
    when = datetime.now()
    title = f"{s['name']} · {when.strftime('%a %d %b')}"
    cid = board.create_conversation(title, s.get("project_dir"))
    if s["kind"] == "brief":
        body, question = brief_body(s, when), f"{s['name']} for {when.strftime('%A %-d %B')}"
    elif s["kind"] == "watch":
        body, question = watch_body(s, when), f"{s['name']} · check at {when.strftime('%H:%M')}"
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
        if s.get("every_min"):
            if interval_due(s, now):
                fired.append(fire(s, "interval"))
            continue
        due = due_slot(s, now)
        if due is None:
            continue
        if s.get("last_run_at") and s["last_run_at"] >= due:
            # this slot already ran — but if that run FAILED (e.g. no network right after wake-up,
            # API retries exhausted), give it one more try inside the grace window
            runs = [r for r in board.schedule_runs(s["id"], limit=5) if r["created_at"] >= due]
            if runs and len(runs) < 2 and all(r["status"] == "failed" for r in runs) and now - s["last_run_at"] >= 600:
                fired.append(fire(s, "retry after a failed run"))
            continue
        fired.append(fire(s, "due" if now - due < 120 else f"late by {int((now - due) / 60)} min"))
    return fired
