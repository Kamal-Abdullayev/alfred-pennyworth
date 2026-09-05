import type React from 'react'
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type Answer, type Asset, type CodeRef, type CodeLink, type Plan } from '../api'

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

let mermaidReady: Promise<typeof import('mermaid')> | null = null
function loadMermaid() {
  if (!mermaidReady) mermaidReady = import('mermaid').then((m) => { m.default.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict', fontFamily: 'inherit' }); return m })
  return mermaidReady
}

export function Mermaid({ code, title }: { code: string; title?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    loadMermaid().then(async (m) => {
      try {
        const { svg } = await m.default.render(`mm-${Math.random().toString(36).slice(2)}`, code)
        if (alive && ref.current) { ref.current.innerHTML = svg; setErr(null) }
      } catch (e) { if (alive) setErr(String(e)) }
    })
    return () => { alive = false }
  }, [code])
  const download = () => {
    const svg = ref.current?.querySelector('svg')
    if (!svg) return
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `${(title || 'diagram').replace(/[^\w-]+/g, '_')}.svg`; a.click(); URL.revokeObjectURL(a.href)
  }
  const copySource = () => { navigator.clipboard?.writeText(code) }
  return (
    <div className="diagram">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        {title ? <span className="small"><b>{title}</b></span> : <span />}
        <span className="actions"><button className="btn-link" onClick={download}>download SVG</button><button className="btn-link" onClick={copySource}>copy Mermaid</button></span>
      </div>
      {err ? <pre className="log small">{code}\n\n{err}</pre> : <div ref={ref} className="mermaid-host" />}
    </div>
  )
}

export function Assets({ assets }: { assets: Asset[] }) {
  const [msg, setMsg] = useState<string | null>(null)
  if (!assets.length) return null
  const show = (a: Asset) => api.canvasShow(a.task_id, a.name).then((r) => setMsg(`on the canvas: ${r.message}`)).catch((e) => setMsg(String(e)))
  return (
    <div style={{ marginTop: 10 }}>
      {assets.map((a) => a.kind === 'image'
        ? <div key={a.url} className="diagram"><img src={a.url} alt={a.name} style={{ maxWidth: '100%', borderRadius: 8, border: '1px solid var(--border)' }} /><div className="muted small">{a.name}</div></div>
        : <span key={a.url} className="actions" style={{ display: 'inline-flex', marginRight: 10, marginBottom: 6 }}>
            {a.kind === 'scene' && <button className="btn-link" onClick={() => show(a)} title="clears the shared canvas and loads this diagram">show on canvas</button>}
            <a className="btn-link" href={a.url} download={a.name}>download {a.name} ({(a.bytes / 1024).toFixed(0)} kB)</a>
          </span>)}
      {msg && <div className="muted small">{msg}</div>}
    </div>
  )
}

/** The body of an answer: text, code, sources. Used by the chat and by the chain page. */
export function AnswerContent({ a, links, compact, assets = [] }: { a: Answer; links: CodeLink[]; compact?: boolean; assets?: Asset[] }) {
  const src = a.source
  const diagrams = a.diagrams ?? []
  return (
    <>
      <div className="small muted" style={{ marginBottom: 8 }}>
        <span className={`pill ${a.confidence}`}>{a.confidence} confidence</span>{' '}
        read from {src.gitlab_project ? <>GitLab <b className="mono">{src.gitlab_project}</b></> : <span className="mono">{src.repo_path ?? 'unknown'}</span>}
        {src.branch ? <span className="mono"> · {src.branch}</span> : ''}{src.commit ? <span className="mono"> @ {src.commit.slice(0, 8)}</span> : ''}
      </div>
      <Markdown text={a.answer} />
      {diagrams.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {!compact && <h2>Diagrams ({diagrams.length})</h2>}
          {diagrams.map((d, i) => <div key={i}><Mermaid code={d.mermaid} title={d.title} /><div className="muted small" style={{ marginBottom: 10 }}>{d.description}</div></div>)}
        </div>
      )}
      <Assets assets={assets} />
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
