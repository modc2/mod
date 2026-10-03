'use client'

// Flow — the canvas where agents are CONNECTED.
//
// It does not build an agent. An agent is a prompt, a model, a toolbox and a
// memory module, and all four are settled in the registry (AgentsPanel /
// AgentEditor, which write the agent's own mod.py). This canvas starts where
// that one stops: every agent node here is an agent that already exists,
// picked whole off a list, and what gets drawn is the thing that has nowhere
// else to live — the wiring between them.
//
//   ▷ input ─▶ ◆ architect ─▶ ⊘ gate ─pass─▶ ◆ builder ─▶ ⚖ judge ─▶ ◎ output
//                                                             └fail─▶ ↻ loop
//
// The vocabulary is the server's, not this file's: GET /graph/kinds returns
// every node kind, the ports it answers on, the fields it carries and the
// predicates a gate may test (src/graph/protocol.py), and the palette, the
// port rows and the inspectors are all generated from it. A kind added on the
// server shows up here without a frontend change — and, more to the point, a
// graph drawn here is a document the server can run, share and validate
// rather than a picture of one.

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { API_URL } from '../config'
import Select from './Select'

// ── the protocol, as the server describes it ──
type KindSpec = {
  label: string; icon: string; accent: string
  inputs: number | string; out: string[] | null; fan: 'each' | 'gather'
  summary: string; doc: string
  fields?: { key: string; type: string; label: string; hint?: string;
             options?: string[]; default?: any; required?: boolean }[]
}
type Protocol = {
  kinds: Record<string, KindSpec>
  ops: Record<string, { label: string; arg: string }>
  fields: { key: string; label: string }[]
}

type FNode = { id: string; kind: string; x: number; y: number; data: Record<string, any> }
type FEdge = { id: string; from: string; to: string; port: string }
type Viewport = { x: number; y: number; k: number }
type GraphCard = {
  id: string; name: string; description?: string; nodes: number; edges: number
  agents?: string[]; owner?: string | null; mine?: boolean; seed?: boolean; cid?: string
}
type AgentInfo = { value: string; label: string; icon: string; description?: string; builtin?: boolean }
type NodeState = { status: 'running' | 'done' | 'error' | 'parked'; text?: string; ms?: number }

const NODE_W = 236
const HEADER_H = 42
const PORT_ROW = 22

const ACCENTS: Record<string, { text: string; border: string; wire: string; glow: string }> = {
  emerald: { text: 'text-emerald-300', border: 'border-emerald-400/30', wire: 'rgb(var(--a-400))', glow: 'bg-emerald-400' },
  sky:     { text: 'text-sky-300',     border: 'border-sky-400/30',     wire: 'rgb(var(--i-400))', glow: 'bg-sky-400' },
  amber:   { text: 'text-amber-300',   border: 'border-amber-400/30',   wire: 'rgb(var(--w-400))', glow: 'bg-amber-400' },
  violet:  { text: 'text-violet-300',  border: 'border-violet-400/30',  wire: 'rgb(var(--v-400))', glow: 'bg-violet-400' },
}
const accentOf = (spec?: KindSpec) => ACCENTS[spec?.accent || 'emerald'] || ACCENTS.emerald

// a port's own colour, so a wire says what left by it without being followed
const PORT_WIRE: Record<string, string> = {
  pass: 'rgb(var(--a-400))', fail: 'rgb(248 113 113)', err: 'rgb(248 113 113)',
  done: 'rgb(var(--v-400))', else: 'rgb(148 163 184)',
}
const wireOf = (spec: KindSpec | undefined, port: string) =>
  PORT_WIRE[port] || accentOf(spec).wire

let seq = 0
const nid = () => `n${Date.now().toString(36)}${(seq++).toString(36)}`
const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

