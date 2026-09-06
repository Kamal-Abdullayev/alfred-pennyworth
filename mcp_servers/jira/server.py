#!/usr/bin/env python3
"""Read-only Jira (Server / Data Center) MCP server for Claude Desktop.

Talks ONLY to the on-prem Jira instance in JIRA_URL, using a Personal Access
Token (Profile -> Personal Access Tokens) sent as an HTTP Bearer credential.
No writes of any kind -- no create, comment, or transition. No third-party MCP
code in the path.

Covers two Jira REST APIs through one client:
  * Platform API  /rest/api/2/     -- issues, JQL search, projects, comments,
                                       transitions, current user.
  * Agile  API    /rest/agile/1.0/ -- boards, sprints, backlog, epics. This API
                                       is not enabled on every instance; the
                                       agile tools degrade to a clear message
                                       instead of a stack trace when it's absent.

Environment variables:
  JIRA_URL        Base URL of the instance, e.g. https://jira.ballys.tech
                  (required)
  JIRA_TOKEN      Personal Access Token with read access  (required)
  JIRA_CA_BUNDLE  Path to an internal CA PEM file. Set ONLY if you hit an SSL
                  verification error (httpx uses certifi, not the macOS
                  keychain, so internal CAs may need this).
  JIRA_LOG_DIR    Directory for debug logs
                  (default: <project>/logs/mcp/)
"""
import logging
import os
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

JIRA_URL = os.environ.get("JIRA_URL", "").rstrip("/")
JIRA_TOKEN = os.environ.get("JIRA_TOKEN")
JIRA_CA_BUNDLE = os.environ.get("JIRA_CA_BUNDLE")
LOG_DIR = os.environ.get(
    "JIRA_LOG_DIR", str(__import__("pathlib").Path(__file__).resolve().parents[2] / "logs" / "mcp")
)

MAX_RESULT = 50
PAGE_SIZE = 100
MAX_LOG_BYTES = 100 * 1024 * 1024  # 100 MB
ERROR_BODY_LIMIT = 2000  # chars of an error response body to log

# Fields requested for issue *lists* -- keep light so big JQL results stay fast.
LIST_FIELDS = "summary,status,issuetype,assignee,priority"

# Sprint custom-field values often arrive as opaque strings like
#   com.atlassian.greenhopper.service.sprint.Sprint@1a2b[name=Sprint 5,state=ACTIVE,...]
# on older Data Center instances. This pulls the human name back out.
SPRINT_NAME_RE = re.compile(r"name=([^,\]]+)")

if not JIRA_URL:
    raise RuntimeError("JIRA_URL environment variable is required")
if not JIRA_TOKEN:
    raise RuntimeError("JIRA_TOKEN environment variable is required")

VERIFY = JIRA_CA_BUNDLE if JIRA_CA_BUNDLE else True


class DatedRotatingFileHandler(RotatingFileHandler):
    """Like RotatingFileHandler, but each rollover starts a NEW file named
    with the current timestamp instead of appending a .1/.2 suffix.
    """

    def __init__(self, log_dir: str, prefix: str = "jira-mcp",
                 max_bytes: int = MAX_LOG_BYTES, encoding: str = "utf-8"):
        self.log_dir = log_dir
        self.prefix = prefix
        os.makedirs(log_dir, exist_ok=True)
        super().__init__(
            self._new_filename(), maxBytes=max_bytes, backupCount=0, encoding=encoding
        )

    def _new_filename(self) -> str:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        return os.path.join(self.log_dir, f"{self.prefix}_{ts}.log")

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = os.path.abspath(self._new_filename())
        self.stream = self._open()


_handlers: list[logging.Handler] = [logging.StreamHandler()]  # -> stderr
try:
    _handlers.append(DatedRotatingFileHandler(LOG_DIR))
except Exception as exc:
    logging.getLogger().warning("Could not set up file logging in %s: %s", LOG_DIR, exc)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("jira-mcp")


def _log_request(request: httpx.Request) -> None:
    log.info("-> %s %s", request.method, request.url)


