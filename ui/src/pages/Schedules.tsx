import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmtTime, type Schedule } from '../api'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
type Draft = { name: string; kind: 'brief' | 'prompt'; role: string; prompt: string; project_dir: string; at_time: string; days: number[] }
const emptyDraft = (): Draft => ({ name: 'Morning brief', kind: 'brief', role: 'briefer', prompt: '', project_dir: '', at_time: '08:00', days: [0, 1, 2, 3, 4] })
type Meta = { schedules: Schedule[]; roles: string[]; me_name: string; tz: { name: string; abbr: string; offset: string; now: string }; now: number; supervisor_alive: boolean }

function until(ts: number | null | undefined, now: number) {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(ts - now))
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))} min`
  if (s < 86400) return `in ${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`
  return `in ${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
}

/** Minutes a run started after its slot that day (0 for on time; null when not a scheduled slot). */
function lateness(run: { created_at: number }, s: Schedule) {
  const d = new Date(run.created_at * 1000)
  const [hh, mm] = s.at_time.split(':').map(Number)
  const due = new Date(d); due.setHours(hh, mm, 0, 0)
  const diff = Math.round((d.getTime() - due.getTime()) / 60000)
  return diff >= 0 && diff <= s.grace_min ? diff : null
}

function DayPicker({ value, onChange }: { value: number[]; onChange: (d: number[]) => void }) {
  return (
    <span className="daypick">
      {DAYS.map((d, i) => <button key={d} type="button" className={value.includes(i) ? 'on' : ''} onClick={() => onChange(value.includes(i) ? value.filter((x) => x !== i) : [...value, i].sort())}>{d}</button>)}
    </span>
  )
}

function ScheduleCard({ s, meta, busy, act }: { s: Schedule; meta: Meta; busy: string | null; act: (label: string, fn: () => Promise<unknown>) => Promise<void> }) {
  const nav = useNavigate()
  const [edit, setEdit] = useState(false)
  const [d, setD] = useState<Draft>({ name: s.name, kind: s.kind, role: s.role, prompt: s.prompt, project_dir: s.project_dir ?? '', at_time: s.at_time, days: s.days.split(',').filter(Boolean).map(Number) })
  const days = s.days.split(',').filter(Boolean).map(Number)
  const lastLate = s.runs[0] ? lateness(s.runs[0], s) : null
  return (
    <div className={`sched ${s.enabled ? '' : 'paused'}`}>
      <div className="sched-time">
        <div className="big">{s.at_time}</div>
        <div className="week">{DAYS.map((dn, i) => <span key={dn} className={days.includes(i) ? 'on' : ''} title={dn}>{dn[0]}</span>)}</div>
        <div className="muted small">{s.enabled ? until(s.next_run_at, meta.now) : 'paused'}</div>
      </div>
      <div className="sched-body">
        <div className="row">
          <b style={{ fontSize: 15 }}>{s.name}</b>
          <span className={`pill ${s.kind === 'brief' ? 'answered' : ''}`}>{s.kind === 'brief' ? 'daily brief' : 'prompt'}</span>
          <span className={`pill ${s.role}`}>{s.role}</span>
          {!s.enabled && <span className="pill">paused</span>}
          <span className="actions" style={{ marginLeft: 'auto' }}>
            <button className="btn-link" disabled={busy !== null} onClick={() => act('run', async () => { const r = await api.runSchedule(s.id); nav(`/ask/${r.conversation_id}`) })}>▶ run now</button>
            <button className="btn-link" onClick={() => setEdit(!edit)}>{edit ? 'close' : 'edit'}</button>
            <button className="btn-link" disabled={busy !== null} onClick={() => act('toggle', () => api.updateSchedule(s.id, { enabled: !s.enabled }))}>{s.enabled ? 'pause' : 'enable'}</button>
            <button className="btn-link" disabled={busy !== null} onClick={() => act('delete', () => api.deleteSchedule(s.id))}>delete</button>
          </span>
        </div>
        <div className="small muted" style={{ marginTop: 4 }}>
          {s.kind === 'brief' ? 'Reads calendar, Teams, mail and Jira, writes the day on one page.' : s.prompt}
          {s.kind === 'brief' && s.prompt ? <> Focus: <i>{s.prompt}</i></> : null}
          {s.project_dir ? <> · <span className="mono">{s.project_dir}</span></> : null}
        </div>
        <div className="small" style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="muted">Next</span><b>{s.enabled && s.next_run_at ? fmtTime(s.next_run_at) : '—'}</b>
          <span className="muted">· Last</span>
          {s.last_run_at ? <b>{fmtTime(s.last_run_at)}</b> : <span className="muted">never</span>}
          {lastLate !== null && lastLate > 5 && <span className="pill stuck" title="Alfred was not running at the scheduled time; the job ran when the supervisor came back, inside the grace window">late by {lastLate} min</span>}
          {lastLate !== null && lastLate <= 5 && <span className="pill done">on time</span>}
        </div>
        {s.runs.length > 0 && (
          <div className="runs">
            {s.runs.map((r) => {
              const late = lateness(r, s)
              return <a key={r.id} className={`run ${r.status}`} href={r.conversation_id ? `/ask/${r.conversation_id}` : `/chains/${r.chain_id}`} onClick={(e) => { e.preventDefault(); nav(r.conversation_id ? `/ask/${r.conversation_id}` : `/chains/${r.chain_id}`) }}
                title={`${fmtTime(r.created_at)} · ${r.status}${late ? ` · ${late} min late` : ''}`}><span className="d" /> <span>{new Date(r.created_at * 1000).toLocaleDateString(undefined, { weekday: 'short' })} {new Date(r.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span><span className="muted">{r.status === 'done' ? (s.kind === 'brief' ? 'brief' : 'answer') : r.status}</span></a>
            })}
          </div>
        )}
        {edit && (
          <div className="sched-edit">
            <div className="grid cols-2" style={{ gap: 8 }}>
              <input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} />
              <div style={{ display: 'flex', gap: 8 }}>
                <select value={d.role} onChange={(e) => setD({ ...d, role: e.target.value })}>{meta.roles.map((r) => <option key={r}>{r}</option>)}</select>
                <input type="time" value={d.at_time} onChange={(e) => setD({ ...d, at_time: e.target.value })} style={{ width: 120 }} />
              </div>
            </div>
            <DayPicker value={d.days} onChange={(days) => setD({ ...d, days })} />
            <textarea value={d.prompt} onChange={(e) => setD({ ...d, prompt: e.target.value })} style={{ minHeight: 60 }} placeholder={s.kind === 'brief' ? 'optional extra focus' : 'the question'} />
            <div><button disabled={busy !== null || d.days.length === 0} onClick={() => act('save', async () => { await api.updateSchedule(s.id, { name: d.name.trim(), role: d.role, at_time: d.at_time, days: d.days.join(','), prompt: d.prompt }); setEdit(false) })}>Save</button></div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function SchedulesPage() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft())
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [name, setName] = useState('')
  const load = () => api.schedules().then((d) => { setMeta(d as Meta); setName(d.me_name) }).catch((e) => setErr(String(e)))
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t) }, [])
  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(label); setErr(null); try { await fn(); await load() } catch (e) { setErr(String(e)) } finally { setBusy(null) } }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1>Schedules</h1>
        {meta && <span className="muted small">local time {meta.tz.now} · {meta.tz.name} ({meta.tz.abbr}, {meta.tz.offset}) · all times below are local</span>}
        <span style={{ marginLeft: 'auto' }}><button onClick={() => setAdding(!adding)}>{adding ? 'Cancel' : '+ New schedule'}</button></span>
      </div>
      {err && <p className="err">{err}</p>}

      {meta && !meta.supervisor_alive && (
        <div className="card" style={{ borderColor: 'var(--warn)', marginBottom: 12 }}>
          <b style={{ color: 'var(--warn)' }}>Alfred is not running, so nothing will fire.</b>
          <div className="small muted" style={{ marginTop: 4 }}>Schedules are checked by the supervisor started by <span className="mono">./run_all.sh</span>. To make Alfred start at login and stay up, run <span className="mono">./alfred-autostart.sh install</span> once. A job due while the Mac was asleep or Alfred was down runs when it comes back, up to three hours late, once.</div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="small">The brief addresses you as</span>
        <input value={name} onChange={(e) => setName(e.target.value)} style={{ width: 150 }} />
        <button className="secondary" disabled={busy !== null || !name.trim() || name === meta?.me_name} onClick={() => act('name', () => api.setMeName(name.trim()))}>Save</button>
        <span className="small muted" style={{ marginLeft: 'auto' }}>Runs land in a chat like any question; the brief also appears on Home and as a printable page.</span>
      </div>

      {adding && (
        <div className="card" style={{ marginBottom: 14, display: 'grid', gap: 10 }}>
          <div className="grid cols-2" style={{ gap: 10 }}>
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="name" />
            <div style={{ display: 'flex', gap: 8 }}>
              <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Draft['kind'], role: e.target.value === 'brief' ? 'briefer' : 'team_lead' })} style={{ width: 150 }}>
                <option value="brief">daily brief</option><option value="prompt">prompt</option>
              </select>
              <select value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })}>{(meta?.roles ?? []).map((r) => <option key={r}>{r}</option>)}</select>
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

      {meta?.schedules.length === 0 && <div className="card muted small">No schedules yet. Start with a morning brief: 08:00, Monday to Friday.</div>}
      {meta?.schedules.map((s) => <ScheduleCard key={s.id} s={s} meta={meta} busy={busy} act={act} />)}

      <p className="muted small" style={{ marginTop: 18 }}>Ideas: an evening "what changed today, what is tomorrow" brief at 17:30 · a Monday prompt "which of my MRs are waiting on reviewers" · a prompt to the team lead every morning "any red pipelines on my projects?"</p>
    </>
  )
}
