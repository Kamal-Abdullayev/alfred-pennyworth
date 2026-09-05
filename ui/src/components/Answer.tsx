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
  if (!mermaidReady) mermaidReady = import('mermaid').then((m) => {
    m.default.initialize({
      startOnLoad: false, securityLevel: 'strict', fontFamily: 'inherit', theme: 'base',
      themeVariables: {
        darkMode: true, background: '#0b0d12', fontSize: '15px',
        primaryColor: '#1e222b', primaryTextColor: '#e6e8ee', primaryBorderColor: '#7aa2f7',
        secondaryColor: '#232838', secondaryTextColor: '#e6e8ee', secondaryBorderColor: '#bb9af7',
        tertiaryColor: '#141720', tertiaryTextColor: '#e6e8ee', tertiaryBorderColor: '#2a2f3a',
        lineColor: '#8b93a7', textColor: '#e6e8ee', mainBkg: '#1e222b', nodeBorder: '#7aa2f7',
        clusterBkg: '#141720', clusterBorder: '#2a2f3a', titleColor: '#e6e8ee', edgeLabelBackground: '#171a21',
        noteBkgColor: '#2b2410', noteTextColor: '#e6e8ee', noteBorderColor: '#e0af68',
        actorBkg: '#1e222b', actorBorder: '#7aa2f7', actorTextColor: '#e6e8ee', actorLineColor: '#3a4052',
        signalColor: '#e6e8ee', signalTextColor: '#e6e8ee', labelBoxBkgColor: '#1e222b', labelBoxBorderColor: '#2a2f3a',
        labelTextColor: '#e6e8ee', loopTextColor: '#e6e8ee', activationBkgColor: '#2a2f3a', activationBorderColor: '#7aa2f7',
        sequenceNumberColor: '#0b0d12', attributeBackgroundColorOdd: '#171a21', attributeBackgroundColorEven: '#1e222b',
      },
    })
    return m
  })
  return mermaidReady
}

