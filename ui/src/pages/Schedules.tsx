import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ago, fmtTime, type Schedule } from '../api'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
type Draft = { name: string; kind: 'brief' | 'prompt'; role: string; prompt: string; project_dir: string; at_time: string; days: number[] }
const emptyDraft = (): Draft => ({ name: 'Morning brief', kind: 'brief', role: 'briefer', prompt: '', project_dir: '', at_time: '08:00', days: [0, 1, 2, 3, 4] })

function DayPicker({ value, onChange }: { value: number[]; onChange: (d: number[]) => void }) {
  return (
    <span className="actions">
      {DAYS.map((d, i) => <button key={d} type="button" className="btn-link" style={value.includes(i) ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
        onClick={() => onChange(value.includes(i) ? value.filter((x) => x !== i) : [...value, i].sort())}>{d}</button>)}
    </span>
  )
}

export default function SchedulesPage() {
  const nav = useNavigate()
  const [data, setData] = useState<{ schedules: Schedule[]; roles: string[]; me_name: string } | null>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft())
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [name, setName] = useState('')
  const load = () => api.schedules().then((d) => { setData(d); setName(d.me_name) }).catch((e) => setErr(String(e)))
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [])

  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(label); setErr(null); try { await fn(); await load() } catch (e) { setErr(String(e)) } finally { setBusy(null) } }

  return (
    <>
      <h1>Schedules</h1>
      <p className="muted small">Recurring jobs the supervisor fires at a set time on chosen days, while <span className="mono">run_all.sh</span> is running. If the laptop was asleep, a job still runs up to three hours late, once. A <b>brief</b> is the daily personal summary (calendar, Teams, mail, Jira) shown on Home and printable as PDF. A <b>prompt</b> is any question sent to a role, answered in a new chat. Each run is a normal chain: transcript, cost, memory proposals.</p>
      {err && <p className="err">{err}</p>}

      <div className="card" style={{ marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="small">The brief addresses you as</span>
        <input value={name} onChange={(e) => setName(e.target.value)} style={{ width: 160 }} />
        <button className="secondary" disabled={busy !== null || !name.trim() || name === data?.me_name} onClick={() => act('name', () => api.setMeName(name.trim()))}>Save</button>
        <span style={{ marginLeft: 'auto' }}><button onClick={() => setAdding(!adding)}>{adding ? 'Cancel' : '+ New schedule'}</button></span>
      </div>

      {adding && (
        <div className="card" style={{ marginBottom: 14, display: 'grid', gap: 10 }}>
          <div className="grid cols-2" style={{ gap: 10 }}>
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="name" />
            <div style={{ display: 'flex', gap: 8 }}>
              <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Draft['kind'], role: e.target.value === 'brief' ? 'briefer' : 'team_lead' })} style={{ width: 150 }}>
                <option value="brief">daily brief</option><option value="prompt">prompt</option>
              </select>
              <select value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })}>{(data?.roles ?? []).map((r) => <option key={r}>{r}</option>)}</select>
              <input type="time" value={draft.at_time} onChange={(e) => setDraft({ ...draft, at_time: e.target.value })} style={{ width: 120 }} />
            </div>
          </div>
          <DayPicker value={draft.days} onChange={(days) => setDraft({ ...draft, days })} />
          <textarea value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} style={{ minHeight: 70 }}
            placeholder={draft.kind === 'brief' ? 'optional extra focus for the brief, e.g. "watch the 2FA epic and anything from Zeynep"' : 'the question to ask, e.g. "Which of my open MRs have a red pipeline this morning, and why?"'} />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input className="mono" value={draft.project_dir} onChange={(e) => setDraft({ ...draft, project_dir: e.target.value })} placeholder="repository path (optional, for prompts about local code)" />
            <button disabled={busy !== null || !draft.name.trim() || draft.days.length === 0 || (draft.kind === 'prompt' && !draft.prompt.trim())}
              onClick={() => act('create', async () => { await api.createSchedule({ ...draft, project_dir: draft.project_dir || null, days: draft.days.join(',') }); setAdding(false); setDraft(emptyDraft()) })}>Create</button>
          </div>
        </div>
      )}

      {data?.schedules.length === 0 && <div className="card muted small">No schedules yet. Start with a morning brief: 08:00, Monday to Friday.</div>}
      {data?.schedules.map((s) => (
        <div key={s.id} className="card" style={{ marginBottom: 10, opacity: s.enabled ? 1 : 0.6 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <b>{s.name}</b>
            <span className={`pill ${s.kind === 'brief' ? 'answered' : ''}`}>{s.kind}</span>
            <span className={`pill ${s.role}`}>{s.role}</span>
            <span className="mono small">{s.at_time}</span>
            <span className="small muted">{s.days.split(',').filter(Boolean).map((d) => DAYS[Number(d)]).join(' ')}</span>
            <span className="small muted">· next {s.enabled && s.next_run_at ? fmtTime(s.next_run_at) : '—'}{s.last_run_at ? ` · last ${ago(s.last_run_at)}` : ''}</span>
            <span className="actions" style={{ marginLeft: 'auto' }}>
              <button className="btn-link" disabled={busy !== null} onClick={() => act('run', async () => { const r = await api.runSchedule(s.id); nav(`/ask/${r.conversation_id}`) })}>▶ run now</button>
              <button className="btn-link" disabled={busy !== null} onClick={() => act('toggle', () => api.updateSchedule(s.id, { enabled: !s.enabled }))}>{s.enabled ? 'pause' : 'enable'}</button>
              <button className="btn-link" disabled={busy !== null} onClick={() => act('delete', () => api.deleteSchedule(s.id))}>delete</button>
            </span>
          </div>
          {s.prompt && <div className="small muted" style={{ marginTop: 4 }}>{s.prompt}</div>}
          {s.runs.length > 0 && (
            <div className="small" style={{ marginTop: 8, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              {s.runs.map((r) => <a key={r.id} href={r.conversation_id ? `/ask/${r.conversation_id}` : `/chains/${r.chain_id}`} onClick={(e) => { e.preventDefault(); nav(r.conversation_id ? `/ask/${r.conversation_id}` : `/chains/${r.chain_id}`) }}><span className={`pill ${r.status}`}>{r.status}</span> {fmtTime(r.created_at)}</a>)}
            </div>
          )}
        </div>
      ))}
    </>
  )
}
