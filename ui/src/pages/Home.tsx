import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ago, fmtCost, subscribe, type BriefDoc, type BriefLatest, type Flow, type FlowTask, type HomeData, type MyCalendar, type MyJira } from '../api'
import Doctor from '../components/Doctor'

const PROJECT_KEY = 'alfred.ask.project'
const ROLE_COLOR: Record<string, string> = { team_lead: '#bb9af7', developer: '#7aa2f7', qa: '#9ece6a', briefer: '#e0af68', watcher: '#7dcfff' }
const toMin = (hhmm: string) => { const [h, m] = hhmm.split(':').map(Number); return (h || 0) * 60 + (m || 0) }

/* ------------------------------------------------------------------ today ---- */

function AgendaTrack({ meetings }: { meetings: BriefDoc['meetings'] }) {
  const start = 8 * 60, end = 19 * 60
  const now = new Date(); const nowMin = now.getHours() * 60 + now.getMinutes()
  const pct = (m: number) => `${((Math.min(Math.max(m, start), end) - start) / (end - start)) * 100}%`
  const MIN = 30, LABEL = 150
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

function Today({ d, brief, onRefresh, busy }: { d: HomeData; brief: BriefLatest | null; onRefresh: () => void; busy: boolean }) {
  const nav = useNavigate()
  const latest = brief?.latest
  const fresh = !!latest && new Date(latest.created_at * 1000).toDateString() === new Date().toDateString()
  const writing = !!brief?.running
  const sched = d.brief_schedule
  return (
    <section className="brief-hero">
      <div className="eyebrow">
        <span className="dotlbl"><i className={writing ? 'live' : ''} />{writing ? 'writing your brief…' : latest ? `Today · brief from ${new Date(latest.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}${fresh ? '' : ' ' + new Date(latest.created_at * 1000).toLocaleDateString(undefined, { weekday: 'short' })}` : 'Today'}</span>
        <span className="tools">
          {latest && <a href={`/brief/${latest.task_id}`} target="_blank" rel="noreferrer">Full brief ↗</a>}
          <button disabled={busy || writing} onClick={onRefresh}>{latest ? 'Refresh' : 'Write my brief'}</button>
          <a href="/schedules" onClick={(e) => { e.preventDefault(); nav('/schedules') }}>{sched ? `${sched.enabled ? 'Daily' : 'Paused'} · ${sched.at_time}` : 'Schedule'}</a>
        </span>
      </div>
      {!latest && (
        <div className="brief-empty">
          <div className="h">{writing ? 'Reading your calendar, Teams, mail and Jira…' : 'Your day, on one card.'}</div>
          <div className="muted">{writing ? 'This takes about a minute.' : 'One run reads Outlook, Teams, your inbox and Jira and writes what matters. Scheduled, it is waiting for you every morning.'}</div>
        </div>
      )}
      {latest && (
        <>
          <h2 className="headline">{latest.brief.headline}</h2>
          <AgendaTrack meetings={latest.brief.meetings} />
          <div className="blocks" style={{ gridTemplateColumns: `repeat(${Math.max(1, Math.min(4, latest.brief.segments.length))}, minmax(0, 1fr))` }}>
            {latest.brief.segments.map((s, i) => <div key={i} className="block"><div className="t">{s.title}</div><div className="d">{s.detail}</div></div>)}
          </div>
          {!fresh && <div className="small" style={{ color: 'var(--warn)', marginTop: 8 }}>This brief is from {new Date(latest.created_at * 1000).toLocaleDateString(undefined, { weekday: 'long' })} — press Refresh for today.</div>}
        </>
      )}
    </section>
  )
}

/* -------------------------------------------------------- needs attention ---- */

type Item = { id: string; kind: 'alert' | 'brief' | 'chain' | 'memory' | 'todo' | 'failed'; tone: string; title: string; detail: string; meta: string; url?: string | null; to?: string; dismiss?: () => Promise<unknown> }

function Attention({ d, brief, reload }: { d: HomeData; brief: BriefLatest | null; reload: () => void }) {
  const nav = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  const items: Item[] = []
  for (const a of d.alerts) items.push({ id: `a-${a.id}`, kind: 'alert', tone: a.severity === 'urgent' ? 'var(--bad)' : a.severity === 'warn' ? 'var(--warn)' : 'var(--run)', title: a.title, detail: a.detail, meta: `${a.source ?? 'watcher'} · ${ago(a.created_at)}`, url: a.url, to: a.chain_id ? `/chains/${a.chain_id}` : undefined, dismiss: () => api.dismissAlert(a.id) })
  const bl = brief?.latest
  if (bl && new Date(bl.created_at * 1000).toDateString() === new Date().toDateString()) {
    bl.brief.needs_attention.forEach((it, i) => items.push({ id: `b-${i}`, kind: 'brief', tone: 'var(--warn)', title: it.title, detail: it.detail, meta: 'from today\'s brief', url: it.url, to: `/brief/${bl.task_id}` }))
  }
  for (const c of d.need_human) items.push({ id: `c-${c.chain_id}`, kind: 'chain', tone: 'var(--bad)', title: c.title, detail: c.status === 'stuck' ? 'The agents are stuck and need a decision.' : c.status === 'failed' ? 'The run failed.' : 'The plan was not dispatched — no repository was attached.', meta: `chain · ${ago(c.updated_at)}`, to: `/chains/${c.chain_id}` })
  for (const c of d.failed_scheduled ?? []) items.push({ id: `f-${c.chain_id}`, kind: 'failed', tone: 'var(--muted)', title: `${c.title} failed`, detail: 'A scheduled run did not finish (usually the API was unreachable right after wake-up). It is retried once automatically.', meta: `schedule · ${ago(c.updated_at)}`, to: `/chains/${c.chain_id}` })
  if (d.stats.proposals) items.push({ id: 'mem', kind: 'memory', tone: 'var(--accent)', title: `${d.stats.proposals} memory proposal${d.stats.proposals === 1 ? '' : 's'} to review`, detail: 'Decisions and facts the agents extracted; nothing reaches them until you accept.', meta: 'memory', to: '/memory?status=proposed' })
  for (const t of d.todos) items.push({ id: `t-${t.id}`, kind: 'todo', tone: 'var(--muted)', title: t.title, detail: '', meta: `${t.kind} · ${t.project_key}`, to: `/memory?project=${encodeURIComponent(t.project_key)}` })
  const order: Record<Item['kind'], number> = { alert: 0, chain: 1, brief: 2, memory: 3, failed: 4, todo: 5 }
  items.sort((x, y) => (order[x.kind] - order[y.kind]) || (x.tone === 'var(--bad)' ? -1 : 0))
  const shown = showAll ? items : items.slice(0, 6)
  const dismiss = async (it: Item) => { if (!it.dismiss) return; setBusy(it.id); try { await it.dismiss(); reload() } finally { setBusy(null) } }
  return (
    <section className="panel">
      <div className="sec"><span>Needs your attention</span><span className="muted small">{items.length ? `${items.length} item${items.length === 1 ? '' : 's'}` : 'all clear'}{d.alerts.length > 1 ? <> · <button className="lnk" onClick={async () => { setBusy('all'); try { await api.dismissAlert(); reload() } finally { setBusy(null) } }}>clear alerts</button></> : null}</span></div>
      {items.length === 0 && <div className="empty">Nothing is waiting on you. {d.watch_schedule ? `The watcher checks every ${d.watch_schedule.every_min} minutes.` : ''}</div>}
      <div className="att">
        {shown.map((it) => (
          <div key={it.id} className="att-row">
            <span className="tone" style={{ background: it.tone }} />
            <div className="body" onClick={() => it.to && nav(it.to)} style={{ cursor: it.to ? 'pointer' : 'default' }}>
              <div className="t">{it.url ? <a href={it.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{it.title}</a> : it.title}</div>
              {it.detail && <div className="s">{it.detail}</div>}
              <div className="m">{it.meta}</div>
            </div>
            {it.dismiss && <button className="x" title="dismiss" disabled={busy !== null} onClick={() => dismiss(it)}>✕</button>}
          </div>
        ))}
      </div>
      {items.length > 6 && <button className="lnk small" onClick={() => setShowAll(!showAll)}>{showAll ? 'show less' : `show all ${items.length}`}</button>}
    </section>
  )
}

/* ------------------------------------------------------------ side lists ---- */

function Meetings({ brief }: { brief: BriefLatest | null }) {
  const [d, setD] = useState<MyCalendar | null>(null)
  const [busy, setBusy] = useState(false)
  const load = (refresh = false) => { setBusy(true); api.myCalendar(refresh).then(setD).catch((e) => setD({ meetings: [], error: String(e), day: '', at: 0 })).finally(() => setBusy(false)) }
  useEffect(() => { load() }, [])
  const now = new Date(); const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
  // prefer the live calendar; fall back to the brief's list while it loads
  const list = d && !d.error ? d.meetings.map((m) => ({ start: m.start, end: m.end, title: m.title, sub: [m.organizer, m.location].filter(Boolean).join(' · '), url: m.join_url }))
    : (brief?.latest?.brief.meetings ?? []).map((m) => ({ start: m.start, end: m.end, title: m.title, sub: [m.where, m.note].filter(Boolean).join(' · '), url: null as string | null }))
  return (
    <section className="panel">
      <div className="sec"><span>Meetings</span><span className="muted small">{d?.at ? `Outlook · ${ago(d.at)} · ` : ''}<button className="lnk" disabled={busy} onClick={() => load(true)}>{busy ? '…' : 'refresh'}</button></span></div>
      {!d && list.length === 0 && <div className="empty">loading…</div>}
      {d?.error && <div className="small" style={{ color: 'var(--warn)' }}>{d.error}</div>}
      {d && !d.error && list.length === 0 && <div className="empty">Nothing on the calendar today.</div>}
      {list.map((m, i) => {
        const past = m.end < hhmm; const live = m.start <= hhmm && hhmm < m.end
        return (
          <div key={i} className={`mt ${past ? 'past' : ''} ${live ? 'live' : ''}`}>
            <span className="when mono">{m.start}</span>
            <span className="what"><span className="t">{m.url ? <a href={m.url} target="_blank" rel="noreferrer">{m.title}</a> : m.title}{live && <span className="pill running" style={{ marginLeft: 6 }}>now</span>}</span>{m.sub && <span className="s muted">{m.sub}</span>}</span>
          </div>
        )
      })}
    </section>
  )
}

function Tickets() {
  const [d, setD] = useState<MyJira | null>(null)
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()
  const load = (refresh = false) => { setBusy(true); api.myJira(refresh).then(setD).catch((e) => setD({ issues: [], error: String(e), at: 0 })).finally(() => setBusy(false)) }
  useEffect(() => { load() }, [])
  return (
    <section className="panel">
      <div className="sec"><span>My tickets</span><span className="muted small">{d?.at ? `${d.issues.length} open · ` : ''}<button className="lnk" disabled={busy} onClick={() => load(true)}>{busy ? '…' : 'refresh'}</button></span></div>
      {!d && <div className="empty">loading…</div>}
      {d?.error && <div className="small" style={{ color: 'var(--warn)' }}>{d.error}</div>}
      {d && !d.error && d.issues.length === 0 && <div className="empty">No unresolved issues assigned to you.</div>}
      {d?.issues.map((i) => (
        <div key={i.key} className="tk" onClick={() => nav('/ask', { state: { prefill: `Let's work on ${i.url ?? i.key}: ${i.summary}` } })} title="ask Alfred about this ticket">
          <span className="k"><a className="mono" href={i.url ?? '#'} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{i.key}</a></span>
          <span className="t">{i.summary}</span>
          <span className={`pill ${/progress|review/i.test(i.status) ? 'running' : /hold|blocked/i.test(i.status) ? 'stuck' : ''}`}>{i.status}</span>
        </div>
      ))}
    </section>
  )
}

/* ------------------------------------------------------------ alfred at work ---- */

function dur(a: number | null | undefined, b: number | null | undefined) {
  if (!a) return ''
  const s = Math.max(0, Math.floor((b ?? Date.now() / 1000) - a))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

/** One node of the flow: who, what, and how it went. */
function Node({ t, short }: { t: FlowTask; short: string }) {
  const running = t.status === 'claimed'
  return (
    <div className={`fnode ${t.status}`} style={{ borderLeftColor: ROLE_COLOR[t.role] ?? 'var(--border)' }} title={`${t.role} · ${t.title}`}>
      <div className="fn-hd"><span className={`pill ${t.role}`}>{t.role}{t.iteration > 1 ? ` r${t.iteration}` : ''}</span>
        <span className="fn-state">{running ? <><span className="dot think live" style={{ background: ROLE_COLOR[t.role] }} /> {dur(t.claimed_at, null)}</> : t.status === 'done' ? <span style={{ color: 'var(--ok)' }}>✓ {dur(t.claimed_at, t.finished_at)}</span> : t.status === 'open' ? 'queued' : <span style={{ color: 'var(--bad)' }}>{t.status}</span>}</span></div>
      <div className="fn-title">{short}</div>
    </div>
  )
}

/** lead → developers → QA → result, for one chain. Parallel agents stack in their column. */
function FlowGraph({ f, jira }: { f: Flow; jira: string | null }) {
  const nav = useNavigate()
  const lead = f.tasks.find((t) => t.role === 'team_lead')
  const devs = f.tasks.filter((t) => t.role !== 'team_lead' && t.role !== 'qa')
  const qas = f.tasks.filter((t) => t.role === 'qa')
  const short = (t: string) => t.replace(/^(implement|verify|fix): /, '')
  const result = f.status === 'running' ? { l: 'in progress', c: 'var(--run)' } : f.status === 'passed' || f.status === 'answered' || f.status === 'done' ? { l: f.status === 'answered' ? 'answered' : 'QA passed', c: 'var(--ok)' } : f.status === 'stuck' || f.status === 'not_dispatched' ? { l: f.status.replace('_', ' '), c: 'var(--warn)' } : { l: f.status, c: 'var(--bad)' }
  const open = () => nav(f.conversation_id ? `/ask/${f.conversation_id}` : `/chains/${f.chain_id}`)
  return (
    <div className={`flow ${f.status}`}>
      <div className="flow-hd">
        <div className="lane-title">
          {f.tickets.map((k) => <a key={k} className="ticket" href={jira ? `${jira.replace(/\/$/, '')}/browse/${k}` : '#'} target="_blank" rel="noreferrer">{k}</a>)}
          <span className="ell" title={f.title} onClick={open}>{f.title}</span>
        </div>
        <div className="lane-meta muted small">{f.project && <span className="mono">{f.project}</span>}<span>{f.finished_at && f.status !== 'running' ? dur(f.started_at, f.finished_at) : dur(f.started_at, null)}</span><span>{fmtCost(f.cost_usd)}</span><a href={`/chains/${f.chain_id}`} onClick={(e) => { e.preventDefault(); nav(`/chains/${f.chain_id}`) }}>chain →</a></div>
      </div>
      <div className="fgraph">
        <div className="fcol">{lead && <Node t={lead} short={f.kind === 'plan' ? `planned ${devs.length} subtask${devs.length === 1 ? '' : 's'}` : 'answered the question'} />}</div>
        {devs.length > 0 && <><span className="farrow">→</span><div className={`fcol stack ${devs.length > 1 ? 'multi' : ''}`}>{devs.map((t) => <Node key={t.id} t={t} short={short(t.title)} />)}</div></>}
        {qas.length > 0 && <><span className="farrow">→</span><div className={`fcol stack ${qas.length > 1 ? 'multi' : ''}`}>{qas.map((t) => <Node key={t.id} t={t} short={short(t.title)} />)}</div></>}
        <span className="farrow">→</span>
        <div className="fresult" style={{ color: result.c, borderColor: result.c }} onClick={open}><span className="dot" style={{ background: result.c }} />{result.l}</div>
      </div>
    </div>
  )
}

function Work({ d }: { d: HomeData }) {
  const nav = useNavigate()
  const idle = d.workers.filter((w) => !w.task)
  const idleByRole = idle.reduce<Record<string, number>>((m, w) => ({ ...m, [w.role]: (m[w.role] ?? 0) + 1 }), {})
  const [showAll, setShowAll] = useState(false)
  const flows = showAll ? d.flows : d.flows.slice(0, 3)
  return (
    <section className="panel">
      <div className="sec"><span>Alfred at work</span><span className="muted small">{d.active.length ? `${d.stats.working} agent${d.stats.working === 1 ? '' : 's'} working · ${d.stats.queued} queued` : `all quiet · ${Object.entries(idleByRole).map(([r, n]) => `${n} ${r}`).join(', ') || 'no workers'} ready`} · <a href="/agents" onClick={(e) => { e.preventDefault(); nav('/agents') }}>agents</a></span></div>
      {d.flows.length === 0 && <div className="empty">No work yet. Ask something below.</div>}
      {flows.map((f) => <FlowGraph key={f.chain_id} f={f} jira={d.jira_url} />)}
      {d.flows.length > 3 && <button className="lnk small" onClick={() => setShowAll(!showAll)}>{showAll ? 'show less' : `show ${d.flows.length - 3} more`}</button>}
    </section>
  )
}

/* -------------------------------------------------------------- composer ---- */

function Composer() {
  const nav = useNavigate()
  const [text, setText] = useState('')
  const [project, setProject] = useState(() => { try { return localStorage.getItem(PROJECT_KEY) ?? '' } catch { return '' } })
  const [showRepo, setShowRepo] = useState(false)
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
        <div className="line">
          <textarea ref={ref} rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask Alfred anything — a ticket, a merge request, a flow… (“remember: …” stores a fact)"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!busy) send() } }} />
          <button className="repo-toggle" title={project ? project : 'attach a repository for changes'} onClick={() => setShowRepo(!showRepo)} style={project ? { color: 'var(--accent)' } : {}}>{project ? 'repo ✓' : 'repo'}</button>
          <button onClick={send} disabled={busy || !text.trim()}>{busy ? '…' : 'Send'}</button>
        </div>
        {showRepo && <input className="mono" value={project} onChange={(e) => setProject(e.target.value)} placeholder="repository path — needed for changes, not for questions" style={{ marginTop: 6 }} />}
        {err && <div className="small" style={{ color: err.startsWith('remembered') ? 'var(--ok)' : 'var(--bad)', marginTop: 4 }}>{err}</div>}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ page ---- */

export default function Home() {
  const nav = useNavigate()
  const [d, setD] = useState<HomeData | null>(null)
  const [brief, setBrief] = useState<BriefLatest | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => { api.home().then(setD).catch(() => {}); api.briefLatest().then(setBrief).catch(() => {}) }
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    const un = subscribe({ tasks: () => load() })
    return () => { clearInterval(t); un() }
  }, [])
  const refreshBrief = async () => {
    if (!d) return
    setBusy(true)
    try { if (d.brief_schedule) { await api.runSchedule(d.brief_schedule.id) } else { const s = await api.createSchedule({ name: 'Morning brief', kind: 'brief', role: 'briefer', prompt: '', project_dir: null, at_time: '08:00', days: '0,1,2,3,4' }); await api.runSchedule(s.id) } load() }
    finally { setBusy(false) }
  }
  const hour = new Date().getHours()
  const greet = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  return (
    <div className="home">
      <div className="home-head">
        <div>
          <h1>{greet}{d?.me_name ? `, ${d.me_name}` : ''}</h1>
          <div className="muted small">{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })} · {d ? (d.stats.working ? `Alfred is working on ${d.stats.working} task${d.stats.working === 1 ? '' : 's'}` : 'Alfred is idle and ready') : '…'}</div>
        </div>
        <div className="head-links small">
          <a href="/ask" onClick={(e) => { e.preventDefault(); nav('/ask') }}>New chat</a>
          <a href="/memory?intake=1" onClick={(e) => { e.preventDefault(); nav('/memory?intake=1') }}>Meeting notes</a>
          <a href="/memory?status=proposed" onClick={(e) => { e.preventDefault(); nav('/memory?status=proposed') }}>Proposals{d?.stats.proposals ? ` · ${d.stats.proposals}` : ''}</a>
        </div>
      </div>
      <Doctor />
      {d && (
        <>
          <Today d={d} brief={brief} onRefresh={refreshBrief} busy={busy} />
          <div className="home-grid">
            <div className="main">
              <Attention d={d} brief={brief} reload={load} />
              <Work d={d} />
            </div>
            <aside className="side">
              <Meetings brief={brief} />
              <Tickets />
            </aside>
          </div>
        </>
      )}
      <Composer />
    </div>
  )
}
