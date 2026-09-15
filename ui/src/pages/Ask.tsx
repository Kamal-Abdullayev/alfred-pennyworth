import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useLocation } from 'react-router-dom'
import { api, streamTask, fmtCost, ago, leadOutput, type ChainDetail, type Conversation, type ConversationTurn, type TranscriptEvent , type Memory, type BriefDoc } from '../api'
import { BriefBody } from './Brief'
import { AnswerContent, PlanContent } from '../components/Answer'

type Live = { status: string | null; events: TranscriptEvent[]; detail: ChainDetail | null; error: string | null; eventsLoaded: boolean }
type Step = { id: string; cls: string; lab: string; txt: string; detail?: string }

const PROJECT_KEY = 'alfred.ask.project'
const base = (p: unknown) => String(p ?? '').split('/').slice(-2).join('/')
const empty = (): Live => ({ status: null, events: [], detail: null, error: null, eventsLoaded: false })

/** One event → one human step, the way Claude's UI narrates "Reading…", "Searching…". */
function toSteps(events: TranscriptEvent[]): Step[] {
  const steps: Step[] = []
  const byToolUse = new Map<string, Step>()
  for (const e of events) {
    const d = e.data as Record<string, unknown>
    switch (e.kind) {
      case 'thinking': { const t = String(d.text ?? ''); steps.push({ id: e.ts + 't', cls: 'think', lab: 'Thinking', txt: t.split('\n')[0]?.slice(0, 140) || '…', detail: t }); break }
      case 'say': steps.push({ id: e.ts + 's', cls: 'say', lab: 'Says', txt: String(d.text ?? '').slice(0, 200), detail: String(d.text ?? '') }); break
      case 'init': {
        const ms = (d.mcp_servers as { name: string; status: string }[]) ?? []
        const n = (st: string) => ms.filter((m) => m.status === st).length
        const ok = ms.filter((m) => m.status === 'connected').map((m) => m.name).join(', ')
        steps.push({ id: e.ts + 'i', cls: 'remote', lab: 'Connected', txt: ms.length ? `${n('connected')} connected${n('failed') ? `, ${n('failed')} failed` : ''}${n('needs-auth') ? `, ${n('needs-auth')} need auth` : ''} — ${ok || 'none usable'}` : 'no connectors', detail: ms.map((m) => `${m.status.padEnd(10)} ${m.name}`).join('\n') })
        break
      }
      case 'tool_call': {
        const tool = String(d.tool ?? ''); const inp = (d.input ?? {}) as Record<string, unknown>
        let s: Step
        if (tool === 'Read') s = { id: '', cls: 'read', lab: 'Reading', txt: base(inp.file_path) }
        else if (tool === 'Grep') s = { id: '', cls: 'search', lab: 'Searching', txt: `${inp.pattern}${inp.path ? ` in ${base(inp.path)}` : ''}` }
        else if (tool === 'Glob') s = { id: '', cls: 'search', lab: 'Finding', txt: String(inp.pattern ?? '') }
        else if (tool === 'Bash') s = { id: '', cls: 'run', lab: 'Running', txt: String(inp.command ?? '').slice(0, 160) }
        else if (tool === 'Write' || tool === 'Edit') s = { id: '', cls: 'run', lab: 'Editing', txt: base(inp.file_path) }
        else if (tool === 'StructuredOutput') s = { id: '', cls: 'say', lab: 'Answering', txt: 'composing the structured answer' }
        else if (tool.startsWith('mcp__')) {
          const [, server, name] = tool.split('__', 3)
          const lab = /excalidraw/i.test(server ?? '') ? 'Drawing' : /gitlab/i.test(server ?? '') ? 'GitLab' : /jira|atlassian/i.test(server ?? '') ? (/confluence/i.test(name ?? '') ? 'Confluence' : 'Jira') : /365|outlook|teams/i.test(server ?? '') ? 'M365' : 'Tool'
          const key = inp.issue_key ?? inp.key ?? inp.project ?? inp.file_path ?? inp.query ?? inp.sql ?? inp.mr_iid ?? inp.pageId ?? inp.jql ?? ''
          s = { id: '', cls: 'remote', lab, txt: `${name}${key ? `  ${String(key).slice(0, 100)}` : ''}` }
        } else s = { id: '', cls: 'run', lab: tool, txt: JSON.stringify(inp).slice(0, 140) }
        s.id = e.ts + tool + steps.length
        s.detail = JSON.stringify(inp, null, 2)
        steps.push(s); if (d.tool_use_id) byToolUse.set(String(d.tool_use_id), s)
        break
      }
      case 'tool_result': { const s = byToolUse.get(String(d.tool_use_id ?? '')); if (s) s.detail = `${s.detail ?? ''}\n\n→ result:\n${String(d.output ?? '').slice(0, 4000)}`; break }
      case 'tool_denied': steps.push({ id: e.ts + 'd', cls: 'deny', lab: 'Denied', txt: `${d.tool} is not allowed for this role` }); break
      case 'timeout': case 'error': steps.push({ id: e.ts + 'e', cls: 'deny', lab: 'Error', txt: JSON.stringify(d).slice(0, 200) }); break
      case 'contract_invalid': case 'contract_missing': steps.push({ id: e.ts + 'c', cls: 'deny', lab: 'Bad output', txt: String(d.error ?? 'no structured output').slice(0, 200) }); break
      case 'done': steps.push({ id: e.ts + 'z', cls: 'say', lab: 'Done', txt: `${d.turns} turns · ${Math.round(Number(d.duration_ms ?? 0) / 1000)}s · ${fmtCost(Number(d.cost_usd ?? 0))}` }); break
      default: break
    }
  }
  return steps
}

