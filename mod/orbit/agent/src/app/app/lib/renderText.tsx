'use client'

import { useState, Fragment } from 'react'

function highlight(code: string, lang: string): React.ReactNode {
  const l = lang.toLowerCase().replace(/[^a-z0-9]/g, '')

  const KW  = 'rgb(167 139 250)'
  const STR = 'rgb(110 231 183)'
  const CMT = 'rgb(107 114 128)'
  const NUM = 'rgb(251 191 36)'
  const TYP = 'rgb(125 211 252)'

  const strSrc = `"(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'`
  const numSrc = `\\b\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b`

  let rx: RegExp | null = null
  let sm: Record<number, { color?: string; fontStyle?: string }> = {}

  if (l === 'python' || l === 'py') {
    const kw = `\\b(?:if|else|elif|for|while|in|not|and|or|is|return|def|class|import|from|as|with|try|except|finally|raise|pass|break|continue|lambda|yield|None|True|False|global|nonlocal|del|assert|async|await)\\b`
    rx = new RegExp(`(#[^\\n]*)|(${strSrc})|(${kw})|(${numSrc})`, 'gm')
    sm = { 1: { color: CMT, fontStyle: 'italic' }, 2: { color: STR }, 3: { color: KW }, 4: { color: NUM } }
  } else if (['ts', 'tsx', 'js', 'jsx', 'typescript', 'javascript'].includes(l)) {
    const kw = `\\b(?:const|let|var|if|else|for|while|return|function|class|import|export|from|as|type|interface|extends|implements|new|this|typeof|instanceof|void|null|undefined|true|false|async|await|try|catch|finally|throw|break|continue|switch|case|default|in|of|do|delete|yield|readonly|abstract|enum|namespace|declare|override|satisfies|keyof|infer)\\b`
    const bkt = "`(?:[^`\\\\]|\\\\.)*`"
    const cmt = `//[^\\n]*|/\\*[\\s\\S]*?\\*/`
    rx = new RegExp(`(${cmt})|(${strSrc}|${bkt})|(${kw})|(${numSrc})|(\\b[A-Z][A-Za-z0-9_]*\\b)`, 'gm')
    sm = { 1: { color: CMT, fontStyle: 'italic' }, 2: { color: STR }, 3: { color: KW }, 4: { color: NUM }, 5: { color: TYP } }
  } else if (['sh', 'bash', 'shell', 'zsh'].includes(l)) {
    const kw = `\\b(?:if|then|else|elif|fi|for|while|do|done|case|esac|function|return|exit|echo|cd|ls|mkdir|rm|mv|cp|export|source|alias|set|unset|local|readonly|declare|true|false)\\b`
    rx = new RegExp(`(#[^\\n]*)|(${strSrc})|(${kw})|(${numSrc})|(\\$\\{?\\w+\\}?)`, 'gm')
    sm = { 1: { color: CMT, fontStyle: 'italic' }, 2: { color: STR }, 3: { color: KW }, 4: { color: NUM }, 5: { color: TYP } }
  }

  if (!rx) return code

  const out: React.ReactNode[] = []
  let last = 0, idx = 0, m: RegExpExecArray | null
  while ((m = rx.exec(code)) !== null) {
    if (m.index > last) out.push(code.slice(last, m.index))
    let gi = 0
    for (let g = 1; g < m.length; g++) {
      if (m[g] !== undefined) { gi = g; break }
    }
    out.push(<span key={idx++} style={sm[gi] ?? {}}>{m[0]}</span>)
    last = m.index + m[0].length
  }
  if (last < code.length) out.push(code.slice(last))
  return <Fragment>{out}</Fragment>
}

export function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <span className="code-block block relative group">
      {lang && <span className="code-lang block">{lang}</span>}
      <button
        onClick={() => {
          navigator.clipboard?.writeText(code).catch(() => {})
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
        className="absolute top-1.5 right-1.5 w-5 h-5 flex items-center justify-center rounded text-[10px] text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/10 opacity-0 group-hover:opacity-100 transition"
        title="Copy"
      >{copied ? '✓' : '⧉'}</button>
      {highlight(code, lang)}
    </span>
  )
}

