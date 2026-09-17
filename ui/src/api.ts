export type Task = {
  id: string; chain_id: string; parent_id: string | null; role: string; title: string; body: string
  status: 'open' | 'claimed' | 'done' | 'failed' | 'stuck' | string
  result: string | null; structured: unknown | null
  project_dir: string | null; worktree: string | null; base_sha: string | null
  iteration: number; created_by: string; claimed_by: string | null
  created_at: number; claimed_at: number | null; finished_at: number | null
}
export type Kind = 'plan' | 'answer' | 'brief' | 'watch'
export type Chain = {
  chain_id: string; title: string; status: 'running' | 'passed' | 'failed' | 'stuck' | 'done' | 'answered' | string
  kind: Kind
  created_at: number; updated_at: number; cost_usd: number; roles: Record<string, number>
  project_dir: string | null; iteration: number; tasks: number
}
export type Finding = {
  id: number; chain_id: string; task_id: string; iteration: number; source: string
  file: string; line: number | null; severity: string; claim: string; evidence: string; status: string; created_at: number
}
export type Usage = {
  id: number; task_id: string; chain_id: string; role: string; model: string
  input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number
  cost_usd: number; turns: number | null; duration_ms: number | null; created_at: number
}
export type Turn = {
  id: number; task_id: string; chain_id: string; role: string; turn_index: number; model: string; message_id: string | null
  input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number
  est_cost_usd: number; tools: string[]; text_chars: number; created_at: number
}
export type ChainDetail = {
  root: Task; tasks: Task[]; findings: Finding[]; usage: Usage[]; turns: Turn[]; code_links: CodeLink[]; assets: Asset[]; canvas_url: string
  diff: string | null; log: string | null; branch: string; status: string; kind: Kind; cost_usd: number
  notes: TaskNote[]; memories: Memory[]
}

// --- the team lead's structured output ---------------------------------------
export type Subtask = { id: string; title: string; description: string; acceptance: string[]; depends_on: string[] }
export type Plan = { summary: string; parallelism: number; subtasks: Subtask[] }
export type CodeRef = { path: string; start_line: number; end_line: number; symbol: string | null; language: string; snippet: string; why: string }
export type Source = { repo_path: string | null; gitlab_project?: string | null; branch: string | null; commit: string | null }
export type CodeLink = { path: string; abs: string | null; exists: boolean; idea: string | null; web: string | null }
export type FlowRow = { ts: string; role: string; action: string; task: string; detail: string }
export type Citation = { source: string; ref: string; url: string | null }
export type Diagram = { title: string; description: string; mermaid: string }
export type Asset = { task_id: string; name: string; kind: 'image' | 'scene' | 'file'; bytes: number; url: string }
export type Answer = { answer: string; code: CodeRef[]; diagrams?: Diagram[]; source: Source; citations: Citation[]; confidence: 'low' | 'medium' | 'high' }
export type MemoryKind = 'decision' | 'fact' | 'convention' | 'glossary' | 'person' | 'question' | 'todo'
export type MemoryProposal = { kind: MemoryKind; title: string; body: string; tags: string[] }
export type LeadOutput = { kind: Kind; plan: Plan | null; answer: Answer | null; memory_proposals?: MemoryProposal[] }
export type Memory = {
  id: string; scope: 'project' | 'global'; project_key: string; kind: MemoryKind; title: string; body: string; tags: string[]
  source: string; status: 'active' | 'proposed' | 'retired'; chain_id: string | null; created_at: number; updated_at: number; accepted_at: number | null
}
export type MemoryProject = { project_key: string; active: number; proposed: number; retired: number; updated_at: number | null; dirs: string[]; export: string }
export type HomeData = {
  now: number
  stats: { working: number; queued: number; workers: number; need_human: number; today_cost: number; today_runs: number; week_cost: number; week_runs: number; proposals: number }
  workers: { pid: number; role: string; ephemeral: boolean; since: number | null; task: { id: string; chain_id: string; title: string; chain_title: string; claimed_at: number | null; project_dir: string | null } | null }[]
  queue: { id: string; chain_id: string; role: string; title: string }[]
  active: { chain_id: string; title: string; tickets: string[]; project: string | null; conversation_id: string | null; started_at: number; cost_usd: number; subtasks: number | null
    tasks: { id: string; role: string; title: string; status: string; iteration: number; claimed_by: string | null; claimed_at: number | null; created_at: number; finished_at: number | null }[] }[]
  jira_url: string | null
  by_day: { day: string; role: string; cost: number; runs: number }[]
  recent_chains: Chain[]; need_human: Chain[]; failed_scheduled: Chain[]
  flows: Flow[]
  todos: { id: string; kind: string; title: string; project_key: string }[]
  conversations: Conversation[]
  me_name: string
  brief_schedule: Schedule | null
  watch_schedule: Schedule | null
  alerts: Alert[]
}
export type MyJira = { issues: { key: string; status: string; type: string; priority: string; assignee: string; summary: string; url: string | null }[]; error?: string; at: number; stale?: boolean }
export type MyCalendar = { day: string; meetings: { start: string; end: string; title: string; location: string | null; join_url: string | null; organizer: string | null; attendees: number; response: string | null }[]; note?: string | null; error?: string; at: number; stale?: boolean; cost_usd?: number }
export type Schedule = { id: string; name: string; kind: 'brief' | 'prompt' | 'watch'; role: string; prompt: string; project_dir: string | null; at_time: string; days: string; enabled: number; grace_min: number; every_min: number | null; active_from: string | null; active_to: string | null; last_run_at: number | null; last_task: string | null; next_run_at: number | null; runs: { id: string; chain_id: string; conversation_id: string | null; status: string; created_at: number; finished_at: number | null; role: string }[] }
export type BriefItem = { title: string; detail: string; url: string | null }
export type BriefDoc = { date: string; headline: string; segments: BriefItem[]; needs_attention: BriefItem[]; resolved: BriefItem[]
  meetings: { start: string; end: string; title: string; where: string | null; note: string | null }[]
  tickets: { key: string; title: string; status: string; note: string | null; url: string | null }[]
  unread_emails: { sender: string; subject: string; when: string; count: number }[]; footer: string | null }
