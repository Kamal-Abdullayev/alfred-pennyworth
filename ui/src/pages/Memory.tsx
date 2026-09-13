import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, ago, type Memory, type MemoryKind, type MemoryProject } from '../api'
import { Markdown } from '../components/Answer'

const KINDS: MemoryKind[] = ['decision', 'fact', 'convention', 'glossary', 'person', 'question', 'todo']
type Draft = { kind: MemoryKind; title: string; body: string; tags: string }
const emptyDraft = (): Draft => ({ kind: 'decision', title: '', body: '', tags: '' })
const splitTags = (s: string) => s.split(/[,\s]+/).map((t) => t.trim().toLowerCase()).filter(Boolean)

function Entry({ m, onChange, projects }: { m: Memory; onChange: () => void; projects: string[] }) {
  const [editing, setEditing] = useState(false)
  const [dest, setDest] = useState(m.project_key)
  const keys = Array.from(new Set(['global', ...projects, m.project_key]))
  const [d, setD] = useState<Draft>({ kind: m.kind, title: m.title, body: m.body, tags: m.tags.join(', ') })
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); try { await fn(); onChange() } finally { setBusy(false) } }
  const save = () => act(async () => { await api.memoryUpdate(m.id, { kind: d.kind, title: d.title.trim(), body: d.body.trim(), tags: splitTags(d.tags) }); setEditing(false) })
  return (
    <div className={`card mem-card ${m.status}`} style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className={`pill ${m.kind}`}>{m.kind}</span>
        {m.status !== 'active' && <span className={`pill ${m.status}`}>{m.status}</span>}
        {m.scope === 'global' && <span className="pill">global</span>}
        <b style={{ flex: 1, minWidth: 200 }}>{editing ? <input value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} /> : m.title}</b>
        <span className="muted small">{m.source}{m.chain_id ? <> · <a href={`/chains/${m.chain_id}`} className="mono">{m.chain_id}</a></> : null} · {ago(m.updated_at)} · <span className="mono">#{m.id}</span></span>
      </div>
      {editing ? (
        <div style={{ marginTop: 8, display: 'grid', gap: 8 }}>
          <textarea value={d.body} onChange={(e) => setD({ ...d, body: e.target.value })} style={{ minHeight: 90 }} />
          <div style={{ display: 'flex', gap: 8 }}>
            <select value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as MemoryKind })} style={{ width: 160 }}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
            <input value={d.tags} onChange={(e) => setD({ ...d, tags: e.target.value })} placeholder="tags, comma separated" />
          </div>
          <span className="actions"><button disabled={busy || !d.title.trim() || !d.body.trim()} onClick={save}>Save</button><button className="secondary" onClick={() => setEditing(false)}>Cancel</button></span>
        </div>
      ) : (
        <div style={{ marginTop: 6 }}><Markdown text={m.body} /></div>
      )}
      {!editing && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
          {m.tags.map((t) => <span key={t} className="tag">{t}</span>)}
          <span className="actions" style={{ marginLeft: 'auto' }}>
            <select value={dest} onChange={(e) => setDest(e.target.value)} title="where this entry belongs: one project's memory, or global (every project)" style={{ width: 'auto', padding: '1px 6px', fontSize: 12 }}>
              {keys.map((k) => <option key={k} value={k}>{k === 'global' ? 'global (all projects)' : k}</option>)}
            </select>
            {m.status === 'proposed' && <button className="btn-link" style={{ color: 'var(--ok)', borderColor: 'var(--ok)' }} disabled={busy} onClick={() => act(() => api.memoryUpdate(m.id, { status: 'active', project_key: dest }))}>✓ accept{dest !== m.project_key ? ` → ${dest === 'global' ? 'global' : 'project'}` : ''}</button>}
            {m.status !== 'proposed' && dest !== m.project_key && <button className="btn-link" disabled={busy} onClick={() => act(() => api.memoryUpdate(m.id, { project_key: dest }))}>move → {dest === 'global' ? 'global' : dest}</button>}
            {m.status === 'proposed' && <button className="btn-link" disabled={busy} onClick={() => act(() => api.memoryDelete(m.id))}>✗ reject</button>}
            {m.status === 'active' && <button className="btn-link" disabled={busy} onClick={() => act(() => api.memoryUpdate(m.id, { status: 'retired' }))}>retire</button>}
            {m.status === 'retired' && <button className="btn-link" disabled={busy} onClick={() => act(() => api.memoryUpdate(m.id, { status: 'active' }))}>reactivate</button>}
            <button className="btn-link" onClick={() => setEditing(true)}>edit</button>
            {m.status !== 'proposed' && (confirm
              ? <><button className="btn-link" style={{ color: 'var(--bad)', borderColor: 'var(--bad)' }} disabled={busy} onClick={() => act(() => api.memoryDelete(m.id))}>delete for good</button><button className="btn-link" onClick={() => setConfirm(false)}>keep</button></>
              : <button className="btn-link" onClick={() => setConfirm(true)}>delete</button>)}
          </span>
        </div>
      )}
    </div>
  )
}

