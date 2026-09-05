# Alfred — local multi-agent engineering team

Three agent roles on one Claude Team seat, coordinating through a shared SQLite
board. No central orchestrator process: each daemon watches the board, does its
part, and creates the next role's task. The team lead is the human's only
contact.

## Layout

```
agents/           one YAML per role (model, prompt, tools, contract, limits) — add a file, get a role
contracts.py      typed handoff contracts (Pydantic) — plan / implement / review / answer
board.py          SQLite board: tasks, findings, usage (atomic claims, lease timeout, round cap)
runner.py         runs one agent on one task via the Agent SDK; enforces tool allowlist + structured output
daemon.py         role loop: claim -> run -> typed handoff
worktree.py       one git worktree per chain; agents never touch your checked-out branch
api.py            FastAPI + SSE over the board; serves the built UI at http://127.0.0.1:8787
connectors.py     MCP connectors as data: templates, Keychain-backed config, live test + tool classification
vault.py          macOS Keychain wrapper for connector secrets
pricing.py        list-price table for per-turn cost estimates
ui/               React (Vite) frontend: dashboard, chain timeline + diff + human review, usage, agents, connectors
seed_task.py      drop a new job on the board (CLI alternative to the UI's Ask page)
monitor.py        terminal board view
mcp_servers/      Python MCP servers (GitLab read-only, MySQL)
logs/             per-agent JSONL + human logs, plus flow.log with every handover
worktrees/        chain worktrees (created on demand)
tasks.db          the board (created on first run)
```

## One-time setup

```bash
cd ~/Desktop/project_x
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in GITLAB_TOKEN etc.

# Authenticate against your Team seat (NOT an API key):
claude                       # /login once, then exit
unset ANTHROPIC_API_KEY      # runner.py refuses to start if this is set
```

## Running

```bash
(cd ui && npm install && npm run build)   # once, and after UI changes
./run_all.sh                 # team_lead + developer + qa daemons + API/UI on :8787
python seed_task.py "Add rate limiting to /charge" --project ~/Desktop/projects/payment-service
python monitor.py            # live board
tail -f logs/flow.log        # every claim, finish and handover
```

Then open http://127.0.0.1:8787 — **Ask** sends a job to the team lead, **Dashboard**
shows the live board and flow log, a chain page shows the plan → commit → verdict
timeline, the findings, the diff, and lets you send your own finding back to the
developer as a new round.

UI development: `.venv/bin/uvicorn api:app --port 8787 --reload` plus `cd ui && npm run dev`
(Vite on :5173 proxies `/api` to :8787).

Or drop a `.md` file into `inbox/` — optional `project: /path` first line, rest is
the job. The team lead daemon picks it up on its next poll.

## Flow

0. **team_lead** reads the request and decides what it is. A **question** ("what
   options do we have", "why is X failing") gets `kind: answer` — the lead answers
   it directly, with the code it relied on as verbatim snippets (path + line range),
   the exact checkout it read (`source`: repo, branch, commit — injected by the
   daemon, never guessed), and citations. The chain is complete; no developer runs.
   The UI renders the answer as Markdown with code blocks and **Open in IntelliJ**
   links (`idea://open?file=…&line=…`, needs IntelliJ installed).
1. **Work** gets `kind: plan`: **team_lead** investigates (local Read, GitLab tools)
   and produces subtasks with acceptance criteria. If the project is a git
   repo, a worktree is created at `worktrees/<chain>` on branch `alfred/<chain>`.
   One **developer** task is created per subtask.
2. **developer** implements in the worktree, runs tests, commits, and reports
   the commit sha as structured output — never a description of the diff.
3. **qa** reads `git diff <base>..HEAD`, runs the tests, probes edge cases, and
   returns a **verdict + typed findings**. Findings are stored on the board.
4. **fail** → a developer fix task carrying the open findings as data, up to 3
   rounds, then the chain parks as `stuck`. **pass** → chain complete.
5. **You** review the branch: `git -C <repo> diff <base>..alfred/<chain>`, then
   merge, push, or `git worktree remove` it. Nothing is pushed by agents.

Every agent finishes by producing JSON that must match its role's contract
(`contracts.py`); the SDK enforces the schema and the runner re-validates it.
Tools outside a role's `allowed_tools` are denied in a hook, and agents run with
`setting_sources=[]` so nothing from `~/.claude` leaks in.

Crashed agent mid-task? Its claim expires after 15 minutes and the task reopens.

`ALFRED_DB=/path/to/other.db` points the board (daemons and API) at a different
SQLite file — useful for tests, or a second API instance on another port.

## Connectors (UI → Connectors)

MCP servers are configuration, not YAML edits. Add one from a template (Atlassian
Cloud via mcp-remote, Confluence/Jira on-prem, GitLab, MySQL, custom stdio/HTTP) or
**Import** a server already registered for Claude Desktop/Code. Secrets go to the
macOS Keychain (`vault.py`, service `alfred-mcp`) — never to SQLite, YAML or logs.

**Test** starts the server, lists its tools and classifies each as *read* or
*mutates* (MCP `readOnlyHint` annotation when present, otherwise a name heuristic
biased towards *mutates*; you can flip any tool, and your decision sticks). Tick the
roles that may use the connector. On the next run the runner adds the server to that
role's `mcp_servers` and allows **only its read tools**; mutating and undiscovered
tools are denied by the PreToolUse gate. A connector with the same name as one in
the role's YAML is shadowed by the YAML one.

## Cost

`usage` rows are the SDK's estimate at API **list price** — on a Team seat that
is a consumption meter against your rate-limit window, not a bill. The SDK reports
cost per run; `turns` adds per-message token counts (exact, from the API) with a
list-price estimate from `pricing.py` — the only pricing arithmetic in Alfred — so a
chain page shows which turn or tool call was expensive. The Usage page shows cost per
request (chain), per day/role/model.

## Adding a role

Create `agents/<role>.yaml` with `name`, `model`, `contract`, `system_prompt`,
`builtin_tools`, `allowed_tools`, optional `mcp_servers`, and start
`python daemon.py <role>`. Add a contract to `contracts.py` if the role needs a
new output shape; reuse `review` for any reviewer-type role.
