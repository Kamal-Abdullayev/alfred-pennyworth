import { useEffect, useState } from 'react'
import { api, type Transcript, type TranscriptEvent } from '../api'

export const KIND_CLASS: Record<string, string> = { tool_denied: 'fail', error: 'fail', timeout: 'fail', contract_invalid: 'fail', contract_missing: 'stuck', hook_error: 'stuck', done: 'pass', response: 'pass', init: 'answered', thinking: '', say: 'developer', tool_call: 'team_lead', tool_result: 'qa', request: 'answered', connector_shadowed: 'stuck' }

const short = (v: unknown, n = 160) => { const t = typeof v === 'string' ? v : JSON.stringify(v ?? ''); return t.length > n ? t.slice(0, n) + '…' : t }

/** One human line per event — what an engineer wants to skim. */
export function summarize(e: TranscriptEvent): string {
  const d = e.data as Record<string, unknown>
  switch (e.kind) {
    case 'say': return short(d.text, 220)
    case 'thinking': return short(d.text, 220)
    case 'tool_call': { const inp = d.input as Record<string, unknown> | undefined; const key = inp ? (inp.command ?? inp.file_path ?? inp.pattern ?? inp.query ?? inp.project ?? inp.path ?? inp) : ''; return `${d.tool}  ${short(key, 150)}` }
    case 'tool_result': return `${d.tool} → ${short(d.output, 150)}`
    case 'tool_denied': return `${d.tool} — denied: not in this role's allowlist`
    case 'init': return `MCP ${((d.mcp_servers as { name: string; status: string }[]) ?? []).map((m) => `${m.name}=${m.status}`).join(', ') || 'none'} · ${((d.tools as string[]) ?? []).length} tools · model ${d.model}`
    case 'request': return `${d.model} · ${(d.allowed_tools as string[] | undefined)?.length ?? 0} allow rules · ${d.workdir} · task: ${short(d.task, 120)}`
    case 'done': return `${d.turns} turns · ${Math.round(((d.duration_ms as number) ?? 0) / 1000)}s · est≈$${((d.cost_usd as number) ?? 0).toFixed(4)} · ${d.subtype}${d.is_error ? ' · ERROR' : ''}`
    case 'response': return `contract ${d.contract} · ${d.structured != null ? 'valid structured output' : 'NO structured output'}${(d.denied as string[] | undefined)?.length ? ` · denied ${(d.denied as string[]).length}` : ''}`
    case 'timeout': return `run exceeded ${d.after_minutes} minutes`
    case 'contract_invalid': return `output did not match contract ${d.contract}: ${short(d.error, 160)}`
    default: return short(d, 160)
  }
}

function expandedView(e: TranscriptEvent): string {
  const d = e.data as Record<string, unknown>
  if ((e.kind === 'say' || e.kind === 'thinking') && typeof d.text === 'string') return d.text
  if (e.kind === 'tool_result' && typeof d.output === 'string') return d.output
  return JSON.stringify(d, null, 2)
}

export function EventList({ events, showTask }: { events: (TranscriptEvent & { task_id?: string | null })[]; showTask?: boolean }) {
  const [expanded, setExpanded] = useState<number | null>(null)
  if (!events.length) return <div className="muted small" style={{ padding: 10 }}>nothing here</div>
  return (
    <div className="card" style={{ padding: 0, maxHeight: 520, overflow: 'auto' }}>
      {events.map((e, i) => (
        <div key={i} style={{ padding: '5px 10px', borderBottom: '1px solid var(--border)', cursor: 'pointer' }} onClick={() => setExpanded(expanded === i ? null : i)}>
          <span className="muted small mono">{e.ts.slice(11, 19)}</span>{' '}
          {showTask && e.task_id && <a className="mono small" href={`/chains/${e.task_id}`} onClick={(ev) => ev.stopPropagation()}>{e.task_id}</a>}{' '}
          <span className={`pill ${KIND_CLASS[e.kind] ?? ''}`}>{e.kind}</span>{' '}
          <span className="small" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{summarize(e)}</span>
          {expanded === i && <pre className="log small" style={{ marginTop: 6, maxHeight: 420, whiteSpace: 'pre-wrap' }}>{expandedView(e)}</pre>}
        </div>
      ))}
    </div>
  )
}

export const FILTERS = ['all', 'tools', 'say', 'thinking', 'tool_denied', 'init', 'done']

export function filterEvents<T extends TranscriptEvent>(events: T[], filter: string): T[] {
  return events.filter((e) => filter === 'all' || e.kind === filter || (filter === 'tools' && e.kind.startsWith('tool_')))
}

export function TranscriptPanel({ taskId }: { taskId: string }) {
  const [t, setT] = useState<Transcript | null>(null)
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('all')
  useEffect(() => { if (open && !t) api.transcript(taskId).then(setT).catch(() => setT({ task_id: taskId, source: null, count: 0, kinds: {}, events: [] })) }, [open, taskId, t])
  return (
    <details style={{ marginTop: 8 }} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="small">transcript{t ? ` · ${t.count} events` : ''}{t?.source ? <span className="muted"> · {t.source.split('/').slice(-2).join('/')}</span> : ''} — <a href={`/api/tasks/${taskId}/transcript?raw=1`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>raw JSONL</a></summary>
      {t && (
        <div style={{ marginTop: 6 }}>
          <div className="actions" style={{ flexWrap: 'wrap', marginBottom: 6 }}>
            {FILTERS.map((k) => <button key={k} className="btn-link" style={{ fontWeight: filter === k ? 700 : 400 }} onClick={() => setFilter(k)}>{k}{k !== 'all' && k !== 'tools' && t.kinds[k] ? ` ${t.kinds[k]}` : ''}</button>)}
          </div>
          {t.count === 0 ? <div className="muted small">no transcript on disk for this task (logs/ was cleared, or the run predates per-task logs)</div> : <EventList events={filterEvents(t.events, filter)} />}
        </div>
      )}
    </details>
  )
}
