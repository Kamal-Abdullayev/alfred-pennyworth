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

GITLAB_URL = os.environ.get("GITLAB_URL", "https://gitlab.ballys.tech").rstrip("/")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")
GITLAB_CA_BUNDLE = os.environ.get("GITLAB_CA_BUNDLE")
LOG_DIR = os.environ.get(
    "GITLAB_LOG_DIR", str(__import__("pathlib").Path(__file__).resolve().parents[2] / "logs" / "mcp")
)

MAX_RESULT = 500
MAX_LOG_BYTES = 100 * 1024 * 1024  # 100 MB
ERROR_BODY_LIMIT = 2000  # chars of an error response body to log
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024  # refuse to inline an artifact bigger than this
MAX_DIFF_BYTES = 512 * 1024  # cap on total MR diff text returned in one call
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


def _fmt_pipeline(p: dict) -> str:
    user = p.get("user") or {}
    lines = [
        f"Pipeline {p.get('id')}: {p.get('status')}"
        + (f"  ({(p.get('detailed_status') or {}).get('text')})"
           if p.get("detailed_status") else ""),
        f"  ref:       {p.get('ref')}",
        f"  sha:       {(p.get('sha') or '')[:8]}",
        f"  source:    {p.get('source')}",
        f"  duration:  {p.get('duration')} s   queued: {p.get('queued_duration')} s",
        f"  created:   {p.get('created_at')}",
        f"  started:   {p.get('started_at')}",
        f"  finished:  {p.get('finished_at')}",
        f"  triggered: {user.get('username') if user else '-'}",
        f"  web_url:   {p.get('web_url')}",
    ]
    if p.get("coverage"):
        lines.append(f"  coverage:  {p['coverage']}")
    if p.get("yaml_errors"):
        lines.append(f"  yaml_errors: {p['yaml_errors']}")
    return "\n".join(lines)


@mcp.tool()
def get_pipeline(project: str, pipeline_id: int) -> str:
    """Get details of a single pipeline: status, ref, source, timing, coverage,
    who triggered it, and any .gitlab-ci.yml errors.

    `project` is the numeric id or full path; `pipeline_id` is the number from
    a pipeline URL (.../-/pipelines/<pipeline_id>).
    """
    log.info("TOOL get_pipeline(project=%r, pipeline_id=%s)", project, pipeline_id)
    pid = quote(project, safe="")
    with _client() as c:
        r = c.get(f"/projects/{pid}/pipelines/{pipeline_id}")
        r.raise_for_status()
        return _fmt_pipeline(r.json())


@mcp.tool()
def get_latest_pipeline(project: str, ref: str = "") -> str:
    """Get the most recent pipeline for a ref -- the quick "is this branch
    green?" check.

    `project` is the numeric id or full path; `ref` defaults to the project's
    default branch.
    """
    log.info("TOOL get_latest_pipeline(project=%r, ref=%r)", project, ref)
    pid = quote(project, safe="")
    with _client() as c:
        if not ref:
            ref = _default_branch(c, pid)
        r = c.get(f"/projects/{pid}/pipelines/latest", params={"ref": ref})
        if r.status_code in (403, 404):
            # GitLab authorizes :read_pipeline against the record it looked up,
            # so when the ref has NO pipeline it authorizes against nil and
            # answers 403 -- indistinguishable from a real permission denial.
            # Probe the list endpoint to tell the two apart.
            probe = c.get(f"/projects/{pid}/pipelines", params={"ref": ref, "per_page": 1})
            if probe.status_code != 200:
                return (f"Cannot read pipelines for {project} (HTTP {r.status_code}). Your "
                        f"token likely lacks read access to this project's CI/CD.")
            items = probe.json()
            if not items:
                return f"No pipeline found for ref '{ref}' in {project}."
            detail = c.get(f"/projects/{pid}/pipelines/{items[0]['id']}")
            detail.raise_for_status()
            return _fmt_pipeline(detail.json())
        r.raise_for_status()
        return _fmt_pipeline(r.json())


