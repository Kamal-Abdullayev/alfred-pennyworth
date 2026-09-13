import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ago, fmtCost, subscribe, type BriefLatest, type Conversation, type HomeData, type MyCalendar, type MyJira } from '../api'
import Doctor from '../components/Doctor'
import { DayLine } from './Brief'

const PROJECT_KEY = 'alfred.ask.project'
const ROLE_COLOR: Record<string, string> = { team_lead: '#bb9af7', developer: '#7aa2f7', qa: '#9ece6a', briefer: '#e0af68' }

function elapsed(since: number | null | undefined) {
  if (!since) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - since))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

/** The daily brief: headline, the day as a line, time blocks, what needs you. */
function BriefCard({ d }: { d: HomeData }) {
  const nav = useNavigate()
  const [b, setB] = useState<BriefLatest | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => api.briefLatest().then(setB).catch(() => {})
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t) }, [])
  const sched = d.brief_schedule
  const run = async () => {
    setBusy(true)
    try { if (sched) { await api.runSchedule(sched.id) } else { const s = await api.createSchedule({ name: 'Morning brief', kind: 'brief', role: 'briefer', prompt: '', project_dir: null, at_time: '08:00', days: '0,1,2,3,4' }); await api.runSchedule(s.id) } load() }
    finally { setBusy(false) }
  }
  const latest = b?.latest
  const today = new Date().toDateString()
  const fresh = latest && new Date(latest.created_at * 1000).toDateString() === today
  return (
    <div className="card brief-card">
      <div className="hd">
        <b>Your brief</b>
        <span className="actions" style={{ alignItems: 'center' }}>
          {latest && <span className="muted small">{fresh ? 'today' : ago(latest.created_at)} · {fmtCost(latest.cost_usd)}</span>}
          {b?.running && <span className="pill running">writing…</span>}
          {latest && <a className="btn-link" href={`/brief/${latest.task_id}`} target="_blank" rel="noreferrer">open · PDF</a>}
          <button className="btn-link" disabled={busy || !!b?.running} onClick={run}>{latest ? 'refresh now' : 'write my first brief'}</button>
          <a className="btn-link" href="/schedules" onClick={(e) => { e.preventDefault(); nav('/schedules') }}>{sched ? `daily at ${sched.at_time}` : 'schedule'}</a>
        </span>
      </div>
      {!latest && !b?.running && <div className="muted small">No brief yet. One run reads your calendar, Teams, mail and Jira and writes the day in one page. Scheduled, it is waiting for you every morning.</div>}
      {!latest && b?.running && <div className="muted small">The briefer is reading your calendar, Teams, mail and Jira…</div>}
      {latest && (
        <>
          <div className="brief-headline home">{latest.brief.headline}</div>
          <DayLine meetings={latest.brief.meetings} height={72} />
          <div className="brief-segments home" style={{ gridTemplateColumns: `repeat(${Math.max(1, Math.min(4, latest.brief.segments.length))}, 1fr)` }}>
            {latest.brief.segments.map((s, i) => <div key={i}><div className="seg-title">{s.title}</div><div className="seg-text">{s.detail}</div></div>)}
          </div>
          {latest.brief.needs_attention.length > 0 && (
            <div className="brief-needs">
              <div className="label">Needs attention</div>
              {latest.brief.needs_attention.map((it, i) => <div key={i} className="item"><span className="n">{i + 1}</span><div><b>{it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.title}</a> : it.title}</b><div className="small muted">{it.detail}</div></div></div>)}
            </div>
          )}
          {!fresh && <div className="small muted" style={{ marginTop: 8 }}>This brief is from {new Date(latest.created_at * 1000).toLocaleDateString(undefined, { weekday: 'long' })}. Press refresh for today.</div>}
        </>
      )}
    </div>
  )
}

function QuickActions({ d }: { d: HomeData }) {
  const nav = useNavigate()
  const items = [
    { l: 'New chat', s: 'ask the team lead', to: '/ask' },
    { l: 'Paste meeting notes', s: 'distil into memory', to: '/memory?intake=1' },
    { l: `Proposals${d.stats.proposals ? ` (${d.stats.proposals})` : ''}`, s: 'review memory', to: '/memory?status=proposed' },
    { l: 'Board', s: `${d.stats.working} working · ${d.stats.queued} queued`, to: '/board' },
    { l: 'Schedules', s: 'recurring jobs', to: '/schedules' },
    { l: 'Connectors', s: 'Jira · GitLab · M365', to: '/connectors' },
  ]
  return <div className="quick">{items.map((i) => <button key={i.l} className="qa" onClick={() => nav(i.to)}><b>{i.l}</b><span className="muted small">{i.s}</span></button>)}</div>
}

