import type React from 'react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Answer, CodeRef, CodeLink, Plan } from '../api'

const mdComponents = {
  // a wide table scrolls inside its own box instead of squeezing columns until words break
  table: (props: React.ComponentProps<'table'>) => <div className="table-wrap"><table {...props} /></div>,
}

export function Markdown({ text }: { text: string }) {
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{text}</ReactMarkdown></div>
}

export function CodeBlock({ code: r, link }: { code: CodeRef; link: CodeLink | undefined }) {
  const [copied, setCopied] = useState(false)
  const lines = r.snippet.replace(/\n$/, '').split('\n')
  const copyText = link?.abs ? `${link.abs}:${r.start_line}` : `${r.path}:${r.start_line}`
  const copy = () => { navigator.clipboard?.writeText(copyText).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) }) }
  return (
    <div className="coderef">
      <div className="hdr">
        <div>
          {r.symbol && <span className="sym mono">{r.symbol}</span>}
          <div className="path mono">{r.path}:{r.start_line}{r.end_line !== r.start_line ? `-${r.end_line}` : ''} <span className="muted">· {r.language}</span></div>
        </div>
        <div className="actions">
          {link?.idea && <a className="btn-link" href={link.idea} title={`Open ${link.abs} at line ${r.start_line} in IntelliJ`}>Open in IntelliJ</a>}
          {link?.web && <a className="btn-link" href={link.web} target="_blank" rel="noreferrer" title="Open these lines in GitLab">Open in GitLab</a>}
          {!link?.idea && !link?.web && <span className="muted small" title="No local checkout and no GitLab project recorded for this answer">not openable locally</span>}
          <button className="btn-link" onClick={copy}>{copied ? 'copied' : 'copy path'}</button>
        </div>
      </div>
      <pre>
        {lines.map((l, i) => (
          <div className="line" key={i}><span className="ln">{r.start_line + i}</span><span className="src">{l || ' '}</span></div>
        ))}
      </pre>
      {r.why && <div className="why">{r.why}</div>}
    </div>
  )
}

/** The body of an answer: text, code, sources. Used by the chat and by the chain page. */
export function AnswerContent({ a, links, compact }: { a: Answer; links: CodeLink[]; compact?: boolean }) {
  const src = a.source
  return (
    <>
      <div className="small muted" style={{ marginBottom: 8 }}>
        <span className={`pill ${a.confidence}`}>{a.confidence} confidence</span>{' '}
        read from {src.gitlab_project ? <>GitLab <b className="mono">{src.gitlab_project}</b></> : <span className="mono">{src.repo_path ?? 'unknown'}</span>}
        {src.branch ? <span className="mono"> · {src.branch}</span> : ''}{src.commit ? <span className="mono"> @ {src.commit.slice(0, 8)}</span> : ''}
      </div>
      <Markdown text={a.answer} />
      {a.code.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {!compact && <h2>Code ({a.code.length})</h2>}
          {a.code.map((c, i) => <CodeBlock key={i} code={c} link={links[i]} />)}
        </div>
      )}
      {a.citations.length > 0 && (
        <details style={{ marginTop: 8 }}><summary className="small">sources ({a.citations.length})</summary>
          <table className="small"><tbody>{a.citations.map((c, i) => <tr key={i}><td className="mono">{c.url ? <a href={c.url} target="_blank" rel="noreferrer">{c.source}</a> : c.source}</td><td>{c.ref}</td></tr>)}</tbody></table>
        </details>
      )}
    </>
  )
}

export function PlanContent({ p }: { p: Plan }) {
  return (
    <div>
      <Markdown text={p.summary} />
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
