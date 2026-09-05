import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, subscribe, fmtCost, fmtTime, type ChainDetail, type Task } from '../api'

type Plan = { summary: string; parallelism: number; subtasks: { id: string; title: string; description: string; acceptance: string[]; depends_on: string[] }[] }
type Impl = { status: string; summary: string; branch: string | null; commit_sha: string | null; files_changed: string[]; approach: string | null; verification: { commands_run: string[]; result: string; notes: string }; open_questions: string[]; blocked: { reason: string; conflict_detail: string; needs: string[] } | null }
type Review = { verdict: string; findings: { file: string; line: number | null; severity: string; claim: string; evidence: string }[]; tests_run: { name: string; status: string; output_excerpt: string }[]; edge_cases_probed: string[] }

function Structured({ task }: { task: Task }) {
  const s = task.structured as Plan | Impl | Review | null
  if (!s) return <span className="muted small">{task.status === 'done' ? 'no structured output' : ''}</span>
  if (task.role === 'team_lead') {
    const p = s as Plan
    return (
      <div>
        <div>{p.summary}</div>
        <ul className="plain">
          {p.subtasks?.map((st) => (
            <li key={st.id}><b>{st.title}</b> <span className="muted small">({st.id}{st.depends_on?.length ? `, after ${st.depends_on.join(', ')}` : ''})</span>
              <div className="small">{st.description}</div>
              <ol className="small muted">{st.acceptance?.map((a, i) => <li key={i}>{a}</li>)}</ol>
            </li>
          ))}
        </ul>
      </div>
    )
  }
  if (task.role === 'developer') {
    const im = s as Impl
    return (
      <div>
        <span className={`pill ${im.status === 'done' ? 'pass' : 'stuck'}`}>{im.status}</span>
        {im.commit_sha && <span className="mono small" style={{ marginLeft: 8 }}>{im.commit_sha.slice(0, 12)}</span>}
        {im.verification && <span className={`pill ${im.verification.result}`} style={{ marginLeft: 8 }}>tests {im.verification.result}</span>}
        <div style={{ marginTop: 6 }}>{im.summary}</div>
        {im.files_changed?.length > 0 && <div className="small muted mono">{im.files_changed.join(', ')}</div>}
        {im.blocked && <div className="err small">Blocked: {im.blocked.reason} — {im.blocked.conflict_detail}</div>}
        {im.open_questions?.length > 0 && <ul className="plain small">{im.open_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>}
      </div>
    )
  }
  const r = s as Review
  return (
    <div>
      <span className={`pill ${r.verdict}`}>{r.verdict}</span>
      <span className="muted small" style={{ marginLeft: 8 }}>{r.findings?.length ?? 0} finding(s) · {r.tests_run?.length ?? 0} test run(s) · {r.edge_cases_probed?.length ?? 0} edge cases probed</span>
      {r.edge_cases_probed?.length > 0 && <details><summary className="small">edge cases</summary><ul className="plain small">{r.edge_cases_probed.map((e, i) => <li key={i}>{e}</li>)}</ul></details>}
    </div>
  )
}

function Diff({ text }: { text: string }) {
  return (
    <pre className="diff">
      {text.split('\n').map((l, i) => {
        const cls = l.startsWith('+++') || l.startsWith('---') ? 'file' : l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : l.startsWith('diff ') ? 'file' : ''
        return <div key={i} className={cls}>{l || ' '}</div>
      })}
    </pre>
  )
}

export default function ChainPage() {
  const { id = '' } = useParams()
  const [d, setD] = useState<ChainDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [f, setF] = useState({ file: '', line: '', severity: 'important', claim: '' })
  const [sent, setSent] = useState<string | null>(null)

  const load = () => api.chain(id).then(setD).catch((e) => setErr(String(e)))
  useEffect(() => { load(); return subscribe({ tasks: () => load() }) }, [id])

  if (err) return <p className="err">{err}</p>
  if (!d) return <p className="muted">loading…</p>
  const root = d.root
  const repo = root.project_dir

  async function sendFinding() {
    const r = await api.addFinding(id, { file: f.file, line: f.line ? Number(f.line) : null, severity: f.severity, claim: f.claim, evidence: 'reported by human reviewer in the UI' })
    setSent(`Sent to developer as round ${r.iteration} (task ${r.task_id})`)
    setF({ file: '', line: '', severity: 'important', claim: '' })
    load()
  }

  return (
    <>
      <h1><span className={`pill ${d.status}`}>{d.status}</span> {root.title}</h1>
      <div className="kv card">
        <div className="k">chain</div><div className="mono">{id} · {d.tasks.length} tasks · round {Math.max(...d.tasks.map((t) => t.iteration))} · {fmtCost(d.cost_usd)}</div>
        <div className="k">repo</div><div className="mono">{repo ?? 'workspace/ (sandbox)'}</div>
        <div className="k">branch</div><div className="mono">{d.branch}{root.base_sha ? <span className="muted"> from {root.base_sha.slice(0, 8)}</span> : ''}</div>
        {root.worktree && <><div className="k">worktree</div><div className="mono">{root.worktree}</div></>}
        <div className="k">started</div><div>{fmtTime(root.created_at)} by {root.created_by}</div>
      </div>

      <h2>Job</h2>
      <div className="card"><pre style={{ margin: 0, whiteSpace: 'pre-wrap', font: 'inherit' }}>{root.body}</pre></div>

      <h2>Timeline</h2>
      <div className="timeline">
        {d.tasks.map((t) => (
          <div key={t.id} className="step">
            <div className="who"><span className={`pill ${t.role}`}>{t.role}</span><div className="muted small">round {t.iteration}</div></div>
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <b>{t.title}</b>
                <span><span className={`pill ${t.status}`}>{t.status}</span> <span className="muted small">{fmtTime(t.finished_at ?? t.claimed_at ?? t.created_at)}</span></span>
              </div>
              <div style={{ marginTop: 8 }}><Structured task={t} /></div>
              {t.status === 'failed' && t.result && <pre className="log small" style={{ marginTop: 8 }}>{t.result.slice(-1500)}</pre>}
              <details style={{ marginTop: 6 }}><summary className="small">task body</summary><pre className="log">{t.body}</pre></details>
            </div>
          </div>
        ))}
      </div>

      <h2>Findings ({d.findings.length})</h2>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Round</th><th>Source</th><th>Severity</th><th>Where</th><th>Claim</th><th>Status</th></tr></thead>
          <tbody>
            {d.findings.map((x) => (
              <tr key={x.id}>
                <td>{x.iteration}</td><td>{x.source}</td><td><span className={`pill ${x.severity}`}>{x.severity}</span></td>
                <td className="mono small">{x.file}{x.line != null ? `:${x.line}` : ''}</td>
                <td>{x.claim}<div className="muted small">{x.evidence}</div></td>
                <td><span className={`pill ${x.status === 'open' ? 'stuck' : 'done'}`}>{x.status}</span></td>
              </tr>
            ))}
            {d.findings.length === 0 && <tr><td colSpan={6} className="muted">none</td></tr>}
          </tbody>
        </table>
      </div>

      <h2>Your review</h2>
      <div className="grid cols-2">
        <div className="card">
          <b>Send a finding to the developer</b>
          <p className="muted small">Becomes a <code>source: human</code> finding and re-enters the fix loop as a new round.</p>
          <div className="grid cols-2" style={{ gap: 8 }}>
            <input placeholder="file" className="mono" value={f.file} onChange={(e) => setF({ ...f, file: e.target.value })} />
            <input placeholder="line (optional)" className="mono" value={f.line} onChange={(e) => setF({ ...f, line: e.target.value })} />
          </div>
          <select style={{ marginTop: 8 }} value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
            <option value="critical">critical</option><option value="important">important</option><option value="minor">minor</option>
          </select>
          <textarea style={{ marginTop: 8, minHeight: 70 }} placeholder="what's wrong" value={f.claim} onChange={(e) => setF({ ...f, claim: e.target.value })} />
          <div style={{ marginTop: 8 }}><button onClick={sendFinding} disabled={!f.file || !f.claim}>Send to developer</button> {sent && <span className="muted small">{sent}</span>}</div>
        </div>
        <div className="card">
          <b>Accept</b>
          <p className="muted small">Agents never push. When the diff below looks right, merge it yourself:</p>
          <pre className="log">{repo ? `git -C ${repo} diff ${root.base_sha?.slice(0, 8)}..${d.branch}\ngit -C ${repo} merge --no-ff ${d.branch}\ngit -C ${repo} worktree remove ${root.worktree ?? '<worktree>'} && git -C ${repo} branch -d ${d.branch}` : 'sandbox run — nothing to merge'}</pre>
        </div>
      </div>

      <h2>Diff {d.log && <span className="muted small mono">{d.log.trim().split('\n').length} commit(s)</span>}</h2>
      {d.log && <pre className="log" style={{ marginBottom: 10 }}>{d.log}</pre>}
      {d.diff ? <Diff text={d.diff} /> : <div className="card muted">No diff available — the repo, base sha or branch is missing (sandbox run, or the worktree was removed).</div>}

      <h2>Usage</h2>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Role</th><th>Model</th><th>Turns</th><th>Input+cache</th><th>Output</th><th>Cost</th><th>Duration</th></tr></thead>
          <tbody>{d.usage.map((u) => (
            <tr key={u.id}><td>{u.role}</td><td className="mono small">{u.model}</td><td>{u.turns ?? '—'}</td>
              <td className="mono">{(u.input_tokens + u.cache_read_tokens + u.cache_write_tokens).toLocaleString()}</td>
              <td className="mono">{u.output_tokens.toLocaleString()}</td><td className="mono">{fmtCost(u.cost_usd)}</td>
              <td>{u.duration_ms ? `${Math.round(u.duration_ms / 1000)}s` : '—'}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </>
  )
}
