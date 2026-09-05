import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

export default function Ask() {
  const [body, setBody] = useState('')
  const [project, setProject] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const nav = useNavigate()

  async function submit() {
    setBusy(true); setErr(null)
    try {
      const r = await api.createJob(body, project.trim() || undefined)
      nav(`/chains/${r.chain_id}`)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h1>Ask the team lead</h1>
      <div className="card" style={{ maxWidth: 820 }}>
        <p className="muted small" style={{ marginTop: 0 }}>
          Describe the job the way you'd brief a senior engineer — or paste a ticket key and let the lead read it.
          The lead plans, developers implement in an isolated worktree, QA reviews, and you get the branch to review.
        </p>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="e.g. PAS-1234 — add rate limiting to the /charge endpoint; see the linked Confluence page for limits" />
        <div style={{ marginTop: 10 }}>
          <label className="muted small">Repository path (optional — leave empty for the sandbox workspace)</label>
          <input value={project} onChange={(e) => setProject(e.target.value)} placeholder="/Users/you/Desktop/projects/payment-service" className="mono" />
        </div>
        <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
          <button onClick={submit} disabled={busy || !body.trim()}>{busy ? 'Submitting…' : 'Send to team lead'}</button>
          <span className="muted small">Creates a board task; the team_lead daemon picks it up within ~10s.</span>
        </div>
        {err && <p className="err">{err}</p>}
      </div>
    </>
  )
}
