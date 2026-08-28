"use client"

// The protocol fleet drawn as what it actually is: a DAG where every edge is a
// constructor argument one contract hands another. Deploy waves aren't a
// hand-kept list — they fall out of the graph, because a contract can only be
// built once the addresses it takes exist. Hover a node to see the subgraph it
// belongs to; click to pick it for a partial deploy.

import { CSSProperties, useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { TERM_FONT, ACCENT, WRITE, short } from './shared'

export interface FleetNode {
  id: string                                  // the mod name /deploy takes
  contract: string                            // contract(s) it puts on chain
  role: string                                // what it's for, in one line
  deps: { from: string; arg: string }[]       // constructor wiring
}

/** Mirrors the constructors in src/contracts — args are the real parameter names. */
export const FLEET: FleetNode[] = [
  { id: 'token', contract: 'Token ×3', role: 'native token + test stables (ERC-20)', deps: [] },
  { id: 'oracle', contract: 'ManualPriceOracle', role: 'owner-set prices, IOracleAdapter', deps: [] },
  { id: 'registry', contract: 'Registry', role: 'modId → owner, name, data', deps: [] },
  { id: 'perms', contract: 'Perms', role: 'parent key → child key lists', deps: [] },
  {
    id: 'tokengate', contract: 'TokenGate', role: 'whitelists + prices payable tokens',
    deps: [{ from: 'oracle', arg: '_defaultOracle' }],
  },
  {
    id: 'bloctime', contract: 'BlocTime', role: 'lock the token → time-weighted stake',
    deps: [{ from: 'token', arg: '_nativeToken' }],
  },
  {
    id: 'treasury', contract: 'Treasury', role: 'splits fees owner / protocol',
    deps: [{ from: 'tokengate', arg: '_tokenGate' }],
  },
  {
    id: 'market', contract: 'Market', role: 'pay for module usage, fees to treasury',
    deps: [{ from: 'treasury', arg: '_treasury' }, { from: 'tokengate', arg: '_tokenGate' }],
  },
  {
    id: 'debit', contract: 'Debit', role: 'EIP-712 signed spends against market credit',
    deps: [{ from: 'market', arg: '_market' }],
  },
]

const BY_ID: Record<string, FleetNode> = Object.fromEntries(FLEET.map(n => [n.id, n]))

/** Config key each mod writes its address under (matches Chain._resolve_deps). */
export const CONFIG_KEY: Record<string, string> = {
  token: 'NativeToken', oracle: 'ManualPriceOracle', tokengate: 'TokenGate',
  bloctime: 'BlocTime', registry: 'Registry', treasury: 'Treasury',
  market: 'Market', debit: 'Debit', perms: 'Perms',
}

/**
 * Longest-path layering: a node sits one column right of its deepest dep.
 * Rows are then barycentre-sorted (down the graph, then back up) so edges
 * mostly run straight instead of crossing — no hand-kept ordering to drift.
 */
export function waves(): string[][] {
  const depth = new Map<string, number>()
  const walk = (id: string): number => {
    if (depth.has(id)) return depth.get(id)!
    const node = BY_ID[id]
    const d = node.deps.length ? Math.max(...node.deps.map(dep => walk(dep.from) + 1)) : 0
    depth.set(id, d)
    return d
  }
  FLEET.forEach(n => walk(n.id))
  const out: string[][] = []
  FLEET.forEach(n => (out[depth.get(n.id)!] ||= []).push(n.id))

  const row = (col: string[], id: string) => col.indexOf(id)
  const sortBy = (col: string[], score: (id: string) => number) =>
    col.map((id, i) => ({ id, i, s: score(id) }))
      .sort((a, b) => (a.s - b.s) || (a.i - b.i))
      .map(x => x.id)
  const mean = (xs: number[], fallback: number) =>
    xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : fallback

  for (let c = 1; c < out.length; c++) {
    out[c] = sortBy(out[c], id =>
      mean(BY_ID[id].deps.map(d => row(out[c - 1], d.from)).filter(r => r >= 0), 99))
  }
  for (let c = out.length - 2; c >= 0; c--) {
    out[c] = sortBy(out[c], id =>
      mean(out[c + 1].map((t, r) => BY_ID[t].deps.some(d => d.from === id) ? r : -1)
        .filter(r => r >= 0), 99))
  }
  return out
}

/** Everything `id` needs, transitively. */
export function ancestorsOf(id: string): Set<string> {
  const seen = new Set<string>()
  const walk = (cur: string) => BY_ID[cur]?.deps.forEach(d => {
    if (seen.has(d.from)) return
    seen.add(d.from)
    walk(d.from)
  })
  walk(id)
  return seen
}

/** Everything that breaks if `id` moves. */
function descendantsOf(id: string): Set<string> {
  const seen = new Set<string>()
  const walk = (cur: string) => FLEET.forEach(n => {
    if (seen.has(n.id) || !n.deps.some(d => d.from === cur)) return
    seen.add(n.id)
    walk(n.id)
  })
  walk(id)
  return seen
}

interface Edge { key: string; from: string; to: string; d: string }

// Five waves have to fit the console's content column without scrolling.
const NODE_W = 158
const GAP = 40

export function DeployGraph({ selected, onToggle, deployed, busy }: {
  selected: string[]
  onToggle: (id: string) => void
  deployed: Record<string, string>
  busy?: boolean
}) {
  const cols = useMemo(waves, [])
  const colOf = useMemo(() => {
    const map: Record<string, number> = {}
    cols.forEach((ids, c) => ids.forEach(id => { map[id] = c }))
    return map
  }, [cols])
  const wrapRef = useRef<HTMLDivElement>(null)
  const nodeRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const [edges, setEdges] = useState<Edge[]>([])
  const [canvas, setCanvas] = useState({ w: 0, h: 0 })
  const [hover, setHover] = useState<string | null>(null)

  // Edges are drawn from measured DOM boxes, so the layout stays plain flexbox
  // and the curves follow it wherever it wraps or scrolls.
  const measure = useCallback(() => {
    const wrap = wrapRef.current
    if (!wrap) return
    const base = wrap.getBoundingClientRect()
    const box = (id: string) => {
      const el = nodeRefs.current[id]
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { x: r.left - base.left + wrap.scrollLeft, y: r.top - base.top, w: r.width, h: r.height }
    }
    // How far down each column reaches — an edge that skips a column is routed
    // under it, so it can't be mistaken for touching the box it passes.
    const floors = cols.map(ids => Math.max(...ids.map(id => {
      const r = box(id)
      return r ? r.y + r.h : 0
    })))

    const next: Edge[] = []
    for (const node of FLEET) {
      for (const dep of node.deps) {
        const a = box(dep.from), b = box(node.id)
        if (!a || !b) continue
        const x1 = a.x + a.w, y1 = a.y + a.h / 2
        const x2 = b.x - 4, y2 = b.y + b.h / 2   // -4 leaves room for the arrow head
        const skipped = floors.slice(colOf[dep.from] + 1, colOf[node.id])
        let d: string
        if (skipped.length) {
          const dip = Math.max(...skipped, y1, y2) + 12
          d = `M ${x1} ${y1} C ${x1 + 28} ${y1}, ${x1 + 28} ${dip}, ${x1 + 56} ${dip}`
            + ` L ${x2 - 56} ${dip}`
            + ` C ${x2 - 28} ${dip}, ${x2 - 28} ${y2}, ${x2} ${y2}`
        } else {
          const mid = (x1 + x2) / 2
          d = `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`
        }
        next.push({ key: `${dep.from}->${node.id}`, from: dep.from, to: node.id, d })
      }
    }
    setEdges(next)
    setCanvas({ w: wrap.scrollWidth, h: wrap.scrollHeight })
  }, [cols, colOf])

  const deployKey = Object.keys(deployed).sort().join(',')
  useLayoutEffect(() => {
    measure()
    const ro = new ResizeObserver(measure)
    if (wrapRef.current) ro.observe(wrapRef.current)
    return () => ro.disconnect()
  }, [measure, deployKey])

  // Hovering focuses the whole chain the node lives on — what it needs above,
  // what depends on it below. Everything else dims out of the way.
  const focus = useMemo(() => {
    if (!hover) return null
    return new Set([hover, ...ancestorsOf(hover), ...descendantsOf(hover)])
  }, [hover])

  const all = selected.length === 0
  const isPicked = (id: string) => all || selected.includes(id)

  return (
    <div ref={wrapRef} style={{ position: 'relative', overflowX: 'auto', paddingBottom: '4px' }}>
      <svg width={canvas.w} height={canvas.h}
        style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', overflow: 'visible' }}>
        <defs>
          {[['tip-dim', 'var(--text-tertiary)'], ['tip-lit', ACCENT]].map(([id, fill]) => (
            <marker key={id} id={id} viewBox="0 0 8 8" refX="7" refY="4"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 8 4 L 0 7 z" fill={fill} />
            </marker>
          ))}
        </defs>
        {edges.map(e => {
          const lit = !!focus && focus.has(e.from) && focus.has(e.to)
          return (
            <g key={e.key} opacity={focus && !lit ? 0.12 : lit ? 1 : 0.55}>
              <path d={e.d} fill="none"
                stroke={lit ? ACCENT : 'var(--text-tertiary)'}
                strokeWidth={lit ? 2 : 1.25}
                strokeDasharray={isPicked(e.from) ? undefined : '4 4'}
                markerEnd={`url(#${lit ? 'tip-lit' : 'tip-dim'})`} />
            </g>
          )
        })}
      </svg>

      <div style={{ display: 'flex', gap: `${GAP}px`, alignItems: 'flex-start', position: 'relative', width: 'fit-content' }}>
        {cols.map((ids, wave) => (
          <div key={wave} style={{ display: 'flex', flexDirection: 'column', gap: '10px', minWidth: `${NODE_W}px` }}>
            <div style={{
              fontFamily: TERM_FONT, fontSize: '10px', letterSpacing: '0.12em',
              color: 'var(--text-tertiary)', marginBottom: '2px',
            }}>
              WAVE {wave + 1}{ids.length > 1 ? ` · ${ids.length} in parallel` : ''}
            </div>
            {ids.map(id => (
              <Node
                key={id}
                node={BY_ID[id]}
                boxRef={el => { nodeRefs.current[id] = el }}
                address={deployed[id] || ''}
                picked={isPicked(id)}
                explicit={selected.includes(id)}
                dimmed={!!focus && !focus.has(id)}
                busy={busy}
                onHover={setHover}
                onClick={() => onToggle(id)}
              />
            ))}
          </div>
        ))}
      </div>

      <div style={{
        display: 'flex', gap: '18px', flexWrap: 'wrap', marginTop: '14px',
        fontFamily: TERM_FONT, fontSize: '10px', color: 'var(--text-tertiary)',
      }}>
        <span><span style={{ color: ACCENT }}>●</span> already on chain</span>
        <span><span style={{ color: WRITE }}>○</span> nothing deployed yet</span>
        <span>{all ? 'nothing picked → DEPLOY ALL' : `picked ${selected.length}, dashed edges feed a skipped contract`}</span>
      </div>
    </div>
  )
}

// ── node ────────────────────────────────────────────────────────────────────

const Node = ({ boxRef, node, address, picked, explicit, dimmed, busy, onHover, onClick }: {
  boxRef: (el: HTMLDivElement | null) => void
  node: FleetNode
  address: string
  picked: boolean
  explicit: boolean
  dimmed: boolean
  busy?: boolean
  onHover: (id: string | null) => void
  onClick: () => void
}) => {
  const live = !!address
  const color = picked ? ACCENT : 'var(--border-color)'
  const style: CSSProperties = {
    border: `2px solid ${color}`,
    background: explicit ? `${ACCENT}14` : 'var(--bg-secondary)',
    boxShadow: explicit ? `3px 3px 0px 0px ${ACCENT}` : 'none',
    padding: '8px 10px',
    cursor: busy ? 'not-allowed' : 'pointer',
    opacity: dimmed ? 0.3 : busy && !picked ? 0.45 : 1,
    transition: 'opacity 120ms linear',
    textAlign: 'left',
    fontFamily: TERM_FONT,
    width: `${NODE_W}px`,
    minHeight: '96px',                       // keeps rows level across columns
  }

  return (
    <div ref={boxRef}
      data-node={node.id}
      onClick={busy ? undefined : onClick}
      onMouseEnter={() => onHover(node.id)}
      onMouseLeave={() => onHover(null)}
      title={live ? address : 'not deployed on this network'}
      style={style}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '6px' }}>
        <span style={{ fontSize: '13px', color: picked ? ACCENT : 'var(--text-secondary)' }}>{node.id}</span>
        <span style={{ fontSize: '9px', color: live ? ACCENT : WRITE }}>{live ? '● live' : '○ new'}</span>
      </div>
      <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '3px' }}>{node.contract}</div>
      <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '5px', lineHeight: 1.35 }}>
        {node.role}
      </div>
      {/* the incoming edges, spelled out — which address lands in which arg */}
      {node.deps.map(dep => (
        <div key={dep.arg} style={{ fontSize: '9.5px', color: picked ? ACCENT : 'var(--text-tertiary)', marginTop: '4px' }}>
          {dep.arg} ← {dep.from}
        </div>
      ))}
      <div style={{ fontSize: '10px', color: live ? 'var(--text-secondary)' : 'var(--text-tertiary)', marginTop: '6px' }}>
        {live ? short(address, 6, 4) : '—'}
      </div>
    </div>
  )
}
