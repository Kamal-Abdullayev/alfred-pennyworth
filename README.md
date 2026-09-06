# Alfred — a local multi-agent engineering team

Three agent roles on one Claude **Team seat** (never an API key), coordinating through a
shared SQLite board: a **team lead** that is your only contact and decides whether a
request is a question (it answers) or work (it plans), **developers** that implement in
private git worktrees, and **QA** that reviews the diff and runs the tests. Everything is
driven from a local web UI: chat, live agent activity, cost per turn, connectors to
Jira/Confluence/GitLab/DBs, agent configuration, shared project memory, and a shared
Excalidraw canvas.

Nothing is pushed by agents. Nothing leaves your laptop except the model calls your Claude
login makes and the read-only connector calls you configure.

## Quick start (fresh clone)

Prerequisites: macOS or Linux, **Python 3.11+**, **Node 20+**, **git**, a **Claude Team (or
Max) login**. Optional: Docker (shared canvas), IntelliJ (click-to-open code links).

```bash
git clone <this repo> alfred && cd alfred
./setup.sh                 # venv, Python deps, UI build, preflight report
claude                     # first time only: /login with your Team account, then exit
./run_all.sh               # workers + API/UI → http://127.0.0.1:8787
```

`setup.sh` ends with a preflight table (`python doctor.py` any time). `run_all.sh` refuses to
start while a required check fails, and the Dashboard shows the same problems with the fix
for each. Do **not** set `ANTHROPIC_API_KEY` — the runner refuses to start if it is set,
because it would bill API rates instead of your seat.

Then, in the UI:

1. **Connectors** → add what your agents may read. Three ways:
   - **Discover account connectors**: the MCP servers your claude.ai account already has
     (Atlassian, Microsoft 365, …) — no configuration, just tick the roles.
   - **Add from template**: Jira Data Center, Confluence, self-hosted GitLab, MySQL, the
     Excalidraw canvas, or any custom stdio/HTTP server. Secrets go to the macOS Keychain
     (a 0600 file on other systems), never to the database or logs.
   - **Import** servers already registered for Claude Desktop / Claude Code.
   Press **Test** on each: it lists the tools and classifies them read/mutates. Agents get
   only the read tools unless you mark the connector "writes are safe".
2. **Ask** → talk to the team lead. Give a local repository path (or mention it in the
   message) when the question is about local code or you want changes applied.
3. **Agents** → models, prompts, tools, worker counts per role. Changes apply to the next run.

## What you can do

| Page | What it is |
|---|---|
| **Ask** | Chat with the team lead. Live steps while it works, then the answer with verbatim code refs (open in IntelliJ / GitLab), Mermaid diagrams, citations, confidence, per-turn cost. **■ stop** interrupts a running turn. `remember: …` stores a fact in project memory, `remember globally: …` for all projects. Proposed memory entries can be accepted right in the chat. |
| **Dashboard** | Live board, every chain with status and cost, flow log, setup problems. |
| **Chain page** | Plan → implementation → QA verdict timeline, findings, the full diff (committed or not), transcripts, per-turn tokens, human actions (requeue, close, dispatch, stop, send a finding back). |
| **Memory** | Shared project memory: decisions, facts, conventions, glossary, people, open questions — per repository (keyed by git remote, so two clones share it) plus global. Agents and meeting-notes intakes can only **propose**; you accept, edit, retire. **Paste meeting notes** → the lead distils them into proposals. Exported as Markdown under `data/memory/`. |
| **Agents** | Edit each role's YAML from the UI; add roles by cloning; see live workers and the global cap. |
| **Usage** | Cost per request, per day/role/model (list-price estimates — a consumption meter on a seat, not a bill). |
| **Connectors** | MCP servers as data, with a masked log per connector. |
| **Logs** | Per-agent readable logs. |

### How a request flows

1. **team_lead** reads the request plus two injected blocks: **PROJECT MEMORY** (active
   entries for that repository + global) and **BOARD** (what other agents are doing right
   now). A question → `kind: answer`. Work → `kind: plan` with self-contained subtasks.
   No repository → it answers with the plan and asks for a path (unless it can name the
   local checkout it read, in which case the harness attaches it).
2. A git worktree is created at `worktrees/<chain>` on branch `alfred/<chain>`; one
   **developer** task per subtask. Developers commit on that branch by default. If the task
   or project memory says you do not want commits, they leave the changes uncommitted
   (**no-commit mode**); either way your own checkout is never touched.