function Agents({ d }: { d: HomeData }) {
  const nav = useNavigate()
  const idle = d.workers.filter((w) => !w.task)
  const idleByRole = idle.reduce<Record<string, number>>((m, w) => ({ ...m, [w.role]: (m[w.role] ?? 0) + 1 }), {})
  const short = (t: string) => t.replace(/^(implement|verify|fix): /, '').replace(/^Morning brief · .*/, 'daily brief')
  return (
    <div className="card work">
      <div className="hd">
        <b>Work in progress</b>
        <span className="muted small">{d.active.length ? `${d.active.length} chain${d.active.length === 1 ? '' : 's'} · ${d.stats.working} agent${d.stats.working === 1 ? '' : 's'} working · ${d.stats.queued} queued` : 'all quiet'}</span>
      </div>
      {d.active.length === 0 && <div className="muted small" style={{ padding: '6px 0 10px' }}>Nothing running. Ask something below, or open a ticket with <b>ask</b> on the right.</div>}
      {d.active.map((c) => {
        const running = c.tasks.filter((t) => t.status === 'claimed'); const done = c.tasks.filter((t) => t.status === 'done').length
        const total = c.tasks.length
        return (
          <div key={c.chain_id} className="lane">
            <div className="lane-hd">
              <div className="lane-title">
                {c.tickets.map((k) => <a key={k} className="ticket" href={d.jira_url ? `${d.jira_url.replace(/\/$/, '')}/browse/${k}` : '#'} target="_blank" rel="noreferrer">{k}</a>)}
                <span className="ell" title={c.title} onClick={() => nav(c.conversation_id ? `/ask/${c.conversation_id}` : `/chains/${c.chain_id}`)}>{c.title}</span>
              </div>
              <div className="lane-meta muted small">{c.project && <span className="mono">{c.project}</span>}<span>{elapsed(c.started_at)} in</span><span>{fmtCost(c.cost_usd)}</span><span>{done}/{total} steps</span><a href={`/chains/${c.chain_id}`} onClick={(e) => { e.preventDefault(); nav(`/chains/${c.chain_id}`) }}>chain →</a></div>
            </div>
            <div className="steps">
              {c.tasks.map((t, i) => (
                <div key={t.id} className={`step ${t.status}`} style={{ borderColor: t.status === 'claimed' ? ROLE_COLOR[t.role] : undefined }} title={`${t.role} · ${t.title} · ${t.status}`}>
                  {i > 0 && <span className="arrow">→</span>}
                  <span className={`pill ${t.role}`}>{t.role}{t.iteration > 1 ? ` r${t.iteration}` : ''}</span>
                  <span className="st-title">{short(t.title)}</span>
                  <span className="st-state">{t.status === 'claimed' ? <><span className="dot think live" style={{ background: ROLE_COLOR[t.role] }} /> {elapsed(t.claimed_at)}</> : t.status === 'done' ? '✓' : t.status === 'open' ? 'queued' : t.status}</span>
                </div>
              ))}
            </div>
            {running.length > 1 && <div className="small muted" style={{ marginTop: 4 }}>{running.length} agents on this chain at once: {running.map((t) => t.role).join(', ')}</div>}
          </div>
        )
      })}
      <div className="idle-strip small">
        <span className="muted">Ready:</span>
        {Object.keys(idleByRole).length === 0 && <span className="muted">everyone is busy{d.stats.queued ? `, ${d.stats.queued} task${d.stats.queued === 1 ? '' : 's'} waiting for a free agent` : ''}</span>}
        {Object.entries(idleByRole).map(([r, n]) => <span key={r} className={`pill ${r}`}>{r} ×{n}</span>)}
        {d.workers.length === 0 && <span className="muted">no workers alive — is <span className="mono">./run_all.sh</span> running?</span>}
        <a className="muted" style={{ marginLeft: 'auto' }} href="/agents" onClick={(e) => { e.preventDefault(); nav('/agents') }}>configure agents →</a>
      </div>
    </div>
  )
}