export default function MemoryPage() {
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const [projects, setProjects] = useState<MemoryProject[]>([])
  const [items, setItems] = useState<Memory[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<Draft>(emptyDraft())
  const [intake, setIntake] = useState(params.get('intake') === '1')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [q, setQ] = useState(params.get('q') ?? '')
  const project = params.get('project') ?? 'global'
  const status = params.get('status') ?? 'all'
  const set = (k: string, v: string | null) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p) }

  const loadProjects = () => api.memoryProjects().then(setProjects).catch((e) => setErr(String(e)))
  const load = () => api.memoryList({ project_key: project, status: status === 'all' ? undefined : status, q: q || undefined }).then(setItems).catch((e) => setErr(String(e)))
  useEffect(() => { loadProjects() }, [])
  useEffect(() => { load() }, [project, status, q])
  const refresh = () => { load(); loadProjects() }

  async function add() {
    setBusy(true); setErr(null)
    try { await api.memoryCreate({ project_key: project, kind: draft.kind, title: draft.title.trim(), body: draft.body.trim(), tags: splitTags(draft.tags) }); setDraft(emptyDraft()); setAdding(false); refresh() }
    catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  async function submitIntake() {
    setBusy(true); setErr(null)
    try { const r = await api.memoryIntake(notes, project); setNotes(''); setIntake(false); nav(`/ask/${r.conversation_id}`) }
    catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const cur = projects.find((p) => p.project_key === project)
  const proposedTotal = projects.reduce((n, p) => n + p.proposed, 0)

  return (
    <>
      <h1>Memory</h1>
      <p className="muted small">What the team remembers between chats. <b>Active</b> entries are injected into every agent run for that project (plus global ones). Agents and meeting-notes intakes can only <b>propose</b>; you accept, edit, retire. Projects are keyed by the git remote, so two clones of one service share a memory. In the chat, <span className="mono">remember: …</span> stores an entry directly. Each project is also exported as Markdown under <span className="mono">data/memory/</span>.</p>
      {err && <p className="err">{err}</p>}
      <div className="mem-layout">
        <aside>
          {projects.map((p) => (
            <a key={p.project_key} className={`mem-proj ${p.project_key === project ? 'active' : ''}`} onClick={() => set('project', p.project_key)} title={p.dirs.join('\n')}>
              <div className="k">{p.project_key}</div>
              <div className="m">{p.active} active{p.proposed ? <> · <span style={{ color: 'var(--warn)' }}>{p.proposed} proposed</span></> : null}{p.dirs.length ? ` · ${p.dirs.length} checkout${p.dirs.length === 1 ? '' : 's'}` : ''}</div>
            </a>
          ))}
          {proposedTotal > 0 && <div className="muted small" style={{ padding: '8px 10px' }}>{proposedTotal} proposal{proposedTotal === 1 ? '' : 's'} waiting for your review</div>}
        </aside>
        <div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <h2 style={{ margin: 0, flex: 1, minWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{project}</h2>
            <span className="actions">
              {(['all', 'active', 'proposed', 'retired'] as const).map((s) => <button key={s} className="btn-link" style={s === status ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}} onClick={() => set('status', s === 'all' ? null : s)}>{s}{s === 'proposed' && cur?.proposed ? ` (${cur.proposed})` : ''}</button>)}
            </span>
            <input value={q} onChange={(e) => { setQ(e.target.value); set('q', e.target.value || null) }} placeholder="search title, body, tags" style={{ width: 240 }} />
            <button onClick={() => { setAdding(!adding); setIntake(false) }}>{adding ? 'Cancel' : '+ Add'}</button>
            <button className="secondary" onClick={() => { setIntake(!intake); setAdding(false) }}>{intake ? 'Cancel' : 'Paste meeting notes'}</button>
          </div>
          {cur?.dirs.length ? <div className="muted small mono" style={{ marginBottom: 10 }}>checkouts: {cur.dirs.join(' · ')}</div> : null}

          {adding && (
            <div className="card" style={{ marginBottom: 14, display: 'grid', gap: 8 }}>
              <div style={{ display: 'flex', gap: 8 }}>
                <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as MemoryKind })} style={{ width: 160 }}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
                <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="one-line title — e.g. OTP completion goes through session-proxy, not ASS directly" />
              </div>
              <textarea value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} placeholder="self-contained: what, why, who decided, when. Markdown is fine." style={{ minHeight: 90 }} />
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} placeholder="tags, comma separated (optional)" />
                <button disabled={busy || !draft.title.trim() || !draft.body.trim()} onClick={add}>Save as active</button>
              </div>
            </div>
          )}

          {intake && (
            <div className="card" style={{ marginBottom: 14, display: 'grid', gap: 8 }}>
              <div className="small muted">Paste raw meeting notes, a Teams summary or a decision log. The team lead reads them once (no code), turns every decision, constraint, open question and glossary term into <b>proposed</b> entries for <b>{project}</b>, and you review them here. One lead run, typically well under a dollar.</div>
              <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Meeting 2026-09-06 — OTP rollout… Decided: … Owner: … Deadline: …" style={{ minHeight: 180 }} />
              <div><button disabled={busy || notes.trim().length < 20} onClick={submitIntake}>{busy ? 'Sending…' : 'Distil into memory'}</button></div>
            </div>
          )}

          {items.length === 0 && <div className="card muted small">nothing here yet{status !== 'all' ? ` with status ${status}` : ''}{q ? ` matching “${q}”` : ''}</div>}
          {items.map((m) => <Entry key={m.id} m={m} onChange={refresh} projects={projects.map((p) => p.project_key)} />)}
        </div>
      </div>
    </>
  )
}