3. **qa** reads the diff (commits or working tree), runs the tests, probes edge cases, and
   returns a verdict with typed findings. Fail → a fix round (max 3), then the chain parks
   as *stuck* for you.
4. You review the diff on the chain page and merge, apply, or discard. The chain page shows
   the exact `git … diff | git … apply` command for taking the change into your checkout.

Every agent ends with structured output validated against its contract
(`contracts.py`). Tools outside a role's allowlist are denied in a hook. Roles run with
`setting_sources=[]` (nothing from `~/.claude` leaks in) unless they use your account's
connectors (`account_connectors: true`). Agents can never push, and mutating connector
tools are denied unless you trust a connector's writes.

## Layout

```
setup.sh / doctor.py   one-time setup and preflight (also /api/doctor → Dashboard banner)
run_all.sh             preflight, build UI if needed, start supervisor + API on :8787
agents/                one YAML per role (model, prompt, tools, contract, workers)
contracts.py           typed handoff contracts (lead / plan / implement / review / answer / memory proposals)
board.py               SQLite board: tasks, findings, usage, turns, connectors, workers, settings, conversations
memory.py              shared project memory + working notes; prompt blocks; Markdown export
runner.py              runs one agent on one task via the Agent SDK; allowlist gate; stop watcher;
                       in-process tools memory_search / memory_propose / board_peek / note_progress
daemon.py              role loop: claim → run → typed handoff (answer, plan dispatch, dev → QA, fix rounds)
supervisor.py          keeps role workers alive and scales them with the queue (per-role min/max, global cap)
worktree.py            one git worktree per chain; agents never touch your checkout
connectors.py          MCP connectors as data: templates, secrets, live test + tool classification
vault.py               secrets: macOS Keychain, or a 0600 JSON file elsewhere
api.py                 FastAPI + SSE; serves ui/dist
ui/                    React (Vite) UI
mcp_servers/           bundled read-only MCP servers (Jira Data Center, GitLab, MySQL)
excalidraw/            docker-compose for the optional shared canvas
tasks.db, logs/, data/, worktrees/, workspace/   runtime state — all git-ignored
```

## Optional pieces

**Shared canvas.** `docker compose -f excalidraw/docker-compose.yml up -d` starts the canvas
at <http://localhost:3000>; add the `excalidraw` connector from its template and assign it to
`team_lead`. The lead then draws its diagrams there too; the chat shows "show live canvas"
on turns that drew, and a snapshot of the scene is saved per run under `data/assets/`.

**Local LLMs** are planned (a provider layer so cheap roles can run on Ollama); today every
role runs on the Claude seat.

## Running pieces separately

```bash
.venv/bin/uvicorn api:app --port 8787 --reload      # API only
(cd ui && npm run dev)                               # Vite on :5173, proxies /api to :8787
.venv/bin/python supervisor.py                       # workers only (replaces daemon.py by hand)
.venv/bin/python seed_task.py "Add rate limiting" --project ~/code/payment-service
python monitor.py                                    # terminal board
tail -f logs/flow.log                                # every claim, finish and handover
ALFRED_DB=/tmp/other.db …                            # point everything at another board
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard shows a red setup banner | Same as `python doctor.py`; each line has the fix. |
| "ANTHROPIC_API_KEY is set" on start | Unset it; Alfred must run on your login, not an API key. |
| UI looks old after a pull | `(cd ui && npm run build)`; the UI shows a reload banner when the served bundle changed. |
| A chain is *stuck* | Open it: requeue, close, or send a finding back to the developer. |
| "PLAN NOT DISPATCHED" | No repository: re-ask with a local checkout path (or mention the path in the message). |
| Run failed with "JSON message exceeded maximum buffer size" | A tool result over 32 MB; narrow the request. |
| Agents cannot see a connector | Test it, tick the role, check the tool is classified *read*; a YAML `mcp_servers` entry with the same name shadows it. |
| Stray API after stopping `run_all.sh` | `pkill -f "uvicorn api:app"` |

## Security posture

Read-only by construction: allowlisted tools, mutating connector tools denied by default,
agents never push, secrets in the Keychain, connector logs masked. Transcripts under
`logs/tasks/` contain whatever the agents read (ticket text, code) — treat that folder like
your own notes and do not share it.
