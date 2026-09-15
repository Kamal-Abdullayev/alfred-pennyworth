import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ago, fmtCost, subscribe, type BriefDoc, type BriefLatest, type Conversation, type HomeData, type MyCalendar, type MyJira } from '../api'
import Doctor from '../components/Doctor'

const PROJECT_KEY = 'alfred.ask.project'
const ROLE_COLOR: Record<string, string> = { team_lead: '#bb9af7', developer: '#7aa2f7', qa: '#9ece6a', briefer: '#e0af68' }

function elapsed(since: number | null | undefined) {
  if (!since) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - since))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

/** The daily brief as a hero: eyebrow, headline, agenda track, day blocks, what needs you. */
const toMin = (hhmm: string) => { const [h, m] = hhmm.split(':').map(Number); return (h || 0) * 60 + (m || 0) }

function AgendaTrack({ meetings }: { meetings: BriefDoc['meetings'] }) {
  const start = 8 * 60, end = 19 * 60
  const now = new Date(); const nowMin = now.getHours() * 60 + now.getMinutes()
  const pct = (m: number) => `${((Math.min(Math.max(m, start), end) - start) / (end - start)) * 100}%`
  // short meetings get a minimum visual width; anything that would overlap drops to the next row
  const MIN = 30, LABEL = 150            // minutes of track a chip and its title label occupy
  const placed = [...meetings].map((m) => ({ m, a: toMin(m.start), b: Math.max(toMin(m.end), toMin(m.start) + MIN) })).sort((x, y) => x.a - y.a)
  const laneEnd: number[] = []
  const rows = placed.map((p) => { let lane = laneEnd.findIndex((e) => e <= p.a); if (lane === -1) { lane = laneEnd.length; laneEnd.push(0) } laneEnd[lane] = p.b + LABEL; return { ...p, lane } })
  const lanes = Math.min(3, Math.max(1, laneEnd.length))
  return (
    <div className="agenda">
      <div className="track" style={{ height: 22 + lanes * 40 }}>
        {[8, 10, 12, 14, 16, 18].map((h) => <span key={h} className="tick" style={{ left: pct(h * 60) }}><i />{h}:00</span>)}
        {nowMin >= start && nowMin <= end && <span className="now" style={{ left: pct(nowMin) }} title="now" />}
        {rows.map((r, i) => {
          const realEnd = toMin(r.m.end); const past = realEnd <= nowMin, live = r.a <= nowMin && nowMin < realEnd
          if (r.lane > 2) return null
          return <span key={i} className={`ev ${past ? 'past' : ''} ${live ? 'live' : ''}`} style={{ left: pct(r.a), top: 12 + r.lane * 40 }} title={`${r.m.start}–${r.m.end} ${r.m.title}`}>
            <span className="chip" style={{ width: `max(34px, calc(${pct(r.b)} - ${pct(r.a)}))` }}><b>{r.m.start}</b></span>
            <span className="ev-t">{r.m.title}<span className="muted"> · {r.m.end}</span></span>
          </span>
        })}
      </div>
      {meetings.length === 0 && <div className="agenda-empty">No meetings today — the whole day is yours.</div>}
    </div>
  )
}

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
  const fresh = !!latest && new Date(latest.created_at * 1000).toDateString() === new Date().toDateString()
  const writing = !!b?.running
  return (
    <section className="brief-hero">
      <div className="eyebrow">
        <span className="dotlbl"><i className={writing ? 'live' : ''} />{writing ? 'writing your brief…' : latest ? `Brief · ${fresh ? 'today' : ago(latest.created_at)} ${new Date(latest.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Daily brief'}</span>
        <span className="tools">
          {latest && <a href={`/brief/${latest.task_id}`} target="_blank" rel="noreferrer" title="full brief, printable">Open ↗</a>}
          <button disabled={busy || writing} onClick={run} title="read calendar, Teams, mail and Jira again (about $1)">{latest ? 'Refresh' : 'Write my first brief'}</button>
          <a href="/schedules" onClick={(e) => { e.preventDefault(); nav('/schedules') }} title="when it is written">{sched ? `${sched.enabled ? 'Daily' : 'Paused'} · ${sched.at_time}` : 'Schedule'}</a>
        </span>
      </div>

      {!latest && (
        <div className="brief-empty">
          <div className="h">{writing ? 'Reading your calendar, Teams, mail and Jira…' : 'Your day, on one card.'}</div>
          <div className="muted">{writing ? 'This takes about a minute.' : 'One run reads Outlook, Teams, your inbox and Jira, and writes what matters. Scheduled, it is waiting for you every morning.'}</div>
        </div>
      )}

      {latest && (
        <>
          <h2 className="headline">{latest.brief.headline}</h2>
          <AgendaTrack meetings={latest.brief.meetings} />
          <div className="blocks" style={{ gridTemplateColumns: `repeat(${Math.max(1, Math.min(4, latest.brief.segments.length))}, minmax(0, 1fr))` }}>
            {latest.brief.segments.map((s, i) => <div key={i} className="block"><div className="t">{s.title}</div><div className="d">{s.detail}</div></div>)}
          </div>
          <div className="lower">
            <div className="needs">
              <div className="lbl">Needs attention{latest.brief.needs_attention.length ? ` · ${latest.brief.needs_attention.length}` : ''}</div>
              {latest.brief.needs_attention.length === 0 && <div className="muted small">Nothing needs a decision from you today.</div>}
              {latest.brief.needs_attention.slice(0, 4).map((it, i) => (
                <div key={i} className="need-item">
                  <span className="idx">{i + 1}</span>
                  <div className="body"><div className="t">{it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.title}</a> : it.title}</div><div className="s">{it.detail}</div></div>
                </div>
              ))}
              {latest.brief.needs_attention.length > 4 && <a className="small" href={`/brief/${latest.task_id}`} target="_blank" rel="noreferrer">+{latest.brief.needs_attention.length - 4} more in the full brief</a>}
            </div>
            <div className="counts">
              {[
                { n: latest.brief.meetings.length, l: 'meetings' },
                { n: latest.brief.tickets.length, l: 'open tickets' },
                { n: latest.brief.unread_emails.reduce((a, e) => a + (e.count || 1), 0), l: 'unread mails' },
                { n: latest.brief.resolved.length, l: 'resolved' },
              ].map((c) => <a key={c.l} className="count" href={`/brief/${latest.task_id}`} target="_blank" rel="noreferrer"><b>{c.n}</b><span>{c.l}</span></a>)}
              {!fresh && <div className="stale small">From {new Date(latest.created_at * 1000).toLocaleDateString(undefined, { weekday: 'long' })}. Refresh for today.</div>}
            </div>
          </div>
        </>
      )}
    </section>
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

/** What the watcher found since you last looked: red pipelines, review comments, mentions. */
function Alerts({ d, reload }: { d: HomeData; reload: () => void }) {
  const nav = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const dismiss = async (id?: string) => { setBusy(id ?? 'all'); try { await api.dismissAlert(id); reload() } finally { setBusy(null) } }
  const ws = d.watch_schedule
  return (
    <div className="card alerts">
      <div className="hd">
        <b>Alerts{d.alerts.length ? ` · ${d.alerts.length}` : ''}</b>
        <span className="actions" style={{ alignItems: 'center' }}>
          <span className="muted small">{ws ? (ws.enabled ? `watching every ${ws.every_min} min` : 'watcher paused') : 'no watcher yet'}</span>
          {d.alerts.length > 0 && <button className="btn-link" disabled={busy !== null} onClick={() => dismiss()}>clear all</button>}
          <a className="btn-link" href="/schedules" onClick={(e) => { e.preventDefault(); nav('/schedules') }}>{ws ? 'settings' : 'set up'}</a>
        </span>
      </div>
      {d.alerts.length === 0 && <div className="muted small">{ws ? 'Nothing new since the last check.' : 'A watcher checks your merge requests, tickets, Teams and mail every half hour and tells you what changed. Set one up on the Schedules page.'}</div>}
      {d.alerts.map((a) => (
        <div key={a.id} className={`alert ${a.severity}`}>
          <span className="sev" />
          <div className="body">
            <div className="t">{a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.title}</a> : a.title}</div>
            <div className="s">{a.detail}</div>
            <div className="m muted">{a.source} · {ago(a.created_at)}{a.chain_id ? <> · <a href={`/chains/${a.chain_id}`} onClick={(e) => { e.preventDefault(); nav(`/chains/${a.chain_id}`) }}>how it was found</a></> : null}</div>
          </div>
          <button className="btn-link tiny" title="dismiss" disabled={busy !== null} onClick={() => dismiss(a.id)}>✕</button>
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
              <Alerts d={d} reload={load} />
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
