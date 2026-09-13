import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, fmtCost, type BriefRun } from '../api'

const toMin = (hhmm: string) => { const [h, m] = hhmm.split(':').map(Number); return (h || 0) * 60 + (m || 0) }

/** The day as a line: meetings are dots sized by length, the current time a thin marker. */
export function DayLine({ meetings, height = 90 }: { meetings: BriefRun['brief']['meetings']; height?: number }) {
  const start = 7 * 60, end = 19 * 60, W = 700, H = height
  const x = (min: number) => 30 + ((Math.min(Math.max(min, start), end) - start) / (end - start)) * (W - 60)
  const now = new Date(); const nowMin = now.getHours() * 60 + now.getMinutes()
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="dayline" role="img" aria-label="today as a timeline">
      <line x1={30} y1={H - 24} x2={W - 30} y2={H - 24} className="axis" />
      {[8, 10, 12, 14, 16, 18].map((h) => <g key={h}><line x1={x(h * 60)} y1={H - 28} x2={x(h * 60)} y2={H - 20} className="axis" /><text x={x(h * 60)} y={H - 6} textAnchor="middle" className="tick">{h}:00</text></g>)}
      {meetings.map((m, i) => {
        const a = toMin(m.start), b = Math.max(toMin(m.end), a + 15)
        const cx = (x(a) + x(b)) / 2, r = Math.max(5, Math.min(12, (b - a) / 10))
        const past = b <= nowMin
        return <g key={i}><line x1={x(a)} y1={H - 24} x2={x(b)} y2={H - 24} className="span" /><circle cx={cx} cy={H - 24 - 22} r={r} className={past ? 'dot past' : 'dot'} /><title>{`${m.start}–${m.end} ${m.title}`}</title></g>
      })}
      {nowMin >= start && nowMin <= end && <line x1={x(nowMin)} y1={12} x2={x(nowMin)} y2={H - 16} className="now" />}
    </svg>
  )
}

export function BriefBody({ b, compact }: { b: BriefRun['brief']; compact?: boolean }) {
  return (
    <>
      <div className="brief-date">{b.date}</div>
      <h1 className="brief-headline">{b.headline}</h1>
      <DayLine meetings={b.meetings} />
      <div className="brief-segments" style={{ gridTemplateColumns: `repeat(${Math.max(1, Math.min(4, b.segments.length))}, 1fr)` }}>
        {b.segments.map((s, i) => <div key={i}><div className="seg-title">{s.title}</div><div className="seg-text">{s.detail}</div></div>)}
      </div>
      <section><h2>Needs attention</h2>
        {b.needs_attention.length === 0 && <div className="muted">Nothing needs a decision from you today.</div>}
        <ol>{b.needs_attention.map((it, i) => <li key={i}><b>{it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.title}</a> : it.title}</b><div>{it.detail}</div></li>)}</ol>
      </section>
      {b.resolved.length > 0 && <section><h2>Resolved</h2><ol>{b.resolved.map((it, i) => <li key={i}><b>{it.title}</b><div>{it.detail}</div></li>)}</ol></section>}
      {!compact && (
        <>
          <section><h2>Today's meetings</h2>
            {b.meetings.length === 0 && <div className="muted">None.</div>}
            <ol>{b.meetings.map((m, i) => <li key={i}><b><span className="mono">{m.start} – {m.end}</span>  {m.title}</b><div>{[m.where, m.note].filter(Boolean).join('. ')}</div></li>)}</ol>
          </section>
          <section><h2>My Jira tickets</h2>
            {b.tickets.length === 0 && <div className="muted">Nothing unresolved assigned to you.</div>}
            <ol>{b.tickets.map((t, i) => <li key={i}><b><span className="mono">{t.url ? <a href={t.url} target="_blank" rel="noreferrer">{t.key}</a> : t.key}</span>  {t.title}</b><div>{[t.status, t.note].filter(Boolean).join('. ')}</div></li>)}</ol>
          </section>
          <section><h2>Unread emails</h2>
            {b.unread_emails.length === 0 && <div className="muted">Inbox is clear.</div>}
            <ol>{b.unread_emails.map((e, i) => <li key={i}><b>{e.sender}</b><div>{e.subject} <span className="muted">· {e.when}{e.count > 1 ? ` · ${e.count} copies` : ''}</span></div></li>)}</ol>
          </section>
          {b.footer && <div className="brief-footer">{b.footer}</div>}
        </>
      )}
    </>
  )
}

export default function BriefPage() {
  const { id } = useParams()
  const [run, setRun] = useState<BriefRun | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { if (id) api.brief(id).then((r) => { setRun(r); document.title = `Brief — ${r.brief.date}` }).catch((e) => setErr(String(e))) }, [id])
  if (err) return <div className="export-page brief-page"><p className="err">{err}</p></div>
  if (!run) return <div className="export-page brief-page"><p className="muted">loading…</p></div>
  return (
    <div className="export-page brief-page">
      <div className="no-print toolbar">
        <span className="muted small">Written {new Date((run.finished_at ?? run.created_at) * 1000).toLocaleString()} · {fmtCost(run.cost_usd)} · <a href={`/ask/${run.conversation_id}`}>how it was made</a></span>
        <span className="actions"><button onClick={() => window.print()}>Save as PDF</button><a className="btn-link" href="/">back home</a></span>
      </div>
      <div className="brief-sheet"><BriefBody b={run.brief} /></div>
    </div>
  )
}
