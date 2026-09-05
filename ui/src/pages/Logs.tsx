import { useEffect, useState } from 'react'
import { api, subscribe, type FlowRow, type LogFile, type TranscriptEvent } from '../api'
import { EventList, FILTERS, filterEvents } from '../components/Transcript'

type Ev = TranscriptEvent & { task_id: string | null }

export default function Logs() {
  const [files, setFiles] = useState<LogFile[]>([])
  const [flow, setFlow] = useState<FlowRow[]>([])
  const [role, setRole] = useState<string | null>(null)
  const [events, setEvents] = useState<Ev[]>([])
  const [meta, setMeta] = useState<{ total: number; kinds: Record<string, number>; tasks: Record<string, number> } | null>(null)
  const [filter, setFilter] = useState('all')
  const [task, setTask] = useState<string>('all')
  const [raw, setRaw] = useState<string | null>(null)

  const roles = files.filter((f) => f.name.endsWith('.jsonl') && !f.name.startsWith('tasks/')).map((f) => f.name.replace('.jsonl', ''))
  const loadFlow = () => api.flowRows(300).then((r) => setFlow(r.rows)).catch(() => {})
  useEffect(() => {
    api.logs().then((r) => setFiles(r.files)).catch(() => {})
    loadFlow()
    return subscribe({ flow: () => loadFlow() })
  }, [])
  useEffect(() => {
    if (!role) return
    api.logEvents(`${role}.jsonl`, 600).then((r) => { setEvents(r.events); setMeta({ total: r.total, kinds: r.kinds, tasks: r.tasks }); setTask('all') }).catch(() => { setEvents([]); setMeta(null) })
  }, [role])

  const shown = filterEvents(events, filter).filter((e) => task === 'all' || e.task_id === task)
  const fmt = (b: number) => b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b > 1e3 ? `${(b / 1e3).toFixed(0)} kB` : `${b} B`

  return (
    <>
      <h1>Logs</h1>
      <p className="muted small">The same events as the files under <code>logs/</code>, made readable: one line per event, click to expand. Per-run views live on each chain page (transcript); this page is for following the whole team and for digging through a role's history.</p>

      <h2>Flow — every claim, finish and handover (live)</h2>
      <div className="card" style={{ padding: 0, maxHeight: 320, overflow: 'auto' }}>
        {flow.length === 0 && <div className="muted small" style={{ padding: 10 }}>no flow.log yet — start the daemons</div>}
        {[...flow].reverse().map((r, i) => r.role ? (
          <div key={i} className="flowrow">
            <span className="muted small mono">{r.ts}</span>
            <span className={`pill ${r.role}`}>{r.role}</span>
            <span className={`act small mono ${r.action}`}>{r.action}</span>
            <span className="small"><a className="mono" href={`/chains/${r.task}`}>{r.task}</a> {r.detail}</span>
          </div>
        ) : <div key={i} className="flowrow"><span /><span /><span /><span className="small muted">{r.detail}</span></div>)}
      </div>

      <h2>Agents</h2>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 }}>
        {roles.length === 0 && <span className="muted small">no role logs yet — a role's files appear when its daemon starts</span>}
        {roles.map((r) => <button key={r} className="btn-link" style={{ fontWeight: role === r ? 700 : 400 }} onClick={() => setRole(r)}><span className={`pill ${r}`}>{r}</span> {fmt(files.find((f) => f.name === `${r}.jsonl`)?.bytes ?? 0)}</button>)}
      </div>
      {role && meta && (
        <>
          <div className="actions" style={{ flexWrap: 'wrap', marginBottom: 6, alignItems: 'center' }}>
            <span className="muted small">{meta.total} events · filter:</span>
            {FILTERS.map((k) => <button key={k} className="btn-link" style={{ fontWeight: filter === k ? 700 : 400 }} onClick={() => setFilter(k)}>{k}{k !== 'all' && k !== 'tools' && meta.kinds[k] ? ` ${meta.kinds[k]}` : ''}</button>)}
            <span className="muted small" style={{ marginLeft: 10 }}>run:</span>
            <select style={{ width: 'auto' }} value={task} onChange={(e) => setTask(e.target.value)}>
              <option value="all">all runs</option>
              {Object.entries(meta.tasks).map(([t, n]) => <option key={t} value={t}>{t} ({n})</option>)}
            </select>
            <a className="btn-link" href={`/api/logs/file?name=${role}.log&tail=0`} target="_blank" rel="noreferrer">human .log</a>
            <button className="btn-link" onClick={() => api.logFile(`${role}.log`, 400).then((r) => setRaw(r.lines.join('\n')))}>show human .log here</button>
          </div>
          <EventList events={shown} showTask />
          {raw && <pre className="log" style={{ marginTop: 10, maxHeight: 420 }}>{raw}</pre>}
        </>
      )}

      <h2>Files</h2>
      <div className="card" style={{ padding: 0 }}>
        <table><thead><tr><th>File</th><th>Size</th><th>Modified</th></tr></thead>
          <tbody>{files.map((f) => <tr key={f.name}><td className="mono small">{f.name}</td><td className="mono small">{fmt(f.bytes)}</td><td className="muted small">{new Date(f.mtime * 1000).toLocaleString()}</td></tr>)}
          {files.length === 0 && <tr><td colSpan={3} className="muted">logs/ is empty</td></tr>}</tbody>
        </table>
      </div>
    </>
  )
}
