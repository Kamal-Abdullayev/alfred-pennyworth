# contracts.py — typed handoff contracts.
#
# Every agent finishes by producing structured output that must validate against
# its role's contract (the SDK enforces the JSON schema, and we re-validate with
# Pydantic). Code moves as commits; only verdicts, plans and findings move as
# data. No field is Optional-with-default on purpose: structured outputs want
# every property present — use `| None` for "may be empty".
from typing import Literal

from pydantic import BaseModel, Field


class Subtask(BaseModel):
    id: str = Field(description="Short stable id like 'st1'")
    title: str
    description: str = Field(description="Self-contained instructions for one developer")
    acceptance: list[str] = Field(description="Numbered, testable acceptance criteria")
    depends_on: list[str] = Field(description="ids of subtasks that must finish first; [] if none")
    role: str | None = Field(description="Role that should do this subtask (must be an existing agent role, e.g. developer or a specialist); null = developer")


class Plan(BaseModel):
    summary: str
    subtasks: list[Subtask]
    parallelism: int = Field(description="How many subtasks may run at once; 1 if unsure")
    repo_path: str | None = Field(description="Absolute path of the LOCAL git checkout developers must work in when the "
                                              "Repository state block says NONE but you know it — a path the human wrote, "
                                              "or a checkout you read with Read. null when a repository was attached or none exists locally.")


class Verification(BaseModel):
    commands_run: list[str]
    result: Literal["pass", "fail", "not_run"]
    notes: str


class Blocked(BaseModel):
    reason: str
    conflict_detail: str
    needs: list[str] = Field(description="What would unblock you")


class Implement(BaseModel):
    status: Literal["done", "blocked"]
    summary: str = Field(description="One paragraph: what you did, or why you are blocked")
    branch: str | None = Field(description="Branch you worked on; null if blocked")
    committed: bool = Field(description="true if you committed. false when the human asked for NO commits (task text or PROJECT MEMORY): changes are left uncommitted in the working tree")
    commit_sha: str | None = Field(description="Full sha of YOUR commit; never invent one; null if blocked or not committed")
    files_changed: list[str]
    approach: str | None
    verification: Verification
    open_questions: list[str]
    blocked: Blocked | None = Field(description="Filled only when status is blocked")


class Finding(BaseModel):
    file: str
    line: int | None
    severity: Literal["critical", "important", "minor"]
    claim: str = Field(description="What is wrong, one sentence")
    evidence: str = Field(description="The command you ran and what it showed, or the code you read")


class TestRun(BaseModel):
    name: str
    status: Literal["pass", "fail", "skipped"]
    output_excerpt: str


class Review(BaseModel):
    verdict: Literal["pass", "fail"]
    findings: list[Finding]
    tests_run: list[TestRun]
    edge_cases_probed: list[str]


class Citation(BaseModel):
    source: str = Field(description="Where this came from: file path, Jira key, Confluence page, MR, config key")
    ref: str = Field(description="Precise locator: line range, section, issue key")
    url: str | None


class CodeRef(BaseModel):
    """A piece of code the answer talks about. Copied verbatim so the reader sees the real thing."""
    path: str = Field(description="Path relative to the repository root, e.g. service/src/main/java/.../MfaType.java — never prefixed with the project name")
    start_line: int = Field(description="First line of the snippet in the file (1-based)")
    end_line: int = Field(description="Last line of the snippet in the file")
    symbol: str | None = Field(description="Method/class/field the snippet shows, e.g. OptionalMfaPolicy.challengeRequiredForMode")
    language: str = Field(description="Language for syntax display: java, python, yaml, sql, ...")
    snippet: str = Field(description="The exact code, copied from the file — never paraphrased or reformatted")
    why: str = Field(description="One sentence: what this snippet proves or shows for the answer")


class Source(BaseModel):
    """What exactly was read. Two clones of one repo can differ by weeks — always say which."""
    repo_path: str | None = Field(description="Absolute path of the LOCAL checkout that was read; null if the code was read through GitLab tools")
    gitlab_project: str | None = Field(description="GitLab project path (e.g. excite/applications/session-proxy) when the code was read through GitLab tools; null if read locally")
    branch: str | None = Field(description="Branch that was read (from the repository state block, or the MR's source branch)")
    commit: str | None = Field(description="Commit sha that was read, when known")


class Diagram(BaseModel):
    title: str
    description: str = Field(description="One sentence: what the diagram shows and why it helps")
    mermaid: str = Field(description="Mermaid source (flowchart, sequenceDiagram, classDiagram, stateDiagram, erDiagram). Rendered in the UI.")


class Answer(BaseModel):
    answer: str = Field(description="Markdown. Lead with the direct answer in one or two sentences, then detail. Tables for enumerations. Caveats last under their own heading.")
    code: list[CodeRef] = Field(description="Code the reader should see, in the order it is referenced. Empty if the answer is not about code.")
    diagrams: list[Diagram] = Field(description="Diagrams that make the answer clearer — flows, sequences, architecture, state machines. Empty when a picture adds nothing.")
    source: Source
    citations: list[Citation]
    confidence: Literal["low", "medium", "high"]