@mcp.tool()
def get_pipeline_test_report(
    project: str, pipeline_id: int, max_failures: int = 20, include_output: bool = True
) -> str:
    """Get a pipeline's JUnit test report: pass/fail counts plus the failing
    test cases. Faster than grepping job logs when tests are the failure.

    Only works if the pipeline's jobs publish `artifacts:reports:junit`;
    otherwise the report comes back empty. `max_failures` caps how many failing
    cases are listed; `include_output` adds each failure's stack trace or
    captured output (trimmed).
    """
    log.info("TOOL get_pipeline_test_report(project=%r, pipeline_id=%s, max_failures=%d, include_output=%s)",
             project, pipeline_id, max_failures, include_output)
    pid = quote(project, safe="")
    with _client() as c:
        r = c.get(f"/projects/{pid}/pipelines/{pipeline_id}/test_report")
        if r.status_code == 404:
            return (f"No test report for pipeline {pipeline_id} -- the pipeline's jobs "
                    f"probably do not publish artifacts:reports:junit.")
        r.raise_for_status()
        rep = r.json()

    total = rep.get("total_count") or 0
    if not total and not rep.get("test_suites"):
        return (f"Pipeline {pipeline_id} has an empty test report (no junit artifacts "
                f"were collected).")

    out = [
        f"Pipeline {pipeline_id} test report: {total} test(s), "
        f"{rep.get('success_count', 0)} passed, {rep.get('failed_count', 0)} failed, "
        f"{rep.get('error_count', 0)} error, {rep.get('skipped_count', 0)} skipped "
        f"({rep.get('total_time')} s)"
    ]

    for suite in rep.get("test_suites") or []:
        out.append(
            f"\nsuite: {suite.get('name')}  "
            f"({suite.get('failed_count', 0)} failed / {suite.get('total_count', 0)})"
        )

    bad = [
        tc
        for suite in (rep.get("test_suites") or [])
        for tc in (suite.get("test_cases") or [])
        if tc.get("status") in ("failed", "error")
    ]
    if not bad:
        out.append("\nNo failing test cases.")
        return "\n".join(out)

    out.append(f"\nFailing test cases ({len(bad)} total, showing up to {max_failures}):")
    for tc in bad[:max_failures]:
        out.append(
            f"\n  [{tc.get('status')}] {tc.get('classname')}::{tc.get('name')}  "
            f"({tc.get('execution_time')} s)"
        )
        if include_output:
            detail = tc.get("stack_trace") or tc.get("system_output") or ""
            for ln in ANSI.sub("", detail).strip().splitlines()[:25]:
                out.append(f"      {ln}")
    if len(bad) > max_failures:
        out.append(f"\n... {len(bad) - max_failures} more failure(s) not shown.")
    return "\n".join(out)


@mcp.tool()
def list_pipeline_bridges(project: str, pipeline_id: int, max_results: int = 100) -> str:
    """List a pipeline's bridge jobs -- the trigger jobs that start child or
    downstream (multi-project) pipelines, with each downstream pipeline's id,
    status and project.

    Use this when a pipeline looks green/red but the real work happens in a
    triggered pipeline. Feed the downstream id back into get_pipeline or
    list_pipeline_jobs (using the downstream project).
    """
    log.info("TOOL list_pipeline_bridges(project=%r, pipeline_id=%s, max_results=%d)",
             project, pipeline_id, max_results)
    pid = quote(project, safe="")
    with _client() as c:
        data = _get_all(
            c, f"/projects/{pid}/pipelines/{pipeline_id}/bridges", {}, max_items=max_results
        )
    if not data:
        return f"Pipeline {pipeline_id} in {project} has no bridge (trigger) jobs."
    out = []
    for b in data:
        down = b.get("downstream_pipeline") or {}
        line = (f"bridge={b.get('id')}  {b.get('status'):9}  stage={b.get('stage')}  "
                f"{b.get('name')}")
        if down:
            line += (f"\n    -> downstream pipeline={down.get('id')}  "
                     f"{down.get('status')}  ref={down.get('ref')}  "
                     f"project_id={down.get('project_id')}  {down.get('web_url')}")
        else:
            line += "\n    -> no downstream pipeline created"
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# CI / CD: job artifacts (read-only)
# --------------------------------------------------------------------------
@mcp.tool()
def list_job_artifacts(project: str, job_id: int) -> str:
    """List the artifact bundles a job produced (filename, type, format, size)
    and when they expire.

    GitLab has no API to list the files INSIDE an artifact archive, so this
    shows the bundles only. To read one file you must know its path inside the
    archive (usually from .gitlab-ci.yml) and use get_job_artifact_file.
    """
    log.info("TOOL list_job_artifacts(project=%r, job_id=%s)", project, job_id)
    pid = quote(project, safe="")
    with _client() as c:
        r = c.get(f"/projects/{pid}/jobs/{job_id}")
        r.raise_for_status()
        j = r.json()
    arts = j.get("artifacts") or []
    # GitLab lists the job's own trace among "artifacts". It is not fetchable
    # through the artifacts path, so keep it out of the readable list.
    real = [a for a in arts if a.get("file_type") != "trace"]
    has_trace = any(a.get("file_type") == "trace" for a in arts)

    if not real:
        msg = f"Job {job_id} ({j.get('name')}) produced no downloadable artifacts."
        if has_trace:
            msg += " It has a job log only -- read it with get_job_log."
        return msg

    out = [f"Job {job_id} ({j.get('name')}) artifacts:"]
    for a in real:
        size = a.get("size")
        size_txt = f"{size / 1024:.1f} KiB" if isinstance(size, int) else str(size)
        out.append(
            f"  {a.get('filename')}  type={a.get('file_type')}  "
            f"format={a.get('file_format')}  size={size_txt}"
        )
    if j.get("artifacts_expire_at"):
        out.append(f"  expires: {j['artifacts_expire_at']}")
    if has_trace:
        out.append("  (job log also available via get_job_log)")
    out.append("\nRead a file with get_job_artifact_file(project, job_id, artifact_path).")
    return "\n".join(out)