def _log_response(response: httpx.Response) -> None:
    req = response.request
    # Ensure the body is read so .elapsed is populated (httpx requires this).
    try:
        response.read()
    except Exception:
        pass
    try:
        elapsed_ms = response.elapsed.total_seconds() * 1000
    except Exception:
        elapsed_ms = -1
    if response.status_code >= 400:
        try:
            body = response.text[:ERROR_BODY_LIMIT]
        except Exception:
            body = "<unreadable>"
        log.error("<- %s %s %s (%.0f ms)  body=%s",
                  response.status_code, req.method, req.url, elapsed_ms, body)
    else:
        log.info("<- %s %s %s (%.0f ms)",
                 response.status_code, req.method, req.url, elapsed_ms)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=JIRA_URL,
        headers={
            "Authorization": f"Bearer {JIRA_TOKEN}",
            "Accept": "application/json",
        },
        timeout=30.0,
        verify=VERIFY,
        event_hooks={"request": [_log_request], "response": [_log_response]},
    )


mcp = FastMCP("jira-onprem")


# --------------------------------------------------------------------------
# HTTP / pagination helpers
# --------------------------------------------------------------------------
def _paginate(c: httpx.Client, path: str, params: dict, items_key: str,
              max_items: int | None = None) -> list:
    """Follow Jira's startAt/maxResults/total pagination until exhausted.

    Works for both API flavours: the Platform search endpoint (items under
    "issues", bounded by "total") and the Agile endpoints (items under "values"
    or "issues", bounded by "isLast"/"total"). `items_key` names the list field.
    """
    items: list = []
    params = dict(params)
    params["maxResults"] = PAGE_SIZE
    start = 0
    while True:
        params["startAt"] = start
        r = c.get(path, params=params)
        r.raise_for_status()
        data = r.json()
        batch = data.get(items_key, []) if isinstance(data, dict) else data
        if not batch:
            break
        items.extend(batch)
        if max_items and len(items) >= max_items:
            log.info("   paginated %s -> %d items (capped at %d)", path, len(items), max_items)
            return items[:max_items]
        if not isinstance(data, dict):
            break  # unpaginated array response (e.g. /project on some versions)
        if data.get("isLast") is True:
            break
        total = data.get("total")
        if total is not None and start + len(batch) >= total:
            break
        start += len(batch)
    log.info("   paginated %s -> %d items", path, len(items))
    return items[:max_items] if max_items else items


def _agile_guard(exc: httpx.HTTPStatusError, what: str) -> str:
    """Turn a failed Agile-API call into a readable message. A 404 usually means
    the Agile API isn't enabled OR the id doesn't exist; both are worth saying
    plainly rather than surfacing a stack trace.
    """
    code = exc.response.status_code
    if code == 404:
        return (f"{what} not found. Either the id is wrong, or this instance "
                f"doesn't expose the Jira Agile API (/rest/agile/1.0/).")
    if code in (401, 403):
        return f"Not authorized to read {what} (HTTP {code}). Check the token's permissions."
    return f"Failed to read {what}: HTTP {code}."


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def _name(obj: dict | None, key: str = "name") -> str:
    return (obj or {}).get(key) or "-"


def _fmt_issue(issue: dict) -> str:
    """One-line summary of an issue, shared by every list-returning tool."""
    f = issue.get("fields", {}) or {}
    key = issue.get("key", "?")
    status = _name(f.get("status"))
    itype = _name(f.get("issuetype"))
    assignee = (f.get("assignee") or {}).get("displayName") or "Unassigned"
    priority = _name(f.get("priority"))
    summary = f.get("summary") or ""
    return f"{key}  [{status}]  {itype}  ({priority})  @{assignee}  {summary}"


def _extract_sprint_names(value) -> list[str]:
    """Sprint custom field is a list of dicts on newer instances and a list of
    opaque toString() strings on older ones. Return the sprint names either way.
    """
    names: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            nm = item.get("name")
            if nm:
                names.append(nm)
        elif isinstance(item, str):
            m = SPRINT_NAME_RE.search(item)
            names.append(m.group(1) if m else item)
    return names


# --------------------------------------------------------------------------
# Core: Platform API (/rest/api/2/)
# --------------------------------------------------------------------------
@mcp.tool()
def get_current_user() -> str:
    """Return the account the token authenticates as. Use this first to confirm
    JIRA_URL and JIRA_TOKEN are correct before running other tools.
    """
    log.info("TOOL get_current_user()")
    with _client() as c:
        r = c.get("/rest/api/2/myself")
        r.raise_for_status()
        u = r.json()
    return (f"Authenticated as {u.get('displayName')} "
            f"({u.get('name') or u.get('key')})  <{u.get('emailAddress') or 'no email'}>  "
            f"active={u.get('active')}")