export type BriefRun = { task_id: string; chain_id: string; conversation_id: string | null; status: string; created_at: number; finished_at: number | null; cost_usd: number; brief: BriefDoc }
export type BriefLatest = { latest: BriefRun | null; running: { id: string; chain_id: string; status: string; created_at: number } | null }
export type FlowTask = { id: string; role: string; title: string; status: string; iteration: number; claimed_by: string | null; claimed_at: number | null; created_at: number; finished_at: number | null }
export type Flow = { chain_id: string; title: string; tickets: string[]; project: string | null; conversation_id: string | null; started_at: number; finished_at: number | null; cost_usd: number; status: string; kind: Kind; verdict: string | null; tasks: FlowTask[] }
export type Alert = { id: string; key: string; severity: 'info' | 'warn' | 'urgent'; title: string; detail: string; url: string | null; source: string | null; chain_id: string | null; status: string; created_at: number }
export type TaskNote = { id: number; task_id: string; chain_id: string; role: string; note: string; created_at: number }

/** Older rows stored a bare Plan; normalise everything to LeadOutput. */
export function leadOutput(structured: unknown): LeadOutput | null {
  if (!structured || typeof structured !== 'object') return null
  const s = structured as Record<string, unknown>
  if (s.kind === 'answer' || s.kind === 'plan') return s as unknown as LeadOutput
  if ('subtasks' in s) return { kind: 'plan', plan: s as unknown as Plan, answer: null }
  return null
}