@mcp.tool()
def get_job_artifact_file(
    project: str, job_id: int, artifact_path: str, max_bytes: int = MAX_ARTIFACT_BYTES
) -> str:
    """Read a single file out of a job's artifact archive as text.

    `artifact_path` is the path INSIDE the archive, e.g.
    `build/reports/tests/index.html` or `target/surefire-reports/summary.txt`.
    Binary files are rejected, and anything larger than `max_bytes`
    (default 2 MiB) is truncated rather than dumped whole.
    """
    log.info("TOOL get_job_artifact_file(project=%r, job_id=%s, artifact_path=%r, max_bytes=%d)",
             project, job_id, artifact_path, max_bytes)
    pid = quote(project, safe="")
    apath = quote(artifact_path.lstrip("/"), safe="/")
    with _client() as c:
        r = c.get(f"/projects/{pid}/jobs/{job_id}/artifacts/{apath}")
        if r.status_code == 404:
            return (f"'{artifact_path}' not found in job {job_id}'s artifacts (wrong path, "
                    f"or the artifacts have expired). Check list_job_artifacts first.")
        r.raise_for_status()
        raw = r.content

    if b"\x00" in raw[:8192]:
        return (f"'{artifact_path}' looks binary ({len(raw)} bytes) -- not inlining it. "
                f"Download it from {GITLAB_URL} instead.")
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    prefix = (f"({len(raw)} bytes, truncated to {max_bytes})\n" if truncated
              else f"({len(raw)} bytes)\n")
    return prefix + text


# --------------------------------------------------------------------------
# Merge requests (read-only)
# --------------------------------------------------------------------------
@mcp.tool()
def list_merge_requests(project: str, state: str = "opened", source_branch: str = "",
                        target_branch: str = "", author: str = "",
                        max_results: int = 20) -> str:
    """List merge requests for a project, most recently updated first.

    `project` is the numeric id or full path (e.g. excite/applications/session-proxy).
    `state` is opened/closed/merged/locked/all (default opened). Optional
    `source_branch`, `target_branch` and `author` (username) narrow the list.
    The `mr=!N` number is the per-project iid used by the other MR tools.
    """
    log.info("TOOL list_merge_requests(project=%r, state=%r, source_branch=%r, "
             "target_branch=%r, author=%r, max_results=%d)",
             project, state, source_branch, target_branch, author, max_results)
    pid = quote(project, safe="")
    params = {"order_by": "updated_at", "sort": "desc"}
    if state:
        params["state"] = state
    if source_branch:
        params["source_branch"] = source_branch
    if target_branch:
        params["target_branch"] = target_branch
    if author:
        params["author_username"] = author
    with _client() as c:
        data = _get_all(c, f"/projects/{pid}/merge_requests", params, max_items=max_results)
    if not data:
        return f"No merge requests found for {project} (state={state or 'any'})."
    return "\n".join(
        f"mr=!{m['iid']}  {(m.get('state') or '?'):8}  "
        f"{m.get('source_branch')} -> {m.get('target_branch')}  "
        f"author={(m.get('author') or {}).get('username')}  {m.get('title')}"
        for m in data
    )