@mcp.tool()
def search_issues(jql: str, max_results: int = MAX_RESULT) -> str:
    """Run a JQL query and return matching issues (one line each).

    `jql` is a standard Jira Query Language string, e.g.
      project = ABC AND status = "In Progress" ORDER BY updated DESC
    Returns key, status, type, priority, assignee, and summary. `max_results`
    caps how many issues come back (fully paginated up to that cap).
    """
    log.info("TOOL search_issues(jql=%r, max_results=%d)", jql, max_results)
    with _client() as c:
        data = _paginate(
            c, "/rest/api/2/search",
            {"jql": jql, "fields": LIST_FIELDS},
            items_key="issues", max_items=max_results,
        )
    if not data:
        return f"No issues match: {jql}"
    return f"{len(data)} issue(s):\n" + "\n".join(_fmt_issue(i) for i in data)


@mcp.tool()
def get_issue(issue_key: str) -> str:
    """Full detail for one issue: type, status, priority, assignee, reporter,
    labels, components, parent, epic link, sprint, dates, and description.

    `issue_key` is the human key such as ABC-123.
    """
    log.info("TOOL get_issue(issue_key=%r)", issue_key)
    key = quote(issue_key, safe="")
    with _client() as c:
        r = c.get(f"/rest/api/2/issue/{key}", params={"expand": "names"})
        r.raise_for_status()
        issue = r.json()

    f = issue.get("fields", {}) or {}
    names = issue.get("names", {}) or {}  # fieldId -> display name
    # Reverse-map so we can find instance-specific custom fields by their label.
    by_label = {v: k for k, v in names.items()}

    lines = [
        f"{issue.get('key')}: {f.get('summary') or ''}",
        f"  type:       {_name(f.get('issuetype'))}",
        f"  status:     {_name(f.get('status'))}",
        f"  priority:   {_name(f.get('priority'))}",
        f"  assignee:   {(f.get('assignee') or {}).get('displayName') or 'Unassigned'}",
        f"  reporter:   {(f.get('reporter') or {}).get('displayName') or '-'}",
        f"  project:    {_name(f.get('project'))} ({(f.get('project') or {}).get('key', '-')})",
    ]

    labels = f.get("labels") or []
    if labels:
        lines.append(f"  labels:     {', '.join(labels)}")
    components = [c.get("name") for c in (f.get("components") or [])]
    if components:
        lines.append(f"  components: {', '.join(components)}")

    parent = f.get("parent")
    if parent:
        lines.append(f"  parent:     {parent.get('key')}  {(parent.get('fields') or {}).get('summary', '')}")

    # Epic Link and Sprint are custom fields whose ids vary per instance; look
    # them up by display name so this works without hardcoding customfield_XXXXX.
    epic_field = by_label.get("Epic Link")
    if epic_field and f.get(epic_field):
        lines.append(f"  epic:       {f.get(epic_field)}")
    sprint_field = by_label.get("Sprint")
    if sprint_field and f.get(sprint_field):
        sprints = _extract_sprint_names(f.get(sprint_field))
        if sprints:
            lines.append(f"  sprint:     {', '.join(sprints)}")

    lines += [
        f"  created:    {f.get('created')}",
        f"  updated:    {f.get('updated')}",
    ]
    if f.get("resolution"):
        lines.append(f"  resolution: {_name(f.get('resolution'))}  ({f.get('resolutiondate')})")

    desc = f.get("description")
    if desc:
        lines += ["", "Description:", str(desc)]
    return "\n".join(lines)


@mcp.tool()
def get_issue_comments(issue_key: str, max_results: int = 100) -> str:
    """List comments on an issue, oldest first, with author and timestamp.

    `issue_key` is the human key such as ABC-123.
    """
    log.info("TOOL get_issue_comments(issue_key=%r, max_results=%d)", issue_key, max_results)
    key = quote(issue_key, safe="")
    with _client() as c:
        data = _paginate(
            c, f"/rest/api/2/issue/{key}/comment", {},
            items_key="comments", max_items=max_results,
        )
    if not data:
        return f"No comments on {issue_key}."
    out = [f"{len(data)} comment(s) on {issue_key}:"]
    for cm in data:
        author = (cm.get("author") or {}).get("displayName") or "-"
        out.append(f"\n[{cm.get('created')}] {author}:\n{cm.get('body') or ''}")
    return "\n".join(out)


