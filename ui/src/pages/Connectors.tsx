import { useEffect, useState } from 'react'
import { api, ago, fmtTime, type Connector, type ConnectorsResponse, type ConnectorIn, type Template, type LogEntry } from '../api'

type Draft = { name: string; template: string; kind: 'stdio' | 'http' | 'sse'; command: string; args: string; url: string; env: Record<string, { secret: boolean; value: string }>; headers: Record<string, { secret: boolean; value: string }>; note: string }

function emptyDraft(): Draft { return { name: '', template: 'custom-stdio', kind: 'stdio', command: '', args: '', url: '', env: {}, headers: {}, note: '' } }

function draftFromTemplate(id: string, t: Template): Draft {
  const env: Draft['env'] = {}
  for (const [k, f] of Object.entries(t.env)) env[k] = { secret: f.secret, value: f.default ?? '' }
  const headers: Draft['headers'] = {}
  for (const [k, f] of Object.entries(t.headers)) headers[k] = { secret: f.secret, value: f.default ?? '' }
  return { name: id === 'custom-stdio' || id === 'custom-http' ? '' : id, template: id, kind: t.kind, command: t.command ?? '', args: t.args.join(' '), url: t.url ?? '', env, headers, note: '' }
}

export default function Connectors() {
  const [data, setData] = useState<ConnectorsResponse | null>(null)
  const [templates, setTemplates] = useState<Record<string, Template>>({})
  const [draft, setDraft] = useState<Draft>(emptyDraft())
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  const load = () => api.connectors().then(setData).catch((e) => setMsg(String(e)))
  useEffect(() => { load(); api.templates().then(setTemplates).catch(() => {}) }, [])

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label); setMsg(null)
    try { await fn(); await load() } catch (e) { setMsg(String(e)) } finally { setBusy(null) }
  }

  async function save() {
    const body: ConnectorIn = {
      name: draft.name.trim(), template: draft.template, kind: draft.kind, command: draft.kind === 'stdio' ? draft.command.trim() : null,
      args: draft.args.trim() ? draft.args.trim().split(/\s+/) : [], url: draft.kind === 'stdio' ? null : draft.url.trim(),
      env: Object.fromEntries(Object.entries(draft.env).map(([k, v]) => [k, { secret: v.secret, value: v.value || null }])),
      headers: Object.fromEntries(Object.entries(draft.headers).map(([k, v]) => [k, { secret: v.secret, value: v.value || null }])),
      note: draft.note || null, enabled: true,
    }
    await run('save', async () => { await api.saveConnector(body); setAdding(false); setOpen(body.name); setDraft(emptyDraft()) })
  }

  const registeredNotImported = (data?.registered ?? []).filter((r) => !data?.connectors.some((c) => c.name === r.name))

  return (
    <>
      <h1>Connectors</h1>
      <p className="muted small">MCP servers the agents can use. Configure here, <b>Test</b> to discover tools, tick the roles that may use it. Only tools classified <i>read</i> are ever allowed to an agent; <i>mutates</i> tools are denied by the runner's gate. Secrets live in the macOS Keychain and are never shown or returned. Connectors marked <b>your Claude account</b> are injected into every run by your claude.ai login (OAuth already done there) — no configuration needed, just tick roles.</p>
      {msg && <p className={msg.startsWith('Found') || msg.startsWith('No claude') ? 'muted small' : 'err'}>{msg}</p>}

      <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button onClick={() => { setAdding(!adding); setDraft(draftFromTemplate('custom-stdio', templates['custom-stdio'] ?? { label: '', kind: 'stdio', command: '', args: [], env: {}, headers: {}, note: '' })) }}>{adding ? 'Cancel' : '+ Add connector'}</button>
        <button className="secondary" disabled={busy !== null} onClick={() => run('discover', async () => { const r = await api.discoverConnectors(); setMsg(r.account_servers.length ? `Found from your Claude account: ${r.account_servers.join(', ')}` : 'No claude.ai connectors visible to agent runs') })}>{busy === 'discover' ? 'Probing…' : 'Discover account connectors'}</button>
        <span className="muted small">runs one tiny haiku turn (≈1¢) to read which MCP servers your Claude login injects</span>
      </div>

      {adding && (
        <div className="card" style={{ marginBottom: 16, maxWidth: 820 }}>
          <div className="grid cols-2" style={{ gap: 10 }}>
            <div>
              <label className="muted small">Template</label>
              <select value={draft.template} onChange={(e) => { const id = e.target.value; setDraft(draftFromTemplate(id, templates[id]!)) }}>
                {Object.entries(templates).map(([id, t]) => <option key={id} value={id}>{t.label}</option>)}
              </select>
            </div>
            <div><label className="muted small">Name (used as mcp__&lt;name&gt;__tool)</label><input className="mono" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="confluence" /></div>
          </div>
          {templates[draft.template]?.note && <p className="muted small">{templates[draft.template]!.note}</p>}
          {draft.kind === 'stdio' ? (
            <div className="grid cols-2" style={{ gap: 10, marginTop: 8 }}>
              <div><label className="muted small">Command</label><input className="mono" value={draft.command} onChange={(e) => setDraft({ ...draft, command: e.target.value })} /></div>
              <div><label className="muted small">Args (space separated)</label><input className="mono" value={draft.args} onChange={(e) => setDraft({ ...draft, args: e.target.value })} /></div>
            </div>
          ) : (
            <div style={{ marginTop: 8 }}><label className="muted small">URL</label><input className="mono" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} /></div>
          )}
          {Object.keys(draft.env).length > 0 && <h2>Environment</h2>}
          {Object.entries(draft.env).map(([k, v]) => (
            <div key={k} className="grid cols-2" style={{ gap: 10, alignItems: 'center', marginBottom: 6 }}>
              <label className="mono small">{k} {v.secret && <span className="pill">secret → Keychain</span>}<div className="muted small">{templates[draft.template]?.env[k]?.help}</div></label>
              <input type={v.secret ? 'password' : 'text'} className="mono" value={v.value} onChange={(e) => setDraft({ ...draft, env: { ...draft.env, [k]: { ...v, value: e.target.value } } })} />
            </div>
          ))}
          {Object.keys(draft.headers).length > 0 && <h2>Headers</h2>}
          {Object.entries(draft.headers).map(([k, v]) => (
            <div key={k} className="grid cols-2" style={{ gap: 10, alignItems: 'center', marginBottom: 6 }}>
              <label className="mono small">{k} {v.secret && <span className="pill">secret → Keychain</span>}</label>
              <input type={v.secret ? 'password' : 'text'} className="mono" value={v.value} onChange={(e) => setDraft({ ...draft, headers: { ...draft.headers, [k]: { ...v, value: e.target.value } } })} />
            </div>
          ))}
          <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button onClick={save} disabled={busy !== null || !draft.name.trim() || (draft.kind === 'stdio' ? !draft.command.trim() : !draft.url.trim())}>{busy === 'save' ? 'Saving…' : 'Save'}</button>
            <span className="muted small">Then press Test to discover its tools.</span>
          </div>
        </div>
      )}

      <h2>Configured ({data?.connectors.length ?? 0})</h2>
      {data?.connectors.length === 0 && <div className="card muted">None yet. Add one above, or import a server already registered for Claude below.</div>}
      {data?.connectors.map((c) => <ConnectorCard key={c.name} c={c} roles={data.roles} open={open === c.name} onToggle={() => setOpen(open === c.name ? null : c.name)} busy={busy} run={run} />)}

      {registeredNotImported.length > 0 && (
        <>
          <h2>Registered for Claude, not yet on the board ({registeredNotImported.length})</h2>
          <div className="card" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>Name</th><th>Kind</th><th>Where</th><th>Command / URL</th><th></th></tr></thead>
              <tbody>{registeredNotImported.map((r) => (
                <tr key={r.source + r.name}><td><b>{r.name}</b></td><td>{r.kind}</td><td className="small">{r.source}</td><td className="mono small muted">{r.command}</td>
                  <td><button className="btn-link" disabled={busy !== null} onClick={() => run('import:' + r.name, () => api.importConnector(r.source, r.name))}>{busy === 'import:' + r.name ? 'Importing…' : 'Import'}</button></td></tr>
              ))}</tbody>
            </table>
          </div>
          <p className="muted small">Import copies the definition onto the board; credential-looking values move to the Keychain. OAuth-only HTTP servers (like the hosted Atlassian one) work via the mcp-remote template instead.</p>
        </>
      )}

      {(data?.yaml.length ?? 0) > 0 && (
        <>
          <h2>Defined in agent YAML</h2>
          <div className="card small">{data!.yaml.map((y) => <div key={y.name}><span className="mono">{y.name}</span> — used by {y.used_by.map((r) => <span key={r} className={`pill ${r}`} style={{ marginLeft: 4 }}>{r}</span>)}</div>)}
            <p className="muted" style={{ marginBottom: 0 }}>These are wired directly in <code>agents/*.yaml</code>; a board connector with the same name is shadowed by the YAML one.</p></div>
        </>
      )}
    </>
  )
}

function ConnectorCard({ c, roles, open, onToggle, busy, run }: { c: Connector; roles: string[]; open: boolean; onToggle: () => void; busy: string | null; run: (l: string, fn: () => Promise<unknown>) => Promise<void> }) {
  const [testMsg, setTestMsg] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const status = c.last_test_status
  return (
    <div className="card" style={{ marginBottom: 10, opacity: c.enabled ? 1 : 0.6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div>
          <b style={{ fontSize: 15 }}>{c.name}</b> {c.provider === 'claude-account' ? <span className="pill answered">your Claude account</span> : <span className="pill">{c.kind}</span>}{' '}
          {status === 'ok' && <span className="pill pass">tested {ago(c.last_test_at)}</span>}
          {status === 'failed' && <span className="pill fail">test failed</span>}
          {!status && <span className="pill">untested</span>}
          {!c.enabled && <span className="pill stuck">disabled</span>}
          <div className="muted small mono">{c.provider === 'claude-account' ? 'discovered from agent runs · tools refresh every run' : c.kind === 'stdio' ? `${c.command ?? ''} ${c.args.join(' ')}` : c.url}</div>
        </div>
        <div className="actions">
          {c.provider !== 'claude-account' && <button className="btn-link" disabled={busy !== null} onClick={() => run('test:' + c.name, async () => { const r = await api.testConnector(c.name); setTestMsg(r.ok ? `${r.tools?.length ?? 0} tools, ${r.mutating ?? 0} mutating` : r.error) })}>{busy === 'test:' + c.name ? 'Testing…' : 'Test'}</button>}
          <button className="btn-link" disabled={busy !== null} onClick={() => run('en:' + c.name, () => api.setConnectorEnabled(c.name, !c.enabled))}>{c.enabled ? 'Disable' : 'Enable'}</button>
          <button className="btn-link" onClick={onToggle}>{open ? 'Hide' : 'Details'}</button>
          {!confirmDelete
            ? <button className="btn-link" disabled={busy !== null} onClick={() => setConfirmDelete(true)}>Delete</button>
            : <>
                <button className="btn-link" style={{ borderColor: 'var(--bad)', color: 'var(--bad)' }} disabled={busy !== null} onClick={() => run('del:' + c.name, () => api.deleteConnector(c.name))}>{busy === 'del:' + c.name ? 'Deleting…' : 'Confirm delete (removes Keychain secrets too)'}</button>
                <button className="btn-link" onClick={() => setConfirmDelete(false)}>Keep</button>
              </>}
        </div>
      </div>
      {testMsg && <div className={`small ${c.last_test_status === 'failed' ? 'err' : 'muted'}`} style={{ marginTop: 6 }}>{testMsg}</div>}
      {c.last_test_status === 'failed' && !testMsg && c.last_test_error && <div className="err small" style={{ marginTop: 6 }}>{c.last_test_error}</div>}

      <div style={{ marginTop: 10, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="muted small">Roles that may use it:</span>
        {roles.map((r) => (
          <label key={r} className="small" style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <input type="checkbox" style={{ width: 'auto' }} checked={c.roles.includes(r)} disabled={busy !== null}
              onChange={(e) => run('roles:' + c.name, () => api.setConnectorRoles(c.name, e.target.checked ? [...c.roles, r] : c.roles.filter((x) => x !== r)))} />
            <span className={`pill ${r}`}>{r}</span>
          </label>
        ))}
        <span className="muted small">· {c.tool_count} tool{c.tool_count === 1 ? '' : 's'}{c.tool_count ? `, ${c.mutating} mutating (denied)` : ' — run Test to discover'}</span>
        {c.yaml_used_by.length > 0 && <span className="pill stuck">shadowed by YAML for {c.yaml_used_by.join(', ')}</span>}
      </div>

      {open && (
        <div style={{ marginTop: 12 }}>
          {Object.keys(c.env).length > 0 && (
            <div className="kv small" style={{ marginBottom: 10 }}>
              {Object.entries(c.env).map(([k, v]) => <><div key={k + 'k'} className="k mono">{k}</div><div key={k + 'v'} className="mono">{v.secret ? (v.set ? '•••••• (Keychain)' : <span className="err">secret not set</span>) : (v.value || <span className="muted">empty</span>)}</div></>)}
              {Object.entries(c.headers).map(([k, v]) => <><div key={'h' + k + 'k'} className="k mono">header {k}</div><div key={'h' + k + 'v'} className="mono">{v.secret ? (v.set ? '•••••• (Keychain)' : <span className="err">secret not set</span>) : 'set'}</div></>)}
            </div>
          )}
          {c.tools.length > 0 ? (
            <table>
              <thead><tr><th>Tool</th><th>Description</th><th>Access</th><th>Decided by</th></tr></thead>
              <tbody>{c.tools.map((t) => (
                <tr key={t.tool}>
                  <td className="mono small">{t.tool}</td>
                  <td className="small muted">{(t.description ?? '').slice(0, 140)}</td>
                  <td><button className={`pill ${t.mutates ? 'fail' : 'pass'}`} style={{ cursor: 'pointer', background: 'none' }} disabled={busy !== null}
                    onClick={() => run('mut:' + t.tool, () => api.setToolMutates(c.name, t.tool, !t.mutates))} title="click to flip">{t.mutates ? 'mutates — denied' : 'read — allowed'}</button></td>
                  <td className="small muted">{t.source}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : <div className="muted small">No tools recorded yet — Test the connector.</div>}
          {c.note && <p className="muted small">{c.note}</p>}
          {c.masked_config && <details style={{ marginTop: 8 }}><summary className="small">resolved config (secrets masked)</summary><pre className="log">{JSON.stringify(c.masked_config, null, 2)}</pre></details>}
          <LogPanel name={c.name} refreshKey={`${c.last_test_at}-${c.tool_count}-${c.roles.join(',')}-${c.enabled}`} />
        </div>
      )}
    </div>
  )
}

function LogPanel({ name, refreshKey }: { name: string; refreshKey: string }) {
  const [log, setLog] = useState<LogEntry[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  useEffect(() => { api.connectorLog(name).then(setLog).catch(() => {}) }, [name, refreshKey])
  return (
    <div style={{ marginTop: 12 }}>
      <h2 style={{ marginTop: 0 }}>Log <span className="muted" style={{ textTransform: 'none', letterSpacing: 0 }}>· {log.length} entries, newest first, secrets masked</span></h2>
      {log.length === 0 && <div className="muted small">nothing yet</div>}
      <div className="card" style={{ padding: 0, maxHeight: 360, overflow: 'auto' }}>
        {log.map((e) => (
          <div key={e.id} style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', cursor: e.data ? 'pointer' : 'default' }} onClick={() => setExpanded(expanded === e.id ? null : e.id)}>
            <span className="muted small mono">{fmtTime(e.created_at)}</span>{' '}
            <span className={`pill ${e.level === 'error' ? 'fail' : e.level === 'warn' ? 'stuck' : ''}`}>{e.event}</span>{' '}
            <span className="small">{e.message}</span>
            {e.data && expanded === e.id && <pre className="log small" style={{ marginTop: 6 }}>{JSON.stringify(e.data, null, 2)}</pre>}
            {e.data && expanded !== e.id && <span className="muted small"> · click for details</span>}
          </div>
        ))}
      </div>
    </div>
  )
}
