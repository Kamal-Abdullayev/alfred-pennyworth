# Alfred — a personal engineering assistant that runs as a team of agents

Alfred is a local web app that sits between you and your work systems. A **team lead**
agent is your only contact: ask it anything about a ticket, a merge request, a pipeline or a
codebase, and it answers with the exact code and sources it relied on, or, when you ask for
a change, it plans the work and hands it to **developer** agents that implement in a private
git worktree while a **QA** agent reviews the diff and runs the tests. Around that core
Alfred adds what a working engineer needs every day: a **morning brief** written from your
calendar, Teams, mail and Jira; a **watcher** that turns new review comments, red pipelines
and mentions into alerts and macOS notifications; a **shared memory** of decisions that
outlives any chat; and **schedules** that run all of it while you sleep.

Everything runs on your machine on one Claude **Team or Max seat** (never an API key).
Nothing leaves your laptop except the model calls your Claude login makes and the read-only
connector calls you configure. Agents never push, never write to Jira or Teams, and never
touch your own checkout.

---

## Contents

1. [Requirements, and what works without them](#requirements-and-what-works-without-them)
2. [Quick start](#quick-start)
3. [Concepts](#concepts)
4. [The pages](#the-pages)
5. [How a request flows](#how-a-request-flows)
6. [Configuration](#configuration)
7. [Architecture and layout](#architecture-and-layout)
8. [Operating Alfred](#operating-alfred)
9. [Cost figures](#cost-figures)
10. [Security posture](#security-posture)
11. [Troubleshooting](#troubleshooting)
12. [Status and roadmap](#status-and-roadmap)

---

## Requirements, and what works without them

| Requirement | Needed for | Without it |
|---|---|---|
| macOS or Linux, Python 3.11+, Node 20+, git | everything | nothing runs |
| A Claude **Team or Max** login (`claude` CLI, `/login` once) | every agent run | nothing runs; an `ANTHROPIC_API_KEY` is refused on purpose |
| **GitLab** (self-hosted) personal token | code and MR questions through the bundled read-only server | the lead reads local checkouts only |
| **Jira Data Center** personal token | ticket questions, "my tickets", the brief and watcher | those parts show "not configured" |
| **Microsoft 365** connected to your claude.ai account | calendar, Teams and mail in the brief and watcher | brief covers Jira and the board only |
| Docker Desktop | the shared Excalidraw canvas | diagrams still render in the chat; no live canvas |
| `terminal-notifier` (`brew install terminal-notifier`) | notifications that open Alfred when clicked | notifications still arrive, clicks open Script Editor |
| IntelliJ IDEA | "Open in IntelliJ" links on code refs | links fall back to GitLab |

Jira Cloud, GitHub, Confluence Cloud and Google Workspace are reachable through the
connectors your claude.ai account provides, or through any MCP server you add as a custom
connector; the bundled servers target self-hosted GitLab and Jira Data Center.

## Quick start

```bash
git clone git@github.com:Kamal-Abdullayev/alfred-pennyworth.git alfred && cd alfred
./setup.sh                 # venv, Python deps, UI build, then a preflight table
claude                     # first time only: /login with your Team account, then exit
./run_all.sh               # supervisor + workers + API/UI → http://127.0.0.1:8787
```

`setup.sh` is safe to re-run and recreates the venv if the folder moved. `doctor.py` is the
preflight: 15 checks, each with its fix; `run_all.sh` refuses to start while a required one
fails, and Home shows the same list in a banner. To keep Alfred running without a terminal,
`./alfred-autostart.sh install` registers a launchd agent that starts it at login and keeps
it alive (schedules only fire while Alfred runs).

First things to do in the UI:

1. **Connectors** → give the agents something to read. *Discover account connectors* lists
   the MCP servers your claude.ai login already has (Atlassian, Microsoft 365, …); *Add from
   template* covers Jira Data Center, Confluence, self-hosted GitLab, the Excalidraw canvas
   and any custom stdio/HTTP server; *Import* pulls servers registered for Claude Desktop or
   Claude Code. Press **Test** on each: it lists the tools and classifies them *read* or
   *mutates*. Agents get only read tools unless you mark a connector "writes are safe".
   Secrets go to the macOS Keychain (a 0600 file elsewhere), never to the database or logs.
2. **Schedules** → set your name and GitLab username, then create a *Morning brief* (08:00,
   Mon–Fri) and a *Watcher* (every 60 min, 08:00–19:00). Press *run now* on each to see them
   work.
3. **Home** → ask something in the box at the bottom.

## Concepts

**Roles** are YAML files under `agents/`. Each declares a model, a system prompt, the built-in
tools and allow rules, a *contract* (the structured output it must return), worker limits and
whether it may use your account's connectors. Five ship: `team_lead` (opus), `developer`
(opus), `qa` (sonnet), `briefer` (sonnet) and `watcher` (haiku). Add a file, get a role; the
Agents page edits them live.

**Chains** are units of work on the SQLite board. A question is a one-task chain. A change is
a chain of lead → developers → QA → fix rounds, each task a row with its transcript, cost
and structured result. The chain page shows the timeline, findings, diff and per-message
tokens; the Home page draws each chain as a flow.

**Worktrees.** When the lead dispatches a plan, Alfred creates `worktrees/<chain>` on branch
`alfred/<chain>` of your repository. Developers work there. Your checkout and your branches
are never modified; you review the worktree diff and merge, apply or discard. If the task or
project memory says you do not want commits, developers leave the change uncommitted
(**no-commit mode**) and QA reviews the working tree.

**Memory.** A per-project store (keyed by git remote, so two clones share it) plus a global
one: decisions, facts, conventions, glossary, people, open questions. Every run gets the
active entries injected and four in-process tools (`memory_search`, `memory_propose`,
`board_peek`, `note_progress`). Agents may **propose**; only you activate. `remember: …` in
any chat stores a fact directly; *Paste meeting notes* has the lead distil notes into
proposals; a Markdown export lives under `data/memory/`.

**Session resume.** Follow-ups in a chat continue the lead's previous Claude session (for 24
hours and up to 12 turns), so they cost cents instead of re-reading everything. If a resume
fails, the run restarts fresh with the last answers injected.

**Schedules.** Recurring jobs fired by the supervisor: a *brief* (one page about your day),
a *watcher* (interval, what changed since the last check), or any *prompt* to any role. A
job due while the Mac was asleep runs once it wakes, inside a three-hour grace window; a
failed scheduled run is retried once.

**Alerts.** The watcher's findings, de-duplicated by a stable key so a red pipeline is
reported once. They appear in "Needs your attention" on Home and as a macOS notification
whose click opens the merge request, chat or ticket.

## The pages

| Page | What it is |
|---|---|
| **Home** | Today's brief (headline, agenda track, day blocks). **Needs your attention**: watcher alerts, stuck chains, items from the brief, memory proposals, failed scheduled runs. Meetings and your tickets (click a ticket to open a pre-filled chat). **Alfred at work**: each recent chain as lead → developers → QA → result. The chat box. |
| **Ask** | Chats with the team lead. Live steps while it works; the answer with verbatim code refs, Mermaid diagrams, sources and confidence; live developer/QA progress under a dispatched plan; **■ stop** on a running turn; `remember:`; proposals accepted in place; **export** as a printable page (PDF) or Markdown. Scheduled runs are grouped and collapsed in the sidebar. |
| **Brief** `/brief/<id>` | The day on one printable page: headline, timeline, needs attention, resolved, meetings, tickets, unread mail. |
| **Chain** `/chains/<id>` | Timeline of every task, findings, the diff (committed or not) with the apply command, transcripts, per-message tokens, human actions: requeue, close, dispatch, stop, send a finding back. |
| **Memory** | Browse, add, edit, accept, retire, move between project and global; paste meeting notes. |
| **Schedules** | Briefs, watchers and prompts: time or interval, days, run history with lateness, run now, pause. Your name and GitLab username. |
| **Agents** | Edit each role's YAML, add roles by cloning, live workers and the global cap. |
| **Usage** | Cost per request, per day, role and model. |
| **Connectors** | MCP servers as data, with a masked log per connector. |
| **Logs** | Per-agent readable logs and the flow log. |

## How a request flows

1. **team_lead** gets your question plus two blocks: **PROJECT MEMORY** and **BOARD** (what
   the other agents are doing). A question → `kind: answer`. Work → `kind: plan` with
   self-contained subtasks and acceptance criteria. With no repository attached it answers
   with the plan and asks for a path, unless it names the local checkout it read, in which
   case the harness attaches that checkout to the chat.
2. A worktree is created; one **developer** task per subtask. Developers run the build and
   tests, commit on the worktree branch (or not, in no-commit mode) and report.
3. **qa** reads the diff, runs the tests, probes edge cases, returns a verdict with typed
   findings. Fail → a fix round, up to three, then the chain parks as *stuck* for you.
4. You review on the chain page and merge, apply or discard. Nothing is pushed.

Every agent ends with structured output validated against its contract (`contracts.py`).
Tools outside a role's allowlist are denied in a PreToolUse hook. Roles run with
`setting_sources=[]` (nothing from `~/.claude` leaks in) unless they use your account's
connectors (`account_connectors: true`).

## Configuration

- **Roles**: `agents/<role>.yaml` — `model`, `contract`, `system_prompt`, `builtin_tools`,
  `allowed_tools`, `account_connectors`, `workers: {min, max}`, `permission_mode`,
  `max_minutes`. Edited on the Agents page or by hand; the supervisor picks up changes.
- **Global settings** (Agents and Schedules pages): `max_workers` (global cap, default 4),
  `me_name` (how the brief addresses you), `gitlab_username` (for "my merge requests").
- **Connectors**: stored on the board with secrets in the vault; bundled servers are
  referenced as `${PYTHON}` / `${PROJECT_ROOT}` so the folder can be renamed or moved.
- **Environment**: `ALFRED_DB=/path/to.db` points everything at another board;
  `ALFRED_VAULT=file` forces the file vault on macOS; `ALFRED_VAULT_FILE` sets its path.

## Architecture and layout

```
run_all.sh             preflight, build UI if needed, start supervisor + API (:8787)
setup.sh / doctor.py   one-time setup; preflight checks (also /api/doctor → Home banner)
alfred-autostart.sh    launchd agent so Alfred starts at login and stays up (macOS)
supervisor.py          keeps role workers alive, scales them with the queue, fires schedules,
                       recycles idle workers when the code changes
daemon.py              one worker: claim → run → typed handoff (answer, plan dispatch, dev → QA,
                       fix rounds, brief/watch reports, memory proposals, session bookkeeping)
runner.py              runs one agent via the Claude Agent SDK: allowlist gate, hooks, stop
                       watcher, session resume, in-process memory/board tools
schedules.py           due-time logic, brief and watch prompts, interval windows, retry-once
board.py               SQLite: tasks, findings, usage, turns, conversations, connectors,
                       workers, settings, schedules, alerts
memory.py              project memory + working notes; prompt blocks; FTS search; export
contracts.py           Pydantic contracts: lead/plan/answer, implement, review, brief, watch
connectors.py          templates, secrets, live test + tool classification, calendar turn
worktree.py / vault.py / pricing.py
api.py                 FastAPI + SSE; serves ui/dist
ui/                    React + Vite: Home, Ask, Brief, Chain, Memory, Schedules, Agents, Usage,
                       Connectors, Logs, Export
agents/                team_lead, developer, qa, briefer, watcher
mcp_servers/           bundled read-only servers: gitlab, jira (Data Center)
excalidraw/            docker-compose for the optional canvas
tasks.db, logs/, data/, worktrees/, workspace/   runtime state — git-ignored
```

## Operating Alfred

- **Start / stop**: `./run_all.sh`, Ctrl-C stops everything. Or install the autostart.
- **Code changes**: workers restart themselves when a `.py` file changes (busy ones finish
  first); the API needs a restart; the UI needs `npm run build` and shows a reload banner.
- **Stopping a run**: ■ stop on the chat turn or the chain page; queued tasks are cancelled
  at once, running ones within seconds.
- **Data**: the board is `tasks.db`; transcripts under `logs/tasks/`; canvas snapshots and
  memory exports under `data/`. Back up `tasks.db` if you care about history.
- **Canvas**: `docker compose -f excalidraw/docker-compose.yml up -d` (restarts with Docker).
- **Other instances**: `ALFRED_DB=… .venv/bin/uvicorn api:app --port 8788` for a second
  board on another port; the supervisor is one per board.

## Cost figures

Every run's cost is the Claude Code CLI's `total_cost_usd`: exact token counts × Anthropic's
public API list prices, including cache reads and writes. On a Team or Max seat nothing is
billed per token, so read it as **what the run would have cost on the API**, a consumption
meter against your seat's rate limits. Typical figures: a question $1–6, a follow-up that
resumes the session cents plus new reading, a two-subtask implementation with QA $20–35,
a brief about $1, a watcher check $0.20–0.50. Per-message tables on a chain page split the
run total by exact input and cache tokens.

## Security posture

Read-only by construction: allowlisted tools, mutating connector tools denied by default,
agents never push, nothing is written to Jira, Teams or mail, secrets in the Keychain,
connector logs masked, the API bound to 127.0.0.1. Transcripts under `logs/tasks/` contain
whatever the agents read — ticket text, code, messages — so treat that folder like your own
notes. There is no transcript retention or redaction yet (see roadmap).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Red banner on Home | Same as `python doctor.py`; each line names the fix. |
| "ANTHROPIC_API_KEY is set" | Unset it; Alfred runs on your login. |
| UI looks old after a pull | `(cd ui && npm run build)` and reload. |
| Brief or watcher failed right after wake-up | The API was unreachable; the run is retried once. Move the brief later or install the autostart. |
| Meeting times are off by hours | Fixed by passing the local zone to the agents; restart `run_all.sh` after pulling. |
| Notifications open Script Editor | Install `terminal-notifier` and allow it once in System Settings → Notifications. |
| Jira / GitLab connector "failed, no tools visible" | Usually `mcp` 2.x got installed; `pip install 'mcp>=1.2,<2'`. The preflight checks this. |
| "PLAN NOT DISPATCHED" | No repository: mention the checkout path in the message or attach it with *repo*. |
| A chain is *stuck* | Open it: requeue, close, or send a finding back to the developer. |
| Stray API after stopping | `pkill -f "uvicorn api:app"`; `run_all.sh` also clears it. |

## Status and roadmap

Alfred is used daily by one engineer; it is not yet a multi-user product. Known gaps, in the
order they are planned: a per-chain and per-day budget cap with a confirm step; tests and
CI; transcript retention and redaction; dependency-aware dispatch with one worktree per
developer; a Docker image for the API and supervisor; local models for the cheap roles
(briefer, watcher); Jira Cloud and GitHub as first-class connectors; multi-user boards.
Windows is not supported.
