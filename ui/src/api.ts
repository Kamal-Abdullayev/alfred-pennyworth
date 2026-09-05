export type Task = {
  id: string; chain_id: string; parent_id: string | null; role: string; title: string; body: string
  status: 'open' | 'claimed' | 'done' | 'failed' | 'stuck' | string
  result: string | null; structured: unknown | null
  project_dir: string | null; worktree: string | null; base_sha: string | null
  iteration: number; created_by: string; claimed_by: string | null
  created_at: number; claimed_at: number | null; finished_at: number | null
}
export type Chain = {
  chain_id: string; title: string; status: 'running' | 'passed' | 'failed' | 'stuck' | 'done' | string
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
export type ChainDetail = {
  root: Task; tasks: Task[]; findings: Finding[]; usage: Usage[]
  diff: string | null; log: string | null; branch: string; status: string; cost_usd: number
}
export type Agent = {
  role: string; name: string; model: string; contract: string; permission_mode: string
  max_minutes: number; max_turns: number | null; builtin_tools: string[]; allowed_tools: string[]
  mcp_servers: string[]; system_prompt: string; path: string
}
export type UsageSummary = {
  days: number
  by_day: { day: string; role: string; model: string; cost: number; in_tok: number; out_tok: number; runs: number }[]
  by_role: { role: string; cost: number; runs: number; in_tok: number; out_tok: number }[]
  by_model: { model: string; cost: number; runs: number }[]
  totals: { cost: number; runs: number; in_tok: number; out_tok: number }
  note: string
}
export type Connector = { name: string; kind: string; command: string; used_by: string[]; registered_in: string[] }

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { 'content-type': 'application/json' }, ...init })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const api = {
  chains: () => j<Chain[]>('/api/chains'),
  chain: (id: string) => j<ChainDetail>(`/api/chains/${id}`),
  tasks: () => j<Task[]>('/api/tasks'),
  agents: () => j<Agent[]>('/api/agents'),
  connectors: () => j<Connector[]>('/api/connectors'),
  usage: (days = 30) => j<UsageSummary>(`/api/usage/summary?days=${days}`),
  createJob: (body: string, project_dir?: string) =>
    j<{ task_id: string; chain_id: string }>('/api/jobs', { method: 'POST', body: JSON.stringify({ body, project_dir: project_dir || null }) }),
  addFinding: (chain: string, f: { file: string; line: number | null; severity: string; claim: string; evidence: string }) =>
    j<{ task_id: string; iteration: number }>(`/api/chains/${chain}/findings`, { method: 'POST', body: JSON.stringify(f) }),
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
