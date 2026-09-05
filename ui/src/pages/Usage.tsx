import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, fmtCost, type UsageSummary } from '../api'

const COLORS: Record<string, string> = { team_lead: '#bb9af7', developer: '#7aa2f7', qa: '#9ece6a' }

export default function UsagePage() {
  const [days, setDays] = useState(30)
  const [u, setU] = useState<UsageSummary | null>(null)
  useEffect(() => { api.usage(days).then(setU).catch(() => {}) }, [days])

  const perDay = useMemo(() => {
    if (!u) return []
    const m = new Map<string, Record<string, number | string>>()
    for (const r of u.by_day) {
      const row = m.get(r.day) ?? { day: r.day }
      row[r.role] = ((row[r.role] as number) ?? 0) + r.cost
      m.set(r.day, row)
    }
    return [...m.values()]
  }, [u])
  const roles = useMemo(() => [...new Set(u?.by_day.map((r) => r.role) ?? [])], [u])

  if (!u) return <p className="muted">loading…</p>
  return (
    <>
      <h1>Usage</h1>
      <p className="muted small">{u.note}. Window: {[7, 30, 90].map((d) => <button key={d} className="secondary small" style={{ marginLeft: 6, padding: '2px 8px', fontWeight: d === days ? 700 : 400 }} onClick={() => setDays(d)}>{d}d</button>)}</p>
      <div className="grid cols-4">
        <div className="card stat"><div className="v">{fmtCost(u.totals.cost)}</div><div className="l">total, list-price estimate</div></div>
        <div className="card stat"><div className="v">{u.totals.runs}</div><div className="l">agent runs</div></div>
        <div className="card stat"><div className="v">{(u.totals.in_tok / 1e6).toFixed(2)}M</div><div className="l">input + cache tokens</div></div>
        <div className="card stat"><div className="v">{(u.totals.out_tok / 1e3).toFixed(1)}k</div><div className="l">output tokens</div></div>
      </div>
      <h2>Cost per day by role</h2>
      <div className="card" style={{ height: 300 }}>
        <ResponsiveContainer>
          <BarChart data={perDay}>
            <CartesianGrid stroke="#2a2f3a" vertical={false} />
            <XAxis dataKey="day" stroke="#8b93a7" tick={{ fontSize: 12 }} />
            <YAxis stroke="#8b93a7" tick={{ fontSize: 12 }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
            <Tooltip contentStyle={{ background: '#171a21', border: '1px solid #2a2f3a' }} formatter={(v) => `$${Number(v).toFixed(3)}`} />
            <Legend />
            {roles.map((r) => <Bar key={r} dataKey={r} stackId="a" fill={COLORS[r] ?? '#e0af68'} />)}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="grid cols-2">
        <div>
          <h2>By role</h2>
          <div className="card" style={{ padding: 0 }}><table><thead><tr><th>Role</th><th>Runs</th><th>In+cache</th><th>Out</th><th>Cost</th></tr></thead>
            <tbody>{u.by_role.map((r) => <tr key={r.role}><td><span className={`pill ${r.role}`}>{r.role}</span></td><td>{r.runs}</td><td className="mono">{r.in_tok.toLocaleString()}</td><td className="mono">{r.out_tok.toLocaleString()}</td><td className="mono">{fmtCost(r.cost)}</td></tr>)}</tbody></table></div>
        </div>
        <div>
          <h2>By model</h2>
          <div className="card" style={{ padding: 0 }}><table><thead><tr><th>Model</th><th>Runs</th><th>Cost</th></tr></thead>
            <tbody>{u.by_model.map((r) => <tr key={r.model}><td className="mono small">{r.model}</td><td>{r.runs}</td><td className="mono">{fmtCost(r.cost)}</td></tr>)}</tbody></table></div>
        </div>
      </div>
    </>
  )
}
