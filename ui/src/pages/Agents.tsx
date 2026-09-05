import { useEffect, useState } from 'react'
import { api, ago, type Agent, type AgentIn, type AgentsResponse, type WorkersResponse } from '../api'

function AgentCard({ a, opts, onSaved, onDeleted, workers, openCount }: { a: Agent; opts: AgentsResponse['options']; onSaved: () => void; onDeleted: () => void; workers: WorkersResponse['workers']; openCount: number }) {
  const [edit, setEdit] = useState(false)
  const [f, setF] = useState<AgentIn>({ model: a.model, contract: a.contract, system_prompt: a.system_prompt, builtin_tools: a.builtin_tools, allowed_tools: a.allowed_tools, permission_mode: a.permission_mode, max_minutes: a.max_minutes, max_turns: a.max_turns, account_connectors: a.account_connectors, workers: a.workers })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState(false)
  const core = ['team_lead', 'developer', 'qa'].includes(a.role)
  useEffect(() => { setF({ model: a.model, contract: a.contract, system_prompt: a.system_prompt, builtin_tools: a.builtin_tools, allowed_tools: a.allowed_tools, permission_mode: a.permission_mode, max_minutes: a.max_minutes, max_turns: a.max_turns, account_connectors: a.account_connectors, workers: a.workers }) }, [a])

  async function save() {
    setBusy(true); setMsg(null)
    try { await api.updateAgent(a.role, f); setMsg('saved — applies to the next run of this role'); setEdit(false); onSaved() } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }
  const toggleTool = (t: string) => setF({ ...f, builtin_tools: f.builtin_tools.includes(t) ? f.builtin_tools.filter((x) => x !== t) : [...f.builtin_tools, t], allowed_tools: f.builtin_tools.includes(t) ? f.allowed_tools.filter((x) => x !== t) : (f.allowed_tools.includes(t) ? f.allowed_tools : [...f.allowed_tools, t]) })
  const mine = workers.filter((w) => w.role === a.role)

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div>
          <b style={{ fontSize: 15 }}><span className={`pill ${a.role}`}>{a.role}</span></b>{' '}
          <span className="mono small">{a.model}</span> <span className="muted small">· contract {a.contract} · {a.permission_mode} · {a.max_minutes} min{a.max_turns ? ` · ${a.max_turns} turns` : ''}</span>
          {a.account_connectors && <span className="pill answered" style={{ marginLeft: 6 }}>account connectors</span>}
        </div>
        <div className="actions">
          <button className="btn-link" onClick={() => setEdit(!edit)}>{edit ? 'Cancel' : 'Edit'}</button>
          {!core && (!confirmDel ? <button className="btn-link" onClick={() => setConfirmDel(true)}>Delete role</button>
            : <><button className="btn-link" style={{ borderColor: 'var(--bad)', color: 'var(--bad)' }} onClick={async () => { await api.deleteAgent(a.role); onDeleted() }}>Confirm delete</button><button className="btn-link" onClick={() => setConfirmDel(false)}>Keep</button></>)}
        </div>
      </div>

      <div className="small" style={{ marginTop: 8, display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="muted">workers {a.workers.min}–{a.workers.max}</span>
        <span>{mine.length} live{mine.filter((w) => w.ephemeral).length ? ` (${mine.filter((w) => w.ephemeral).length} on demand)` : ''}</span>
        <span className={openCount ? '' : 'muted'}>{openCount} queued</span>
        {mine.map((w) => (
          <span key={w.pid} className="pill" title={`pid ${w.pid} · started ${ago(w.started_at)} · seen ${ago(w.last_seen)}`}>
            {w.ephemeral ? '⚡' : '●'} {w.current_title ? <>working: <a href={`/chains/${w.current_chain}`}>{w.current_title.slice(0, 40)}</a></> : 'idle'}
          </span>
        ))}
      </div>

      {msg && <div className={`small ${msg.startsWith('saved') ? 'muted' : 'err'}`} style={{ marginTop: 6 }}>{msg}</div>}

      {!edit && (
        <div className="kv small" style={{ marginTop: 10 }}>
          <div className="k">built-in tools</div><div className="mono">{a.builtin_tools.join(', ') || '—'}</div>
          <div className="k">allow rules</div><div className="mono">{a.allowed_tools.join(', ') || '—'}</div>
          <div className="k">YAML connectors</div><div className="mono">{a.mcp_servers.join(', ') || '—'}</div>
          <div className="k">system prompt</div><div><details><summary className="muted">{a.system_prompt.split('\n')[0]?.slice(0, 90)}…</summary><pre className="log" style={{ marginTop: 6 }}>{a.system_prompt}</pre></details></div>
        </div>
      )}

      {edit && (
        <div style={{ marginTop: 12 }}>
          <div className="grid cols-4" style={{ gap: 10 }}>
            <div><label className="muted small">Model</label><select value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })}>{[...new Set([...opts.models, f.model])].map((m) => <option key={m}>{m}</option>)}</select></div>
            <div><label className="muted small">Contract</label><select value={f.contract} onChange={(e) => setF({ ...f, contract: e.target.value })}>{opts.contracts.map((c) => <option key={c}>{c}</option>)}</select></div>
            <div><label className="muted small">Permission mode</label><select value={f.permission_mode} onChange={(e) => setF({ ...f, permission_mode: e.target.value })}>{opts.permission_modes.map((c) => <option key={c}>{c}</option>)}</select></div>
            <div><label className="muted small">Max minutes / turns</label><div style={{ display: 'flex', gap: 6 }}><input type="number" value={f.max_minutes} onChange={(e) => setF({ ...f, max_minutes: Number(e.target.value) })} /><input type="number" placeholder="∞" value={f.max_turns ?? ''} onChange={(e) => setF({ ...f, max_turns: e.target.value ? Number(e.target.value) : null })} /></div></div>
          </div>
          <div className="grid cols-2" style={{ gap: 10, marginTop: 10 }}>
            <div>
              <label className="muted small">Workers (permanent min · on-demand max)</label>
              <div style={{ display: 'flex', gap: 6 }}><input type="number" min={0} value={f.workers.min} onChange={(e) => setF({ ...f, workers: { ...f.workers, min: Number(e.target.value) } })} /><input type="number" min={1} value={f.workers.max} onChange={(e) => setF({ ...f, workers: { ...f.workers, max: Number(e.target.value) } })} /></div>
              <div className="muted small">The supervisor keeps <i>min</i> running and spawns up to <i>max</i> while this role's queue is longer than its live workers.</div>
            </div>
            <div>
              <label className="muted small">Account connectors</label>
              <label className="small" style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}><input type="checkbox" style={{ width: 'auto' }} checked={f.account_connectors} onChange={(e) => setF({ ...f, account_connectors: e.target.checked })} /> attach the MCP servers your claude.ai account provides (loads ~/.claude settings into the run)</label>
            </div>
          </div>
          <div style={{ marginTop: 10 }}>
            <label className="muted small">Built-in tools (ticking one also allows it)</label>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 4 }}>{opts.builtin_tools.map((t) => <label key={t} className="small mono" style={{ display: 'flex', gap: 4, alignItems: 'center' }}><input type="checkbox" style={{ width: 'auto' }} checked={f.builtin_tools.includes(t)} onChange={() => toggleTool(t)} />{t}</label>)}</div>
          </div>
          <div style={{ marginTop: 10 }}>
            <label className="muted small">Allow rules — one per line (tool names or mcp__server__* wildcards; connectors assigned on the Connectors page are added automatically)</label>
            <textarea className="mono" style={{ minHeight: 80 }} value={f.allowed_tools.join('\n')} onChange={(e) => setF({ ...f, allowed_tools: e.target.value.split('\n').map((x) => x.trim()).filter(Boolean) })} />
          </div>
          <div style={{ marginTop: 10 }}>
            <label className="muted small">System prompt</label>
            <textarea style={{ minHeight: 220 }} value={f.system_prompt} onChange={(e) => setF({ ...f, system_prompt: e.target.value })} />
          </div>
          <div style={{ marginTop: 10, display: 'flex', gap: 10, alignItems: 'center' }}>
            <button onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save to YAML'}</button>
            <span className="muted small">writes agents/{a.role}.yaml; the next run of this role uses it (no restart). Comments in the file are not preserved.</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Agents() {
  const [data, setData] = useState<AgentsResponse | null>(null)
  const [w, setW] = useState<WorkersResponse | null>(null)
  const [newRole, setNewRole] = useState('')
  const [cloneFrom, setCloneFrom] = useState('developer')
  const [msg, setMsg] = useState<string | null>(null)
  const load = () => { api.agents().then(setData).catch((e) => setMsg(String(e))); api.workers().then(setW).catch(() => {}) }
  useEffect(() => { load(); const t = setInterval(() => api.workers().then(setW).catch(() => {}), 5000); return () => clearInterval(t) }, [])

  async function create() {
    setMsg(null)
    try { await api.createAgent(newRole.trim(), cloneFrom); setNewRole(''); load() } catch (e) { setMsg(String(e)) }
  }
  const total = w?.workers.length ?? 0

  return (
    <>
      <h1>Agents</h1>
      <p className="muted small">Every <code>agents/&lt;role&gt;.yaml</code> is a role; edit it here. The team lead decides how many subtasks a job needs and may name a role per subtask; the <b>supervisor</b> spawns workers per role between <i>min</i> and <i>max</i> as the queue grows, under a global cap that protects your seat's rate limit.</p>
      {msg && <p className="err">{msg}</p>}

      <div className="card" style={{ marginBottom: 14, display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
        <div><div className="stat"><div className="v">{total}<span className="muted" style={{ fontSize: 14 }}> / {w?.max_workers ?? '–'}</span></div><div className="l">live workers / global cap</div></div></div>
        <div className="small">
          <label className="muted">Global cap </label>
          <input type="number" min={1} max={16} style={{ width: 70, display: 'inline-block', marginLeft: 6 }} value={w?.max_workers ?? 4} onChange={(e) => api.setMaxWorkers(Number(e.target.value)).then(load).catch((er) => setMsg(String(er)))} />
          <div className="muted">agents running at once, all roles — your Team seat shares one rate limit</div>
        </div>
        <div className="small muted">queued: {Object.entries(w?.open ?? {}).map(([r, n]) => `${r} ${n}`).join(' · ') || 'nothing'}</div>
        {total === 0 && <span className="pill stuck">no workers reporting — start ./run_all.sh (supervisor)</span>}
      </div>

      {data?.agents.map((a) => <AgentCard key={a.role} a={a} opts={data.options} workers={w?.workers ?? []} openCount={w?.open[a.role] ?? 0} onSaved={load} onDeleted={load} />)}

      <h2>Add a role</h2>
      <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input className="mono" style={{ maxWidth: 220 }} placeholder="security_reviewer" value={newRole} onChange={(e) => setNewRole(e.target.value)} />
        <span className="muted small">cloned from</span>
        <select style={{ width: 'auto' }} value={cloneFrom} onChange={(e) => setCloneFrom(e.target.value)}>{data?.agents.map((a) => <option key={a.role}>{a.role}</option>)}</select>
        <button onClick={create} disabled={!newRole.trim()}>Create</button>
        <span className="muted small">starts with workers 0–1: the supervisor spawns it only when the lead routes a subtask to it. Then edit its prompt and tools.</span>
      </div>
    </>
  )
}