@mcp.tool()
def get_issue_transitions(issue_key: str) -> str:
    """List the workflow transitions currently available on an issue -- i.e.
    which statuses it could move to. Read-only: this does NOT perform any
    transition, it only reports the options.

    `issue_key` is the human key such as ABC-123.
    """
    log.info("TOOL get_issue_transitions(issue_key=%r)", issue_key)
    key = quote(issue_key, safe="")
    with _client() as c:
        r = c.get(f"/rest/api/2/issue/{key}/transitions")
        r.raise_for_status()
        transitions = r.json().get("transitions", [])
    if not transitions:
        return f"No transitions available on {issue_key} (or you lack permission)."
    return f"Available transitions for {issue_key}:\n" + "\n".join(
        f"  id={t.get('id')}  -> {_name((t.get('to') or {}))}  (\"{t.get('name')}\")"
        for t in transitions
    )


@mcp.tool()
def list_projects() -> str:
    """List every project the token can see: key, name, and project lead."""
    log.info("TOOL list_projects()")
    with _client() as c:
        r = c.get("/rest/api/2/project")
        r.raise_for_status()
        projects = r.json()
    if not projects:
        return "No projects visible to this token."
    return f"{len(projects)} project(s):\n" + "\n".join(
        f"  {p.get('key')}  {p.get('name')}"
        + (f"  (lead: {(p.get('lead') or {}).get('displayName')})" if p.get("lead") else "")
        for p in projects
    )


