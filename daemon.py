# daemon.py — one generic loop for every role.
#
#   python daemon.py team_lead
#   python daemon.py developer
#   python daemon.py qa
#
# Roles are whatever YAML files exist in agents/. Each daemon polls the board
# for tasks addressed to its role, runs the agent, writes the typed result back,
# and creates the follow-up tasks for the next role. Run them together
# (./run_all.sh) or one at a time — the board doesn't care.
#
# Flow (no approval gate — the human gate is the final diff on the chain branch):
#   job → team_lead: answer                → chain complete (questions, lookups, diagnosis)
#         team_lead: plan (subtasks)      → one developer task per subtask
#         developer: implement (commit)    → qa task
#         qa:        review (verdict+findings) → pass: chain done
#                                             fail: developer fix task, ≤ MAX_ITERATIONS
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

import board
import worktree
from runner import run_agent

POLL_SECONDS = 10

ROOT = Path(__file__).parent
AGENTS_DIR = ROOT / "agents"
INBOX = ROOT / "inbox"
PROCESSED = INBOX / "processed"


def configs() -> dict[str, str]:
    """Roles are data: every agents/<role>.yaml is a role. Add a file, get a role."""
    return {p.stem: f"agents/{p.name}" for p in sorted(AGENTS_DIR.glob("*.yaml"))}


