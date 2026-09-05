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


class Plan(BaseModel):
    summary: str
    subtasks: list[Subtask]
    parallelism: int = Field(description="How many subtasks may run at once; 1 if unsure")


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
    branch: str | None = Field(description="Branch you committed to; null if blocked")
    commit_sha: str | None = Field(description="Full sha of YOUR commit; never invent one; null if blocked")
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
    path: str = Field(description="Repo-relative file path, e.g. service/src/main/java/.../MfaType.java")
    start_line: int = Field(description="First line of the snippet in the file (1-based)")
    end_line: int = Field(description="Last line of the snippet in the file")
    symbol: str | None = Field(description="Method/class/field the snippet shows, e.g. OptionalMfaPolicy.challengeRequiredForMode")
    language: str = Field(description="Language for syntax display: java, python, yaml, sql, ...")
    snippet: str = Field(description="The exact code, copied from the file — never paraphrased or reformatted")
    why: str = Field(description="One sentence: what this snippet proves or shows for the answer")


class Source(BaseModel):
    """What exactly was read. Two clones of one repo can differ by weeks — always say which."""
    repo_path: str = Field(description="Absolute path of the checkout that was read")
    branch: str | None = Field(description="Branch name of that checkout, as given in the task's repository state")
    commit: str | None = Field(description="Commit sha of that checkout, as given in the task's repository state")


class Answer(BaseModel):
    answer: str = Field(description="Markdown. Lead with the direct answer in one or two sentences, then detail. Tables for enumerations. Caveats last under their own heading.")
    code: list[CodeRef] = Field(description="Code the reader should see, in the order it is referenced. Empty if the answer is not about code.")
    source: Source
    citations: list[Citation]
    confidence: Literal["low", "medium", "high"]


class LeadOutput(BaseModel):
    """The team lead decides what a request is. A question gets an answer; work gets a plan."""
    kind: Literal["plan", "answer"]
    plan: Plan | None = Field(description="Filled when kind is plan")
    answer: Answer | None = Field(description="Filled when kind is answer")


CONTRACTS: dict[str, type[BaseModel]] = {
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
