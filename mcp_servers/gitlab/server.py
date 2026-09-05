#!/usr/bin/env python3
"""Read-only GitLab MCP server for Claude Desktop.

Talks ONLY to the GitLab instance in GITLAB_URL, using a personal access
token (read_api scope is sufficient). No third-party MCP code in the path.

Environment variables:
  GITLAB_URL        Base URL (default: https://gitlab.ballys.tech)
  GITLAB_TOKEN      Personal access token with read_api scope  (required)
  GITLAB_CA_BUNDLE  Path to an internal CA PEM file. Set ONLY if you hit an
                    SSL verification error (httpx uses certifi, not the
                    macOS keychain, so internal CAs may need this).
  GITLAB_LOG_DIR    Directory for debug logs
                    (default: /Users/kamal.abdullayev/Desktop/mcp-server-logs/)
"""
import logging
import os
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

GITLAB_URL = os.environ.get("GITLAB_URL", "https://gitlab.ballys.tech").rstrip("/")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")
GITLAB_CA_BUNDLE = os.environ.get("GITLAB_CA_BUNDLE")
LOG_DIR = os.environ.get(
    "GITLAB_LOG_DIR", "/Users/kamal.abdullayev/Desktop/mcp-server-logs/"
)

MAX_RESULT = 500
MAX_LOG_BYTES = 100 * 1024 * 1024  # 100 MB
ERROR_BODY_LIMIT = 2000  # chars of an error response body to log
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # strip terminal color codes from job logs

if not GITLAB_TOKEN:
    raise RuntimeError("GITLAB_TOKEN environment variable is required")

API = f"{GITLAB_URL}/api/v4"
VERIFY = GITLAB_CA_BUNDLE if GITLAB_CA_BUNDLE else True


class DatedRotatingFileHandler(RotatingFileHandler):
    """Like RotatingFileHandler, but each rollover starts a NEW file named
    with the current timestamp instead of appending a .1/.2 suffix.
    """

    def __init__(self, log_dir: str, prefix: str = "gitlab-mcp",
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
log = logging.getLogger("gitlab-mcp")


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
        base_url=API,
        headers={"PRIVATE-TOKEN": GITLAB_TOKEN},
        timeout=30.0,
        verify=VERIFY,
        event_hooks={"request": [_log_request], "response": [_log_response]},
    )


mcp = FastMCP("gitlab-onprem")


def _get_all(c: httpx.Client, path: str, params: dict, max_items: int | None = None) -> list:
    """Follow GitLab's X-Next-Page pagination until exhausted or max_items hit."""
    items: list = []
    params = dict(params)
    params["per_page"] = 100
    page = 1
    while True:
        params["page"] = page
        r = c.get(path, params=params)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        items.extend(batch)
        if max_items and len(items) >= max_items:
            log.info("   paginated %s -> %d items (capped at %d)", path, len(items), max_items)
            return items[:max_items]
        nxt = r.headers.get("x-next-page")
        if not nxt:
            break
        page = int(nxt)
    log.info("   paginated %s -> %d items", path, len(items))
    return items


def _default_branch(c: httpx.Client, pid: str) -> str:
    r = c.get(f"/projects/{pid}")
    r.raise_for_status()
    return r.json().get("default_branch") or "main"


@mcp.tool()
def list_all_projects(membership: bool = True, search: str = "", max_results: int = MAX_RESULT) -> str:
    """Enumerate projects on the instance.

    `membership=True` (default) lists projects you belong to. Set it to False
    to list ALL projects your token can see -- on a large corporate instance
    this can be huge, so `max_results` caps it. Optional `search` narrows by
    name or path. Returns each project's numeric id and full path.
    """
    log.info("TOOL list_all_projects(membership=%s, search=%r, max_results=%d)",
             membership, search, max_results)
    params = {"simple": "true", "order_by": "last_activity_at", "archived": "false"}
    if membership:
        params["membership"] = "true"
    if search:
        params["search"] = search
    with _client() as c:
        data = _get_all(c, "/projects", params, max_items=max_results)
    if not data:
        return "No projects found."
    header = f"{len(data)} project(s):\n"
    return header + "\n".join(f"id={p['id']}  {p['path_with_namespace']}" for p in data)


@mcp.tool()
def search_projects(query: str, limit: int = 20) -> str:
    """Search projects by name or path (quick lookup). For full enumeration
    use list_all_projects. Returns numeric id, full path, and description.
    """
    log.info("TOOL search_projects(query=%r, limit=%d)", query, limit)
    with _client() as c:
        r = c.get(
            "/projects",
            params={"search": query, "per_page": limit, "order_by": "last_activity_at"},
        )
        r.raise_for_status()
        data = r.json()
    if not data:
        return f"No projects found for '{query}'."
    return "\n".join(
        f"id={p['id']}  {p['path_with_namespace']}  -  {p.get('description') or 'no description'}"
        for p in data
    )