function Steps({ steps, live }: { steps: Step[]; live: boolean }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <div className="steps">
      {steps.map((s, i) => (
        <div key={s.id}>
          <div className={`step-row ${open === s.id ? 'open' : ''}`} onClick={() => setOpen(open === s.id ? null : s.id)} title={s.detail ? 'click for input and result' : ''}>
            <span className={`dot ${s.cls} ${live && i === steps.length - 1 ? 'live' : ''}`} />
            <span className="lab">{s.lab}</span>
            <span className="txt mono">{s.txt}</span>
          </div>
          {open === s.id && s.detail && <pre className="log small step-detail" style={{ maxHeight: 360, whiteSpace: 'pre-wrap' }}>{s.detail}</pre>}
        </div>
      ))}
    </div>
  )
}

/** Keeps the Excalidraw frontend connected (invisibly) while the Ask page is open, so the
 *  lead's Mermaid→canvas conversion and image exports work. No UI of its own. */
function CanvasConnection({ url, configured }: { url: string; configured: boolean }) {
  if (!configured) return null
  return <iframe src={url} title="Excalidraw connection" aria-hidden style={{ position: 'fixed', width: 1, height: 1, opacity: 0, pointerEvents: 'none', bottom: 0, right: 0, border: 0 }} />
}

/** Shown under a turn only when that turn drew on the canvas. */
function TurnCanvas({ url }: { url: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginTop: 8 }}>
      <span className="actions" style={{ alignItems: 'center' }}>
        <button className="btn-link" onClick={() => setOpen(!open)}>{open ? 'hide live canvas' : 'show live canvas'}</button>
        <a className="btn-link" href={url} target="_blank" rel="noreferrer">open in a tab</a>
        <span className="muted small">the lead drew on the shared canvas</span>
      </span>
      {open && <iframe src={url} title="Excalidraw canvas" style={{ width: '100%', height: 600, border: '1px solid var(--border)', borderRadius: 8, marginTop: 8, background: '#fff', display: 'block' }} />}
    </div>
  )
}

