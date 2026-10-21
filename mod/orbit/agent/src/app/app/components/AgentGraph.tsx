'use client'

// AgentGraph — every agent, drawn as the DAG it runs.
//
// One agent = one graph: the request comes in on the left, the answer leaves
// on the right, and everything the run passes through sits in between. An
// agent nobody has wired is the trivial graph (request ▸ agent ▸ answer), and
// even then the agent node is not a black box — its card is the agent's
// anatomy, the four things a run of it is made of:
//
//   prompt  — its system prompt (goal), or the node's own instruction
//   memory  — the memory module it recalls from and writes to
//   model   — the intelligence each step is sampled from
//   tools   — what the model can call between steps
//
// While a run streams, the card lights the stage it is in: the prompt as it
// is assembled, the model while it thinks, the tool it just called. Wires
// carry a pulse every time a message crosses them.
//
// The graph itself is the server's (GET/PUT/DELETE /agents/{name}/graph,
// stored as an ordinary graph under `agent-<name>`, vocabulary from
// GET /graph/kinds). Wire a reviewer behind an agent, a gate before its
// answer, a loop round it — and that whole graph is what the agent means.
// A wire that closes a cycle gets a Loop node dropped onto it, because the
// server refuses a cycle that nothing counts.

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { API_URL } from '../config'

// ── the protocol, as the server describes it ──
type Field = { key: string; type: string; label: string; hint?: string; options?: string[];
               default?: any; required?: boolean; min?: number; max?: number }
type KindSpec = { label: string; icon: string; accent: string; inputs: number | string;
                  out: string[] | null; fan: string; summary: string; doc?: string; fields?: Field[] }
type Protocol = { kinds: Record<string, KindSpec>; ops: Record<string, { label: string; arg: string }>;
                  fields: { key: string; label: string }[] }

type GNode = { id: string; kind: string; x: number; y: number; data: Record<string, any> }
type GEdge = { id: string; from: string; to: string; port: string }
type Graph = { id?: string; name?: string; agent?: string; nodes: GNode[]; edges: GEdge[];
               default?: boolean; can_edit?: boolean; cid?: string;
               valid?: { ok: boolean; errors: string[]; warnings: string[] } }
type AgentCfg = { name: string; description?: string; goal?: string | null; icon?: string;
                  tools?: string[] | null; model?: string | null; memory?: string | null;
                  harness?: string | null; loop?: Record<string, string> | null;
                  builtin?: boolean; owner?: string | null; owner_source?: string }
type Stage = 'prompt' | 'memory' | 'model' | 'tools'
type Live = { status: 'running' | 'done' | 'error' | 'parked'; stage?: Stage; tool?: string;
              steps?: number; tools?: number; ms?: number; out?: string }
type View = { x: number; y: number; k: number }

const W = 256          // node width
const HEAD = 48        // header height — the input port and the main out port sit on its midline
const ROW = 24         // one anatomy / port row
const BODY_PAD = 10
const ACCENT: Record<string, string> = { emerald: '--a-400', sky: '--i-400', amber: '--w-400', violet: '--v-400' }
const PORT_TONE: Record<string, string> = { pass: '--a-400', fail: '--d-400', err: '--d-400',
                                            done: '--v-400', else: '--g-500' }
const STAGES: { key: Stage; label: string }[] = [
  { key: 'prompt', label: 'prompt' }, { key: 'memory', label: 'memory' },
  { key: 'model', label: 'model' }, { key: 'tools', label: 'tools' },
]

let seq = 0
const nid = (p = 'n') => `${p}${Date.now().toString(36)}${(seq++).toString(36)}`
const tone = (v: string, a = 1) => `rgb(var(${v}) / ${a})`
const firstLine = (s?: string | null, n = 60) => {
  const l = (s || '').split('\n').map(x => x.trim()).find(Boolean) || ''
  return l.length > n ? l.slice(0, n - 1) + '…' : l
}

// ── geometry: one function both the cards and the wires read ──
const outPorts = (n: GNode, spec?: KindSpec): string[] => {
  if (n.kind === 'router') {
    const r = ((n.data?.routes || []) as any[]).map(r => String(r?.port || r?.name || '').trim()).filter(Boolean)
    return [...r, 'else']
  }
  return spec?.out || []
}
// the forward port rides the header midline so a chain reads as a straight line
const mainPort = (ports: string[]) => (ports[0] === 'out' || ports[0] === 'pass') ? ports[0] : null
const bodyRows = (n: GNode) => n.kind === 'agent' ? 4 : n.kind === 'judge' ? 2 : 1
const geom = (n: GNode, spec?: KindSpec) => {
  const ports = outPorts(n, spec)
  const main = mainPort(ports)
  const side = main ? ports.slice(1) : ports
  const body = bodyRows(n) * ROW + BODY_PAD * 2
  const h = HEAD + body + (side.length ? side.length * ROW + 8 : 0)
  const at: Record<string, { x: number; y: number }> = {}
  if (main) at[main] = { x: n.x + W, y: n.y + HEAD / 2 }
  side.forEach((p, i) => { at[p] = { x: n.x + W, y: n.y + HEAD + body + 4 + i * ROW + ROW / 2 } })
  return { h, ports, main, side, body, at, inAt: { x: n.x, y: n.y + HEAD / 2 } }
}