def _fmt_merge_request(m: dict) -> str:
    author = m.get("author") or {}
    pipeline = m.get("head_pipeline") or {}
    reviewers = ", ".join(
        (r or {}).get("username", "?") for r in (m.get("reviewers") or [])
    ) or "-"
    lines = [
        f"MR !{m.get('iid')}: {m.get('title')}",
        f"  state:     {m.get('state')}" + ("  (draft)" if m.get("draft") else ""),
        f"  branches:  {m.get('source_branch')} -> {m.get('target_branch')}",
        f"  sha:       {(m.get('sha') or '')[:8]}",
        f"  author:    {author.get('username')}",
        f"  reviewers: {reviewers}",
        f"  merge:     {m.get('detailed_merge_status') or m.get('merge_status')}",
        f"  created:   {m.get('created_at')}",
        f"  updated:   {m.get('updated_at')}",
        f"  web_url:   {m.get('web_url')}",
    ]
    if pipeline:
        lines.append(f"  pipeline:  {pipeline.get('id')} {pipeline.get('status')}")
    if m.get("description"):
        lines.append("  description:")
        lines.extend(f"    {ln}" for ln in m["description"].splitlines())
    return "\n".join(lines)


@mcp.tool()
def get_merge_request(project: str, mr_iid: int) -> str:
    """Get one merge request's details: state, branches, author, reviewers,
    merge status, head pipeline and description.

    `mr_iid` is the per-project !number shown in the GitLab UI, not the
    global id. See list_merge_requests.
    """
    log.info("TOOL get_merge_request(project=%r, mr_iid=%s)", project, mr_iid)
    pid = quote(project, safe="")
    with _client() as c:
        r = c.get(f"/projects/{pid}/merge_requests/{mr_iid}")
        r.raise_for_status()
        return _fmt_merge_request(r.json())


@mcp.tool()
def get_merge_request_changes(project: str, mr_iid: int,
                              max_bytes: int = MAX_DIFF_BYTES) -> str:
    """Get the unified diff of a merge request, file by file.

    Stops after `max_bytes` of diff text and reports how many files were
    omitted, so a large MR cannot exhaust the context window. Call
    get_merge_request first for the summary.
    """
    log.info("TOOL get_merge_request_changes(project=%r, mr_iid=%s, max_bytes=%d)",
             project, mr_iid, max_bytes)
    pid = quote(project, safe="")
    with _client() as c:
        r = c.get(f"/projects/{pid}/merge_requests/{mr_iid}/changes")
        r.raise_for_status()
        payload = r.json()
    changes = payload.get("changes") or []
    if not changes:
        return f"MR !{mr_iid} in {project} has no file changes."
    out: list[str] = []
    used = 0
    omitted = 0
    for ch in changes:
        diff = ch.get("diff") or ""
        if used + len(diff) > max_bytes:
            omitted += 1
            continue
        used += len(diff)
        flags = ""
        if ch.get("new_file"):
            flags = "  (new file)"
        elif ch.get("deleted_file"):
            flags = "  (deleted)"
        elif ch.get("renamed_file"):
            flags = "  (renamed)"
        out.append(f"--- {ch.get('old_path')}\n+++ {ch.get('new_path')}{flags}\n{diff}")
    if omitted:
        out.append(f"[{omitted} more file(s) omitted: diff exceeded {max_bytes} bytes]")
    return "\n".join(out)


@mcp.tool()
def get_merge_request_discussions(project: str, mr_iid: int,
                                  unresolved_only: bool = False,
                                  max_results: int = 100) -> str:
    """Get review discussions on a merge request: inline code comments with
    their file and line, plus general comments.

    Set `unresolved_only` to skip resolved threads. GitLab's system notes
    (label changes, assignments) are always filtered out.
    """
    log.info("TOOL get_merge_request_discussions(project=%r, mr_iid=%s, "
             "unresolved_only=%s, max_results=%d)",
             project, mr_iid, unresolved_only, max_results)
    pid = quote(project, safe="")
    with _client() as c:
        data = _get_all(c, f"/projects/{pid}/merge_requests/{mr_iid}/discussions",
                        {}, max_items=max_results)
    out: list[str] = []
    for d in data:
        notes = [n for n in (d.get("notes") or []) if not n.get("system")]
        if not notes:
            continue
        first = notes[0]
        resolved = bool(first.get("resolved"))
        if unresolved_only and resolved:
            continue
        pos = first.get("position") or {}
        if pos:
            loc = (f"{pos.get('new_path') or pos.get('old_path')}:"
                   f"{pos.get('new_line') or pos.get('old_line')}")
        else:
            loc = "(general)"
        out.append(f"discussion={d.get('id')}  {loc}  "
                   f"{'resolved' if resolved else 'UNRESOLVED'}")
        for n in notes:
            author = (n.get("author") or {}).get("username")
            body = " ".join((n.get("body") or "").split())
            out.append(f"    {author}: {body}")
    if not out:
        return (f"No {'unresolved ' if unresolved_only else ''}review comments "
                f"on MR !{mr_iid} in {project}.")
    return "\n".join(out)


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