def flow(msg):
    """One shared human-readable file with every claim, finish and handover."""
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    with open(logdir / "flow.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def scan_inbox():
    """Turn .md/.txt files dropped into inbox/ into team_lead tasks.

    File format: optional `project: /path/to/repo` first line, then the job.
    Processed files are moved (not deleted) to inbox/processed/.
    """
    INBOX.mkdir(exist_ok=True)
    PROCESSED.mkdir(exist_ok=True)
    for f in sorted(INBOX.glob("*.md")) + sorted(INBOX.glob("*.txt")):
        text = f.read_text(encoding="utf-8").strip()
        if not text:
            continue
        project_dir = None
        lines = text.splitlines()
        if lines and lines[0].strip().lower().startswith("project:"):
            candidate = Path(lines[0].split(":", 1)[1].strip()).expanduser()
            if not candidate.is_dir():
                print(f"[inbox] {f.name}: project dir not found: {candidate} — skipping file")
                continue
            project_dir = str(candidate.resolve())
            lines = lines[1:]
        body = "\n".join(lines).strip()
        if not body:
            continue
        task_id = board.create_task(
            role="team_lead",
            title=f.stem.replace("_", " ").replace("-", " ")[:60],
            body=body,
            created_by=f"inbox:{f.name}",
            project_dir=project_dir,
        )
        f.rename(PROCESSED / f"{time.strftime('%Y%m%d-%H%M%S')}_{f.name}")
        flow(f"[inbox] NEW JOB {task_id} from file {f.name} [workdir: {project_dir or 'workspace/'}]")


# ------------------------------------------------------------- prompts ----

def developer_body(job: str, subtask: dict) -> str:
    acceptance = "\n".join(f"  {i + 1}. {a}" for i, a in enumerate(subtask.get("acceptance", [])))
    return (
        f"--- Job ---\n{job}\n\n"
        f"--- Your subtask: {subtask['title']} ---\n{subtask['description']}\n\n"
        f"Acceptance criteria:\n{acceptance or '  (none given)'}\n\n"
        f"Commit your work on the current branch and report the commit sha."
    )


def fix_body(original_body: str, iteration: int, findings: list[dict]) -> str:
    items = "\n".join(
        f"{i + 1}. [{f['severity']}] {f['file']}{':' + str(f['line']) if f.get('line') is not None else ''}"
        f" — {f['claim']}\n   evidence: {f['evidence']}"
        for i, f in enumerate(findings)
    )
    return (
        f"{original_body}\n\n"
        f"--- Fix round {iteration} ---\n"
        f"Your previous implementation was rejected by QA. Address every finding below, "
        f"re-run the tests, and commit. If you believe a finding is wrong, say why in "
        f"open_questions instead of silently ignoring it.\n\n"
        f"Open findings:\n{items}"
    )


def qa_body(dev_body: str, impl: dict, base_sha: str | None, iteration: int) -> str:
    base = (base_sha or "")[:12]
    diff_hint = (f"  git log --oneline {base}..HEAD\n  git diff {base}..HEAD"
                 if base else "  git log --oneline -5\n  git show HEAD")
    v = impl.get("verification") or {}
    return (
        f"Review a developer's change against this task.\n\n"
        f"{dev_body}\n\n"
        f"--- Change under review (round {iteration}) ---\n"
        f"The developer's commits are on the current branch:\n{diff_hint}\n"
        f"Developer reports commit {impl.get('commit_sha') or '(none)'} and says verification "
        f"was \"{v.get('result')}\" via: {'; '.join(v.get('commands_run') or []) or '(nothing)'}.\n\n"
        f"Do not trust that report. Read the diff, run the tests yourself, probe edge cases "
        f"the tests miss. A \"pass\" verdict requires that YOU ran the tests and saw them pass. "
        f"Every finding needs a file, a line where applicable, and the command + output that proves it."
    )


def repo_state(project_dir) -> str:
    """Appended to the team lead's prompt so it can fill Answer.source without a shell.
    Two clones of one repo on the same machine can differ by weeks — the lead must say which it read."""
    if not project_dir or not worktree.is_repo(project_dir):
        return ""
    try:
        root = worktree.repo_root(project_dir)
        branch = worktree._git(root, "rev-parse", "--abbrev-ref", "HEAD")
        sha = worktree.head_sha(root)
    except Exception:
        return ""
    return (f"\n\n--- Repository state (copy into source; do not guess) ---\n"
            f"repo_path: {root}\nbranch: {branch}\ncommit: {sha}\n")


# ------------------------------------------------------------- handoff ----

def handoff(role, task, res) -> str:
    """Decide what happens after an agent finishes. Returns a log line."""
    chain = task["chain_id"]
    root = board.chain_root(chain) or task
    structured = res.get("structured")

    if structured is None:
        board.park_chain(chain, f"{role} produced no valid structured output "
                                f"(subtype={res.get('subtype')}): {res.get('text', '')[:120]!r}")
        return f"chain {chain} parked: {role} output did not match its contract"

    # ---------------------------------------------------------- team_lead --
    if role == "team_lead":
        out = structured
        kind = out.get("kind") or ("plan" if "subtasks" in out else None)
        if kind == "answer":
            ans = out.get("answer") or {}
            src = ans.get("source") or {}
            return (f"ANSWERED chain {chain}: {len(ans.get('answer', ''))} chars, "
                    f"{len(ans.get('code') or [])} code ref(s), confidence={ans.get('confidence')}, "
                    f"read {src.get('branch') or '?'}@{(src.get('commit') or '')[:8] or '?'}")
        plan = out.get("plan") if "plan" in out else out
        if not plan or not plan.get("subtasks"):
            board.park_chain(chain, "team lead plan has no subtasks")
            return f"chain {chain} parked: empty plan"

        # One isolated worktree per chain when the project is a git repo.
        workdir = task.get("project_dir")
        if workdir and worktree.is_repo(workdir):
            wt = worktree.create(workdir, chain)
            board.set_chain_worktree(chain, wt.path, wt.base_sha)
            workdir = wt.path
            where = f"worktree {wt.path} (branch {wt.branch}, base {wt.base_sha[:8]})"
        else:
            where = f"in place: {workdir or 'workspace/'}"

        ids = []
        for st in plan["subtasks"]:
            ids.append(board.create_task(
                role="developer",
                title=f"implement: {st['title']}"[:80],
                body=developer_body(root["body"], st),
                created_by="team_lead",
                chain_id=chain,
                project_dir=workdir,
                parent_id=task["id"],
            ))
        return (f"HANDOVER team_lead -> developer: {len(ids)} subtask(s) {ids} "
                f"parallelism={plan.get('parallelism')} — {where}")

    # ---------------------------------------------------------- developer --
    if role == "developer":
        impl = structured
        if impl.get("status") == "blocked":
            b = impl.get("blocked") or {}
            board.park_chain(chain, f"developer blocked: {b.get('reason', '')[:200]}")
            return f"CHAIN {chain} PARKED: developer blocked — {b.get('reason', '')[:120]}"
        new_id = board.create_task(
            role="qa",
            title=f"verify: {task['title'].removeprefix('implement: ').removeprefix('fix: ')}"[:80],
            body=qa_body(task["body"], impl, root.get("base_sha"), task["iteration"]),
            created_by="developer",
            chain_id=chain,
            iteration=task["iteration"],
            project_dir=task.get("project_dir"),
            parent_id=task["id"],
        )
        return f"HANDOVER developer -> qa (new task {new_id}): commit {(impl.get('commit_sha') or '')[:8]} ready for review"

    # ----------------------------------------------------------------- qa --
    if role == "qa":
        review = structured
        board.add_findings(chain, task["id"], task["iteration"], "qa", review.get("findings", []))
        dev_task = board.get_task(task.get("parent_id")) if task.get("parent_id") else None

        if review.get("verdict") == "pass":
            board.resolve_findings(chain)
            remaining = board.chain_open_count(chain)
            cost = board.chain_cost(chain)
            if remaining:
                return f"qa PASSED subtask; {remaining} task(s) still in flight on chain {chain}"
            hint = ""
            if root.get("worktree"):
                hint = f" — review: git -C {root.get('project_dir')} diff {root['base_sha'][:8]}..alfred/{chain}"
            return f"CHAIN {chain} COMPLETE: qa PASSED — '{root['title']}' done, est≈${cost:.2f}{hint}"

        iteration = board.chain_iteration(chain) + 1
        if iteration > board.MAX_ITERATIONS:
            board.park_chain(chain, f"failed QA {board.MAX_ITERATIONS} times")
            return f"CHAIN {chain} PARKED: failed qa {board.MAX_ITERATIONS} times, needs a human"
        original = dev_task["body"] if dev_task else root["body"]
        # strip any earlier fix-round suffix so the body doesn't snowball
        original = original.split("\n\n--- Fix round ")[0]
        new_id = board.create_task(
            role="developer",
            title=f"fix (round {iteration}): {board.chain_title(chain)}"[:80],
            body=fix_body(original, iteration, board.open_findings(chain)),
            created_by="qa",
            chain_id=chain,
            iteration=iteration,
            project_dir=task.get("project_dir"),
            parent_id=task["id"],
        )
        n = len(review.get("findings", []))
        return f"HANDOVER qa -> developer (new task {new_id}): FAILED with {n} finding(s), round {iteration} of {board.MAX_ITERATIONS}"

    return "no handoff rule for this role"


def record_usage(task, role, res):
    board.record_turns(task["id"], task["chain_id"], role, res.get("turn_log") or [])
    for model, u in (res.get("model_usage") or {}).items():
        board.record_usage(task["id"], task["chain_id"], role, model,
                           u["input_tokens"], u["output_tokens"], u["cache_read_tokens"],
                           u["cache_write_tokens"], u["cost_usd"], res.get("turns"), res.get("duration_ms"))


# ---------------------------------------------------------------- loop ----

async def main(role):
    cfgs = configs()
    if role not in cfgs:
        sys.exit(f"unknown role {role!r}. roles are agents/*.yaml: {', '.join(cfgs)}")
    board.init()
    # every role gets its log files up front, so the Logs page shows all agents even before they run
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "tasks").mkdir(exist_ok=True)
    for suffix in (".jsonl", ".log"):
        (ROOT / "logs" / f"{role}{suffix}").touch()
    agent_name = f"{role}-daemon"
    if role == "team_lead":
        print(f"[{role}] also watching {INBOX}/ for .md/.txt job files")
    print(f"[{role}] watching the board (ctrl-c to stop)")

    while True:
        if role == "team_lead":
            scan_inbox()
        task = board.claim_next(role, agent_name)
        if task is None:
            time.sleep(POLL_SECONDS)
            continue

        flow(f"[{role}] CLAIMED {task['id']} (chain {task['chain_id']}, round {task['iteration']}): "
             f"{task['title']}  [workdir: {task.get('project_dir') or 'workspace/'}]")
        try:
            prompt = task["body"] + (repo_state(task.get("project_dir")) if role == "team_lead" else "")
            res = await run_agent(
                cfgs[role], prompt, task.get("project_dir"),
                meta={"id": task["id"], "chain_id": task["chain_id"], "iteration": task["iteration"]},
            )
            record_usage(task, role, res)
            if res.get("is_error") or res.get("subtype") not in (None, "success"):
                board.fail(task["id"], res.get("text", ""), res.get("structured"))
                board.park_chain(task["chain_id"], f"{role} run ended with {res.get('subtype')}")
                flow(f"[{role}] FAILED {task['id']} — subtype={res.get('subtype')}: {res.get('text', '')[:160]}")
                continue
            board.complete(task["id"], res.get("text", ""), res.get("structured"))
            outcome = handoff(role, task, res)
            denied = f"  (denied tools: {sorted(set(res['denied']))})" if res.get("denied") else ""
            flow(f"[{role}] FINISHED {task['id']} est≈${res.get('cost_usd', 0):.4f} — {outcome}{denied}")
        except Exception as e:
            err = traceback.format_exc()
            board.fail(task["id"], err[-2000:])
            flow(f"[{role}] FAILED {task['id']} — {e}")
            print(err)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: python daemon.py <{'|'.join(configs())}>")
    asyncio.run(main(sys.argv[1]))
