import { useEffect, useState } from 'react'
import { api, type Connector } from '../api'

export default function Connectors() {
  const [c, setC] = useState<Connector[]>([])
  useEffect(() => { api.connectors().then(setC).catch(() => {}) }, [])
  return (
    <>
      <h1>Connectors</h1>
      <p className="muted small">MCP servers. <b>used by</b> = agents that can reach it (from their YAML). Registered-only servers are visible to Claude Desktop/Code but not to Alfred's agents until a role lists them. Credentials are never shown.</p>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Name</th><th>Kind</th><th>Used by</th><th>Registered in</th><th>Command</th></tr></thead>
          <tbody>{c.map((x) => (
            <tr key={x.name}><td><b>{x.name}</b></td><td>{x.kind}</td>
              <td>{x.used_by.length ? x.used_by.map((r) => <span key={r} className={`pill ${r}`} style={{ marginRight: 4 }}>{r}</span>) : <span className="muted small">no agent</span>}</td>
              <td className="small">{x.registered_in.join(', ')}</td><td className="mono small muted">{x.command}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </>
  )
}