/** Memory entries a run proposed, reviewed right in the chat: keep for this chat's project, make global, or reject. */
function Proposals({ items, onChange }: { items: Memory[]; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const act = async (id: string, fn: () => Promise<unknown>) => { setBusy(id); try { await fn(); onChange() } finally { setBusy(null) } }
  const pending = items.filter((m) => m.status === 'proposed')
  const decided = items.filter((m) => m.status !== 'proposed')
  return (
    <div className="small" style={{ marginTop: 10, borderTop: '1px dashed var(--border)', paddingTop: 8 }}>
      <div style={{ color: 'var(--warn)', marginBottom: 4 }}>✎ memory {pending.length ? `— ${pending.length} proposal${pending.length === 1 ? '' : 's'} to review` : `— ${decided.length} entr${decided.length === 1 ? 'y' : 'ies'} recorded`} · <a href={`/memory?project=${encodeURIComponent(items[0].project_key)}`}>Memory page</a></div>
      {pending.map((m) => (
        <div key={m.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', padding: '3px 0' }}>
          <span className={`pill ${m.kind}`}>{m.kind}</span>
          <span style={{ flex: 1, minWidth: 240 }}><b>{m.title}</b> <span className="muted">{m.body.length > 140 ? m.body.slice(0, 140) + '…' : m.body}</span></span>
          <span className="actions">
            <button className="btn-link" style={{ color: 'var(--ok)', borderColor: 'var(--ok)' }} disabled={busy === m.id} onClick={() => act(m.id, () => api.memoryUpdate(m.id, { status: 'active' }))} title={`keep for ${m.project_key}`}>✓ {m.project_key === 'global' ? 'accept' : 'this project'}</button>
            {m.project_key !== 'global' && <button className="btn-link" disabled={busy === m.id} onClick={() => act(m.id, () => api.memoryUpdate(m.id, { status: 'active', project_key: 'global' }))} title="applies to every project">✓ global</button>}
            <button className="btn-link" disabled={busy === m.id} onClick={() => act(m.id, () => api.memoryDelete(m.id))}>✗ reject</button>
          </span>
        </div>
      ))}
      {decided.length > 0 && <div className="muted">{decided.map((m) => `${m.title} (${m.status}${m.project_key === 'global' ? ', global' : ''})`).join(' · ')}</div>}
    </div>
  )
}

function AssistantTurn({ turn, live, onNeedEvents, onNeedDetail, canvasUrl, onStop }: { turn: ConversationTurn; live: Live; onNeedEvents: () => void; onNeedDetail: () => void; canvasUrl: string; onStop: () => Promise<void> }) {
  const [stopping, setStopping] = useState(false)
  const [showSteps, setShowSteps] = useState(false)
  const steps = toSteps(live.events)
  const finished = ['done', 'failed', 'stuck', 'cancelled', 'closed'].includes(turn.status) || !!live.detail
  const running = !finished && !live.error
  const lead = leadOutput(live.detail?.root.structured ?? turn.structured)
  const rawS = live.detail?.root.structured ?? turn.structured
  const brief = (rawS && typeof rawS === 'object' && 'headline' in (rawS as object)) ? (rawS as BriefDoc) : null
  const links = live.detail?.code_links ?? []
  const last = steps[steps.length - 1]
  const drew = !!turn.drew || live.events.some((e) => e.kind === 'tool_call' && String((e.data as Record<string, unknown>).tool ?? '').startsWith('mcp__excalidraw__'))
  const headline = running
    ? (live.status === 'open' || turn.status === 'open' ? 'waiting for the team lead to pick this up…' : 'team lead is working…')
    : `${lead?.kind === 'answer' ? 'answered' : turn.status} · ${live.eventsLoaded ? `${steps.length} steps` : 'show what it did'}${turn.resumed ? ' · continued the session' : ''}`
  const toggle = () => { const next = !showSteps; setShowSteps(next); if (next && !live.eventsLoaded && finished) onNeedEvents() }
  return (
    <div className="bubble assistant">
      <div className="working" style={{ cursor: 'pointer', userSelect: 'none', marginBottom: showSteps ? 8 : 0 }} onClick={toggle} title={showSteps ? 'hide steps' : 'show what it did'}>
        <span style={{ width: 8, height: 8, borderRadius: 4, display: 'inline-block', background: running ? '#bb9af7' : 'var(--muted)' }} className={running ? 'dot think live' : ''} />
        <span>{headline}</span>
        {running && last && !showSteps && <span className="mono muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 520 }}>· {last.lab} {last.txt}</span>}
        <span className="muted">{showSteps ? '▾' : '▸'}</span>
        {running && <button className="btn-link" style={{ marginLeft: 'auto', borderColor: 'var(--bad)', color: 'var(--bad)' }} disabled={stopping}
          onClick={async (e) => { e.stopPropagation(); setStopping(true); try { await onStop() } finally { setStopping(false) } }}>{stopping ? 'stopping…' : '■ stop'}</button>}
      </div>
      {drew && <TurnCanvas url={canvasUrl} />}
      {showSteps && (steps.length > 0 ? <Steps steps={steps} live={running} /> : <div className="muted small" style={{ marginTop: 6 }}>{live.eventsLoaded ? 'no transcript on disk for this turn' : 'loading…'}</div>)}
      {live.error && <div className="err small">{live.error}</div>}
      {finished && lead?.kind === 'answer' && lead.answer && <div style={{ marginTop: 10 }}><AnswerContent a={lead.answer} links={links} compact assets={live.detail?.assets ?? []} /></div>}
      {finished && brief && <div style={{ marginTop: 10 }} className="brief-inline"><BriefBody b={brief} compact /><a className="btn-link" href={`/brief/${turn.chain_id}`} target="_blank" rel="noreferrer">open the full brief · PDF</a></div>}
      {finished && (live.detail?.memories?.length ?? 0) > 0 && <Proposals items={live.detail!.memories} onChange={onNeedDetail} />}
      {finished && !live.detail?.memories?.length && !!lead?.memory_proposals?.length && (
        <div className="small" style={{ marginTop: 10, color: 'var(--warn)' }}>✎ proposed {lead.memory_proposals.length} memory entr{lead.memory_proposals.length === 1 ? 'y' : 'ies'} · <a href="/memory?status=proposed">review</a></div>
      )}

      {finished && lead?.kind === 'plan' && lead.plan && (() => { const dispatched = (live.detail?.tasks.length ?? 1) > 1; return (
        <div style={{ marginTop: 10 }}>
          {dispatched
            ? <div className="small" style={{ marginBottom: 6 }}><span className="pill plan">plan</span> This is work, not a question — the lead split it into {lead.plan.subtasks.length} subtask(s) and handed them to developers.</div>
            : <div className="small" style={{ marginBottom: 6 }}><span className="pill stuck">plan · not dispatched</span> The lead produced a {lead.plan.subtasks.length}-subtask plan but <b>no repository was given</b>, so no developers were started. To implement it, ask again with the repository path filled in below.</div>}
          <PlanContent p={lead.plan} />
          {dispatched && <div className="small">Follow the developers and QA on the <a href={`/chains/${turn.chain_id}`}>chain page</a>.</div>}
        </div>
      ) })()}
      {finished && turn.status === 'cancelled' && <div className="small" style={{ color: 'var(--warn)', marginTop: 6 }}>■ stopped by you{turn.result?.includes('before start') ? ' before it started' : ''} — cost so far {fmtCost(live.detail?.cost_usd ?? turn.cost_usd)}</div>}
      {finished && !lead && !['done', 'cancelled'].includes(turn.status) && <div className="err small">The run ended with status <b>{turn.status}</b>{turn.result ? `: ${turn.result.slice(0, 400)}` : ''}</div>}
      {finished && <div className="meta">{fmtCost(live.detail?.cost_usd ?? turn.cost_usd)} · <a href={`/chains/${turn.chain_id}`}>full chain, transcript and per-turn cost</a></div>}
    </div>
  )
}

export default function Ask() {
  const { cid } = useParams()
  const nav = useNavigate()
  const [convs, setConvs] = useState<Conversation[]>([])
  const [turns, setTurns] = useState<ConversationTurn[]>([])
  const [title, setTitle] = useState<string>('')
  const [lives, setLives] = useState<Record<string, Live>>({})
  const [text, setText] = useState('')
  const [project, setProject] = useState(() => { try { return localStorage.getItem(PROJECT_KEY) ?? '' } catch { return '' } })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [canvas, setCanvas] = useState<{ url: string; configured: boolean }>({ url: 'http://localhost:3000', configured: false })
  const closers = useRef<Record<string, () => void>>({})
  const bottom = useRef<HTMLDivElement>(null)
  const stick = useRef(true)

  const patch = (id: string, f: (l: Live) => Live) => setLives((m) => ({ ...m, [id]: f(m[id] ?? empty()) }))
  const loadConvs = () => api.conversations().then(setConvs).catch(() => {})

  function follow(id: string) {
    closers.current[id]?.()
    closers.current[id] = streamTask(id, {
      status: (s) => patch(id, (l) => ({ ...l, status: s.status })),
      log: (e) => patch(id, (l) => ({ ...l, events: [...l.events, e], eventsLoaded: true })),
      end: (d) => { patch(id, (l) => ({ ...l, detail: d, eventsLoaded: true })); setTurns((ts) => ts.map((t) => t.chain_id === id ? { ...t, status: d.root.status, structured: d.root.structured, cost_usd: d.cost_usd, kind: d.kind } : t)); loadConvs() },
      error: () => patch(id, (l) => l.detail ? l : { ...l, error: 'lost connection to the stream — reload to reconnect' }),
    })
  }
  function loadEvents(id: string) {
    api.transcript(id).then((tr) => patch(id, (l) => ({ ...l, events: tr.events, eventsLoaded: true }))).catch(() => patch(id, (l) => ({ ...l, eventsLoaded: true })))
    api.chain(id).then((d) => patch(id, (l) => ({ ...l, detail: d }))).catch(() => {})
  }

  useEffect(() => {
    const onScroll = () => { stick.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 120 }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  useEffect(() => { loadConvs(); api.canvas().then(setCanvas).catch(() => {}) }, [])
  useEffect(() => {
    Object.values(closers.current).forEach((c) => c()); closers.current = {}
    setLives({}); setTurns([]); setTitle('')
    if (!cid) return
    api.conversation(cid).then((c) => {
      setTitle(c.title); setTurns(c.turns)
      if (c.project_dir) setProject(c.project_dir)   // attached by you, or by the lead when it found the checkout
      for (const t of c.turns) {
        if (['done', 'failed', 'stuck'].includes(t.status)) api.chain(t.chain_id).then((d) => patch(t.chain_id, (l) => ({ ...l, detail: d }))).catch(() => {})
        else follow(t.chain_id)
      }
      stick.current = true; setTimeout(() => bottom.current?.scrollIntoView(), 50)
    }).catch(() => setErr(`conversation ${cid} not found`))
    return () => { Object.values(closers.current).forEach((c) => c()) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid])
  useEffect(() => { try { localStorage.setItem(PROJECT_KEY, project) } catch { /* ignore */ } }, [project])
  useEffect(() => { if (stick.current) bottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [turns.length, Object.values(lives).reduce((n, l) => n + l.events.length, 0)])

  const loc = useLocation()
  useEffect(() => { const p = (loc.state as { prefill?: string } | null)?.prefill; if (p) setText(p) }, [loc.state])
  const [remembered, setRemembered] = useState<Memory[]>([])
  useEffect(() => { setRemembered([]) }, [cid])

  async function send() {
    const q = text.trim(); if (!q) return
    setBusy(true); setErr(null)
    try {
      const r = await api.createJob(q, project.trim() || undefined, cid)
      stick.current = true
      setText('')
      if (r.remembered) { setRemembered((xs) => [...xs, r.remembered!]); return }
      if (!cid) { nav(`/ask/${r.conversation_id}`); return }
      setTurns((ts) => [...ts, { chain_id: r.chain_id!, question: q, status: 'open', kind: 'answer', created_at: Date.now() / 1000, finished_at: null, cost_usd: 0, structured: null, result: null }])
      patch(r.chain_id!, (l) => ({ ...l, status: 'open' }))
      follow(r.chain_id!)
      loadConvs()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  async function rename() {
    if (!cid) return
    const t = window.prompt('Conversation title', title)
    if (t && t.trim()) { await api.renameConversation(cid, t.trim()); setTitle(t.trim()); loadConvs() }
  }
  async function forget() {
    if (!cid) return
    await api.deleteConversation(cid); loadConvs(); nav('/ask')
  }

  return (
    <div className="ask-layout">
      <aside className="convs">
        <button style={{ width: '100%', marginBottom: 10 }} onClick={() => nav('/ask')}>+ New chat</button>
        {convs.length === 0 && <div className="muted small" style={{ padding: 8 }}>no conversations yet</div>}
        {convs.map((c) => (
          <a key={c.id} className={`conv ${c.id === cid ? 'active' : ''}`} href={`/ask/${c.id}`} onClick={(e) => { e.preventDefault(); nav(`/ask/${c.id}`) }}>
            <div className="t">{c.title}</div>
            <div className="m">{c.turns} turn{c.turns === 1 ? '' : 's'} · {fmtCost(c.cost_usd)} · {ago(c.updated_at)}{c.last_status === 'claimed' || c.last_status === 'open' ? ' · working' : ''}</div>
          </a>
        ))}
      </aside>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
          <h1 style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cid ? title || '…' : 'Ask'}</h1>
          {cid && <span className="actions"><a className="btn-link" href={`/ask/${cid}/export`} target="_blank" rel="noreferrer" title="Printable page of the whole chat — save as PDF or download Markdown">export</a><button className="btn-link" onClick={rename}>rename</button><button className="btn-link" onClick={forget} title="Removes it from this list; the chains stay on the board">forget</button></span>}
        </div>
        {err && <p className="err">{err}</p>}
        <CanvasConnection url={canvas.url} configured={canvas.configured} />
        <div className="chat">
          {!cid && <div className="card muted small">Ask the team lead anything about your systems — a ticket, a merge request, how something works, why a pipeline is red. You'll see what it reads and runs while it works, then the answer with the code it relied on. Give a repository path below if the question is about local code; otherwise it reads through GitLab. Follow-ups in the same conversation carry the earlier answers as context.</div>}
          {turns.map((t) => (
            <div key={t.chain_id} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="bubble user">{t.question}<div className="meta">{new Date(t.created_at * 1000).toLocaleString()} · <a href={`/chains/${t.chain_id}`} className="mono">{t.chain_id}</a></div></div>
              <AssistantTurn turn={t} live={lives[t.chain_id] ?? empty()} onNeedEvents={() => loadEvents(t.chain_id)} canvasUrl={canvas.url}
                onNeedDetail={() => api.chain(t.chain_id).then((d) => patch(t.chain_id, (l) => ({ ...l, detail: d }))).catch(() => {})}
                onStop={async () => { await api.stopTask(t.chain_id); setTurns((ts) => ts.map((x) => x.chain_id === t.chain_id && x.status === 'open' ? { ...x, status: 'cancelled', result: 'cancelled before start' } : x)) }} />
            </div>
          ))}
          {remembered.map((m) => (
            <div key={m.id} className="bubble system">
              ✓ remembered for <b>{m.project_key}</b> as a {m.kind}: “{m.title}” · <a href={`/memory?project=${encodeURIComponent(m.project_key)}`}>Memory page</a>
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <div className="composer">
          <div className="inner">
            <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={cid ? 'Continue the conversation… (Enter to send, Shift+Enter for a new line; "remember: …" stores a memory for this project, "remember globally: …" for all)' : 'Ask the team lead… (Enter to send, Shift+Enter for a new line; "remember: …" stores a memory for this project, "remember globally: …" for all)'}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!busy) send() } }} />
            {!project.trim() && <div className="small" style={{ color: 'var(--warn)', margin: '2px 2px 0' }}>no repository attached — the lead can only answer; paste a local checkout path below (or mention it in the message) for developers to apply changes</div>}
            <div className="bar">
              <input className="mono" value={project} onChange={(e) => setProject(e.target.value)} placeholder="repository path (optional) — /Users/you/Desktop/projects/payment-service"
                style={project.trim() ? {} : { borderColor: 'var(--warn)' }} title={project.trim() ? 'developers work in a private worktree of this checkout' : 'no repository attached: the lead can answer and read, but developers cannot change anything'} />
              <button onClick={send} disabled={busy || !text.trim()}>{busy ? 'Sending…' : 'Send'}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