export default function Flow({
  token, isHost, address, onSignIn, onOpenAgents, initialAgent, initialGraph,
}: {
  token?: string | null
  isHost?: boolean
  address?: string | null
  onSignIn?: () => void
  /** "an agent is missing" — this canvas never makes one, it sends you there */
  onOpenAgents?: (name?: string | null) => void
  /** start a new flow with this agent already on it */
  initialAgent?: string | null
  /** open a saved flow */
  initialGraph?: string | null
}) {
  const canSave = !!token || !!isHost

  const [proto, setProto] = useState<Protocol | null>(null)
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [tools, setTools] = useState<string[]>([])
  const [boxes, setBoxes] = useState<string[]>([])
  const [saved, setSaved] = useState<GraphCard[]>([])

  const [nodes, setNodes] = useState<FNode[]>([])
  const [edges, setEdges] = useState<FEdge[]>([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const [viewport, setViewport] = useState<Viewport>({ x: 40, y: 40, k: 0.9 })
  const [selected, setSelected] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [paletteQ, setPaletteQ] = useState('')

  // ── the run ──
  const [query, setQuery] = useState('')
  const [running, setRunning] = useState(false)
  const [states, setStates] = useState<Record<string, NodeState>>({})
  const [result, setResult] = useState<any>(null)
  const [log, setLog] = useState<string[]>([])
  const [showRun, setShowRun] = useState(true)
  const abortRef = useRef<AbortController | null>(null)

  const canvasRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef(viewport)
  useEffect(() => { viewRef.current = viewport }, [viewport])
  const nodesRef = useRef(nodes)
  useEffect(() => { nodesRef.current = nodes }, [nodes])
  // measured node heights — ports hang off the bottom edge, and a node's
  // height is whatever its fields came out as, so the wires read the DOM
  // rather than a table of guesses that drifts the first time a field grows
  const [heights, setHeights] = useState<Record<string, number>>({})
  const nodeEls = useRef<Record<string, HTMLDivElement | null>>({})

  const flashT = useRef<number | undefined>(undefined)
  const flash = (ok: boolean, text: string) => {
    setMsg({ ok, text })
    window.clearTimeout(flashT.current)
    flashT.current = window.setTimeout(() => setMsg(null), 4000)
  }

  // ── catalogs ──
  const loadGraphs = useCallback(() => {
    fetch(`${API_URL}/graphs${token ? `?key=${encodeURIComponent(token)}` : ''}`,
      { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setSaved(d.graphs || [])).catch(() => {})
  }, [token])

  useEffect(() => {
    fetch(`${API_URL}/graph/kinds`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(setProto).catch(() => {})
    fetch(`${API_URL}/agents`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setAgents(Object.entries(d.schemas || {}).map(
        ([k, v]: [string, any]) => ({ value: k, label: v.name || k, icon: v.icon || '>_',
                                      description: v.description, builtin: !!v.builtin }))))
      .catch(() => {})
    fetch(`${API_URL}/tools`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setTools((d.tools || []).map((t: any) => t.name))).catch(() => {})
    fetch(`${API_URL}/toolboxes`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setBoxes((d.toolboxes || []).map((b: any) => b.name))).catch(() => {})
    loadGraphs()
  }, [loadGraphs])

  // ── graph helpers ──
  const specOf = (n: FNode): KindSpec | undefined => proto?.kinds[n.kind]
  const portsOf = useCallback((n: FNode): string[] => {
    if (n.kind === 'router') {
      const routes = (n.data.routes || []) as any[]
      return [...routes.map(r => String(r.port || '').trim()).filter(Boolean), 'else']
    }
    return (proto?.kinds[n.kind]?.out || []) as string[]
  }, [proto])
  const heightOf = (n: FNode) => heights[n.id] || (HEADER_H + 90 + portsOf(n).length * PORT_ROW)

  const toWorld = (cx: number, cy: number) => {
    const r = canvasRef.current?.getBoundingClientRect()
    const v = viewRef.current
    return { x: ((cx - (r?.left || 0)) - v.x) / v.k, y: ((cy - (r?.top || 0)) - v.y) / v.k }
  }

  const defaults = (kind: string): Record<string, any> => {
    const out: Record<string, any> = {}
    for (const f of proto?.kinds[kind]?.fields || []) {
      if (f.default !== undefined) out[f.key] = f.default
    }
    if (kind === 'gate') out.rules = [{ op: 'contains', value: '', field: 'text' }]
    if (kind === 'router') out.routes = [{ port: 'a', rules: [{ op: 'contains', value: '', field: 'text' }] }]
    if (kind === 'agent') out.agent = agents[0]?.value || 'default'
    return out
  }

  const placeSeq = useRef(0)
  const clickPlace = () => {
    const r = canvasRef.current?.getBoundingClientRect()
    const w = toWorld((r?.left || 0) + (r?.width || 700) * 0.42, (r?.top || 0) + (r?.height || 400) * 0.3)
    const i = placeSeq.current++
    return { x: w.x + (i % 3) * 60, y: w.y + (i % 4) * 90 }
  }

  const addNode = (kind: string, x: number, y: number, data: Record<string, any> = {}) => {
    const node: FNode = { id: nid(), kind, x: x - NODE_W / 2, y: y - 20,
                          data: { ...defaults(kind), ...data } }
    setNodes(ns => [...ns, node])
    setSelected(node.id)
    setDirty(true)
    return node
  }

  const removeNode = (id: string) => {
    setNodes(ns => ns.filter(n => n.id !== id))
    setEdges(es => es.filter(e => e.from !== id && e.to !== id))
    if (selected === id) setSelected(null)
    setDirty(true)
  }

  const patch = (id: string, p: Record<string, any>) => {
    setNodes(ns => ns.map(n => n.id === id ? { ...n, data: { ...n.data, ...p } } : n))
    setDirty(true)
  }

  const connect = (from: string, port: string, to: string) => {
    if (from === to) return
    setEdges(es => es.some(e => e.from === from && e.to === to && e.port === port)
      ? es : [...es, { id: nid(), from, to, port }])
    setDirty(true)
  }

  // ── starters ──
  const blank = useCallback((withAgent?: string | null) => {
    const ins: FNode = { id: nid(), kind: 'input', x: 40, y: 180, data: { label: 'request' } }
    const out: FNode = { id: nid(), kind: 'output', x: 700, y: 180, data: { label: 'answer' } }
    const mid: FNode | null = withAgent
      ? { id: nid(), kind: 'agent', x: 360, y: 150, data: { agent: withAgent, prompt: '' } }
      : null
    const ns = mid ? [ins, mid, out] : [ins, out]
    const es: FEdge[] = mid
      ? [{ id: nid(), from: ins.id, to: mid.id, port: 'out' },
         { id: nid(), from: mid.id, to: out.id, port: 'out' }]
      : [{ id: nid(), from: ins.id, to: out.id, port: 'out' }]
    setNodes(ns); setEdges(es)
    setName(''); setDescription(''); setLoadedId(null)
    setStates({}); setResult(null); setLog([])
    setDirty(false)
    setViewport({ x: 40, y: 40, k: 0.9 })
  }, [])

  const loadGraph = useCallback((id: string) => {
    fetch(`${API_URL}/graphs/${id}${token ? `?key=${encodeURIComponent(token)}` : ''}`,
      { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(g => {
        if (g.error) { flash(false, g.error); return }
        setNodes((g.nodes || []).map((n: any) => ({ ...n, data: n.data || {} })))
        setEdges((g.edges || []).map((e: any) => ({ ...e, id: e.id || nid(), port: e.port || 'out' })))
        setName(g.name || id); setDescription(g.description || '')
        setLoadedId(g.id); setDirty(false)
        setStates({}); setResult(null); setLog([])
        if (g.viewport) setViewport(g.viewport)
      })
      .catch(e => flash(false, String(e.message || e)))
  }, [token])

  // first paint: whatever was asked for
  const booted = useRef(false)
  useEffect(() => {
    if (booted.current || !proto) return
    booted.current = true
    if (initialGraph) loadGraph(initialGraph)
    else blank(initialAgent || null)
  }, [proto, initialGraph, initialAgent, loadGraph, blank])

  // ── validation, as the server sees it ──
  const [valid, setValid] = useState<{ ok: boolean; errors: string[]; warnings: string[] } | null>(null)
  useEffect(() => {
    if (!nodes.length) { setValid(null); return }
    const t = setTimeout(() => {
      fetch(`${API_URL}/graphs/validate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name || 'draft', nodes, edges }),
        signal: AbortSignal.timeout(8000),
      }).then(r => r.json()).then(v => setValid(v)).catch(() => {})
    }, 400)
    return () => clearTimeout(t)
  }, [nodes, edges, name])

  // ── save / delete ──
  const save = async (): Promise<string | null> => {
    if (!canSave) { flash(false, 'sign in to keep this flow'); return null }
    if (!name.trim()) { flash(false, 'give the flow a name'); return null }
    setSaving(true)
    try {
      const res = await fetch(`${API_URL}/graphs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: loadedId || slugify(name), name, description,
                               nodes, edges, viewport, key: token }),
      })
      const d = await res.json()
      if (d.error) { flash(false, d.error); return null }
      setLoadedId(d.id); setDirty(false); loadGraphs()
      flash(true, d.id !== (loadedId || slugify(name))
        ? `saved as "${d.id}" — the starter it came from stays as it was`
        : `saved "${d.id}"`)
      return d.id
    } catch (e: any) {
      flash(false, e.message); return null
    } finally { setSaving(false) }
  }

  const del = async () => {
    if (!loadedId) return
    const res = await fetch(`${API_URL}/graphs/${loadedId}${token ? `?key=${encodeURIComponent(token)}` : ''}`,
      { method: 'DELETE' })
    const d = await res.json()
    if (d.error) { flash(false, d.error); return }
    loadGraphs(); blank(null); flash(true, `deleted "${loadedId}"`)
  }

  // ── run ──
  const stop = () => { abortRef.current?.abort(); abortRef.current = null; setRunning(false) }

  const run = async () => {
    if (running) return stop()
    setStates({}); setResult(null); setLog([]); setShowRun(true)
    setRunning(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    const body = {
      ...(loadedId && !dirty ? { id: loadedId } : { graph: { name: name || 'draft', nodes, edges } }),
      query, key: token,
    }
    try {
      const res = await fetch(`${API_URL}/graphs/run/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), signal: ctrl.signal,
      })
      if (!res.ok || !res.body) throw new Error('the run could not be started')
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const frames = buf.split('\n\n')
        buf = frames.pop() || ''
        for (const frame of frames) {
          const line = frame.split('\n').find(l => l.startsWith('data: '))
          if (!line) continue
          let ev: any
          try { ev = JSON.parse(line.slice(6)) } catch { continue }
          onEvent(ev)
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') flash(false, e.message || String(e))
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }

  const onEvent = (ev: any) => {
    if (ev.type === 'node_start') {
      setStates(s => ({ ...s, [ev.node]: { status: 'running' } }))
      setLog(l => [...l.slice(-200), `▸ ${ev.name || ev.kind}`])
    } else if (ev.type === 'node_done') {
      setStates(s => ({ ...s, [ev.node]: { status: ev.ok === false ? 'error' : 'done',
                                           text: ev.out, ms: ev.ms } }))
      setLog(l => [...l.slice(-200), `  ${ev.ok === false ? '✕' : '✓'} ${ev.name} · ${
        (ev.ports || []).join('/')} · ${ev.ms}ms`])
    } else if (ev.type === 'parked') {
      setStates(s => ({ ...s, [ev.node]: { status: 'parked' } }))
    } else if (ev.type === 'step') {
      const st = ev.step || {}
      setLog(l => [...l.slice(-200), `    ${st.tool || 'step'}${st.error ? ' · err' : ''}`])
    } else if (ev.type === 'done') {
      setResult(ev.result)
      if (ev.result?.stopped) flash(false, ev.result.stopped)
    } else if (ev.type === 'error') {
      flash(false, ev.error)
      setResult({ error: ev.error })
    }
  }

  // ── canvas interaction ──
  const [connecting, setConnecting] = useState<{ from: string; port: string; mx: number; my: number } | null>(null)
  const panRef = useRef<{ sx: number; sy: number; vx: number; vy: number } | null>(null)

  const onCanvasDown = (e: React.PointerEvent) => {
    if (e.target !== e.currentTarget) return
    setSelected(null)
    panRef.current = { sx: e.clientX, sy: e.clientY, vx: viewRef.current.x, vy: viewRef.current.y }
    const move = (ev: PointerEvent) => {
      const p = panRef.current
      if (!p) return
      setViewport(v => ({ ...v, x: p.vx + (ev.clientX - p.sx), y: p.vy + (ev.clientY - p.sy) }))
    }
    const up = () => {
      panRef.current = null
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  useEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = el.getBoundingClientRect()
      const mx = e.clientX - r.left, my = e.clientY - r.top
      setViewport(v => {
        const k = Math.min(1.8, Math.max(0.3, v.k * (e.deltaY < 0 ? 1.08 : 0.93)))
        return { x: mx - ((mx - v.x) / v.k) * k, y: my - ((my - v.y) / v.k) * k, k }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const startDrag = (e: React.PointerEvent, n: FNode) => {
    e.stopPropagation()
    setSelected(n.id)
    const s = { sx: e.clientX, sy: e.clientY, nx: n.x, ny: n.y }
    const move = (ev: PointerEvent) => {
      const k = viewRef.current.k
      setNodes(ns => ns.map(x => x.id === n.id
        ? { ...x, x: s.nx + (ev.clientX - s.sx) / k, y: s.ny + (ev.clientY - s.sy) / k } : x))
    }
    const up = () => {
      setDirty(true)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const startConnect = (e: React.PointerEvent, from: FNode, port: string) => {
    e.stopPropagation(); e.preventDefault()
    const w = toWorld(e.clientX, e.clientY)
    setConnecting({ from: from.id, port, mx: w.x, my: w.y })
    const move = (ev: PointerEvent) => {
      const p = toWorld(ev.clientX, ev.clientY)
      setConnecting(c => c ? { ...c, mx: p.x, my: p.y } : c)
    }
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setConnecting(null)
      const hit = nodesRef.current.find(n => {
        const el = nodeEls.current[n.id]
        if (!el || n.id === from.id) return false
        const r = el.getBoundingClientRect()
        return ev.clientX >= r.left - 12 && ev.clientX <= r.right + 12
            && ev.clientY >= r.top - 12 && ev.clientY <= r.bottom + 12
      })
      if (hit) connect(from.id, port, hit.id)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const spec = e.dataTransfer.getData('text/flow-node')
    if (!spec) return
    const w = toWorld(e.clientX, e.clientY)
    const [kind, arg] = spec.split(':')
    addNode(kind, w.x, w.y, kind === 'agent' && arg ? { agent: arg } : {})
  }

  // ── geometry ──
  const portOut = (n: FNode, port: string) => {
    const ports = portsOf(n)
    const i = Math.max(0, ports.indexOf(port))
    const h = heightOf(n)
    return { x: n.x + NODE_W, y: n.y + h - (ports.length - i - 0.5) * PORT_ROW }
  }
  const portIn = (n: FNode) => ({ x: n.x, y: n.y + HEADER_H / 2 })
  const path = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const c = Math.max(48, Math.abs(b.x - a.x) * 0.45)
    return `M ${a.x} ${a.y} C ${a.x + c} ${a.y}, ${b.x - c} ${b.y}, ${b.x} ${b.y}`
  }

  const measure = useCallback((id: string, el: HTMLDivElement | null) => {
    nodeEls.current[id] = el
    if (!el) return
    const set = () => setHeights(h => {
      const next = el.offsetHeight
      return h[id] === next ? h : { ...h, [id]: next }
    })
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── inspectors ──
  const RuleRows = ({ rules, onChange }: { rules: any[]; onChange: (r: any[]) => void }) => (
    <div className="space-y-1">
      {(rules || []).map((r, i) => (
        <div key={i} className="flex items-center gap-1">
          <Select size="sm" accent="amber" className="w-[38%]"
            value={r.field || 'text'} onChange={v => onChange(rules.map((x, j) => j === i ? { ...x, field: v } : x))}
            options={(proto?.fields || []).map(f => ({ value: f.key, label: f.label }))} />
          <Select size="sm" accent="amber" className="w-[34%]"
            value={r.op || 'contains'} onChange={v => onChange(rules.map((x, j) => j === i ? { ...x, op: v } : x))}
            options={Object.entries(proto?.ops || {}).map(([k, o]) => ({ value: k, label: o.label }))} />
          {(proto?.ops?.[r.op]?.arg || 'text') !== 'none' && (
            <input value={r.value ?? ''} placeholder="value"
              onChange={e => onChange(rules.map((x, j) => j === i ? { ...x, value: e.target.value } : x))}
              className="flex-1 min-w-0 bg-white/[0.05] border border-white/[0.09] rounded px-1.5 py-1 text-[11px] text-gray-200 outline-none focus:border-amber-400/40" />
          )}
          <button onClick={() => onChange(rules.filter((_, j) => j !== i))}
            className="text-gray-600 hover:text-red-400 text-[11px] px-0.5">✕</button>
        </div>
      ))}
      <button onClick={() => onChange([...(rules || []), { op: 'contains', value: '', field: 'text' }])}
        className="text-[10px] text-gray-500 hover:text-amber-300 transition">+ rule</button>
    </div>
  )

  const field = (n: FNode, f: NonNullable<KindSpec['fields']>[number]) => {
    const v = n.data[f.key]
    const common = "w-full bg-white/[0.05] border border-white/[0.09] rounded-md px-2 py-1.5 text-[11px] text-gray-200 outline-none placeholder:text-gray-600 focus:border-white/25 transition"
    if (f.type === 'agent') {
      return (
        <div key={f.key} className="space-y-1">
          <Select size="sm" accent="emerald" className="w-full" placeholder="pick an agent…"
            value={v || ''} onChange={x => patch(n.id, { [f.key]: x })}
            options={agents.map(a => ({ value: a.value, label: a.label, icon: a.icon,
                                        hint: a.description, badge: a.builtin ? 'built-in' : undefined }))} />
          {/* the canvas never writes an agent — it sends you where they live */}
          <button onClick={() => onOpenAgents?.(v || null)}
            className="text-[9px] text-gray-600 hover:text-emerald-300 transition">
            {v ? `edit "${v}" in AGENTS ↗` : 'no agent yet — make one in AGENTS ↗'}
          </button>
        </div>
      )
    }
    if (f.type === 'toolbox') {
      return <Select key={f.key} size="sm" accent="emerald" className="w-full"
        placeholder={f.label} value={v || ''} onChange={x => patch(n.id, { [f.key]: x })}
        options={[{ value: '', label: "the agent's own tools" },
                  ...boxes.map(b => ({ value: b, label: b, icon: '◇' }))]} />
    }
    if (f.type === 'tool') {
      return <Select key={f.key} size="sm" accent="emerald" className="w-full"
        placeholder="pick a tool…" value={v || ''} onChange={x => patch(n.id, { [f.key]: x })}
        options={tools.map(t => ({ value: t, label: t, icon: '◇' }))} />
    }
    if (f.type === 'select') {
      return <Select key={f.key} size="sm" accent="sky" className="w-full" placeholder={f.label}
        value={v || f.default || ''} onChange={x => patch(n.id, { [f.key]: x })}
        options={(f.options || []).map(o => ({ value: o, label: o }))} />
    }
    if (f.type === 'bool') {
      return (
        <label key={f.key} className="flex items-center gap-1.5 text-[10px] text-gray-400 cursor-pointer">
          <input type="checkbox" checked={!!v} onChange={e => patch(n.id, { [f.key]: e.target.checked })}
            className="accent-emerald-500" />
          {f.label}
        </label>
      )
    }
    if (f.type === 'rules') {
      return <div key={f.key} className="space-y-1">
        <div className="text-[9px] uppercase tracking-wider text-gray-600">{f.label}</div>
        <RuleRows rules={v || []} onChange={r => patch(n.id, { [f.key]: r })} />
      </div>
    }
    if (f.type === 'routes') {
      const routes = (v || []) as any[]
      return (
        <div key={f.key} className="space-y-1.5">
          {routes.map((r, i) => (
            <div key={i} className="rounded-md border border-violet-400/20 p-1.5 space-y-1">
              <div className="flex items-center gap-1">
                <span className="text-[9px] text-gray-600 uppercase tracking-wider">port</span>
                <input value={r.port || ''} placeholder="name"
                  onChange={e => patch(n.id, { routes: routes.map((x, j) => j === i ? { ...x, port: e.target.value } : x) })}
                  className="flex-1 min-w-0 bg-white/[0.05] border border-white/[0.09] rounded px-1.5 py-1 text-[11px] text-violet-200 outline-none focus:border-violet-400/40" />
                <button onClick={() => patch(n.id, { routes: routes.filter((_, j) => j !== i) })}
                  className="text-gray-600 hover:text-red-400 text-[11px] px-0.5">✕</button>
              </div>
              <RuleRows rules={r.rules || []}
                onChange={rr => patch(n.id, { routes: routes.map((x, j) => j === i ? { ...x, rules: rr } : x) })} />
            </div>
          ))}
          <button onClick={() => patch(n.id, { routes: [...routes, { port: `route${routes.length + 1}`, rules: [] }] })}
            className="text-[10px] text-gray-500 hover:text-violet-300 transition">+ route</button>
        </div>
      )
    }
    if (f.type === 'textarea') {
      return <textarea key={f.key} value={v || ''} rows={3} placeholder={f.hint || f.label}
        onChange={e => patch(n.id, { [f.key]: e.target.value })}
        className={`${common} resize-none leading-relaxed`} />
    }
    if (f.type === 'json') {
      return <textarea key={f.key} rows={2} placeholder={f.hint || f.label}
        value={typeof v === 'string' ? v : v ? JSON.stringify(v) : ''}
        onChange={e => {
          const raw = e.target.value
          try { patch(n.id, { [f.key]: JSON.parse(raw) }) } catch { patch(n.id, { [f.key]: raw }) }
        }}
        className={`${common} font-mono resize-none`} />
    }
    if (f.type === 'number') {
      return <div key={f.key} className="flex items-center gap-1.5">
        <span className="text-[10px] text-gray-500 shrink-0">{f.label}</span>
        <input type="number" value={v ?? ''} placeholder={String(f.default ?? '')}
          onChange={e => patch(n.id, { [f.key]: e.target.value === '' ? undefined : Number(e.target.value) })}
          className={`${common} w-20`} />
      </div>
    }
    if (f.type === 'model') {
      return <input key={f.key} value={v || ''} placeholder={f.hint || "the agent's own model"}
        onChange={e => patch(n.id, { [f.key]: e.target.value })} className={`${common} font-mono`} />
    }
    return <input key={f.key} value={v || ''} placeholder={f.hint || f.label}
      onChange={e => patch(n.id, { [f.key]: e.target.value })} className={common} />
  }

  // ── palette ──
  const paletteKinds = useMemo(() => Object.entries(proto?.kinds || {}), [proto])
  const shownAgents = agents.filter(a => !paletteQ.trim()
    || a.label.toLowerCase().includes(paletteQ.trim().toLowerCase())
    || a.value.includes(paletteQ.trim().toLowerCase()))

  if (!proto) {
    return <div className="h-full flex items-center justify-center text-xs text-gray-600">
      loading the graph protocol…
    </div>
  }

  const runDisabled = running ? false : (!nodes.length || !!(valid && !valid.ok))

  return (
    <div className="h-full flex min-h-0">
      {/* ── palette ── */}
      <div className="w-56 shrink-0 border-r border-white/[0.06] flex flex-col min-h-0 bg-surface-1">
        <div className="px-3 py-2.5 border-b border-white/[0.06] shrink-0">
          <div className="text-[10px] text-gray-500 uppercase tracking-wider font-medium">Agents</div>
          <div className="text-[9px] text-gray-700 mt-0.5">drag one on — it runs whole, as it is</div>
        </div>
        <div className="px-2 py-1.5 shrink-0">
          <input value={paletteQ} onChange={e => setPaletteQ(e.target.value)} placeholder="filter agents…"
            className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-[11px] text-gray-300 outline-none placeholder:text-gray-700 focus:border-emerald-500/40 transition" />
        </div>
        <div className="flex-1 overflow-y-auto min-h-0 px-2 pb-2 space-y-0.5">
          {shownAgents.map(a => (
            <div key={a.value} draggable
              onDragStart={e => { e.dataTransfer.setData('text/flow-node', `agent:${a.value}`); e.dataTransfer.effectAllowed = 'copy' }}
              onClick={() => { const p = clickPlace(); addNode('agent', p.x, p.y, { agent: a.value }) }}
              title={a.description || a.value}
              className="flex items-center gap-2 px-2 py-1.5 rounded-md border border-transparent hover:border-emerald-500/20 hover:bg-emerald-500/[0.05] cursor-grab active:cursor-grabbing transition select-none group">
              <span className="text-[11px] text-emerald-400/70 w-4 text-center shrink-0">{a.icon}</span>
              <span className="text-[11px] truncate text-gray-400 group-hover:text-gray-200">{a.label}</span>
              <span className="ml-auto text-gray-800 group-hover:text-gray-600 text-[10px]">⠿</span>
            </div>
          ))}
          {!shownAgents.length && (
            <button onClick={() => onOpenAgents?.(null)}
              className="w-full text-left px-2 py-2 text-[10px] text-gray-600 hover:text-emerald-300 transition">
              no agents match — make one in AGENTS ↗
            </button>
          )}
        </div>
        <div className="px-3 pt-2 pb-1 border-t border-white/[0.06] shrink-0">
          <div className="text-[10px] text-gray-500 uppercase tracking-wider font-medium">Control</div>
          <div className="text-[9px] text-gray-700 mt-0.5">what decides who runs next</div>
        </div>
        <div className="p-2 pt-1 space-y-1 shrink-0 max-h-[46%] overflow-y-auto">
          {paletteKinds.filter(([k]) => k !== 'agent').map(([k, spec]) => (
            <div key={k} draggable
              onDragStart={e => { e.dataTransfer.setData('text/flow-node', k); e.dataTransfer.effectAllowed = 'copy' }}
              onClick={() => { const p = clickPlace(); addNode(k, p.x, p.y) }}
              title={spec.doc}
              className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-white/[0.07] bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/[0.14] cursor-grab active:cursor-grabbing transition select-none group">
              <span className={`${accentOf(spec).text} text-sm w-4 text-center shrink-0`}>{spec.icon}</span>
              <div className="min-w-0 flex-1">
                <div className="text-[11px] text-gray-300 group-hover:text-gray-100 truncate">{spec.label}</div>
                <div className="text-[9px] text-gray-600 truncate">{spec.summary}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── canvas column ── */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {/* toolbar */}
        <div className="border-b border-white/[0.06] px-3 py-2 flex items-center gap-2 shrink-0 bg-surface-1">
          <Select accent="violet" className="max-w-[170px]" title="Open a saved flow"
            value={loadedId || ''} onChange={v => v ? loadGraph(v) : blank(null)}
            options={[{ value: '', label: 'new flow…', icon: '✦' },
                      ...saved.map(g => ({ value: g.id, label: g.name, icon: '⋔',
                                           hint: `${g.nodes} nodes · ${(g.agents || []).join(', ')}`,
                                           badge: g.seed ? 'starter' : g.mine ? 'yours' : undefined }))]} />
          <input value={name} onChange={e => { setName(e.target.value); setDirty(true) }}
            placeholder="flow name"
            className="w-36 bg-white/[0.05] border border-white/[0.09] rounded-md px-2 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-violet-400/40 transition" />
          <button onClick={() => blank(null)}
            className="px-2.5 py-1.5 rounded-md text-xs border border-white/[0.08] text-gray-500 hover:text-gray-300 hover:border-white/20 transition">
            new
          </button>
          {loadedId && (
            <button onClick={del}
              className="px-2.5 py-1.5 rounded-md text-xs border border-red-500/20 text-red-400/80 hover:text-red-300 hover:bg-red-500/10 transition">
              delete
            </button>
          )}
          {msg && (
            <span className={`text-[11px] px-2 py-1 rounded-md truncate min-w-0 ${msg.ok ? 'text-emerald-300 bg-emerald-500/10' : 'text-red-400 bg-red-500/10'}`}>
              {msg.text}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1.5 shrink-0">
            {dirty && <span className="text-[10px] text-amber-300/70">● unsaved</span>}
            {!canSave && (
              <button onClick={onSignIn}
                className="px-3 py-1.5 rounded-md text-xs font-medium border border-sky-500/30 bg-sky-500/10 text-sky-200 hover:bg-sky-500/20 transition">
                sign in to save
              </button>
            )}
            <button onClick={save} disabled={saving || !canSave}
              className="px-3 py-1.5 rounded-md text-xs font-medium border border-violet-500/25 bg-violet-500/10 text-violet-200 hover:bg-violet-500/20 disabled:opacity-40 transition">
              {saving ? 'saving…' : 'save'}
            </button>
            <button onClick={() => setShowRun(s => !s)}
              className="px-2.5 py-1.5 rounded-md text-xs border border-white/[0.08] text-gray-500 hover:text-gray-300 transition"
              title="Show or hide the run bar">
              {showRun ? 'hide run' : 'run ▸'}
            </button>
          </div>
        </div>

        {/* validation — what the server says about this graph, before it runs */}
        {valid && (valid.errors.length > 0 || valid.warnings.length > 0) && (
          <div className="shrink-0 px-3 py-1.5 border-b border-white/[0.06] bg-surface-1 flex items-start gap-3 flex-wrap">
            {valid.errors.map((e, i) => (
              <span key={`e${i}`} className="text-[10px] text-red-300/90">✕ {e}</span>
            ))}
            {valid.warnings.map((w, i) => (
              <span key={`w${i}`} className="text-[10px] text-amber-300/70">△ {w}</span>
            ))}
          </div>
        )}

        {/* canvas */}
        <div ref={canvasRef}
          className="flex-1 min-h-0 relative overflow-hidden builder-grid cursor-grab active:cursor-grabbing"
          style={{ backgroundPosition: `${viewport.x}px ${viewport.y}px`,
                   backgroundSize: `${22 * viewport.k}px ${22 * viewport.k}px` }}
          onPointerDown={onCanvasDown}
          onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
          onDrop={onDrop}>

          {/* wires */}
          <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ overflow: 'visible' }}>
            <g transform={`translate(${viewport.x},${viewport.y}) scale(${viewport.k})`}>
              {edges.map(e => {
                const from = nodes.find(n => n.id === e.from)
                const to = nodes.find(n => n.id === e.to)
                if (!from || !to) return null
                const colour = wireOf(specOf(from), e.port)
                const d = path(portOut(from, e.port), portIn(to))
                const live = states[e.from]?.status === 'done' && states[e.to]
                return (
                  <g key={e.id} className="pointer-events-auto">
                    <path d={d} stroke="transparent" strokeWidth={14} fill="none" className="cursor-pointer"
                      onClick={() => { setEdges(es => es.filter(x => x.id !== e.id)); setDirty(true) }}>
                      <title>{`${e.port} → click to disconnect`}</title>
                    </path>
                    <path d={d} style={{ stroke: colour }} strokeWidth={live ? 2.6 : 1.8} fill="none"
                      opacity={live ? 0.95 : 0.6} className="pointer-events-none builder-wire" />
                  </g>
                )
              })}
              {connecting && (() => {
                const from = nodes.find(n => n.id === connecting.from)
                if (!from) return null
                return <path d={path(portOut(from, connecting.port), { x: connecting.mx, y: connecting.my })}
                  style={{ stroke: wireOf(specOf(from), connecting.port) }} strokeWidth={1.8}
                  strokeDasharray="5 4" fill="none" opacity={0.85} />
              })()}
            </g>
          </svg>

          {/* nodes */}
          <div className="absolute inset-0"
            style={{ transform: `translate(${viewport.x}px,${viewport.y}px) scale(${viewport.k})`,
                     transformOrigin: '0 0', pointerEvents: 'none' }}>
            {nodes.map(n => {
              const spec = specOf(n)
              if (!spec) return null
              const acc = accentOf(spec)
              const sel = selected === n.id
              const st = states[n.id]
              const ports = portsOf(n)
              const title = n.kind === 'agent'
                ? (agents.find(a => a.value === n.data.agent)?.label || n.data.agent || 'agent')
                : (n.data.label || spec.label)
              return (
                <div key={n.id} ref={el => { measure(n.id, el) }}
                  className={`absolute rounded-xl border shadow-xl bg-surface-2 transition-shadow ${
                    st?.status === 'running' ? 'border-sky-400/70 shadow-[0_0_24px_rgb(56_189_248/0.25)]'
                    : st?.status === 'error' ? 'border-red-400/60'
                    : st?.status === 'done' ? 'border-emerald-400/40'
                    : sel ? 'border-white/30' : acc.border}`}
                  style={{ left: n.x, top: n.y, width: NODE_W, pointerEvents: 'auto' }}
                  onPointerDown={e => { e.stopPropagation(); setSelected(n.id) }}>

                  {/* header = drag handle */}
                  <div className="flex items-center gap-1.5 px-2.5 h-[42px] rounded-t-xl cursor-grab active:cursor-grabbing select-none border-b border-white/[0.06]"
                    onPointerDown={e => startDrag(e, n)}>
                    <span className={`text-sm ${acc.text}`}>{spec.icon}</span>
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-300 truncate">
                      {title}
                    </span>
                    {n.kind === 'agent' && (
                      <span className="text-[8px] px-1 py-0.5 rounded bg-white/[0.06] text-gray-600 border border-white/[0.06]">
                        agent
                      </span>
                    )}
                    {st?.status === 'running' && <span className="text-[9px] text-sky-300 animate-pulse">running</span>}
                    {st?.status === 'done' && <span className="text-[9px] text-emerald-400/80">{st.ms}ms</span>}
                    {st?.status === 'error' && <span className="text-[9px] text-red-400">failed</span>}
                    {st?.status === 'parked' && <span className="text-[9px] text-amber-300">parked</span>}
                    <span className="ml-auto" />
                    <button onPointerDown={e => e.stopPropagation()} onClick={() => removeNode(n.id)}
                      className="text-gray-600 hover:text-red-400 transition text-xs px-1">✕</button>
                  </div>

                  {/* fields */}
                  <div className="p-2.5 space-y-1.5" onPointerDown={e => e.stopPropagation()}>
                    {(spec.fields || []).map(f => field(n, f))}
                    {!spec.fields?.length && (
                      <div className="text-[10px] text-gray-600 leading-snug">{spec.summary}</div>
                    )}
                    {st?.text && (
                      <div className="text-[10px] text-gray-500 leading-snug line-clamp-3 border-t border-white/[0.06] pt-1.5"
                        title={st.text}>
                        {st.text}
                      </div>
                    )}
                  </div>

                  {/* output ports — one row each, named, with the dot on the edge */}
                  {!!ports.length && (
                    <div className="border-t border-white/[0.06] py-0.5">
                      {ports.map(p => {
                        const wired = edges.some(e => e.from === n.id && e.port === p)
                        return (
                          <div key={p} className="relative flex items-center justify-end gap-1.5 pr-3"
                            style={{ height: PORT_ROW }}>
                            <span className={`text-[9px] uppercase tracking-wider ${
                              wired ? 'text-gray-400' : 'text-gray-700'}`}>{p}</span>
                            <div
                              className={`absolute -right-[7px] top-1/2 -translate-y-1/2 w-[13px] h-[13px] rounded-full border-2 border-surface-1 cursor-crosshair hover:scale-125 transition-transform`}
                              style={{ background: wireOf(spec, p), opacity: wired ? 1 : 0.55,
                                       boxShadow: wired ? `0 0 8px ${wireOf(spec, p)}` : 'none' }}
                              onPointerDown={e => startConnect(e, n, p)}
                              title={`drag from "${p}" to the node that runs next`} />
                          </div>
                        )
                      })}
                    </div>
                  )}

                  {/* input port */}
                  {spec.inputs !== 0 && (
                    <div className="absolute -left-[7px] w-[13px] h-[13px] rounded-full border-2 border-surface-1 bg-white/25"
                      style={{ top: HEADER_H / 2 - 6.5 }} title="input" />
                  )}
                </div>
              )
            })}
          </div>

          {/* zoom */}
          <div className="absolute bottom-3 right-3 flex flex-col gap-1 z-10">
            {[['+', 1.2], ['−', 0.84]].map(([label, f]: any) => (
              <button key={label} onClick={() => setViewport(v => {
                const r = canvasRef.current?.getBoundingClientRect()
                const mx = (r?.width || 0) / 2, my = (r?.height || 0) / 2
                const k = Math.min(1.8, Math.max(0.3, v.k * f))
                return { x: mx - ((mx - v.x) / v.k) * k, y: my - ((my - v.y) / v.k) * k, k }
              })}
                className="w-7 h-7 rounded-md bg-surface-2 border border-white/10 text-gray-400 hover:text-gray-200 hover:border-white/20 transition text-sm flex items-center justify-center">
                {label}
              </button>
            ))}
            <button onClick={() => setViewport({ x: 40, y: 40, k: 0.9 })}
              className="w-7 h-7 rounded-md bg-surface-2 border border-white/10 text-gray-400 hover:text-gray-200 transition text-sm flex items-center justify-center">⌂</button>
          </div>

          <div className="absolute bottom-3 left-3 text-[10px] text-gray-700 select-none pointer-events-none space-y-0.5">
            <div>every node is a whole <span className="text-emerald-500/80">agent</span> — build one in AGENTS, wire it here</div>
            <div>drag a port ● to the node that runs next · click a wire to disconnect · scroll to zoom</div>
          </div>
          <div className="absolute top-3 right-3 text-[10px] text-gray-700 font-mono select-none pointer-events-none">
            {Math.round(viewport.k * 100)}%
          </div>
        </div>

        {/* ── run bar ── */}
        {showRun && (
          <div className="shrink-0 border-t border-white/[0.06] bg-surface-1">
            <div className="px-3 py-2 flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-wider text-gray-600 shrink-0">input</span>
              <input value={query} onChange={e => setQuery(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !running) run() }}
                placeholder="what goes into the Input node…"
                className="flex-1 min-w-0 bg-white/[0.05] border border-white/[0.09] rounded-md px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-violet-400/40 transition" />
              <button onClick={run} disabled={runDisabled}
                title={valid && !valid.ok ? valid.errors[0] : 'Run this flow'}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition disabled:opacity-40 ${
                  running ? 'border border-red-500/30 bg-red-500/10 text-red-300'
                          : 'bg-violet-600/90 hover:bg-violet-500 text-white'}`}>
                {running ? 'stop' : '▶ run'}
              </button>
            </div>
            {(result || log.length > 0) && (
              <div className="px-3 pb-2 grid grid-cols-2 gap-3 max-h-44">
                <div className="min-w-0 overflow-y-auto rounded-md border border-white/[0.06] bg-white/[0.02] p-2">
                  <div className="text-[9px] uppercase tracking-wider text-gray-600 mb-1">answer</div>
                  {result?.error ? (
                    <div className="text-[11px] text-red-300 whitespace-pre-wrap">{result.error}</div>
                  ) : (
                    <>
                      {(result?.outputs || []).map((o: any, i: number) => (
                        <div key={i} className="mb-2">
                          <div className="text-[9px] text-violet-300/80">{o.label}{o.agent ? ` · ${o.agent}` : ''}</div>
                          <div className="text-[11px] text-gray-300 whitespace-pre-wrap">{o.text}</div>
                        </div>
                      ))}
                      {(result?.parked || []).map((p: any, i: number) => (
                        <div key={`p${i}`} className="mb-2 rounded border border-amber-400/25 bg-amber-400/[0.05] p-1.5">
                          <div className="text-[9px] text-amber-300">✋ waiting on you{p.note ? ` — ${p.note}` : ''}</div>
                          <div className="text-[11px] text-gray-300 whitespace-pre-wrap line-clamp-6">{p.text}</div>
                        </div>
                      ))}
                      {result && !result.outputs?.length && !result.parked?.length && (
                        <div className="text-[11px] text-gray-600">nothing reached an output</div>
                      )}
                    </>
                  )}
                </div>
                <div className="min-w-0 overflow-y-auto rounded-md border border-white/[0.06] bg-white/[0.02] p-2 font-mono">
                  <div className="text-[9px] uppercase tracking-wider text-gray-600 mb-1 font-sans">
                    trail{result?.nodes_run ? ` · ${result.nodes_run} nodes · ${result.ms}ms` : ''}
                  </div>
                  {log.map((l, i) => <div key={i} className="text-[10px] text-gray-500 whitespace-pre">{l}</div>)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
