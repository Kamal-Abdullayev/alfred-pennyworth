import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, fmtCost, leadOutput, type ChainDetail, type ConversationDetail } from '../api'
import { AnswerContent, PlanContent } from '../components/Answer'

/** A whole conversation as one printable document. Light theme, no navigation, every answer
 *  expanded. "Save as PDF" is the browser's print dialog; Markdown comes from the API. */
export default function ExportPage() {
  const { cid } = useParams()
  const [conv, setConv] = useState<ConversationDetail | null>(null)
  const [details, setDetails] = useState<Record<string, ChainDetail>>({})
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!cid) return
    api.conversation(cid).then(async (c) => {
      setConv(c)
      const ds = await Promise.all(c.turns.map((t) => api.chain(t.chain_id).catch(() => null)))
      const map: Record<string, ChainDetail> = {}
      ds.forEach((d, i) => { if (d) map[c.turns[i].chain_id] = d })
      setDetails(map)
      document.title = `Alfred — ${c.title}`
    }).catch((e) => setErr(String(e)))
  }, [cid])

  if (err) return <div className="export-page"><p className="err">{err}</p></div>
  if (!conv) return <div className="export-page"><p className="muted">loading…</p></div>
  const total = conv.turns.reduce((a, t) => a + (t.cost_usd || 0), 0)
  const ready = Object.keys(details).length >= conv.turns.filter((t) => t.status === 'done').length

  return (
    <div className="export-page">
      <div className="no-print toolbar">
        <span className="muted small">{ready ? 'Everything is loaded.' : 'Loading answers…'} Landscape or portrait both work; code blocks scroll on screen and wrap in print.</span>
        <span className="actions">
          <button onClick={() => window.print()} disabled={!ready}>Save as PDF</button>
          <a className="btn-link" href={`/api/conversations/${cid}/export.md`}>Download Markdown</a>
          <a className="btn-link" href={`/ask/${cid}`}>back to chat</a>
        </span>
      </div>

      <h1>{conv.title}</h1>
      <div className="muted small" style={{ marginBottom: 20 }}>
        Alfred conversation {conv.id} · {new Date(conv.created_at * 1000).toLocaleString()} · {conv.turns.length} turn{conv.turns.length === 1 ? '' : 's'} · {fmtCost(total)}
        {conv.project_dir ? <> · <span className="mono">{conv.project_dir}</span></> : null}
      </div>

      {conv.turns.map((t, i) => {
        const d = details[t.chain_id]
        const lead = leadOutput(d?.root.structured ?? t.structured)
        return (
          <section key={t.chain_id} className="turn">
            <div className="q">
              <div className="label">Question {i + 1}</div>
              <div className="text">{t.question}</div>
              <div className="muted small">{new Date(t.created_at * 1000).toLocaleString()} · chain {t.chain_id} · {t.status} · {fmtCost(t.cost_usd)}</div>
            </div>
            <div className="a">
              {lead?.kind === 'answer' && lead.answer && <AnswerContent a={lead.answer} links={d?.code_links ?? []} assets={d?.assets ?? []} />}
              {lead?.kind === 'plan' && lead.plan && <PlanContent p={lead.plan} />}
              {!lead && <div className="muted small">No answer — the run ended with status <b>{t.status}</b>{t.result ? `: ${t.result.slice(0, 300)}` : ''}.</div>}
              {!!lead?.memory_proposals?.length && <div className="muted small" style={{ marginTop: 8 }}>Memory proposals: {lead.memory_proposals.map((p) => p.title).join(' · ')}</div>}
            </div>
          </section>
        )
      })}
      <div className="muted small" style={{ marginTop: 24 }}>Costs are the API list-price equivalent of each run, not a bill. Exported from Alfred on {new Date().toLocaleString()}.</div>
    </div>
  )
}