function Recent({ d }: { d: HomeData }) {
  const nav = useNavigate()
  return (
    <div className="card">
      <div className="hd"><b>Recent work</b><a className="small" href="/board" onClick={(e) => { e.preventDefault(); nav('/board') }}>full board →</a></div>
      <table className="small compact"><tbody>
        {d.recent_chains.map((c) => (
          <tr key={c.chain_id} className="link" onClick={() => nav(`/chains/${c.chain_id}`)}>
            <td><span className={`pill ${c.status}`}>{c.status}</span></td>
            <td className="ell" title={c.title}>{c.title}</td>
            <td className="muted mono">{fmtCost(c.cost_usd)}</td>
            <td className="muted">{ago(c.updated_at)}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}

function Meetings() {
  const [d, setD] = useState<MyCalendar | null>(null)
  const [busy, setBusy] = useState(false)
  const load = (refresh = false) => { setBusy(true); api.myCalendar(refresh).then(setD).catch((e) => setD({ meetings: [], error: String(e), day: '', at: 0 })).finally(() => setBusy(false)) }
  useEffect(() => { load() }, [])
  const now = new Date(); const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
  return (
    <div className="card">
      <div className="hd"><b>Today's meetings</b><span className="actions"><span className="muted small">{d?.at ? `Outlook · ${ago(d.at)}` : ''}</span><button className="btn-link" disabled={busy} onClick={() => load(true)}>{busy ? '…' : 'refresh'}</button></span></div>
      {!d && <div className="muted small">loading…</div>}
      {d?.error && <div className="small" style={{ color: 'var(--warn)' }}>{d.error}</div>}
      {d && !d.error && d.meetings.length === 0 && <div className="muted small">Nothing on the calendar today.</div>}
      {d?.meetings.map((m, i) => {
        const past = m.end < hhmm; const live = m.start <= hhmm && hhmm < m.end
        return (
          <div key={i} className={`meeting ${past ? 'past' : ''} ${live ? 'live' : ''}`}>
            <div className="when mono">{m.start}<br /><span className="muted">{m.end}</span></div>
            <div className="what">
              <div className="t">{m.join_url ? <a href={m.join_url} target="_blank" rel="noreferrer">{m.title}</a> : m.title}{live && <span className="pill running" style={{ marginLeft: 6 }}>now</span>}</div>
              <div className="muted small">{[m.organizer, m.location, m.attendees ? `${m.attendees} people` : null, m.response && m.response !== 'accepted' ? m.response : null].filter(Boolean).join(' · ')}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Jira() {
  const [d, setD] = useState<MyJira | null>(null)
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()
  const load = (refresh = false) => { setBusy(true); api.myJira(refresh).then(setD).catch((e) => setD({ issues: [], error: String(e), at: 0 })).finally(() => setBusy(false)) }
  useEffect(() => { load() }, [])
  return (
    <div className="card">
      <div className="hd"><b>My Jira tickets</b><span className="actions"><span className="muted small">{d?.at ? `${d.issues.length} open · ${ago(d.at)}` : ''}</span><button className="btn-link" disabled={busy} onClick={() => load(true)}>{busy ? '…' : 'refresh'}</button></span></div>
      {!d && <div className="muted small">loading…</div>}
      {d?.error && <div className="small" style={{ color: 'var(--warn)' }}>{d.error}</div>}
      {d && !d.error && d.issues.length === 0 && <div className="muted small">No unresolved issues assigned to you.</div>}
      {d?.issues.map((i) => (
        <div key={i.key} className="issue">
          <div className="row"><a className="mono" href={i.url ?? '#'} target="_blank" rel="noreferrer">{i.key}</a><span className={`pill ${/progress|review/i.test(i.status) ? 'running' : /done|closed/i.test(i.status) ? 'done' : ''}`}>{i.status}</span><span className="muted small">{i.type} · {i.priority}</span>
            <button className="btn-link tiny" title="ask Alfred about this ticket" onClick={() => nav('/ask', { state: { prefill: `Let's work on ${i.url ?? i.key}: ${i.summary}` } })}>ask</button></div>
          <div className="small ell" title={i.summary}>{i.summary}</div>
        </div>
      ))}
    </div>
  )
}

function NeedsYou({ d }: { d: HomeData }) {
  const nav = useNavigate()
  const items = [
    ...d.need_human.map((c) => ({ k: c.chain_id, tone: 'var(--warn)', label: `${c.status.replace('_', ' ')} · ${c.title}`, to: `/chains/${c.chain_id}` })),
    ...(d.stats.proposals ? [{ k: 'mem', tone: 'var(--accent)', label: `${d.stats.proposals} memory proposal${d.stats.proposals === 1 ? '' : 's'} to review`, to: '/memory?status=proposed' }] : []),
    ...d.todos.map((t) => ({ k: t.id, tone: 'var(--muted)', label: `${t.kind}: ${t.title}`, to: `/memory?project=${encodeURIComponent(t.project_key)}` })),
  ]
  return (
    <div className="card">
      <div className="hd"><b>Waiting on you</b><span className="muted small">{items.length ? `${items.length} item${items.length === 1 ? '' : 's'}` : 'all clear'}</span></div>
      {items.length === 0 && <div className="muted small">Nothing waiting on a decision.</div>}
      {items.map((i) => <div key={i.k} className="need link" onClick={() => nav(i.to)}><span className="bullet" style={{ background: i.tone }} /><span className="ell">{i.label}</span></div>)}
    </div>
  )
}

function Composer({ convs }: { convs: Conversation[] }) {
  const nav = useNavigate()
  const [text, setText] = useState('')
  const [project, setProject] = useState(() => { try { return localStorage.getItem(PROJECT_KEY) ?? '' } catch { return '' } })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { try { localStorage.setItem(PROJECT_KEY, project) } catch { /* ignore */ } }, [project])
  async function send() {
    const q = text.trim(); if (!q) return
    setBusy(true); setErr(null)
    try {
      const r = await api.createJob(q, project.trim() || undefined)
      if (r.remembered) { setText(''); setErr(`remembered for ${r.remembered.project_key}: ${r.remembered.title}`); return }
      nav(`/ask/${r.conversation_id}`)
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  return (
    <div className="home-composer">
      <div className="inner">
        <div className="row" style={{ marginBottom: 6 }}>
          <b className="small">Ask Alfred</b>
          <span className="chips">{convs.slice(0, 4).map((c) => <a key={c.id} className="chip" href={`/ask/${c.id}`} onClick={(e) => { e.preventDefault(); nav(`/ask/${c.id}`) }} title={c.title}>{c.title.length > 34 ? c.title.slice(0, 33) + '…' : c.title}{c.last_status === 'claimed' || c.last_status === 'open' ? ' · working' : ''}</a>)}</span>
        </div>
        <textarea ref={ref} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about a ticket, a merge request, a flow… (Enter to send, Shift+Enter for a new line; “remember: …” stores a fact)"
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!busy) send() } }} />
        <div className="bar">
          <input className="mono" value={project} onChange={(e) => setProject(e.target.value)} placeholder="repository path (optional) — needed for changes, not for questions" />
          <button onClick={send} disabled={busy || !text.trim()}>{busy ? 'Sending…' : 'Send'}</button>
        </div>
        {err && <div className="small" style={{ color: err.startsWith('remembered') ? 'var(--ok)' : 'var(--bad)', marginTop: 4 }}>{err}</div>}
      </div>
    </div>
  )
}

export default function Home() {
  const [d, setD] = useState<HomeData | null>(null)
  const load = () => api.home().then(setD).catch(() => {})
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    const un = subscribe({ tasks: () => load() })
    return () => { clearInterval(t); un() }
  }, [])
  const hour = new Date().getHours()
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  return (
    <div className="home">
      <div className="home-head">
        <div><h1>{greet}{d?.me_name ? `, ${d.me_name}` : ''}</h1><div className="muted small">{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })} · Alfred is {d ? (d.stats.working ? `working on ${d.stats.working} task${d.stats.working === 1 ? '' : 's'}` : 'idle and ready') : '…'}</div></div>
      </div>
      <Doctor />
      {d && (
        <>
          <QuickActions d={d} />
          <div className="home-grid">
            <div className="main">
              <BriefCard d={d} />
              <Agents d={d} />
              <Recent d={d} />
            </div>
            <aside className="side">
              <Meetings />
              <Jira />
              <NeedsYou d={d} />
            </aside>
          </div>
        </>
      )}
      <Composer convs={d?.conversations ?? []} />
    </div>
  )
}