@mcp.tool()
def list_repository_tree(
    project: str, path: str = "", ref: str = "", recursive: bool = False, max_results: int = 1000
) -> str:
    """List files and folders in a repository path (fully paginated).

    `project` is the numeric id or full path (e.g. accounts/account-security-service-v2).
    `ref` defaults to the project's default branch. Set `recursive=True` to
    walk the whole tree; `max_results` caps very large trees.
    """
    log.info("TOOL list_repository_tree(project=%r, path=%r, ref=%r, recursive=%s, max_results=%d)",
             project, path, ref, recursive, max_results)
    pid = quote(project, safe="")
    with _client() as c:
        if not ref:
            ref = _default_branch(c, pid)
        items = _get_all(
            c,
            f"/projects/{pid}/repository/tree",
            {"path": path, "ref": ref, "recursive": recursive},
            max_items=max_results,
        )
    if not items:
        return f"Nothing found at '{path or '/'}' on ref '{ref}'."
    return "\n".join(
        f"[{'dir ' if i['type'] == 'tree' else 'file'}] {i['path']}" for i in items
    )


@mcp.tool()
def get_file_contents(project: str, file_path: str, ref: str = "") -> str:
    """Read a single file's contents from a repository.

    `project` is the numeric id or full path; `file_path` is the path inside
    the repo; `ref` defaults to the project's default branch.
    """
    log.info("TOOL get_file_contents(project=%r, file_path=%r, ref=%r)", project, file_path, ref)
    pid = quote(project, safe="")
    fpath = quote(file_path, safe="")
    with _client() as c:
        if not ref:
            ref = _default_branch(c, pid)
        r = c.get(f"/projects/{pid}/repository/files/{fpath}/raw", params={"ref": ref})
        r.raise_for_status()
        log.info("   read %s (%d bytes)", file_path, len(r.text))
        return r.text


@mcp.tool()
def search_in_project(project: str, query: str, ref: str = "", max_results: int = 50) -> str:
    """Search file contents (code) within a single project.

    Returns matching file paths, line numbers, and a snippet of the matching
    line. `ref` defaults to the default branch. Uses GitLab basic search, so
    it works WITHOUT Elasticsearch.
    """
    log.info("TOOL search_in_project(project=%r, query=%r, ref=%r, max_results=%d)",
             project, query, ref, max_results)
    pid = quote(project, safe="")
    params = {"scope": "blobs", "search": query}
    if ref:
        params["ref"] = ref
    with _client() as c:
        data = _get_all(c, f"/projects/{pid}/search", params, max_items=max_results)
    if not data:
        return f"No matches for '{query}' in {project}."
    out = []
    for hit in data:
        loc = hit.get("path") or hit.get("filename")
        line = hit.get("startline")
        snippet = (hit.get("data") or "").strip().splitlines()
        first = snippet[0].strip() if snippet else ""
        out.append(f"{loc}:{line}  {first}")
    return "\n".join(out)