export type Agent = {
  role: string; name: string; model: string; contract: string; permission_mode: string
  max_minutes: number; max_turns: number | null; builtin_tools: string[]; allowed_tools: string[]
  mcp_servers: string[]; system_prompt: string; path: string
  account_connectors: boolean; workers: { min: number; max: number }; logging: Record<string, unknown>; raw: Record<string, unknown>
}
export type AgentsResponse = { agents: Agent[]; options: { models: string[]; contracts: string[]; builtin_tools: string[]; permission_modes: string[] } }
export type AgentIn = { model: string; contract: string; system_prompt: string; builtin_tools: string[]; allowed_tools: string[]; permission_mode: string; max_minutes: number; max_turns: number | null; account_connectors: boolean; workers: { min: number; max: number } }
export type Worker = { pid: number; role: string; ephemeral: number; started_at: number; last_seen: number; current_task: string | null; current_title: string | null; current_chain: string | null; host: string | null }
export type WorkersResponse = { workers: Worker[]; open: Record<string, number>; max_workers: number }
export type UsageSummary = {
  days: number
  by_day: { day: string; role: string; model: string; cost: number; in_tok: number; out_tok: number; runs: number }[]
  by_role: { role: string; cost: number; runs: number; in_tok: number; out_tok: number }[]
  by_model: { model: string; cost: number; runs: number }[]
  by_chain: { chain_id: string; title: string; cost: number; runs: number; in_tok: number; out_tok: number; started: number }[]
  totals: { cost: number; runs: number; in_tok: number; out_tok: number }
  note: string
}
export type FieldState = { secret: boolean; set: boolean; value?: string | null }
export type ConnectorTool = { connector: string; tool: string; description: string | null; mutates: number; source: 'annotation' | 'heuristic' | 'user' }
export type LogEntry = { id: number; connector: string; level: 'info' | 'warn' | 'error'; event: string; message: string; data: Record<string, unknown> | null; created_at: number }
export type TranscriptEvent = { ts: string; kind: string; data: Record<string, unknown> }
export type Transcript = { task_id: string; source: string | null; count: number; kinds: Record<string, number>; events: TranscriptEvent[] }
export type LogFile = { name: string; bytes: number; mtime: number }
export type Conversation = { id: string; title: string; project_dir: string | null; created_at: number; updated_at: number; turns: number; cost_usd: number; last_status: string | null; scheduled?: number }
export type ConversationTurn = { chain_id: string; question: string; status: string; kind: Kind; created_at: number; finished_at: number | null; cost_usd: number; structured: unknown | null; result: string | null; drew?: boolean; resumed?: boolean }
export type ConversationDetail = { id: string; title: string; project_dir: string | null; created_at: number; updated_at: number; turns: ConversationTurn[] }
export type Connector = {
  name: string; template: string | null; kind: 'stdio' | 'http' | 'sse' | 'claude-ai'; command: string | null; args: string[]; url: string | null
  provider: 'configured' | 'claude-account'; masked_config: Record<string, unknown> | null; trust_writes: number
  env: Record<string, FieldState>; headers: Record<string, FieldState>; enabled: number; note: string | null
  last_test_at: number | null; last_test_status: 'ok' | 'failed' | null; last_test_error: string | null
  roles: string[]; tools: ConnectorTool[]; tool_count: number; mutating: number; yaml_used_by: string[]
}
export type Registered = { name: string; source: 'claude-code' | 'claude-settings' | 'claude-desktop'; kind: string; command: string }
export type ConnectorsResponse = { connectors: Connector[]; registered: Registered[]; roles: string[]; yaml: { name: string; used_by: string[] }[] }
export type TemplateField = { secret: boolean; default: string | null; help: string }
export type Template = { label: string; kind: 'stdio' | 'http' | 'sse'; command: string | null; args: string[]; url?: string; env: Record<string, TemplateField>; headers: Record<string, TemplateField>; note: string }
export type ConnectorIn = { name: string; template: string | null; kind: string; command: string | null; args: string[]; url: string | null; env: Record<string, { secret: boolean; value: string | null }>; headers: Record<string, { secret: boolean; value: string | null }>; note: string | null; enabled: boolean }