/** Full-screen viewer for a diagram or image: fit to screen, wheel/buttons zoom, drag to pan, Esc closes. */
export function Lightbox({ title, onClose, children }: { title?: string; onClose: () => void; children: (scale: number) => React.ReactNode }) {
  const [scale, setScale] = useState(1)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); if (e.key === '+' || e.key === '=') setScale((s) => Math.min(s * 1.25, 8)); if (e.key === '-') setScale((s) => Math.max(s / 1.25, 0.2)); if (e.key === '0') { setScale(1); setPos({ x: 0, y: 0 }) } }
    window.addEventListener('keydown', onKey); document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = '' }
  }, [onClose])
  return (
    <div className="lightbox" onClick={onClose}>
      <div className="lb-bar" onClick={(e) => e.stopPropagation()}>
        <b className="small">{title ?? 'diagram'}</b>
        <span className="actions">
          <button className="btn-link" onClick={() => setScale((s) => Math.max(s / 1.25, 0.2))}>−</button>
          <span className="mono small" style={{ minWidth: 48, textAlign: 'center' }}>{Math.round(scale * 100)}%</span>
          <button className="btn-link" onClick={() => setScale((s) => Math.min(s * 1.25, 8))}>+</button>
          <button className="btn-link" onClick={() => { setScale(1); setPos({ x: 0, y: 0 }) }}>fit</button>
          <button className="btn-link" onClick={onClose}>close (Esc)</button>
        </span>
      </div>
      <div className="lb-stage" onClick={(e) => e.stopPropagation()}
        onWheel={(e) => { e.preventDefault(); setScale((s) => Math.min(8, Math.max(0.2, s * (e.deltaY < 0 ? 1.1 : 0.9)))) }}
        onMouseDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y } }}
        onMouseMove={(e) => { if (drag.current) setPos({ x: drag.current.px + (e.clientX - drag.current.x), y: drag.current.py + (e.clientY - drag.current.y) }) }}
        onMouseUp={() => { drag.current = null }} onMouseLeave={() => { drag.current = null }}>
        <div className="lb-content" style={{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})` }}>{children(scale)}</div>
      </div>
      <div className="lb-hint muted small">scroll to zoom · drag to pan · + / − / 0 · Esc to close</div>
    </div>
  )
}

/** SVG sized so "fit" fills ~92% of the viewport width or ~85% of its height, whichever binds. */
function FitSvg({ markup }: { markup: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const svg = ref.current?.querySelector('svg')
    if (!svg) return
    const vb = svg.getAttribute('viewBox')?.split(/[\s,]+/).map(Number)
    const ratio = vb && vb.length === 4 && vb[3]! > 0 ? vb[2]! / vb[3]! : (svg.getBoundingClientRect().width / Math.max(1, svg.getBoundingClientRect().height))
    const w = Math.min(window.innerWidth * 0.92, window.innerHeight * 0.85 * ratio)
    svg.style.maxWidth = 'none'; svg.style.width = `${Math.max(320, w)}px`; svg.style.height = 'auto'
  }, [markup])
  return <div ref={ref} className="lb-svg" dangerouslySetInnerHTML={{ __html: markup }} />
}

/** Agents sometimes hard-code colours (light fills under a dark theme = unreadable). Keep their
 *  *intent* — a highlighted node — but map every colour onto the UI palette. */
const RECT_TINTS = ['rgba(122,162,247,0.10)', 'rgba(224,175,104,0.12)', 'rgba(158,206,106,0.10)', 'rgba(187,154,247,0.10)']
export function normaliseColours(src: string): string {
  let rects = 0
  return src.split('\n').filter((l) => !/^\s*%%\{\s*init/.test(l)).map((l) => {
    // sequence-diagram background blocks: `rect rgb(245,238,220)` → translucent tint, one per block
    if (/^\s*rect\s+(rgba?\(|#|[a-zA-Z]+\s*$)/.test(l)) return l.replace(/rect\s+.*$/, `rect ${RECT_TINTS[rects++ % RECT_TINTS.length]}`)
    if (!/^\s*(style|classDef|linkStyle)\b/.test(l)) return l
    return l
      .replace(/fill\s*:\s*#?[0-9a-fA-F]{3,8}|fill\s*:\s*[a-zA-Z]+/g, 'fill:#2b2410')
      .replace(/stroke\s*:\s*#?[0-9a-fA-F]{3,8}|stroke\s*:\s*[a-zA-Z]+/g, 'stroke:#e0af68')
      .replace(/(^|[,\s])color\s*:\s*#?[0-9a-fA-F]{3,8}|(^|[,\s])color\s*:\s*[a-zA-Z]+/g, '$1$2color:#e6e8ee')
  }).join('\n')
}

export function Mermaid({ code: rawCode, title }: { code: string; title?: string }) {
  const code = normaliseColours(rawCode)
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
  const copySource = () => { navigator.clipboard?.writeText(rawCode) }
  const [zoom, setZoom] = useState(false)
  const svgMarkup = () => ref.current?.innerHTML ?? ''
  return (
    <div className="diagram">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        {title ? <span className="small"><b>{title}</b></span> : <span />}
        <span className="actions"><button className="btn-link" onClick={() => setZoom(true)} disabled={!!err}>zoom</button><button className="btn-link" onClick={download}>download SVG</button><button className="btn-link" onClick={copySource}>copy Mermaid</button></span>
      </div>
      {err ? <pre className="log small">{code}\n\n{err}</pre> : <div ref={ref} className="mermaid-host zoomable" title="click to zoom" onClick={() => setZoom(true)} />}
      {zoom && <Lightbox title={title} onClose={() => setZoom(false)}>{() => <FitSvg markup={svgMarkup()} />}</Lightbox>}
    </div>
  )
}

function ZoomableImage({ a }: { a: Asset }) {
  const [zoom, setZoom] = useState(false)
  return (
    <div className="diagram">
      <img src={a.url} alt={a.name} className="zoomable" title="click to zoom" onClick={() => setZoom(true)} style={{ maxWidth: '100%', borderRadius: 8, border: '1px solid var(--border)' }} />
      <div className="muted small">{a.name} · <a href={a.url} download={a.name}>download</a></div>
      {zoom && <Lightbox title={a.name} onClose={() => setZoom(false)}>{() => <img src={a.url} alt={a.name} style={{ display: 'block' }} />}</Lightbox>}
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
        ? <ZoomableImage key={a.url} a={a} />
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