// markdown-lite: render ``` fences, `inline code`, **bold**, *italic*, ## headings, bullet lists
export function renderText(text: string) {
  const hasAny = text.includes('`') || text.includes('\n> ') || text.startsWith('> ') || /\*\*|\*[^*]|\[.+\]\(https?:\/\/|^[*\-] |^#{1,3} |^\d+\. /m.test(text) || /^\|/m.test(text) || /^-{3,}$/m.test(text)
  if (!hasAny) return text

  // inline pass: **bold**, *italic*, `code` within a single string
  const renderInline = (s: string, kb: string) => {
    const out: React.ReactNode[] = []
    const parts = s.split('`')
    const hasUnpaired = parts.length % 2 === 0
    parts.forEach((b, j) => {
      if (j % 2 === 1) {
        if (hasUnpaired && j === parts.length - 1) { out.push('`' + b); return }
        out.push(<code key={`${kb}-ic${j}`} className="inline-code">{b}</code>); return
      }
      const re = /\*\*(.+?)\*\*|\*(\S[^*\n]*?\S|\S)\*|\[([^\]]+)\]\((https?:\/\/(?:[^\s()]+|\([^\s()]*\))+)\)/g
      let last = 0, n = 0, m: RegExpExecArray | null
      while ((m = re.exec(b)) !== null) {
        if (m.index > last) out.push(b.slice(last, m.index))
        if (m[1] !== undefined)
          out.push(<strong key={`${kb}-b${n++}`}>{m[1]}</strong>)
        else if (m[2] !== undefined)
          out.push(<em key={`${kb}-e${n++}`}>{m[2]}</em>)
        else
          out.push(<a key={`${kb}-a${n++}`} href={m[4]} target="_blank" rel="noopener noreferrer" className="text-emerald-300 underline underline-offset-2 hover:text-emerald-200">{m[3]}</a>)
        last = m.index + m[0].length
      }
      if (last < b.length) out.push(b.slice(last))
    })
    return out
  }

  // per-segment renderer: headings, bullet lists, then inline
  const renderSeg = (seg: string, si: number) => {
    const hasBlock = /^#{1,3} |^[*\-] |^\d+\. |^> |^\||^---/m.test(seg)
    if (!hasBlock) return <Fragment key={si}>{renderInline(seg, `${si}`)}</Fragment>

    const lines = seg.split('\n')
    const nodes: React.ReactNode[] = []
    let listBuf: React.ReactNode[][] = []
    let olistBuf: React.ReactNode[][] = []
    let quoteBuf: React.ReactNode[][] = []
    let k = 0

    const flushList = () => {
      if (!listBuf.length) return
      nodes.push(
        <ul key={`${si}-ul${k++}`} style={{ listStyle: 'none', padding: '0 0 0 0.5rem', margin: '0.125rem 0' }}>
          {listBuf.map((item, li) => (
            <li key={li} style={{ display: 'flex', gap: '0.375rem', lineHeight: '1.5' }}>
              <span style={{ color: 'rgb(75 85 99)', flexShrink: 0 }}>–</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )
      listBuf = []
    }

    const flushOList = () => {
      if (!olistBuf.length) return
      nodes.push(
        <ol key={`${si}-ol${k++}`} style={{ listStyle: 'none', padding: '0 0 0 0.5rem', margin: '0.125rem 0' }}>
          {olistBuf.map((item, li) => (
            <li key={li} style={{ display: 'flex', gap: '0.375rem', lineHeight: '1.5' }}>
              <span style={{ color: 'rgb(75 85 99)', flexShrink: 0 }}>{li + 1}.</span>
              <span>{item}</span>
            </li>
          ))}
        </ol>
      )
      olistBuf = []
    }

    const flushQuote = () => {
      if (!quoteBuf.length) return
      nodes.push(
        <blockquote key={`${si}-bq${k++}`} style={{ borderLeft: '2px solid rgb(75 85 99)', paddingLeft: '0.625rem', margin: '0.25rem 0', color: 'rgb(156 163 175)' }}>
          {quoteBuf.map((item, qi) => (
            <div key={qi}>{item}</div>
          ))}
        </blockquote>
      )
      quoteBuf = []
    }

    let tableBuf: string[] = []

    const flushTable = () => {
      if (!tableBuf.length) return
      const rows = tableBuf.map(line => line.split('|').slice(1, -1).map(c => c.trim()))
      const sepIdx = rows.findIndex(cells => cells.length > 0 && cells.every(c => /^[\s\-:]+$/.test(c)))
      const headerRows = sepIdx >= 0 ? rows.slice(0, sepIdx) : []
      const bodyRows = sepIdx >= 0 ? rows.slice(sepIdx + 1) : rows
      nodes.push(
        <div key={`${si}-tbl${k++}`} style={{ overflowX: 'auto', margin: '0.25rem 0' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: '0.75rem', width: '100%' }}>
            {headerRows.length > 0 && (
              <thead>
                {headerRows.map((cells, ri) => (
                  <tr key={ri}>
                    {cells.map((cell, ci) => (
                      <th key={ci} style={{ padding: '0.2rem 0.5rem', border: '1px solid rgba(255,255,255,0.08)', color: 'rgb(209 213 219)', fontWeight: 500, textAlign: 'left' }}>
                        {renderInline(cell, `${si}-th${ri}-${ci}`)}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
            )}
            <tbody>
              {bodyRows.map((cells, ri) => (
                <tr key={ri}>
                  {cells.map((cell, ci) => (
                    <td key={ci} style={{ padding: '0.2rem 0.5rem', border: '1px solid rgba(255,255,255,0.08)', color: 'rgb(156 163 175)' }}>
                      {renderInline(cell, `${si}-td${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      tableBuf = []
    }

    lines.forEach((line, li) => {
      if (line.startsWith('### ')) {
        flushList(); flushOList(); flushQuote(); flushTable()
        nodes.push(<h3 key={`${si}-h3${k++}`} style={{ fontSize: '0.65rem', fontWeight: 600, color: 'rgb(107 114 128)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.375rem 0 0.125rem' }}>{renderInline(line.slice(4), `${si}-h3${li}`)}</h3>)
      } else if (line.startsWith('## ')) {
        flushList(); flushOList(); flushQuote(); flushTable()
        nodes.push(<h2 key={`${si}-h2${k++}`} style={{ fontSize: '0.7rem', fontWeight: 600, color: 'rgb(156 163 175)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0.5rem 0 0.125rem' }}>{renderInline(line.slice(3), `${si}-h2${li}`)}</h2>)
      } else if (line.startsWith('# ')) {
        flushList(); flushOList(); flushQuote(); flushTable()
        nodes.push(<h1 key={`${si}-h1${k++}`} style={{ fontSize: '0.75rem', fontWeight: 600, color: 'rgb(209 213 219)', margin: '0.5rem 0 0.125rem' }}>{renderInline(line.slice(2), `${si}-h1${li}`)}</h1>)
      } else if (/^[*\-] /.test(line)) {
        flushOList(); flushQuote(); flushTable()
        listBuf.push(renderInline(line.slice(2), `${si}-li${li}`))
      } else if (/^\d+\. /.test(line)) {
        flushList(); flushQuote(); flushTable()
        olistBuf.push(renderInline(line.replace(/^\d+\. /, ''), `${si}-oli${li}`))
      } else if (line.startsWith('> ')) {
        flushList(); flushOList(); flushTable()
        quoteBuf.push(renderInline(line.slice(2), `${si}-bq${li}`))
      } else if (/^\s*\|/.test(line)) {
        flushList(); flushOList(); flushQuote()
        tableBuf.push(line)
      } else if (/^-{3,}$/.test(line)) {
        flushList(); flushOList(); flushQuote(); flushTable()
        nodes.push(<hr key={`${si}-hr${k++}`} style={{ border: 'none', borderTop: '1px solid rgba(255,255,255,0.07)', margin: '0.5rem 0' }} />)
      } else {
        flushList(); flushOList(); flushQuote(); flushTable()
        nodes.push(<Fragment key={`${si}-t${k++}`}>{renderInline(line, `${si}-ln${li}`)}{li < lines.length - 1 ? '\n' : ''}</Fragment>)
      }
    })
    flushList(); flushOList(); flushQuote(); flushTable()
    return <Fragment key={si}>{nodes}</Fragment>
  }

  const segs = text.split('```')
  return segs.map((seg, i) => {
    if (i % 2 === 1) {
      if (i === segs.length - 1 && segs.length % 2 === 0) return renderSeg(seg, i)
      const nl = seg.indexOf('\n')
      const lang = nl > -1 ? seg.slice(0, nl).trim() : ''
      const code = (nl > -1 ? seg.slice(nl + 1) : seg).replace(/\n$/, '')
      return <CodeBlock key={i} lang={lang} code={code} />
    }
    return renderSeg(seg, i)
  })
}