const wirePath = (a: { x: number; y: number }, b: { x: number; y: number }, below: number) => {
  if (b.x > a.x + 30) {
    const dx = Math.max(50, (b.x - a.x) * 0.5)
    return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`
  }
  // a wire going back — round underneath both cards instead of through them
  const y = below + 46
  return `M ${a.x} ${a.y} C ${a.x + 90} ${a.y}, ${a.x + 90} ${y}, ${a.x} ${y} L ${b.x} ${y} ` +
         `C ${b.x - 90} ${y}, ${b.x - 90} ${b.y}, ${b.x} ${b.y}`
}

// ── layout: layered left → right, longest path from the inputs ──
function tidy(nodes: GNode[], edges: GEdge[], kinds: Record<string, KindSpec>): GNode[] {
  if (!nodes.length) return nodes
  const out: Record<string, GEdge[]> = {}
  edges.forEach(e => { (out[e.from] ||= []).push(e) })
  // back edges = the ones a DFS meets on its own stack; layering ignores them
  const back = new Set<string>(), state: Record<string, number> = {}
  const roots = nodes.filter(n => n.kind === 'input' || !edges.some(e => e.to === n.id))
  const dfs = (id: string) => {
    state[id] = 1
    for (const e of out[id] || []) {
      if (state[e.to] === 1) back.add(e.id)
      else if (!state[e.to]) dfs(e.to)
    }
    state[id] = 2
  }
  roots.forEach(r => !state[r.id] && dfs(r.id))
  nodes.forEach(n => !state[n.id] && dfs(n.id))
  const fwd = edges.filter(e => !back.has(e.id))
  const layer: Record<string, number> = {}
  nodes.forEach(n => { layer[n.id] = 0 })
  for (let i = 0; i < nodes.length; i++) {
    let moved = false
    for (const e of fwd) if (layer[e.to] < layer[e.from] + 1) { layer[e.to] = layer[e.from] + 1; moved = true }
    if (!moved) break
  }
  // outputs sit in the last column, whatever fed them
  const last = Math.max(...Object.values(layer))
  nodes.forEach(n => { if (n.kind === 'output') layer[n.id] = Math.max(layer[n.id], last) })
  const cols: GNode[][] = []
  nodes.forEach(n => { (cols[layer[n.id]] ||= []).push(n) })
  const pos: Record<string, { x: number; y: number }> = {}
  const GAP_X = 110, GAP_Y = 44
  const heights = cols.map(c => (c || []).reduce((s, n) => s + geom(n, kinds[n.kind]).h + GAP_Y, -GAP_Y))
  const SPINE = 80 + Math.max(...heights, 0) / 2
  // the main line: what the inputs reach through forward ports alone
  const spine = new Set<string>(roots.map(r => r.id))
  for (let grew = true; grew;) {
    grew = false
    for (const e of fwd) if (spine.has(e.from) && !spine.has(e.to) && (e.port === 'out' || e.port === 'pass')) { spine.add(e.to); grew = true }
  }
  cols.forEach((col, li) => {
    if (!col) return
    // order a column by where its parents sit, so wires cross as little as they can
    col.sort((a, b) => {
      const avg = (n: GNode) => {
        const ps = fwd.filter(e => e.to === n.id).map(e => pos[e.from]?.y ?? 0)
        return ps.length ? ps.reduce((s, v) => s + v, 0) / ps.length : 0
      }
      return avg(a) - avg(b)
    })
    // one-card columns share a spine, so the main chain is a straight line;
    // a column of several is centred on that spine
    // a card only a side port reaches (fail, err, done, else…) drops below the
    // spine, so the forward wire never seems to run into it
    const offSpine = col.length === 1 && !spine.has(col[0].id)
    let y = col.length === 1 ? SPINE - HEAD / 2 + (offSpine ? 150 : 0) : SPINE - heights[li] / 2
    col.forEach(n => {
      pos[n.id] = { x: 80 + li * (W + GAP_X), y }
      y += geom(n, kinds[n.kind]).h + GAP_Y
    })
  })
  return nodes.map(n => ({ ...n, ...pos[n.id] }))
}

// does `to` already reach `from`? then from→to closes a cycle
const reaches = (edges: GEdge[], start: string, goal: string) => {
  const seen = new Set<string>(), stack = [start]
  while (stack.length) {
    const id = stack.pop()!
    if (id === goal) return true
    if (seen.has(id)) continue
    seen.add(id)
    edges.forEach(e => e.from === id && stack.push(e.to))
  }
  return false
}
const cyclePath = (edges: GEdge[], start: string, goal: string): string[] => {
  const prev: Record<string, string> = {}, q = [start], seen = new Set([start])
  while (q.length) {
    const id = q.shift()!
    if (id === goal) break
    for (const e of edges) if (e.from === id && !seen.has(e.to)) { seen.add(e.to); prev[e.to] = id; q.push(e.to) }
  }
  const path = [goal]
  while (path[0] !== start && prev[path[0]]) path.unshift(prev[path[0]])
  return path
}

// ── the component ──
type Props = {
  agent: string
  token?: string | null
  isHost?: boolean
  address?: string | null
  onSignIn?: () => void
  /** the canvas changed which agent it shows (picker, or a dive into a node) */
  onPickAgent?: (name: string) => void
  /** select the agent in the console and go talk to it */
  onChat?: (name: string) => void
}

export default function AgentGraph({ agent, token, isHost, address, onSignIn, onPickAgent, onChat }: Props) {
  const [proto, setProto] = useState<Protocol | null>(null)
  const [agents, setAgents] = useState<Record<string, AgentCfg>>({})
  const [order, setOrder] = useState<string[]>([])
  const [graph, setGraph] = useState<Graph | null>(null)
  const [nodes, setNodes] = useState<GNode[]>([])
  const [edges, setEdges] = useState<GEdge[]>([])
  const [dirty, setDirty] = useState(false)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [sel, setSel] = useState<{ node?: string; edge?: string } | null>(null)
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 })
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [trail, setTrail] = useState<string[]>([agent])
  const [picker, setPicker] = useState(false)
  const [palette, setPalette] = useState(false)
  // run
  const [query, setQuery] = useState('')
  const [running, setRunning] = useState(false)
  const [live, setLive] = useState<Record<string, Live>>({})
  const [flows, setFlows] = useState<{ id: number; edge: string }[]>([])
  const [result, setResult] = useState<any>(null)
  const [showResult, setShowResult] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef(view); viewRef.current = view
  const nodesRef = useRef(nodes); nodesRef.current = nodes
  const edgesRef = useRef(edges); edgesRef.current = edges
  const flowSeq = useRef(0)

  const kinds = proto?.kinds || {}
  const flash = useCallback((ok: boolean, text: string) => {
    setToast({ ok, text }); setTimeout(() => setToast(t => (t?.text === text ? null : t)), 3200)
  }, [])

  // ── load ──
  useEffect(() => {
    fetch(`${API_URL}/graph/kinds`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(setProto).catch(() => {})
    fetch(`${API_URL}/agents`, { signal: AbortSignal.timeout(10000) })
      .then(r => r.json())
      .then(d => { setAgents(d.schemas || {}); setOrder(d.agents || Object.keys(d.schemas || {})) })
      .catch(() => {})
  }, [])

  const fit = useCallback((ns: GNode[] = nodesRef.current) => {
    const box = boxRef.current
    if (!box || !ns.length) return
    const xs = ns.map(n => n.x), ys = ns.map(n => n.y)
    const x2 = Math.max(...ns.map(n => n.x + W)), y2 = Math.max(...ns.map(n => n.y + geom(n, kinds[n.kind]).h))
    const bw = x2 - Math.min(...xs) + 160, bh = y2 - Math.min(...ys) + 200
    const k = Math.max(0.35, Math.min(1.15, Math.min(box.clientWidth / bw, box.clientHeight / bh)))
    setView({ k, x: (box.clientWidth - (x2 + Math.min(...xs)) * k) / 2,
              y: (box.clientHeight - 40 - (y2 + Math.min(...ys)) * k) / 2 })
  }, [kinds])

  const framed = useRef<string | null>(null)
  const load = useCallback(async (name: string) => {
    setLoadErr(null); setSel(null); setLive({}); setResult(null); setShowResult(false)
    try {
      const q = token ? `?key=${encodeURIComponent(token)}` : ''
      const r = await fetch(`${API_URL}/agents/${encodeURIComponent(name)}/graph${q}`,
                            { signal: AbortSignal.timeout(10000) })
      const g: Graph = await r.json()
      if ((g as any).error) throw new Error((g as any).error)
      setGraph(g)
      const ns = (g.nodes || []).map(n => ({ ...n, data: n.data || {} }))
      setNodes(ns)
      setEdges((g.edges || []).map(e => ({ ...e, id: e.id || nid('e'), port: e.port || 'out' })))
      setDirty(false)
    } catch (e: any) {
      setLoadErr(e?.message || 'could not load the graph')
      setGraph(null); setNodes([]); setEdges([])
    }
  }, [token])

  // the agent the canvas last picked itself — the parent echoes it back as a
  // prop, and that echo must not wipe the breadcrumb a dive just built
  const pickedRef = useRef<string | null>(null)
  useEffect(() => {
    if (pickedRef.current === agent) { pickedRef.current = null; return }
    setTrail([agent]); framed.current = null; void load(agent)
  }, [agent, load])

  // a fresh graph is laid out and framed once its kinds are known
  useEffect(() => {
    if (!proto || !graph || framed.current === `${graph.id}·${graph.default}`) return
    framed.current = `${graph.id}·${graph.default}`
    const ns = graph.default ? tidy(nodes, edges, proto.kinds) : nodes
    if (graph.default) setNodes(ns)
    requestAnimationFrame(() => fit(ns))
  }, [proto, graph, nodes, edges, fit])

  // leaving with unsaved wiring takes a second press — the first one says so
  const [leaveArm, setLeaveArm] = useState<string | null>(null)
  const goAgent = (name: string, dive = false, crumbs?: string[]) => {
    setPicker(false)
    if (dirty && leaveArm !== name) {
      setLeaveArm(name)
      flash(false, `unsaved changes on ${graph?.agent || agent} — press again to leave them`)
      return
    }
    setLeaveArm(null)
    setTrail(t => crumbs || (dive ? [...t, name] : [name]))
    framed.current = null
    void load(name)
    pickedRef.current = name
    onPickAgent?.(name)
  }

  const cfgOf = (name?: string) => (name ? agents[name] : undefined)
  const canEditAgent = (name?: string) => {
    const c = cfgOf(name)
    if (!c || !token) return false
    if (isHost) return true
    if (!address || c.builtin || c.owner_source === 'host') return false
    return (c.owner || '').toLowerCase() === address.toLowerCase()
  }
  const canSave = !!graph?.can_edit && !!token

  // ── edits ──
  const touch = () => setDirty(true)
  const patchNode = (id: string, data: Record<string, any>) => {
    setNodes(ns => ns.map(n => n.id === id ? { ...n, data: { ...n.data, ...data } } : n)); touch()
  }
  const removeSel = useCallback(() => {
    if (!sel) return
    if (sel.edge) setEdges(es => es.filter(e => e.id !== sel.edge))
    if (sel.node) {
      setNodes(ns => ns.filter(n => n.id !== sel.node))
      setEdges(es => es.filter(e => e.from !== sel.node && e.to !== sel.node))
    }
    setSel(null); touch()
  }, [sel])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if ((e.key === 'Delete' || e.key === 'Backspace') && sel) { e.preventDefault(); removeSel() }
      if (e.key === 'Escape') { setSel(null); setPalette(false); setPicker(false); setQuick(null) }
      if (e.key === 'f') fit()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sel, removeSel, fit])

  const freshNode = (kind: string, x: number, y: number): GNode => {
    const data: Record<string, any> = {}
    kinds[kind]?.fields?.forEach(f => { if (f.default !== undefined) data[f.key] = f.default })
    if (kind === 'agent' || kind === 'judge') data.agent = kind === 'judge' ? 'reviewer' : agent
    return { id: nid(kind === 'join' ? 'join' : 'n'), kind, x: Math.round(x), y: Math.round(y), data }
  }
  const addNode = (kind: string) => {
    setPalette(false)
    const box = boxRef.current, v = viewRef.current
    const cx = box ? (box.clientWidth / 2 - v.x) / v.k - W / 2 : 200
    const cy = box ? (box.clientHeight / 2 - v.y) / v.k - 60 : 200
    const n = freshNode(kind, cx + (seq % 5) * 18, cy + (seq % 5) * 18)
    setNodes(ns => [...ns, n]); setSel({ node: n.id }); touch()
  }

  // a wire that FEEDS a node (as opposed to an alternative path a gate, router
  // or judge picks between, or a loop coming round for another pass)
  const feeds = (es: GEdge[], to: string) => es.filter(e => {
    const src = nodesRef.current.find(n => n.id === e.from)
    return e.to === to && !!src && !['loop', 'gate', 'router', 'judge'].includes(src.kind) && !reaches(es, to, e.from)
  })
  // would from→to land a second input on a node that runs once per message?
  const wouldAggregate = (from: string, to: string, es: GEdge[] = edgesRef.current) => {
    const a = nodesRef.current.find(n => n.id === from), b = nodesRef.current.find(n => n.id === to)
    if (!a || !b || kinds[b.kind]?.fan === 'gather' || ['loop', 'gate', 'router', 'judge'].includes(a.kind)) return false
    if (reaches(es, to, from)) return false
    return feeds(es, to).some(e => e.from !== from)
  }

  // connect from→to on a port, on top of `base` (the wiring minus whatever
  // wire is being dragged). Two rules keep what's drawn runnable:
  //  · a wire that closes a cycle with nothing to count its passes gets a Loop
  //    node dropped onto it — the server refuses it otherwise;
  //  · a second input into a node that runs once per message gets a Join in
  //    front of it, so the branches (predictions, drafts) are aggregated into
  //    ONE message instead of running the node once per branch.
  const connect = (from: string, port: string, to: string, base: GEdge[] = edgesRef.current) => {
    if (from === to) return false
    const target = nodesRef.current.find(n => n.id === to)
    const a = nodesRef.current.find(n => n.id === from)
    if (!a || !target || kinds[target.kind]?.inputs === 0) return false
    if (base.some(e => e.from === from && e.to === to && e.port === port)) { setEdges(base); touch(); return true }
    if (reaches(base, to, from)) {
      const path = cyclePath(base, to, from)
      const counted = path.some(id => nodesRef.current.find(n => n.id === id)?.kind === 'loop') || a.kind === 'loop'
      if (!counted) {
        const loop: GNode = { id: nid('loop'), kind: 'loop', x: (a.x + target.x) / 2, y: Math.max(a.y, target.y) + 230,
                              data: { max: 3 } }
        const outNode = nodesRef.current.find(n => n.kind === 'output')
        setNodes(ns => [...ns, loop])
        setEdges([...base,
          { id: nid('e'), from, to: loop.id, port },
          { id: nid('e'), from: loop.id, to, port: 'out' },
          ...(outNode ? [{ id: nid('e'), from: loop.id, to: outNode.id, port: 'done' }] : [])])
        touch(); setSel({ node: loop.id })
        flash(true, 'that wire closes a cycle — a Loop now counts its passes (max 3)')
        return true
      }
    }
    if (wouldAggregate(from, to, base)) {
      const fed = feeds(base, to)
      // already behind an aggregator: the new branch joins that one
      const agg = fed.map(e => nodesRef.current.find(n => n.id === e.from)).find(n => n && kinds[n.kind]?.fan === 'gather')
      if (agg) return connect(from, port, agg.id, base)
      const srcs = [...fed.map(e => nodesRef.current.find(n => n.id === e.from)!), a]
      const join = freshNode('join', Math.max(...srcs.map(n => n.x)) + W + 50,
                             srcs.reduce((s, n) => s + n.y, 0) / srcs.length)
      join.data = { ...join.data, mode: 'auto', wait: 'all' }
      // the node it feeds moves right to make room, if it has to
      const room = join.x + W + 50 - target.x
      setNodes(ns => [...ns.map(n => room > 0 && n.id === to ? { ...n, x: n.x + room } : n), join])
      const moved = new Set(fed.map(e => e.id))
      setEdges([...base.map(e => moved.has(e.id) ? { ...e, to: join.id } : e),
        { id: nid('e'), from, to: join.id, port },
        { id: nid('e'), from: join.id, to, port: 'out' }])
      touch(); setSel({ node: join.id })
      const name = target.kind === 'agent' ? (target.data.agent || 'agent') : (kinds[target.kind]?.label || target.kind).toLowerCase()
      flash(true, `${fed.length + 1} inputs into ${name} — a Join now aggregates them (numbers → mean; change it in the inspector)`)
      return true
    }
    setEdges([...base, { id: nid('e'), from, to, port }]); touch()
    return true
  }

  // ── save / reset ──
  const save = async () => {
    if (!token) { onSignIn?.(); return }
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/agents/${encodeURIComponent(graph?.agent || trail[trail.length - 1])}/graph`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nodes, edges, name: graph?.name, viewport: view, key: token }),
      })
      const d = await r.json()
      if (d?.error) { flash(false, d.error); return }
      setGraph(g => ({ ...(g || {}), ...d, nodes, edges }))
      setDirty(false)
      flash(!!d.valid?.ok, d.valid?.ok ? 'saved — this graph is what the agent runs as'
                                       : `saved, but: ${d.valid?.errors?.[0] || 'it does not validate'}`)
    } catch (e: any) { flash(false, e?.message || 'save failed') } finally { setBusy(false) }
  }
  const reset = async () => {
    if (graph?.default && !dirty) return
    if (graph?.default || !canSave) { framed.current = null; void load(graph?.agent || agent); return }
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/agents/${encodeURIComponent(graph?.agent || agent)}/graph?key=${encodeURIComponent(token || '')}`,
                            { method: 'DELETE' })
      const d = await r.json()
      if (d?.error) { flash(false, d.error); return }
      framed.current = null
      await load(graph?.agent || agent)
      flash(true, 'back to request ▸ agent ▸ answer')
    } finally { setBusy(false) }
  }

  // ── run ──
  const stop = () => { abortRef.current?.abort(); abortRef.current = null; setRunning(false) }
  const runningAgents = useRef<Set<string>>(new Set())
  const stageTimers = useRef<Record<string, any>>({})
  const setStage = (id: string, stage: Stage, tool?: string) =>
    setLive(l => l[id]?.status === 'running' ? { ...l, [id]: { ...l[id], stage, tool } } : l)

  const onEvent = (ev: any) => {
    const kindOf = (id: string) => nodesRef.current.find(n => n.id === id)?.kind
    if (ev.type === 'node_start') {
      setLive(l => ({ ...l, [ev.node]: { status: 'running', stage: 'prompt', steps: 0, tools: 0 } }))
      if (kindOf(ev.node) === 'agent' || kindOf(ev.node) === 'judge') {
        runningAgents.current.add(ev.node)
        clearTimeout(stageTimers.current[ev.node])
        stageTimers.current[ev.node] = setTimeout(() => setStage(ev.node, 'memory'), 350)
        setTimeout(() => setStage(ev.node, 'model'), 800)
      }
    } else if (ev.type === 'node_done') {
      runningAgents.current.delete(ev.node)
      setLive(l => ({ ...l, [ev.node]: { ...(l[ev.node] || {}), status: ev.ok === false ? 'error' : 'done',
                                          stage: undefined, ms: ev.ms, out: ev.out } }))
    } else if (ev.type === 'parked') {
      setLive(l => ({ ...l, [ev.node]: { ...(l[ev.node] || {}), status: 'parked' } }))
    } else if (ev.type === 'message') {
      const e = edges.find(x => x.from === ev.from && x.to === ev.to && (!ev.port || x.port === ev.port))
        || edges.find(x => x.from === ev.from && x.to === ev.to)
      if (e) {
        const id = ++flowSeq.current
        setFlows(f => [...f.slice(-24), { id, edge: e.id }])
        setTimeout(() => setFlows(f => f.filter(x => x.id !== id)), 1300)
      }
    } else if (ev.type === 'step') {
      const st = ev.step || {}
      const tool = st.tool || st.name
      // a step doesn't say which node it belongs to; the agent nodes running now own it
      runningAgents.current.forEach(id => {
        setLive(l => ({ ...l, [id]: { ...(l[id] || { status: 'running' }), steps: (l[id]?.steps || 0) + 1,
          tools: (l[id]?.tools || 0) + (tool && tool !== 'finish' ? 1 : 0),
          stage: tool && tool !== 'finish' && tool !== 'think' ? 'tools' : 'model', tool } }))
        clearTimeout(stageTimers.current[id])
        stageTimers.current[id] = setTimeout(() => setStage(id, 'model'), 900)
      })
    } else if (ev.type === 'done') {
      setResult(ev.result); setShowResult(true)
      if (ev.result?.stopped) flash(false, ev.result.stopped)
    } else if (ev.type === 'error') {
      flash(false, ev.error); setResult({ error: ev.error }); setShowResult(true)
    }
  }

  const run = async () => {
    if (running) return stop()
    if (!query.trim()) { flash(false, 'say what the agent should do'); return }
    if (!token) { onSignIn?.(); return }
    setLive({}); setResult(null); setShowResult(false); setRunning(true)
    runningAgents.current = new Set()
    const ctrl = new AbortController(); abortRef.current = ctrl
    try {
      const res = await fetch(`${API_URL}/graphs/run/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: ctrl.signal,
        body: JSON.stringify({ graph: { name: graph?.name || `${agent} graph`, nodes, edges }, query, key: token }),
      })
      if (!res.ok || !res.body) throw new Error('the run could not be started')
      const reader = res.body.getReader(), dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const frames = buf.split('\n\n'); buf = frames.pop() || ''
        for (const f of frames) {
          const line = f.split('\n').find(l => l.startsWith('data: '))
          if (!line) continue
          try { onEvent(JSON.parse(line.slice(6))) } catch {}
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') flash(false, e?.message || String(e))
    } finally { setRunning(false); abortRef.current = null; runningAgents.current.clear() }
  }

  // ── canvas interaction ──
  // a wire in the hand. `from` set = its loose end is the TARGET (dragged off
  // an out port, or the arrow end of an existing wire); only `to` set = its
  // loose end is the SOURCE (dragged off an in port, or a wire's tail).
  // `orig` = the existing wire picked up — hidden while it's carried, gone if
  // it's dropped on empty canvas.
  type Carry = { from?: string; port?: string; to?: string; orig?: GEdge; x: number; y: number }
  const [wire, setWire] = useState<Carry | null>(null)
  // dropped a fresh wire on empty canvas → pick what to make there
  const [quick, setQuick] = useState<{ x: number; y: number; sx: number; sy: number; from: string; port: string } | null>(null)
  const [hoverTarget, setHoverTarget] = useState<string | null>(null)
  const toWorld = (cx: number, cy: number) => {
    const r = boxRef.current!.getBoundingClientRect(), v = viewRef.current
    return { x: (cx - r.left - v.x) / v.k, y: (cy - r.top - v.y) / v.k }
  }
  const onBgDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('[data-node],[data-ui]')) return
    setSel(null); setPalette(false); setPicker(false)
    const sx = e.clientX, sy = e.clientY, v0 = viewRef.current
    const move = (ev: PointerEvent) => setView(v => ({ ...v, x: v0.x + ev.clientX - sx, y: v0.y + ev.clientY - sy }))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const onWheel = (e: WheelEvent) => {
      if ((e.target as HTMLElement).closest('[data-ui]')) return
      e.preventDefault()
      const r = box.getBoundingClientRect(), v = viewRef.current
      const k = Math.max(0.3, Math.min(1.8, v.k * Math.exp(-e.deltaY * 0.0015)))
      const px = e.clientX - r.left, py = e.clientY - r.top
      setView({ k, x: px - (px - v.x) * (k / v.k), y: py - (py - v.y) * (k / v.k) })
    }
    box.addEventListener('wheel', onWheel, { passive: false })
    return () => box.removeEventListener('wheel', onWheel)
  }, [])

  const onNodeDown = (e: React.PointerEvent, n: GNode) => {
    if ((e.target as HTMLElement).closest('[data-port]')) return
    e.stopPropagation()
    setSel({ node: n.id }); setPalette(false); setPicker(false)
    const sx = e.clientX, sy = e.clientY, x0 = n.x, y0 = n.y
    let moved = false
    const move = (ev: PointerEvent) => {
      const k = viewRef.current.k
      const dx = (ev.clientX - sx) / k, dy = (ev.clientY - sy) / k
      if (!moved && Math.abs(dx) + Math.abs(dy) < 3) return
      moved = true
      setNodes(ns => ns.map(m => m.id === n.id ? { ...m, x: Math.round(x0 + dx), y: Math.round(y0 + dy) } : m))
    }
    const up = () => {
      if (moved) touch()
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  // the node under a world point — generous, so a drop near a card counts
  const nodeAt = (w: { x: number; y: number }, skip?: string) =>
    nodesRef.current.find(m => m.id !== skip && w.x >= m.x - 28 && w.x <= m.x + W + 12
      && w.y >= m.y - 12 && w.y <= m.y + geom(m, kinds[m.kind]).h + 12)
  const outPortOf = (m: GNode, prefer?: string) => {
    const ps = outPorts(m, kinds[m.kind])
    return prefer && ps.includes(prefer) ? prefer : ps[0]
  }

  // carry a wire until the pointer lets go: drop on a card = wire it there,
  // drop on nothing = a picked-up wire is removed, a fresh one offers to make
  // a node right there
  const carry = (ev0: { clientX: number; clientY: number }, c: Omit<Carry, 'x' | 'y'>) => {
    setQuick(null); setPalette(false); setPicker(false)
    setWire({ ...c, ...toWorld(ev0.clientX, ev0.clientY) })
    const fixed = c.from ?? c.to
    const move = (ev: PointerEvent) => {
      const w = toWorld(ev.clientX, ev.clientY)
      setWire(x => x ? { ...x, ...w } : x)
      const hit = nodeAt(w, fixed)
      setHoverTarget(hit && (c.from ? kinds[hit.kind]?.inputs !== 0 : outPorts(hit, kinds[hit.kind]).length) ? hit.id : null)
    }
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      setWire(null); setHoverTarget(null)
      const w = toWorld(ev.clientX, ev.clientY)
      const hit = nodeAt(w, fixed)
      const base = c.orig ? edgesRef.current.filter(e => e.id !== c.orig!.id) : edgesRef.current
      if (hit && c.from && c.port) {
        if (c.orig && hit.id === c.orig.to) return              // put back where it was
        if (!connect(c.from, c.port, hit.id, base) && c.orig) flash(false, 'that node takes no input')
        return
      }
      if (hit && c.to) {
        if (c.orig && hit.id === c.orig.from) return
        const port = outPortOf(hit, c.orig?.port)
        if (!port) { flash(false, 'that node has no output to wire from'); return }
        connect(hit.id, port, c.to, base)
        return
      }
      if (c.orig) { setEdges(base); touch(); flash(true, 'wire removed'); return }
      if (c.from && c.port) {
        const r = boxRef.current!.getBoundingClientRect()
        setQuick({ ...w, sx: ev.clientX - r.left, sy: ev.clientY - r.top, from: c.from, port: c.port })
      }
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  const onPortDown = (e: React.PointerEvent, n: GNode, port: string) => {
    e.stopPropagation(); e.preventDefault()
    carry(e, { from: n.id, port })
  }
  // an in port: grab the last wire into it (to move it elsewhere), or, with
  // none, draw one backwards to whatever should feed this node
  const onInDown = (e: React.PointerEvent, n: GNode) => {
    e.stopPropagation(); e.preventDefault()
    const last = [...edgesRef.current].reverse().find(x => x.to === n.id)
    if (last && !e.altKey) carry(e, { from: last.from, port: last.port, orig: last })
    else carry(e, { to: n.id })
  }
  // a wire's body: a click selects it, a drag picks up whichever end is nearer
  const onWireDown = (ev: React.PointerEvent, w: { e: GEdge; p1: { x: number; y: number }; p2: { x: number; y: number } }) => {
    ev.stopPropagation()
    setSel({ edge: w.e.id })
    const sx = ev.clientX, sy = ev.clientY
    const at = toWorld(sx, sy)
    const move = (pe: PointerEvent) => {
      if (Math.abs(pe.clientX - sx) + Math.abs(pe.clientY - sy) < 5) return
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      const nearTail = Math.hypot(at.x - w.p1.x, at.y - w.p1.y) < Math.hypot(at.x - w.p2.x, at.y - w.p2.y)
      setSel(null)
      carry(pe, nearTail ? { to: w.e.to, orig: w.e } : { from: w.e.from, port: w.e.port, orig: w.e })
    }
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  const quickAdd = (kind: string) => {
    if (!quick) return
    const n = freshNode(kind, quick.x + 8, quick.y - HEAD / 2)
    setNodes(ns => [...ns, n])
    setEdges(es => [...es, { id: nid('e'), from: quick.from, to: n.id, port: quick.port }])
    setSel({ node: n.id }); setQuick(null); touch()
  }

  // ── derived ──
  const byId = useMemo(() => Object.fromEntries(nodes.map(n => [n.id, n])), [nodes])
  const geoms = useMemo(() => Object.fromEntries(nodes.map(n => [n.id, geom(n, kinds[n.kind])])), [nodes, kinds])
  const flowing = useMemo(() => new Set(flows.map(f => f.edge)), [flows])
  const selNode = sel?.node ? byId[sel.node] : null
  const cur = graph?.agent || trail[trail.length - 1] || agent
  const curCfg = cfgOf(cur)
  const doneCount = Object.values(live).filter(l => l.status === 'done').length

  const wires = edges.map(e => {
    const a = byId[e.from], b = byId[e.to]
    if (!a || !b) return null
    const ga = geoms[a.id], gb = geoms[b.id]
    const p1 = ga.at[e.port] || { x: a.x + W, y: a.y + HEAD / 2 }
    const p2 = gb.inAt
    const below = Math.max(a.y + ga.h, b.y + gb.h)
    return { e, d: wirePath(p1, p2, below), p1, p2,
             color: PORT_TONE[e.port] || ACCENT[kinds[a.kind]?.accent] || '--a-400' }
  }).filter(Boolean) as { e: GEdge; d: string; p1: any; p2: any; color: string }[]

  // ── card bodies ──
  const anatomy = (n: GNode) => {
    const c = cfgOf(n.data.agent)
    const lv = live[n.id]
    const tools = (n.data.toolbox ? [`box: ${[].concat(n.data.toolbox).join(', ')}`] : null)
      || (c?.tools?.length ? c.tools : null)
    const vals: Record<Stage, { v: string; muted?: boolean }> = {
      prompt: n.data.prompt ? { v: firstLine(n.data.prompt, 46) }
            : c?.loop ? { v: `${c.loop.builder} ⇄ ${c.loop.reviewer}` }
            : { v: firstLine(c?.goal, 46) || 'no prompt', muted: !c?.goal },
      memory: { v: c?.harness ? 'the CLI\'s own' : (c?.memory || 'default'), muted: !c?.memory },
      model: c?.harness ? { v: `${c.harness} CLI` }
           : { v: n.data.model || c?.model || 'console model', muted: !(n.data.model || c?.model) },
      tools: c?.harness ? { v: 'its own', muted: true }
           : tools ? { v: `${tools.length > 1 || !String(tools[0]).startsWith('box') ? tools.length + ' · ' : ''}${tools.slice(0, 4).join(' · ')}` }
           : { v: 'every tool', muted: true },
    }
    return (
      <div className="dag-anatomy">
        {STAGES.map((s, i) => {
          const on = lv?.status === 'running' && lv.stage === s.key
          const val = s.key === 'tools' && on && lv?.tool ? { v: `▸ ${lv.tool}` } : vals[s.key]
          return (
            <div key={s.key} className={`dag-stage ${on ? 'dag-stage--on' : ''}`}>
              <span className="dag-stage-rail">
                <span className="dag-stage-dot" />
                {i < STAGES.length - 1 && <span className="dag-stage-line" />}
              </span>
              <span className="dag-stage-k">{s.label}</span>
              <span className={`dag-stage-v ${val.muted ? 'dag-muted' : ''}`} title={val.v}>{val.v}</span>
              {s.key === 'model' && !c?.harness && <span className="dag-stage-loop" title="the model and the tools take turns until it finishes">⟲</span>}
            </div>
          )
        })}
      </div>
    )
  }
  const summary = (n: GNode): string => {
    const d = n.data || {}
    switch (n.kind) {
      case 'input': return d.label || 'the request'
      case 'output': return d.label || 'the answer'
      case 'gate': return (d.rules || []).length ? `${(d.rules || []).length} rule${d.rules.length > 1 ? 's' : ''} · match ${d.mode || 'all'}` : 'open — no rules yet'
      case 'router': return `${(d.routes || []).length} route${(d.routes || []).length === 1 ? '' : 's'} + else`
      case 'join': return `${d.mode === 'auto' || !d.mode ? 'aggregate (auto)' : `aggregate: ${d.mode}`} · wait ${d.wait || 'all'}`
      case 'loop': return `up to ${d.max || 3} passes${(d.until || []).length ? ' · stops early' : ''}`
      case 'tool': return d.tool ? `call ${d.tool}` : 'pick a tool'
      case 'human': return firstLine(d.note, 40) || 'waits for a person'
      default: return kinds[n.kind]?.summary || ''
    }
  }

  // ── render ──
  const title = (n: GNode) => {
    if (n.kind === 'agent' || n.kind === 'judge') {
      const c = cfgOf(n.data.agent)
      return { icon: c?.icon || kinds[n.kind]?.icon, name: n.data.agent || 'pick an agent',
               sub: n.kind === 'judge' ? 'judge' : (c?.harness ? 'harness' : c?.loop ? 'feedback loop' : 'agent') }
    }
    return { icon: kinds[n.kind]?.icon, name: n.data.label || kinds[n.kind]?.label || n.kind, sub: n.kind }
  }

  return (
    <div className="dag-root h-full w-full flex min-h-0">
      <div ref={boxRef} className="dag-canvas relative flex-1 min-w-0 overflow-hidden select-none"
        onPointerDown={onBgDown}
        style={{ backgroundPosition: `${view.x}px ${view.y}px`, backgroundSize: `${22 * view.k}px ${22 * view.k}px` }}>

        {/* the world */}
        <div className="absolute left-0 top-0 origin-top-left" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
          <svg className="absolute overflow-visible pointer-events-none" style={{ left: 0, top: 0, width: 1, height: 1 }}>
            <defs>
              <filter id="dag-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
              </filter>
            </defs>
            {wires.map(w => {
              const on = sel?.edge === w.e.id
              const hot = flowing.has(w.e.id)
              const done = live[w.e.from]?.status === 'done' && !!live[w.e.to]
              return (
                <g key={w.e.id}>
                  <path d={w.d} fill="none" stroke="transparent" strokeWidth={14} className="pointer-events-auto cursor-pointer"
                    onPointerDown={ev => { ev.stopPropagation(); setSel({ edge: w.e.id }) }} />
                  <path d={w.d} fill="none" stroke={tone(w.color, on ? 0.95 : done ? 0.7 : 0.38)}
                    strokeWidth={on ? 2.4 : 1.6} strokeLinecap="round"
                    className={hot ? 'dag-wire--hot' : ''} style={hot ? { stroke: tone(w.color, 1) } : undefined} />
                  <circle cx={w.p2.x} cy={w.p2.y} r={3} fill={tone(w.color, on ? 1 : 0.55)} />
                  {w.e.port !== 'out' && (
                    <text x={w.p1.x + 10} y={w.p1.y - 6} className="dag-wire-label" fill={tone(w.color, 0.85)}>{w.e.port}</text>
                  )}
                </g>
              )
            })}
            {flows.map(f => {
              const w = wires.find(x => x.e.id === f.edge)
              if (!w) return null
              return (
                <circle key={f.id} r={4.5} fill={tone(w.color, 1)} filter="url(#dag-glow)">
                  <animateMotion dur="1.1s" path={w.d} fill="freeze" />
                </circle>
              )
            })}
            {wire && (() => {
              const a = byId[wire.from]
              const p1 = a ? geoms[a.id].at[wire.port] : null
              return p1 ? <path d={wirePath(p1, wire, Math.max(p1.y, wire.y))} fill="none"
                stroke={tone(PORT_TONE[wire.port] || '--a-400', 0.9)} strokeWidth={2} strokeDasharray="5 5" /> : null
            })()}
          </svg>

          {nodes.map(n => {
            const spec = kinds[n.kind]
            const g = geoms[n.id]
            const t = title(n)
            const lv = live[n.id]
            const acc = ACCENT[spec?.accent || 'emerald'] || '--a-400'
            const isSel = sel?.node === n.id
            const isSelf = (n.kind === 'agent') && n.data.agent === cur
            return (
              <div key={n.id} data-node className={`dag-node ${isSel ? 'dag-node--sel' : ''} ${lv ? `dag-node--${lv.status}` : ''} ${hoverTarget === n.id ? 'dag-node--target' : ''} ${isSelf ? 'dag-node--self' : ''}`}
                style={{ left: n.x, top: n.y, width: W, height: g.h, ['--acc' as any]: `var(${acc})` }}
                onPointerDown={e => onNodeDown(e, n)}
                onDoubleClick={() => { if ((n.kind === 'agent' || n.kind === 'judge') && n.data.agent && n.data.agent !== cur) goAgent(n.data.agent, true) }}>
                {/* in */}
                {spec?.inputs !== 0 && <span className="dag-port dag-port--in" style={{ top: HEAD / 2 }} />}
                <div className="dag-head" style={{ height: HEAD }}>
                  <span className="dag-icon">{t.icon}</span>
                  <span className="min-w-0 flex-1">
                    <span className="dag-name">{t.name}</span>
                    <span className="dag-sub">{t.sub}{isSelf ? ' · this agent' : ''}</span>
                  </span>
                  {lv?.status === 'running' && <span className="dag-spin" />}
                  {lv?.status === 'done' && <span className="dag-badge dag-badge--ok">✓ {lv.ms != null ? `${(lv.ms / 1000).toFixed(1)}s` : ''}</span>}
                  {lv?.status === 'error' && <span className="dag-badge dag-badge--err">✕</span>}
                  {lv?.status === 'parked' && <span className="dag-badge">⚑</span>}
                  {g.main && (
                    <span data-port className="dag-port dag-port--out" style={{ top: HEAD / 2 }}
                      title={`${g.main} — drag to wire`} onPointerDown={e => onPortDown(e, n, g.main!)} />
                  )}
                </div>
                <div className="dag-body" style={{ height: g.body }}>
                  {n.kind === 'agent' ? anatomy(n) : n.kind === 'judge' ? (
                    <div className="dag-anatomy">
                      <div className="dag-stage"><span className="dag-stage-k">asks</span><span className="dag-stage-v">{firstLine(n.data.question, 40) || 'does this pass?'}</span></div>
                      <div className="dag-stage"><span className="dag-stage-k">model</span><span className="dag-stage-v dag-muted">{n.data.model || 'free model'}</span></div>
                    </div>
                  ) : <div className="dag-summary">{summary(n)}</div>}
                </div>
                {g.side.length > 0 && (
                  <div className="dag-ports">
                    {g.side.map(p => (
                      <div key={p} className="dag-port-row" style={{ height: ROW }}>
                        <span style={{ color: tone(PORT_TONE[p] || acc, 0.9) }}>{p}</span>
                        <span data-port className="dag-port dag-port--out" style={{ top: ROW / 2, background: tone(PORT_TONE[p] || acc, 1) }}
                          title={`${p} — drag to wire`} onPointerDown={e => onPortDown(e, n, p)} />
                      </div>
                    ))}
                  </div>
                )}
                {lv?.status === 'running' && (lv.steps || 0) > 0 && (
                  <div className="dag-under">{lv.steps} step{lv.steps === 1 ? '' : 's'} · {lv.tools || 0} tool call{lv.tools === 1 ? '' : 's'}</div>
                )}
                {lv?.out && lv.status !== 'running' && <div className="dag-under dag-under--out" title={lv.out}>{firstLine(lv.out, 90)}</div>}
              </div>
            )
          })}
        </div>

        {/* top bar */}
        <div data-ui className="dag-bar absolute left-3 right-3 top-3 flex items-center gap-2 pointer-events-none">
          <div className="dag-glass pointer-events-auto flex items-center gap-1 pl-1 pr-2 py-1 min-w-0">
            {trail.length > 1 && trail.slice(0, -1).map((t, i) => (
              <button key={i} className="dag-crumb" onClick={() => goAgent(t, false, trail.slice(0, i + 1))}>
                {t} <span className="opacity-40">/</span>
              </button>
            ))}
            <div className="relative">
              <button className="dag-pick" onClick={() => setPicker(p => !p)} title="Every agent's graph — pick one">
                <span className="dag-pick-icon">{curCfg?.icon || '◆'}</span>
                <span className="truncate max-w-[180px]">{cur}</span>
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="6 9 12 15 18 9" /></svg>
              </button>
              {picker && <AgentPicker agents={agents} order={order} current={cur} onPick={n => goAgent(n)} />}
            </div>
            <span className={`dag-chip ${dirty ? 'dag-chip--warn' : graph?.default ? '' : 'dag-chip--on'}`}>
              {dirty ? 'unsaved' : graph?.default ? 'default' : 'wired'}
            </span>
            <span className="dag-meta hidden md:inline">{nodes.length} nodes · {edges.length} wires</span>
            {graph?.valid && !graph.valid.ok && !dirty && <span className="dag-chip dag-chip--err" title={graph.valid.errors.join('\n')}>{graph.valid.errors.length} issue{graph.valid.errors.length > 1 ? 's' : ''}</span>}
          </div>
          <div className="flex-1" />
          <div className="dag-glass pointer-events-auto flex items-center gap-0.5 p-1">
            <div className="relative">
              <button className="dag-btn dag-btn--accent" onClick={() => setPalette(p => !p)} title="Add a node">+ node</button>
              {palette && (
                <div className="dag-pop right-0 w-72">
                  {Object.entries(kinds).filter(([k]) => k !== 'input').map(([k, s]) => (
                    <button key={k} className="dag-pop-row" onClick={() => addNode(k)}>
                      <span className="dag-icon dag-icon--sm" style={{ ['--acc' as any]: `var(${ACCENT[s.accent] || '--a-400'})` }}>{s.icon}</span>
                      <span className="min-w-0"><span className="block text-[12px] text-gray-200">{s.label}</span>
                        <span className="block text-[10px] text-gray-500 truncate">{s.summary}</span></span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button className="dag-btn" onClick={() => { const ns = tidy(nodes, edges, kinds); setNodes(ns); touch(); requestAnimationFrame(() => fit(ns)) }} title="Lay it out left to right">tidy</button>
            <button className="dag-btn" onClick={() => fit()} title="Fit to screen (f)">fit</button>
            {(dirty || !graph?.default) && <button className="dag-btn" onClick={reset} disabled={busy}
              title={graph?.default || !canSave ? 'Throw away the unsaved changes' : 'Back to request ▸ agent ▸ answer'}>
              {graph?.default || !canSave ? 'revert' : 'reset'}</button>}
            <button className={`dag-btn ${dirty ? 'dag-btn--primary' : ''}`} onClick={save} disabled={busy || !dirty || (!!token && !canSave)}
              title={!token ? 'Sign in to save' : !canSave ? 'Only the agent\'s owner or the host can change its graph — you can still edit and run it here' : 'Save — the agent runs as this graph'}>
              {busy ? '…' : 'save'}</button>
          </div>
        </div>

        {loadErr && <div className="absolute inset-0 grid place-items-center text-[12px] text-red-300/80">{loadErr}</div>}
        {!graph && !loadErr && <div className="absolute inset-0 grid place-items-center text-[11px] text-gray-600">loading the graph…</div>}

        {/* run bar */}
        <div data-ui className="absolute left-1/2 -translate-x-1/2 bottom-4 w-[min(680px,calc(100%-24px))] pointer-events-auto">
          {showResult && result && (
            <div className="dag-glass dag-result mb-2">
              <div className="flex items-center gap-2 px-3 pt-2.5">
                <span className={`dag-chip ${result.error ? 'dag-chip--err' : result.ok === false ? 'dag-chip--warn' : 'dag-chip--on'}`}>
                  {result.error ? 'failed' : result.ok === false ? 'finished with errors' : 'answer'}</span>
                {result.usage?.cost != null && <span className="dag-meta">${Number(result.usage.cost).toFixed(4)}</span>}
                <span className="dag-meta">{doneCount} node{doneCount === 1 ? '' : 's'} ran</span>
                <span className="flex-1" />
                {onChat && <button className="dag-btn" onClick={() => onChat(cur)}>chat with {cur}</button>}
                <button className="dag-btn" onClick={() => setShowResult(false)}>✕</button>
              </div>
              <div className="px-3 pb-3 pt-1.5 text-[12px] leading-relaxed text-gray-200 whitespace-pre-wrap max-h-56 overflow-y-auto">
                {result.error || result.answer || (result.outputs && Object.values(result.outputs).join('\n\n')) || '(no answer)'}
              </div>
            </div>
          )}
          <form className="dag-glass dag-run flex items-center gap-2 p-1.5" onSubmit={e => { e.preventDefault(); void run() }}>
            <span className="dag-run-dot" data-on={running || undefined} />
            <input value={query} onChange={e => setQuery(e.target.value)} disabled={running}
              placeholder={`ask ${cur} — watch it go from request to answer`}
              className="flex-1 min-w-0 bg-transparent outline-none text-[13px] text-gray-100 placeholder:text-gray-600 px-1" />
            {!running && result && !showResult && <button type="button" className="dag-btn" onClick={() => setShowResult(true)}>last answer</button>}
            <button type="submit" className={`dag-btn ${running ? 'dag-btn--stop' : 'dag-btn--primary'}`}>
              {running ? 'stop' : token ? 'run ▸' : 'sign in to run'}</button>
          </form>
        </div>

        {toast && <div data-ui className={`dag-toast ${toast.ok ? '' : 'dag-toast--err'}`}>{toast.text}</div>}
      </div>

      {/* inspector */}
      {(selNode || sel?.edge) && (
        <aside data-ui className="dag-inspector w-[320px] shrink-0 overflow-y-auto">
          {sel?.edge && !selNode && (() => {
            const e = edges.find(x => x.id === sel.edge)
            if (!e) return null
            return (
              <div className="p-4 space-y-3">
                <div className="dag-ins-title">wire</div>
                <div className="text-[12px] text-gray-300">{title(byId[e.from]).name} <span className="text-gray-600">·{e.port}·▸</span> {title(byId[e.to]).name}</div>
                <button className="dag-btn dag-btn--danger" onClick={removeSel}>delete wire</button>
              </div>
            )
          })()}
          {selNode && (
            <NodeInspector key={selNode.id} node={selNode} spec={kinds[selNode.kind]} proto={proto}
              agents={agents} order={order} live={live[selNode.id]}
              canEditAgent={canEditAgent(selNode.data.agent)} token={token}
              onPatch={d => patchNode(selNode.id, d)}
              onDelete={removeSel}
              onDive={selNode.data.agent && selNode.data.agent !== cur ? () => goAgent(selNode.data.agent, true) : undefined}
              onAgentSaved={(name, cfg) => setAgents(a => ({ ...a, [name]: { ...a[name], ...cfg } }))}
              flash={flash} />
          )}
        </aside>
      )}
    </div>
  )
}

// ── agent picker: every agent, searchable ──
function AgentPicker({ agents, order, current, onPick }: {
  agents: Record<string, AgentCfg>; order: string[]; current: string; onPick: (n: string) => void
}) {
  const [q, setQ] = useState('')
  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    return order.filter(n => !s || n.includes(s) || (agents[n]?.description || '').toLowerCase().includes(s)).slice(0, 120)
  }, [q, order, agents])
  return (
    <div className="dag-pop left-0 w-80" data-ui>
      <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder={`search ${order.length} agents`}
        className="w-full bg-transparent border-b border-white/[0.07] px-3 py-2 text-[12px] text-gray-100 outline-none placeholder:text-gray-600" />
      <div className="max-h-80 overflow-y-auto py-1">
        {list.map(n => (
          <button key={n} className={`dag-pop-row ${n === current ? 'dag-pop-row--on' : ''}`} onClick={() => onPick(n)}>
            <span className="dag-icon dag-icon--sm">{agents[n]?.icon || '◆'}</span>
            <span className="min-w-0">
              <span className="block text-[12px] text-gray-200 truncate">{n}</span>
              <span className="block text-[10px] text-gray-500 truncate">{agents[n]?.description || ''}</span>
            </span>
          </button>
        ))}
        {!list.length && <div className="px-3 py-3 text-[11px] text-gray-600">no agent matches</div>}
      </div>
    </div>
  )
}

// ── inspector: the node's own fields (from the protocol) + the agent itself ──
function NodeInspector({ node, spec, proto, agents, order, live, canEditAgent, token,
  onPatch, onDelete, onDive, onAgentSaved, flash }: {
  node: GNode; spec?: KindSpec; proto: Protocol | null; agents: Record<string, AgentCfg>; order: string[]
  live?: Live; canEditAgent: boolean; token?: string | null
  onPatch: (d: Record<string, any>) => void; onDelete: () => void; onDive?: () => void
  onAgentSaved: (name: string, cfg: Partial<AgentCfg>) => void; flash: (ok: boolean, t: string) => void
}) {
  const d = node.data || {}
  const ops = proto?.ops || {}
  const fieldsOf = proto?.fields || []
  const isAgent = node.kind === 'agent' || node.kind === 'judge'
  const cfg = isAgent ? agents[d.agent] : undefined

  const input = 'w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[12px] text-gray-100 outline-none focus:border-[rgb(var(--a-400)/0.5)] transition'
  const rules = (rs: any[], set: (r: any[]) => void) => (
    <div className="space-y-1.5">
      {rs.map((r, i) => (
        <div key={i} className="flex gap-1">
          <select value={r.field || 'text'} onChange={e => set(rs.map((x, j) => j === i ? { ...x, field: e.target.value } : x))} className={`${input} !w-20`}>
            {fieldsOf.map(f => <option key={f.key} value={f.key}>{f.key}</option>)}
          </select>
          <select value={r.op || ''} onChange={e => set(rs.map((x, j) => j === i ? { ...x, op: e.target.value } : x))} className={`${input} !w-28`}>
            <option value="">op…</option>
            {Object.entries(ops).map(([k, o]) => <option key={k} value={k}>{o.label || k}</option>)}
          </select>
          {r.op && ops[r.op]?.arg !== 'none' && (
            <input value={r.value ?? ''} onChange={e => set(rs.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} className={`${input} flex-1 min-w-0`} />
          )}
          <button className="text-gray-600 hover:text-red-400 px-1" onClick={() => set(rs.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button className="text-[11px] text-gray-500 hover:text-gray-200" onClick={() => set([...rs, { field: 'text', op: 'contains', value: '' }])}>+ rule</button>
    </div>
  )

  const field = (f: Field) => {
    const v = d[f.key] ?? f.default
    if (f.type === 'agent') return (
      <select value={v || ''} onChange={e => onPatch({ [f.key]: e.target.value })} className={input}>
        <option value="">pick an agent…</option>
        {order.map(n => <option key={n} value={n}>{agents[n]?.icon || '◆'} {n}</option>)}
      </select>
    )
    if (f.type === 'select') return (
      <select value={v || ''} onChange={e => onPatch({ [f.key]: e.target.value })} className={input}>
        {(f.options || []).map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    )
    if (f.type === 'bool') return (
      <button onClick={() => onPatch({ [f.key]: !v })} className={`dag-toggle ${v ? 'dag-toggle--on' : ''}`}><span /></button>
    )
    if (f.type === 'number') return (
      <input type="number" value={v ?? ''} min={f.min} max={f.max}
        onChange={e => onPatch({ [f.key]: e.target.value === '' ? undefined : Number(e.target.value) })} className={input} />
    )
    if (f.type === 'textarea') return (
      <textarea value={v || ''} rows={4} placeholder={f.hint} onChange={e => onPatch({ [f.key]: e.target.value })} className={`${input} resize-y`} />
    )
    if (f.type === 'rules') return rules(v || [], r => onPatch({ [f.key]: r }))
    if (f.type === 'routes') {
      const rs = (v || []) as any[]
      return (
        <div className="space-y-2">
          {rs.map((r, i) => (
            <div key={i} className="rounded-md border border-white/[0.07] p-2 space-y-1.5">
              <div className="flex gap-1">
                <input value={r.port || ''} placeholder="port name" className={`${input} flex-1`}
                  onChange={e => onPatch({ routes: rs.map((x, j) => j === i ? { ...x, port: e.target.value } : x) })} />
                <button className="text-gray-600 hover:text-red-400 px-1" onClick={() => onPatch({ routes: rs.filter((_, j) => j !== i) })}>✕</button>
              </div>
              {rules(r.rules || [], rr => onPatch({ routes: rs.map((x, j) => j === i ? { ...x, rules: rr } : x) }))}
            </div>
          ))}
          <button className="text-[11px] text-gray-500 hover:text-gray-200" onClick={() => onPatch({ routes: [...rs, { port: `route${rs.length + 1}`, rules: [] }] })}>+ route</button>
        </div>
      )
    }
    if (f.type === 'json') return (
      <textarea rows={3} defaultValue={v ? JSON.stringify(v, null, 2) : ''} placeholder="{}" className={`${input} font-mono text-[11px]`}
        onBlur={e => { try { onPatch({ [f.key]: e.target.value.trim() ? JSON.parse(e.target.value) : undefined }) } catch { flash(false, 'params must be JSON') } }} />
    )
    // text, model, tool, toolbox — a plain field
    return <input value={Array.isArray(v) ? v.join(', ') : (v || '')} placeholder={f.hint || (f.type === 'model' ? 'the agent\'s own' : '')}
      onChange={e => onPatch({ [f.key]: f.type === 'toolbox' ? e.target.value.split(',').map(s => s.trim()).filter(Boolean) : e.target.value })} className={input} />
  }

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-start gap-2.5">
        <span className="dag-icon" style={{ ['--acc' as any]: `var(${ACCENT[spec?.accent || 'emerald']})` }}>{cfg?.icon || spec?.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] text-gray-100 font-medium truncate">{isAgent ? d.agent || spec?.label : spec?.label}</div>
          <div className="text-[11px] text-gray-500 leading-snug">{isAgent && cfg?.description ? cfg.description : spec?.summary}</div>
        </div>
      </div>

      {live?.out && (
        <div>
          <div className="dag-ins-title">last output {live.ms != null && <span className="text-gray-600 normal-case tracking-normal">· {(live.ms / 1000).toFixed(1)}s</span>}</div>
          <div className="text-[11px] text-gray-300 whitespace-pre-wrap max-h-40 overflow-y-auto rounded-md bg-white/[0.03] border border-white/[0.06] p-2">{live.out}</div>
        </div>
      )}

      {node.kind === 'agent' && cfg && (
        <AgentInside name={d.agent} cfg={cfg} canEdit={canEditAgent} token={token} onSaved={onAgentSaved} flash={flash} onDive={onDive} />
      )}

      {(spec?.fields || []).length > 0 && (
        <div className={`space-y-3 ${node.kind === 'agent' && cfg ? 'pt-3 border-t border-white/[0.06]' : ''}`}>
          <div className="dag-ins-title">{node.kind === 'agent' ? 'at this point in the graph' : 'settings'}</div>
          {(spec?.fields || []).map(f => f.type === 'bool' ? (
            <div key={f.key} className="flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-gray-500">{f.label}</span>
              {field(f)}
            </div>
          ) : (
            <label key={f.key} className="block space-y-1">
              <span className="text-[10px] uppercase tracking-wider text-gray-500">{f.label}</span>
              {field(f)}
            </label>
          ))}
        </div>
      )}

      {node.kind !== 'input' && (
        <button className="dag-btn dag-btn--danger" onClick={onDelete}>delete node</button>
      )}
    </div>
  )
}

// ── the agent itself — prompt, model, memory, tools, written to its mod.py ──
function AgentInside({ name, cfg, canEdit, token, onSaved, flash, onDive }: {
  name: string; cfg: AgentCfg; canEdit: boolean; token?: string | null
  onSaved: (n: string, c: Partial<AgentCfg>) => void; flash: (ok: boolean, t: string) => void; onDive?: () => void
}) {
  const [goal, setGoal] = useState(cfg.goal || '')
  const [model, setModel] = useState(cfg.model || '')
  const [memory, setMemory] = useState(cfg.memory || '')
  const [tools, setTools] = useState<string[]>(cfg.tools || [])
  const [toolAdd, setToolAdd] = useState('')
  const [allTools, setAllTools] = useState<string[]>([])
  const [mems, setMems] = useState<{ name: string; label?: string }[]>([])
  const [models, setModels] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    fetch(`${API_URL}/tools`).then(r => r.json()).then(d => setAllTools((d.tools || []).map((t: any) => t.name))).catch(() => {})
    fetch(`${API_URL}/memory/modules`).then(r => r.json()).then(d => setMems(d.memories || [])).catch(() => {})
    fetch(`${API_URL}/providers`).then(r => r.json()).then(d => setModels((d.providers || []).flatMap((p: any) => p.models || []).slice(0, 400))).catch(() => {})
  }, [])
  const changed = goal !== (cfg.goal || '') || model !== (cfg.model || '') || memory !== (cfg.memory || '')
    || tools.join('|') !== (cfg.tools || []).join('|')
  const input = 'w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[12px] text-gray-100 outline-none focus:border-[rgb(var(--a-400)/0.5)] transition disabled:opacity-60'

  const save = async () => {
    if (!goal.trim()) { flash(false, 'an agent needs a prompt'); return }
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/agents/${encodeURIComponent(name)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal, key: token,
          ...(tools.length ? { tools } : { clear_tools: true }),
          ...(model ? { model } : { clear_model: true }),
          ...(memory ? { memory } : { clear_memory: true }) }),
      })
      const d = await r.json()
      if (d?.error) { flash(false, d.error); return }
      onSaved(name, { goal, model: model || null, memory: memory || null, tools: tools.length ? tools : null })
      flash(true, `saved ${name}`)
    } catch (e: any) { flash(false, e?.message || 'save failed') } finally { setBusy(false) }
  }

  if (cfg.loop) return (
    <div className="space-y-2">
      <div className="dag-ins-title">inside</div>
      <div className="text-[11px] text-gray-400 leading-relaxed">A feedback loop: <b className="text-gray-200">{cfg.loop.builder}</b> does the task, <b className="text-gray-200">{cfg.loop.reviewer}</b> reviews it, and the builder redoes it with the notes — for a set number of rounds.</div>
    </div>
  )

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="dag-ins-title flex-1">inside {name}</div>
        {onDive && <button className="dag-btn" onClick={onDive} title="Open this agent's own graph">open graph ↘</button>}
      </div>
      {cfg.harness && <div className="text-[11px] text-violet-300/80">hands the whole run to the {cfg.harness} CLI — it brings its own model, tools and memory</div>}
      {!canEdit && <div className="text-[10px] text-gray-500">read-only — {token ? 'only its owner or the host can change it' : 'sign in to change it'}</div>}
      <label className="block space-y-1">
        <span className="text-[10px] uppercase tracking-wider text-gray-500">prompt</span>
        <textarea value={goal} onChange={e => setGoal(e.target.value)} rows={7} disabled={!canEdit} className={`${input} resize-y leading-relaxed`} />
      </label>
      {!cfg.harness && (<>
        <label className="block space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-gray-500">model</span>
          <input value={model} onChange={e => setModel(e.target.value)} disabled={!canEdit} list="dag-models" placeholder="the console's model" className={input} />
          <datalist id="dag-models">{models.map(m => <option key={m} value={m} />)}</datalist>
        </label>
        <label className="block space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-gray-500">memory</span>
          <select value={memory} onChange={e => setMemory(e.target.value)} disabled={!canEdit} className={input}>
            <option value="">default</option>
            {mems.filter(m => m.name !== 'default').map(m => <option key={m.name} value={m.name}>{m.label || m.name}</option>)}
          </select>
        </label>
        <div className="space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-gray-500">tools {tools.length ? `· ${tools.length}` : '· every tool'}</span>
          <div className="flex flex-wrap gap-1">
            {tools.map(t => (
              <span key={t} className="dag-tool">{t}{canEdit && <button onClick={() => setTools(tools.filter(x => x !== t))}>×</button>}</span>
            ))}
          </div>
          {canEdit && (
            <form className="flex gap-1" onSubmit={e => { e.preventDefault(); const t = toolAdd.trim(); if (t && !tools.includes(t)) setTools([...tools, t]); setToolAdd('') }}>
              <input value={toolAdd} onChange={e => setToolAdd(e.target.value)} list="dag-tools" placeholder="add a tool" className={input} />
              <datalist id="dag-tools">{allTools.filter(t => !tools.includes(t)).map(t => <option key={t} value={t} />)}</datalist>
            </form>
          )}
        </div>
      </>)}
      {canEdit && changed && (
        <button className="dag-btn dag-btn--primary w-full justify-center" onClick={save} disabled={busy}>{busy ? 'saving…' : `save ${name}`}</button>
      )}
    </div>
  )
}
