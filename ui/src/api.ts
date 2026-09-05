export type Task = {
  id: string; chain_id: string; parent_id: string | null; role: string; title: string; body: string
  status: 'open' | 'claimed' | 'done' | 'failed' | 'stuck' | string
  result: string | null; structured: unknown | null
  project_dir: string | null; worktree: string | null; base_sha: string | null
  iteration: number; created_by: string; claimed_by: string | null
  created_at: number; claimed_at: number | null; finished_at: number | null
}
export type Kind = 'plan' | 'answer'
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
  root: Task; tasks: Task[]; findings: Finding[]; usage: Usage[]; turns: Turn[]; code_links: CodeLink[]
  diff: string | null; log: string | null; branch: string; status: string; kind: Kind; cost_usd: number
}

// --- the team lead's structured output ---------------------------------------
export type Subtask = { id: string; title: string; description: string; acceptance: string[]; depends_on: string[] }
export type Plan = { summary: string; parallelism: number; subtasks: Subtask[] }
export type CodeRef = { path: string; start_line: number; end_line: number; symbol: string | null; language: string; snippet: string; why: string }
export type Source = { repo_path: string | null; gitlab_project?: string | null; branch: string | null; commit: string | null }
export type CodeLink = { path: string; abs: string | null; exists: boolean; idea: string | null; web: string | null }
export type FlowRow = { ts: string; role: string; action: string; task: string; detail: string }
export type Citation = { source: string; ref: string; url: string | null }
export type Answer = { answer: string; code: CodeRef[]; source: Source; citations: Citation[]; confidence: 'low' | 'medium' | 'high' }
export type LeadOutput = { kind: Kind; plan: Plan | null; answer: Answer | null }

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
export type Conversation = { id: string; title: string; project_dir: string | null; created_at: number; updated_at: number; turns: number; cost_usd: number; last_status: string | null }
export type ConversationTurn = { chain_id: string; question: string; status: string; kind: Kind; created_at: number; finished_at: number | null; cost_usd: number; structured: unknown | null; result: string | null }
export type ConversationDetail = { id: string; title: string; project_dir: string | null; created_at: number; updated_at: number; turns: ConversationTurn[] }
export type Connector = {
  name: string; template: string | null; kind: 'stdio' | 'http' | 'sse' | 'claude-ai'; command: string | null; args: string[]; url: string | null
  provider: 'configured' | 'claude-account'; masked_config: Record<string, unknown> | null
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
    j<{ task_id: string; chain_id: string; conversation_id: string }>('/api/jobs', { method: 'POST', body: JSON.stringify({ body, project_dir: project_dir || null, conversation_id: conversation_id || null }) }),
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
