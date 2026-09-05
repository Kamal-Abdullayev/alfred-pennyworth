import { useEffect, useState } from 'react'
import { api, type Agent } from '../api'

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([])
  useEffect(() => { api.agents().then(setAgents).catch(() => {}) }, [])
  return (
    <>
      <h1>Agents</h1>
      <p className="muted small">Every <code>agents/&lt;role&gt;.yaml</code> is a role. Editing from here is the next slice; for now this is what the daemons run with.</p>
      <div className="grid cols-3">
        {agents.map((a) => (
          <div key={a.role} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><b><span className={`pill ${a.role}`}>{a.role}</span></b><span className="mono small">{a.model}</span></div>
            <div className="kv small" style={{ marginTop: 10 }}>
              <div className="k">contract</div><div className="mono">{a.contract}</div>
              <div className="k">permission</div><div className="mono">{a.permission_mode}</div>
              <div className="k">limits</div><div>{a.max_minutes} min{a.max_turns ? `, ${a.max_turns} turns` : ''}</div>
              <div className="k">built-in tools</div><div className="mono">{a.builtin_tools.join(', ') || '—'}</div>
              <div className="k">allow rules</div><div className="mono">{a.allowed_tools.join(', ')}</div>
              <div className="k">connectors</div><div className="mono">{a.mcp_servers.join(', ') || '—'}</div>
            </div>
            <details style={{ marginTop: 10 }}><summary className="small">system prompt</summary><pre className="log">{a.system_prompt}</pre></details>
          </div>
        ))}
      </div>
    </>
  )
}
