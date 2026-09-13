import { useEffect, useState } from 'react'
import { api } from '../api'

/** Setup problems, shown only when something is wrong (same checks as `python doctor.py`). */
export default function Doctor() {
  const [r, setR] = useState<Awaited<ReturnType<typeof api.doctor>> | null>(null)
  const [show, setShow] = useState(false)
  useEffect(() => { api.doctor().then(setR).catch(() => {}) }, [])
  if (!r) return null
  const bad = r.checks.filter((c) => !c.ok)
  if (bad.length === 0) return null
  const fails = bad.filter((c) => c.required)
  return (
    <div className="card" style={{ marginBottom: 14, borderColor: fails.length ? 'var(--bad)' : 'var(--warn)' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <b style={{ color: fails.length ? 'var(--bad)' : 'var(--warn)' }}>{fails.length ? `${fails.length} setup problem${fails.length === 1 ? '' : 's'} — agents cannot run` : `${bad.length} optional feature${bad.length === 1 ? '' : 's'} unavailable`}</b>
        <span className="muted small">{bad.map((c) => c.name).join(' · ')}</span>
        <button className="btn-link" style={{ marginLeft: 'auto' }} onClick={() => setShow(!show)}>{show ? 'hide' : 'details'}</button>
      </div>
      {show && bad.map((c) => (
        <div key={c.name} className="small" style={{ marginTop: 6 }}>
          <span className={`pill ${c.required ? 'failed' : 'stuck'}`}>{c.required ? 'required' : 'optional'}</span> <b>{c.name}</b> — {c.detail}
          {c.fix && <pre className="log" style={{ margin: '4px 0 0' }}>{c.fix}</pre>}
        </div>
      ))}
    </div>
  )
}