class MemoryProposal(BaseModel):
    """A candidate entry for the shared project memory. The human accepts or rejects it in the UI."""
    kind: Literal["decision", "fact", "convention", "glossary", "person", "question", "todo"]
    title: str = Field(description="One line, specific: 'OTP completion goes through session-proxy, not ASS directly'")
    body: str = Field(description="Self-contained: what, why, who decided, when — enough for an agent that has no other context")
    tags: list[str] = Field(description="Lowercase keywords for search, e.g. ['2fa', 'session-proxy']; empty is fine")


class LeadOutput(BaseModel):
    """The team lead decides what a request is. A question gets an answer; work gets a plan."""
    kind: Literal["plan", "answer"]
    plan: Plan | None = Field(description="Filled when kind is plan")
    answer: Answer | None = Field(description="Filled when kind is answer")
    memory_proposals: list[MemoryProposal] = Field(
        description="Decisions, constraints or facts the HUMAN stated in this request that are not derivable from the code "
                    "and are not already in PROJECT MEMORY. Usually empty. Never restate what the code says.")


class BriefItem(BaseModel):
    title: str = Field(description="One line, specific, in plain words")
    detail: str = Field(description="One to three sentences: what, who said it, where (Teams thread, email, ticket, invite) and why it matters today")
    url: str | None = Field(description="Deep link when there is one: ticket, invite, message, MR")


class BriefMeeting(BaseModel):
    start: str = Field(description="HH:MM local")
    end: str = Field(description="HH:MM local")
    title: str
    where: str | None = Field(description="Room, 'Teams', or a join link")
    note: str | None = Field(description="Organizer, who else, whether you are optional — or null")


class BriefTicket(BaseModel):
    key: str
    title: str
    status: str
    note: str | None = Field(description="Why it matters today, or null")
    url: str | None


class BriefEmail(BaseModel):
    sender: str
    subject: str
    when: str = Field(description="e.g. 'today 07:15' or 'Sep 3, 19:20'")
    count: int = Field(description="Copies of the same alert/thread folded into this line; 1 if a single mail")


class Brief(BaseModel):
    """A personal daily brief for one software engineer — written from their calendar, Teams, email and Jira."""
    date: str = Field(description="e.g. 'Wednesday · September 9 2026'")
    headline: str = Field(description="One warm, specific sentence addressed to the person by first name, summarising the shape of the day. No bullet, no emoji.")
    segments: list[BriefItem] = Field(description="The day in 2-4 time blocks: title like '8 – 10 AM', detail says what is in that block (or 'Empty').")
    needs_attention: list[BriefItem] = Field(description="Things that need a decision or action from the person today. Most important first. Empty if nothing.")
    resolved: list[BriefItem] = Field(description="Things that got settled since yesterday that the person may not have seen (cancelled meeting, closed ticket, answered thread).")
    meetings: list[BriefMeeting]
    tickets: list[BriefTicket] = Field(description="Unresolved Jira issues assigned to the person")
    unread_emails: list[BriefEmail] = Field(description="Unread mail since the cut-off, repeated alerts of the same name folded into one line, newest first")
    footer: str | None = Field(description="One sentence on scope: what period was read, what was left out — or null")


class WatchItem(BaseModel):
    key: str = Field(description="Stable id for de-duplication: '<source>:<object>:<what>[:<value>]', e.g. 'gitlab:mr:701:pipeline:failed', 'gitlab:mr:701:comment:98213', 'jira:GTECH-1325570:status:In Review', 'teams:chat:Zeynep:mention:2026-09-14T10:02'")
    source: Literal["gitlab", "jira", "teams", "mail", "board"]
    severity: Literal["info", "warn", "urgent"] = Field(description="urgent: blocks you or someone waits on you now; warn: needs a look today; info: good to know")
    title: str = Field(description="One line, specific, with the ticket/MR key")
    detail: str = Field(description="One to three sentences: what happened, who, when, and the likely cause when visible (e.g. the failing job and its last error line)")
    url: str | None


class WatchReport(BaseModel):
    """What changed since the last check that the person should know about. Empty lists are the normal outcome."""
    items: list[WatchItem]
    checked: list[str] = Field(description="What you looked at, one short line each, e.g. 'GitLab: 3 open MRs by kamal.abdullayev in session-proxy'")
    note: str | None = Field(description="Sources you could not read, or null")


CONTRACTS: dict[str, type[BaseModel]] = {
    "brief": Brief,
    "watch": WatchReport,
    "lead": LeadOutput,
    "plan": Plan,
    "implement": Implement,
    "review": Review,
    "answer": Answer,
}


def schema_for(name: str) -> dict:
    """JSON schema for the SDK's output_format={"type": "json_schema", "schema": ...}."""
    return CONTRACTS[name].model_json_schema()


def parse(name: str, data) -> BaseModel:
    """Re-validate the SDK's structured_output. Raises pydantic.ValidationError."""
    return CONTRACTS[name].model_validate(data)