async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { 'content-type': 'application/json' }, ...init })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const api = {
  chains: () => j<Chain[]>('/api/chains'),
  chain: (id: string) => j<ChainDetail>(`/api/chains/${id}`),
  tasks: () => j<Task[]>('/api/tasks'),
  agents: () => j<AgentsResponse>('/api/agents'),
  updateAgent: (role: string, a: AgentIn) => j<unknown>(`/api/agents/${role}`, { method: 'PUT', body: JSON.stringify(a) }),
  createAgent: (role: string, clone_from: string) => j<unknown>('/api/agents', { method: 'POST', body: JSON.stringify({ role, clone_from }) }),
  deleteAgent: (role: string) => j<unknown>(`/api/agents/${role}`, { method: 'DELETE' }),
  workers: () => j<WorkersResponse>('/api/workers'),
  setMaxWorkers: (n: number) => j<unknown>('/api/settings/max_workers', { method: 'PUT', body: JSON.stringify({ max_workers: n }) }),
  connectors: () => j<ConnectorsResponse>('/api/connectors'),
  templates: () => j<Record<string, Template>>('/api/connectors/templates'),
  saveConnector: (c: ConnectorIn) => j<Connector>('/api/connectors', { method: 'POST', body: JSON.stringify(c) }),
  importConnector: (source: string, name: string) => j<Connector>('/api/connectors/import', { method: 'POST', body: JSON.stringify({ source, name }) }),
  testConnector: (name: string) => j<{ ok: boolean; error: string | null; tools?: ConnectorTool[]; mutating?: number; connector: Connector }>(`/api/connectors/${name}/test`, { method: 'POST' }),
  setConnectorRoles: (name: string, roles: string[]) => j<{ roles: string[] }>(`/api/connectors/${name}/roles`, { method: 'PUT', body: JSON.stringify({ roles }) }),
  setToolMutates: (name: string, tool: string, mutates: boolean) => j<unknown>(`/api/connectors/${name}/tools/${encodeURIComponent(tool)}`, { method: 'PUT', body: JSON.stringify({ mutates }) }),
  canvas: () => j<{ url: string; configured: boolean }>('/api/canvas'),
  stopTask: (id: string) => j<{ task_id: string; result: string }>(`/api/tasks/${id}/stop`, { method: 'POST' }),
  stopChain: (id: string) => j<{ chain_id: string; tasks: Record<string, string> }>(`/api/chains/${id}/stop`, { method: 'POST' }),
  canvasShow: (task_id: string, name: string) => j<{ ok: boolean; message: string }>('/api/canvas/show', { method: 'POST', body: JSON.stringify({ task_id, name }) }),
  canvasClear: () => j<{ ok: boolean }>('/api/canvas/clear', { method: 'POST' }),
  setTrustWrites: (name: string, trust: boolean) => j<unknown>(`/api/connectors/${name}/trust_writes`, { method: 'PUT', body: JSON.stringify({ trust_writes: trust }) }),
  setConnectorEnabled: (name: string, enabled: boolean) => j<unknown>(`/api/connectors/${name}/enabled`, { method: 'PUT', body: JSON.stringify({ enabled }) }),
  deleteConnector: (name: string) => j<unknown>(`/api/connectors/${name}`, { method: 'DELETE' }),
  connectorLog: (name: string) => j<LogEntry[]>(`/api/connectors/${name}/log?limit=150`),
  transcript: (taskId: string) => j<Transcript>(`/api/tasks/${taskId}/transcript`),
  logs: () => j<{ dir: string; files: LogFile[] }>('/api/logs'),
  flowRows: (tail = 300) => j<{ rows: FlowRow[] }>(`/api/logs/flow?tail=${tail}`),
  logEvents: (name: string, tail = 400, kind?: string) => j<{ name: string; total: number; kinds: Record<string, number>; tasks: Record<string, number>; events: (TranscriptEvent & { task_id: string | null })[] }>(`/api/logs/events?name=${encodeURIComponent(name)}&tail=${tail}${kind ? `&kind=${kind}` : ''}`),
  logFile: (name: string, tail = 500) => j<{ name: string; total_lines: number; lines: string[] }>(`/api/logs/file?name=${encodeURIComponent(name)}&tail=${tail}`),
  discoverConnectors: () => j<{ account_servers: string[]; tools: Record<string, string[]>; connectors: Connector[] }>('/api/connectors/discover', { method: 'POST' }),
  usage: (days = 30) => j<UsageSummary>(`/api/usage/summary?days=${days}`),
  createJob: (body: string, project_dir?: string, conversation_id?: string) =>
    j<{ task_id: string | null; chain_id: string | null; conversation_id: string | null; remembered?: Memory }>('/api/jobs', { method: 'POST', body: JSON.stringify({ body, project_dir: project_dir || null, conversation_id: conversation_id || null }) }),
  home: () => j<HomeData>('/api/home'),
  schedules: () => j<{ schedules: Schedule[]; roles: string[]; me_name: string }>('/api/schedules'),
  createSchedule: (s: { name: string; kind: string; role: string; prompt: string; project_dir: string | null; at_time: string; days: string; every_min?: number | null; active_from?: string | null; active_to?: string | null }) => j<Schedule>('/api/schedules', { method: 'POST', body: JSON.stringify(s) }),
  updateSchedule: (id: string, p: Partial<{ name: string; enabled: boolean; at_time: string; days: string; prompt: string; role: string; every_min: number; active_from: string; active_to: string }>) => j<Schedule>(`/api/schedules/${id}`, { method: 'PATCH', body: JSON.stringify(p) }),
  deleteSchedule: (id: string) => j<{ deleted: string }>(`/api/schedules/${id}`, { method: 'DELETE' }),
  runSchedule: (id: string) => j<{ task_id: string; chain_id: string; conversation_id: string }>(`/api/schedules/${id}/run`, { method: 'POST' }),
  alerts: () => j<Alert[]>('/api/alerts'),
  dismissAlert: (id?: string) => j<{ dismissed: number }>('/api/alerts/dismiss', { method: 'POST', body: JSON.stringify(id ? { id } : { all: true }) }),
  settings: () => j<Record<string, string | null>>('/api/settings'),
  setSetting: (key: string, value: string) => j<Record<string, string>>(`/api/settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) }),
  setMeName: (name: string) => j<{ me_name: string }>('/api/settings/me_name', { method: 'PUT', body: JSON.stringify({ name }) }),
  briefLatest: () => j<BriefLatest>('/api/briefs/latest'),
  brief: (taskId: string) => j<BriefRun>(`/api/briefs/${taskId}`),
  myJira: (refresh = false) => j<MyJira>(`/api/me/jira${refresh ? '?refresh=1' : ''}`),
  myCalendar: (refresh = false) => j<MyCalendar>(`/api/me/calendar${refresh ? '?refresh=1' : ''}`),
  doctor: () => j<{ ok: boolean; checks: { name: string; ok: boolean; required: boolean; detail: string; fix: string | null }[] }>('/api/doctor'),
  memoryProjects: () => j<MemoryProject[]>('/api/memory/projects'),
  memoryKey: (project_dir: string) => j<{ project_key: string }>(`/api/memory/key?project_dir=${encodeURIComponent(project_dir)}`),
  memoryList: (p: { project_key?: string; status?: string; q?: string; chain_id?: string }) => {
    const qs = Object.entries(p).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join('&')
    return j<Memory[]>(`/api/memory${qs ? `?${qs}` : ''}`)
  },
  memoryCreate: (m: { project_key: string; kind: MemoryKind; title: string; body: string; tags: string[]; status?: string }) => j<Memory>('/api/memory', { method: 'POST', body: JSON.stringify(m) }),
  memoryUpdate: (id: string, p: Partial<Pick<Memory, 'kind' | 'title' | 'body' | 'tags' | 'status' | 'project_key'>>) => j<Memory>(`/api/memory/${id}`, { method: 'PATCH', body: JSON.stringify(p) }),
  memoryDelete: (id: string) => j<{ deleted: string }>(`/api/memory/${id}`, { method: 'DELETE' }),
  memoryIntake: (notes: string, project_key?: string, project_dir?: string) => j<{ task_id: string; chain_id: string; conversation_id: string; project_key: string }>('/api/memory/intake', { method: 'POST', body: JSON.stringify({ notes, project_key: project_key || null, project_dir: project_dir || null }) }),
  chainNotes: (chain_id: string) => j<TaskNote[]>(`/api/chains/${chain_id}/notes`),
  conversations: () => j<Conversation[]>('/api/conversations'),
  conversation: (id: string) => j<ConversationDetail>(`/api/conversations/${id}`),
  renameConversation: (id: string, title: string) => j<unknown>(`/api/conversations/${id}`, { method: 'PUT', body: JSON.stringify({ title }) }),
  deleteConversation: (id: string) => j<unknown>(`/api/conversations/${id}`, { method: 'DELETE' }),
  chainAction: (chain: string, body: { action: 'requeue' | 'close' | 'dispatch'; reason?: string; project_dir?: string }) =>
    j<Record<string, unknown>>(`/api/chains/${chain}/actions`, { method: 'POST', body: JSON.stringify(body) }),
  addFinding: (chain: string, f: { file: string; line: number | null; severity: string; claim: string; evidence: string }) =>
    j<{ task_id: string; iteration: number }>(`/api/chains/${chain}/findings`, { method: 'POST', body: JSON.stringify(f) }),
}

/** Live view of one run: replay + tail of its event log, then the finished chain. */
export function streamTask(taskId: string, h: { status?: (s: { status: string | null; role: string | null; chain_id: string | null }) => void; log?: (e: TranscriptEvent) => void; end?: (d: ChainDetail) => void; error?: () => void }) {
  const es = new EventSource(`/api/tasks/${taskId}/stream`)
  es.addEventListener('status', (e) => h.status?.(JSON.parse((e as MessageEvent).data)))
  es.addEventListener('log', (e) => h.log?.(JSON.parse((e as MessageEvent).data)))
  es.addEventListener('end', (e) => { h.end?.(JSON.parse((e as MessageEvent).data)); es.close() })
  es.onerror = () => { h.error?.() }
  return () => es.close()
}

/** Subscribe to the board's SSE stream. Returns an unsubscribe function. */
export function subscribe(handlers: { tasks?: (t: Task[]) => void; chains?: (c: Chain[]) => void; flow?: (line: string) => void }) {
  const es = new EventSource('/api/events')
  if (handlers.tasks) es.addEventListener('tasks', (e) => handlers.tasks!(JSON.parse((e as MessageEvent).data)))
  if (handlers.chains) es.addEventListener('chains', (e) => handlers.chains!(JSON.parse((e as MessageEvent).data)))
  if (handlers.flow) es.addEventListener('flow', (e) => handlers.flow!(JSON.parse((e as MessageEvent).data)))
  return () => es.close()
}

export const fmtCost = (n: number | null | undefined) => `est≈$${(n ?? 0).toFixed(n && n < 0.01 ? 4 : 2)}`
export const fmtTime = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleString() : '—')
export const ago = (t: number | null | undefined) => {
  if (!t) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
