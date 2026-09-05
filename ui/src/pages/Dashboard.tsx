import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, subscribe, ago, fmtCost, type Chain, type Task } from '../api'

export default function Dashboard() {
  const [chains, setChains] = useState<Chain[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [flow, setFlow] = useState<string[]>([])
  const nav = useNavigate()

  useEffect(() => {
    api.chains().then(setChains).catch(() => {})
    api.tasks().then(setTasks).catch(() => {})
    return subscribe({
      chains: setChains,
      tasks: setTasks,
      flow: (line) => setFlow((f) => [...f.slice(-199), line]),
    })
  }, [])

  const running = tasks.filter((t) => t.status === 'claimed')
  const queued = tasks.filter((t) => t.status === 'open')
  const stuck = chains.filter((c) => c.status === 'stuck')
  const cost = chains.reduce((s, c) => s + (c.cost_usd || 0), 0)

  return (
    <>
      <h1>Dashboard</h1>
      <div className="grid cols-4">
        <div className="card stat"><div className="v">{running.length}</div><div className="l">agents working now</div></div>
        <div className="card stat"><div className="v">{queued.length}</div><div className="l">tasks queued</div></div>
        <div className="card stat"><div className="v" style={{ color: stuck.length ? 'var(--warn)' : undefined }}>{stuck.length}</div><div className="l">chains need a human</div></div>
        <div className="card stat"><div className="v">{fmtCost(cost)}</div><div className="l">all chains, list-price estimate</div></div>
      </div>

      {running.length > 0 && (
        <>
          <h2>In progress</h2>
          <div className="card">
            {running.map((t) => (
              <div key={t.id} style={{ padding: '4px 0' }}>
                <span className={`pill ${t.role}`}>{t.role}</span>{' '}
                <a href={`/chains/${t.chain_id}`} onClick={(e) => { e.preventDefault(); nav(`/chains/${t.chain_id}`) }}>{t.title}</a>
                <span className="muted small"> · round {t.iteration} · claimed {ago(t.claimed_at)}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <h2>Chains</h2>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Status</th><th>Kind</th><th>Job</th><th>Roles</th><th>Round</th><th>Cost</th><th>Updated</th></tr></thead>
          <tbody>
            {chains.map((c) => (
              <tr key={c.chain_id} className="row" onClick={() => nav(`/chains/${c.chain_id}`)}>
                <td><span className={`pill ${c.status}`}>{c.status}</span></td>
                <td><span className={`pill ${c.kind}`}>{c.kind}</span></td>
                <td>{c.title}<div className="muted small mono">{c.chain_id}{c.project_dir ? ` · ${c.project_dir.split('/').slice(-2).join('/')}` : ''}</div></td>
                <td>{Object.entries(c.roles).map(([r, n]) => <span key={r} className={`pill ${r}`} style={{ marginRight: 4 }}>{r} {n}</span>)}</td>
                <td>{c.iteration}</td>
                <td className="mono">{fmtCost(c.cost_usd)}</td>
                <td className="muted">{ago(c.updated_at)}</td>
              </tr>
            ))}
            {chains.length === 0 && <tr><td colSpan={7} className="muted">No chains yet — give the team lead a job in <a href="/ask">Ask</a>.</td></tr>}
          </tbody>
        </table>
      </div>

      <h2>Live flow</h2>
      <pre className="log">{flow.length ? flow.join('\n') : 'waiting for daemons… (start them with ./run_all.sh)'}</pre>
    </>
  )
}