@mcp.tool()
def get_project(project_key: str) -> str:
    """Detail for one project: name, key, lead, description, and its issue types.

    `project_key` is the short project key such as ABC.
    """
    log.info("TOOL get_project(project_key=%r)", project_key)
    key = quote(project_key, safe="")
    with _client() as c:
        r = c.get(f"/rest/api/2/project/{key}")
        r.raise_for_status()
        p = r.json()
    lines = [
        f"{p.get('key')}: {p.get('name')}",
        f"  lead:       {(p.get('lead') or {}).get('displayName') or '-'}",
        f"  category:   {(p.get('projectCategory') or {}).get('name') or '-'}",
    ]
    if p.get("description"):
        lines.append(f"  description: {p.get('description')}")
    types = [t.get("name") for t in (p.get("issueTypes") or [])]
    if types:
        lines.append(f"  issue types: {', '.join(types)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Agile API (/rest/agile/1.0/) -- boards, sprints, backlog, epics
# --------------------------------------------------------------------------
@mcp.tool()
def list_boards(project_key: str = "", max_results: int = 50) -> str:
    """List Agile boards, optionally filtered to one project.

    `project_key` (optional) narrows to boards for that project, e.g. ABC. Each
    row shows the board id (needed by the sprint/backlog/epic tools), its name,
    and its type (scrum/kanban).
    """
    log.info("TOOL list_boards(project_key=%r, max_results=%d)", project_key, max_results)
    params = {}
    if project_key:
        params["projectKeyOrId"] = project_key
    with _client() as c:
        try:
            data = _paginate(c, "/rest/agile/1.0/board", params,
                             items_key="values", max_items=max_results)
        except httpx.HTTPStatusError as exc:
            return _agile_guard(exc, "boards")
    if not data:
        return "No boards found" + (f" for project {project_key}." if project_key else ".")
    return f"{len(data)} board(s):\n" + "\n".join(
        f"  id={b.get('id')}  {b.get('name')}  ({b.get('type')})" for b in data
    )


@mcp.tool()
def list_sprints(board_id: int, state: str = "active", max_results: int = 50) -> str:
    """List sprints on a board. `state` is active / future / closed (or "all").

    For the CURRENT sprint, use the default state="active". `board_id` comes
    from list_boards. Each row shows the sprint id (for get_sprint_issues),
    name, state, and dates.
    """
    log.info("TOOL list_sprints(board_id=%s, state=%r, max_results=%d)", board_id, state, max_results)
    params = {}
    if state and state.lower() != "all":
        params["state"] = state.lower()
    with _client() as c:
        try:
            data = _paginate(c, f"/rest/agile/1.0/board/{board_id}/sprint", params,
                             items_key="values", max_items=max_results)
        except httpx.HTTPStatusError as exc:
            return _agile_guard(exc, f"sprints for board {board_id}")
    if not data:
        return f"No {state} sprints on board {board_id}."
    return f"{len(data)} sprint(s) on board {board_id}:\n" + "\n".join(
        f"  id={s.get('id')}  {s.get('name')}  [{s.get('state')}]  "
        f"{s.get('startDate', '')[:10]}..{s.get('endDate', '')[:10]}"
        for s in data
    )


@mcp.tool()
def get_sprint_issues(sprint_id: int, max_results: int = 100) -> str:
    """List the issues in a sprint (one line each). `sprint_id` comes from
    list_sprints.
    """
    log.info("TOOL get_sprint_issues(sprint_id=%s, max_results=%d)", sprint_id, max_results)
    with _client() as c:
        try:
            data = _paginate(c, f"/rest/agile/1.0/sprint/{sprint_id}/issue",
                             {"fields": LIST_FIELDS}, items_key="issues", max_items=max_results)
        except httpx.HTTPStatusError as exc:
            return _agile_guard(exc, f"issues for sprint {sprint_id}")
    if not data:
        return f"No issues in sprint {sprint_id}."
    return f"{len(data)} issue(s) in sprint {sprint_id}:\n" + "\n".join(_fmt_issue(i) for i in data)


@mcp.tool()
def get_board_backlog(board_id: int, max_results: int = 100) -> str:
    """List the backlog issues for a board -- issues not assigned to any active
    or future sprint. `board_id` comes from list_boards.
    """
    log.info("TOOL get_board_backlog(board_id=%s, max_results=%d)", board_id, max_results)
    with _client() as c:
        try:
            data = _paginate(c, f"/rest/agile/1.0/board/{board_id}/backlog",
                             {"fields": LIST_FIELDS}, items_key="issues", max_items=max_results)
        except httpx.HTTPStatusError as exc:
            return _agile_guard(exc, f"backlog for board {board_id}")
    if not data:
        return f"Backlog is empty for board {board_id}."
    return f"{len(data)} backlog issue(s) on board {board_id}:\n" + "\n".join(_fmt_issue(i) for i in data)


@mcp.tool()
def list_epics(board_id: int, done: bool = False, max_results: int = 50) -> str:
    """List epics on a board. By default only epics that are NOT done; pass
    done=True to include completed epics. `board_id` comes from list_boards.
    Each row shows the epic key (for get_epic_issues) and its name.
    """
    log.info("TOOL list_epics(board_id=%s, done=%s, max_results=%d)", board_id, done, max_results)
    with _client() as c:
        try:
            data = _paginate(c, f"/rest/agile/1.0/board/{board_id}/epic",
                             {"done": str(done).lower()}, items_key="values", max_items=max_results)
        except httpx.HTTPStatusError as exc:
            return _agile_guard(exc, f"epics for board {board_id}")
    if not data:
        return f"No {'' if done else 'open '}epics on board {board_id}."
    return f"{len(data)} epic(s) on board {board_id}:\n" + "\n".join(
        f"  {e.get('key')}  {e.get('name')}  "
        f"[{'done' if e.get('done') else 'open'}]" for e in data
    )


@mcp.tool()
def get_epic_issues(epic_key: str, max_results: int = 100) -> str:
    """List all issues under an epic (one line each). `epic_key` is the epic's
    human key such as ABC-42 (from list_epics or an issue's epic link).
    """
    log.info("TOOL get_epic_issues(epic_key=%r, max_results=%d)", epic_key, max_results)
    key = quote(epic_key, safe="")
    with _client() as c:
        try:
            data = _paginate(c, f"/rest/agile/1.0/epic/{key}/issue",
                             {"fields": LIST_FIELDS}, items_key="issues", max_items=max_results)
        except httpx.HTTPStatusError as exc:
            return _agile_guard(exc, f"issues for epic {epic_key}")
    if not data:
        return f"No issues under epic {epic_key}."
    return f"{len(data)} issue(s) under epic {epic_key}:\n" + "\n".join(_fmt_issue(i) for i in data)


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("jira-onprem MCP server starting")
    log.info("instance=%s  verify=%s  log_dir=%s", JIRA_URL, VERIFY, LOG_DIR)
    log.info("=" * 60)
    try:
        mcp.run()
    except Exception:
        log.exception("server crashed")
        raise
    finally:
        log.info("jira-onprem MCP server stopped")