@mcp.tool()
def search_code_global(query: str, max_results: int = 50) -> str:
    """Search code across ALL projects on the instance (scope=blobs).

    Requires GitLab Advanced Search (Elasticsearch) to be enabled on the
    instance. If it is NOT enabled, this may return an error or nothing -- in
    that case enumerate with list_all_projects and run search_in_project per
    repository instead.
    """
    log.info("TOOL search_code_global(query=%r, max_results=%d)", query, max_results)
    params = {"scope": "blobs", "search": query}
    with _client() as c:
        data = _get_all(c, "/search", params, max_items=max_results)
    if not data:
        return f"No global matches for '{query}' (or Advanced Search is not enabled)."
    out = []
    for hit in data:
        proj = hit.get("project_id")
        loc = hit.get("path") or hit.get("filename")
        line = hit.get("startline")
        snippet = (hit.get("data") or "").strip().splitlines()
        first = snippet[0].strip() if snippet else ""
        out.append(f"project={proj}  {loc}:{line}  {first}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# CI / CD: pipelines and jobs (read-only)
# --------------------------------------------------------------------------
@mcp.tool()
def list_pipelines(project: str, ref: str = "", status: str = "", max_results: int = 20) -> str:
    """List recent CI pipelines for a project, newest first.

    `project` is the numeric id or full path (e.g. excite/applications/session-proxy).
    Optional `ref` (branch/tag) and `status` (running/success/failed/canceled/
    pending/manual) narrow the list.
    """
    log.info("TOOL list_pipelines(project=%r, ref=%r, status=%r, max_results=%d)",
             project, ref, status, max_results)
    pid = quote(project, safe="")
    params = {"order_by": "id", "sort": "desc"}
    if ref:
        params["ref"] = ref
    if status:
        params["status"] = status
    with _client() as c:
        data = _get_all(c, f"/projects/{pid}/pipelines", params, max_items=max_results)
    if not data:
        return f"No pipelines found for {project}."
    return "\n".join(
        f"pipeline={p['id']}  {p.get('status'):9}  ref={p.get('ref')}  "
        f"sha={(p.get('sha') or '')[:8]}  {p.get('created_at')}"
        for p in data
    )


@mcp.tool()
def list_pipeline_jobs(project: str, pipeline_id: int, max_results: int = 100) -> str:
    """List the jobs in a specific pipeline, with their stage and status.

    `project` is the numeric id or full path; `pipeline_id` is a pipeline's id
    (see list_pipelines).
    """
    log.info("TOOL list_pipeline_jobs(project=%r, pipeline_id=%s, max_results=%d)",
             project, pipeline_id, max_results)
    pid = quote(project, safe="")
    with _client() as c:
        data = _get_all(c, f"/projects/{pid}/pipelines/{pipeline_id}/jobs", {}, max_items=max_results)
    if not data:
        return f"No jobs found for pipeline {pipeline_id} in {project}."
    return "\n".join(
        f"job={j['id']}  {j.get('status'):9}  stage={j.get('stage')}  {j.get('name')}"
        for j in data
    )


@mcp.tool()
def list_project_jobs(project: str, scope: str = "", max_results: int = 20) -> str:
    """List recent jobs across a project's pipelines, newest first.

    Optional `scope` filters by state: created/pending/running/failed/success/
    canceled/skipped/manual. Handy for e.g. "show the last failed jobs".
    """
    log.info("TOOL list_project_jobs(project=%r, scope=%r, max_results=%d)",
             project, scope, max_results)
    pid = quote(project, safe="")
    params = {}
    if scope:
        params["scope[]"] = scope
    with _client() as c:
        data = _get_all(c, f"/projects/{pid}/jobs", params, max_items=max_results)
    if not data:
        return f"No jobs found for {project}" + (f" with scope '{scope}'." if scope else ".")
    return "\n".join(
        f"job={j['id']}  {j.get('status'):9}  stage={j.get('stage')}  {j.get('name')}  "
        f"(pipeline {j.get('pipeline', {}).get('id')}, ref {j.get('ref')})"
        for j in data
    )


@mcp.tool()
def get_job(project: str, job_id: int) -> str:
    """Get details of a single CI job: status, stage, timing, runner, commit,
    and failure reason if any.

    `project` is the numeric id or full path (e.g. excite/applications/session-proxy);
    `job_id` is the number from a job URL (.../-/jobs/<job_id>).
    """
    log.info("TOOL get_job(project=%r, job_id=%s)", project, job_id)
    pid = quote(project, safe="")
    with _client() as c:
        r = c.get(f"/projects/{pid}/jobs/{job_id}")
        r.raise_for_status()
        j = r.json()
    commit = j.get("commit") or {}
    pipeline = j.get("pipeline") or {}
    runner = j.get("runner") or {}
    user = j.get("user") or {}
    lines = [
        f"Job {j.get('id')}: {j.get('name')}",
        f"  status:    {j.get('status')}" + (f"  (reason: {j['failure_reason']})"
                                              if j.get("failure_reason") else ""),
        f"  stage:     {j.get('stage')}",
        f"  ref:       {j.get('ref')}",
        f"  pipeline:  {pipeline.get('id')}  ({pipeline.get('status')})",
        f"  commit:    {(commit.get('short_id') or '')}  {commit.get('title') or ''}",
        f"  duration:  {j.get('duration')} s   queued: {j.get('queued_duration')} s",
        f"  created:   {j.get('created_at')}",
        f"  started:   {j.get('started_at')}",
        f"  finished:  {j.get('finished_at')}",
        f"  runner:    {runner.get('description') if runner else '-'}",
        f"  triggered: {user.get('username') if user else '-'}",
        f"  web_url:   {j.get('web_url')}",
    ]
    tags = j.get("tag_list")
    if tags:
        lines.append(f"  tags:      {', '.join(tags)}")
    return "\n".join(lines)


@mcp.tool()
def get_job_log(project: str, job_id: int, tail_lines: int = 200) -> str:
    """Read a CI job's log (trace). Terminal color codes are stripped.

    `project` is the numeric id or full path; `job_id` from the job URL. By
    default returns the LAST `tail_lines` lines (failures are usually at the
    end); pass tail_lines=0 for the entire log (can be very large).
    """
    log.info("TOOL get_job_log(project=%r, job_id=%s, tail_lines=%d)", project, job_id, tail_lines)
    pid = quote(project, safe="")
    with _client() as c:
        r = c.get(f"/projects/{pid}/jobs/{job_id}/trace")
        r.raise_for_status()
        text = r.text
    if not text.strip():
        return f"Job {job_id} has no log output (not started, or trace unavailable)."
    lines = text.splitlines()
    if tail_lines and tail_lines > 0 and len(lines) > tail_lines:
        shown = lines[-tail_lines:]
        prefix = f"(showing last {tail_lines} of {len(lines)} lines)\n"
    else:
        shown = lines
        prefix = f"({len(lines)} lines)\n"
    return prefix + "\n".join(ANSI.sub("", ln) for ln in shown)


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("gitlab-onprem MCP server starting")
    log.info("instance=%s  verify=%s  log_dir=%s", GITLAB_URL, VERIFY, LOG_DIR)
    log.info("=" * 60)
    try:
        mcp.run()
    except Exception:
        log.exception("server crashed")
        raise
    finally:
        log.info("gitlab-onprem MCP server stopped")