'use client'

// Builder — n8n-style visual agent composer.
// Drag Prompt / Model / Toolbox / Memory nodes from the palette onto the canvas,
// wire them into the AGENT node, then save — the graph compiles down to the
// backend's agent config (goal / tools / model) via POST /agents.
// Tools are never loose nodes: they all land as chips inside one Toolbox node,
// mirroring the backend's snap-on bundles (src/toolbox/mod.py). A chip is any
// name in the registry — a built-in, a custom shell tool, or a fleet module
// (`mod.<name>`), which the palette search fetches on demand.

import { useState, useRef, useEffect, useCallback } from 'react'
import { API_URL } from '../config'
import Select from './Select'

type NodeKind = 'agent' | 'prompt' | 'model' | 'toolbox' | 'memory'
type BNode = { id: string; kind: NodeKind; x: number; y: number; data: Record<string, any> }
type BEdge = { id: string; from: string; to: string }
type Viewport = { x: number; y: number; k: number }

type LibPrompt = { id: string; name: string; description?: string; body?: string }
type MemNote = { id: string; name: string; content?: string }
type ProviderInfo = { key: string; models: string[]; default_model: string; configured?: boolean; encrypted?: boolean; unlocked?: boolean; keyless?: boolean; runtime?: string | null; hint?: string | null }
type AgentInfo = { value: string; label: string; icon: string; builtin?: boolean }
type BoxPreset = { name: string; description?: string; tools: string[]; builtin?: boolean }

const NODE_W = 228
const TOOLBOX_W = 268
const wOf = (n: BNode) => (n.kind === 'toolbox' ? TOOLBOX_W : NODE_W)
// fallback only — the live list from /agents carries an authoritative builtin flag
const BUILTINS = new Set(['default', 'architect', 'reviewer', 'debugger', 'builder', 'refactorer',
                          'safety', 'claude-code', 'codex'])
const GRAPHS_KEY = 'agent_builder_graphs_v1'

const KIND_META: Record<Exclude<NodeKind, 'agent'>, { label: string; icon: string; accent: string; border: string; wire: string; hint: string }> = {
  prompt: { label: 'Prompt', icon: '¶', accent: 'text-amber-300', border: 'border-amber-400/30', wire: 'rgb(var(--w-400))', hint: 'system prompt' },
  model:  { label: 'Model',  icon: '⬢', accent: 'text-sky-300',   border: 'border-sky-400/30',   wire: 'rgb(var(--i-400))', hint: 'LLM override' },
  toolbox:{ label: 'Toolbox', icon: '◇', accent: 'text-emerald-300', border: 'border-emerald-400/30', wire: 'rgb(var(--a-400))', hint: 'the tools the agent may use' },
  memory: { label: 'Memory', icon: '◈', accent: 'text-violet-300', border: 'border-violet-400/30', wire: 'rgb(var(--v-400))', hint: 'note injected as context' },
}

let idSeq = 0
const nid = () => `n${Date.now().toString(36)}${(idSeq++).toString(36)}`

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

// Graphs saved before tools were bundled hold one `tool` node per tool. Fold
// them into a single toolbox so an old layout doesn't reopen as confetti.
const foldToolNodes = (nodes: BNode[], edges: BEdge[]): { nodes: BNode[]; edges: BEdge[] } => {
  const loose = nodes.filter(n => (n.kind as string) === 'tool')
  if (!loose.length) return { nodes, edges }
  const head = loose[0]
  const box: BNode = {
    ...head, kind: 'toolbox',
    data: { names: Array.from(new Set(loose.map(n => n.data?.name).filter(Boolean))) },
  }
  const dropped = new Set(loose.slice(1).map(n => n.id))
  return {
    nodes: nodes.map(n => (n.id === head.id ? box : n)).filter(n => !dropped.has(n.id)),
    edges: edges.filter(e => !dropped.has(e.from) && !dropped.has(e.to)),
  }
}

type Props = {
  onUseAgent: (name: string, memoryIds: string[]) => void
  onAgentsChanged: () => void
  initialAgent?: string | null
  // opens the encrypted-vault key panel for a provider — API keys are entered here, not in the console
  onManageKey?: (provider: string) => void
  // bumped by the parent after a key save/unlock so provider key state refreshes
  keyVersion?: number
  // the caller's signed token — the server records it as the agent's owner and
  // checks it before letting an edit or delete through
  token?: string | null
  // the host owns every agent nobody else does, so they may edit and delete
  // the shipped ones too
  isHost?: boolean
  // wallet sign-in, so the canvas can offer it where saving is blocked
  onSignIn?: () => void
}

export default function Builder({ onUseAgent, onAgentsChanged, initialAgent, onManageKey, keyVersion, token, isHost, onSignIn }: Props) {
  // an agent is filed under the address that made it, so saving takes a
  // sign-in — the server refuses an unsigned create either way
  const canSave = !!token || !!isHost
  // ── graph state ──
  const [nodes, setNodes] = useState<BNode[]>([])
  const [edges, setEdges] = useState<BEdge[]>([])
  const [viewport, setViewport] = useState<Viewport>({ x: 0, y: 0, k: 1 })
  const [selected, setSelected] = useState<string | null>(null)
  const [loadedName, setLoadedName] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // ── catalog data ──
  const [tools, setTools] = useState<Record<string, { description?: string; kind?: string }>>({})
  // fleet matches for the current palette search — fetched, not held: there are
  // hundreds of modules and only the ones you look for belong in the list
  const [fleet, setFleet] = useState<Record<string, { description?: string; kind?: string }>>({})
  // the backend's snap-on bundles — a toolbox node can be filled from one
  const [boxes, setBoxes] = useState<BoxPreset[]>([])
  const [prompts, setPrompts] = useState<LibPrompt[]>([])
  const [notes, setNotes] = useState<MemNote[]>([])
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [paletteSearch, setPaletteSearch] = useState('')

  const canvasRef = useRef<HTMLDivElement>(null)
  const nodeEls = useRef<Record<string, HTMLDivElement | null>>({})
  const viewRef = useRef(viewport)
  useEffect(() => { viewRef.current = viewport }, [viewport])
  const nodesRef = useRef(nodes)
  useEffect(() => { nodesRef.current = nodes }, [nodes])

  // live wire while dragging from an output port
  const [connecting, setConnecting] = useState<{ from: string; mx: number; my: number } | null>(null)

  // cascade click-added nodes so they don't stack on one spot
  const placeSeq = useRef(0)
  const clickPlace = () => {
    const rect = canvasRef.current?.getBoundingClientRect()
    const w = toWorld((rect?.left || 0) + (rect?.width || 600) * 0.3, (rect?.top || 0) + (rect?.height || 400) * 0.25)
    const i = placeSeq.current++
    return { x: w.x + (i % 3) * 70, y: w.y + ((i % 5) * 110) }
  }

  const msgTimer = useRef<number | undefined>(undefined)
  const flash = (ok: boolean, text: string) => {
    setMsg({ ok, text })
    window.clearTimeout(msgTimer.current)
    msgTimer.current = window.setTimeout(() => setMsg(null), 3500)
  }

  // ── fetch catalogs ──
  const fetchAgentList = useCallback(() => {
    fetch(`${API_URL}/agents`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => {
        if (d.schemas && typeof d.schemas === 'object') {
          setAgents(Object.entries(d.schemas).map(([key, val]: [string, any]) => ({
            value: key, label: val.name || key, icon: val.icon || '>_', builtin: !!val.builtin,
          })))
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    // the default loadout material: shipped tools + the custom ones
    fetch(`${API_URL}/tools`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => setTools(Object.fromEntries((d.tools || []).map((t: any) =>
        [t.name, { description: t.description, kind: t.kind }]))))
      .catch(() => {})
    fetch(`${API_URL}/toolboxes`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setBoxes(d.toolboxes || [])).catch(() => {})
    fetch(`${API_URL}/library?kind=prompt`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setPrompts((d.items || []).filter((i: any) => i.kind === 'prompt'))).catch(() => {})
    fetch(`${API_URL}/memory`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setNotes(d.memory || [])).catch(() => {})
    fetchAgentList()
  }, [fetchAgentList])

  // a palette search is a fleet search too, once typing stops
  useEffect(() => {
    const q = paletteSearch.trim()
    if (q.length < 2) { setFleet({}); return }
    const t = setTimeout(() => {
      fetch(`${API_URL}/tools?mods=true&q=${encodeURIComponent(q)}&limit=25`,
        { signal: AbortSignal.timeout(10000) })
        .then(r => r.json())
        .then(d => setFleet(Object.fromEntries((d.tools || [])
          .filter((t: any) => t.kind === 'mod')
          .map((t: any) => [t.name, { description: t.description, kind: 'mod' }]))))
        .catch(() => {})
    }, 250)
    return () => clearTimeout(t)
  }, [paletteSearch])

  // providers carry key state (configured / encrypted / unlocked) — refetch after key changes
  useEffect(() => {
    fetch(`${API_URL}/providers`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json()).then(d => setProviders(d.providers || [])).catch(() => {})
  }, [keyVersion])

  // ── graph persistence (shared localStorage origin — keep small, quota-safe) ──
  const readGraphs = (): Record<string, any> => {
    try { return JSON.parse(localStorage.getItem(GRAPHS_KEY) || '{}') } catch { return {} }
  }
  const persistGraph = (name: string) => {
    try {
      const graphs = readGraphs()
      graphs[name] = {
        ts: Date.now(),
        viewport: viewRef.current,
        nodes: nodesRef.current.map(n => ({
          ...n,
          data: n.kind === 'prompt' ? { ...n.data, text: String(n.data.text || '').slice(0, 4000) } : n.data,
        })),
        edges,
      }
      const names = Object.keys(graphs)
      if (names.length > 12) {
        names.sort((a, b) => (graphs[a].ts || 0) - (graphs[b].ts || 0))
        names.slice(0, names.length - 12).forEach(k => delete graphs[k])
      }
      localStorage.setItem(GRAPHS_KEY, JSON.stringify(graphs))
    } catch {} // quota-full is non-fatal — the layout just isn't remembered
  }

  // ── graph construction ──
  const starterGraph = useCallback(() => {
    const agentId = nid()
    const promptId = nid()
    setNodes([
      { id: agentId, kind: 'agent', x: 520, y: 150, data: { name: '', icon: '>_', description: '' } },
      { id: promptId, kind: 'prompt', x: 120, y: 130, data: { text: '', promptId: '' } },
    ])
    setEdges([{ id: nid(), from: promptId, to: agentId }])
    setViewport({ x: 0, y: 0, k: 1 })
    setSelected(null)
    setLoadedName(null)
    setDirty(false)
  }, [])

  // on mount: open the requested agent (edit flow) or a fresh starter canvas
  const initialRef = useRef(initialAgent)
  useEffect(() => {
    if (initialRef.current) loadAgent(initialRef.current)
    else starterGraph()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [starterGraph])

  // build a graph from an existing agent's server config
  const loadAgent = async (name: string) => {
    // restored layout wins when we have one
    const saved = readGraphs()[name]
    if (saved?.nodes?.length) {
      const folded = foldToolNodes(saved.nodes, saved.edges || [])
      setNodes(folded.nodes)
      setEdges(folded.edges)
      setViewport(saved.viewport || { x: 0, y: 0, k: 1 })
      setLoadedName(name)
      setSelected(null)
      setDirty(false)
      return
    }
    try {
      const r = await fetch(`${API_URL}/agents/${name}`, { signal: AbortSignal.timeout(8000) })
      const cfg = await r.json()
      if (cfg.error) { flash(false, cfg.error); return }
      const agentId = nid()
      const ns: BNode[] = [{
        id: agentId, kind: 'agent', x: 560, y: 170,
        data: { name, icon: cfg.icon || '>_', description: cfg.description || '' },
      }]
      const es: BEdge[] = []
      const promptId = nid()
      ns.push({ id: promptId, kind: 'prompt', x: 90, y: 60, data: { text: cfg.goal || '', promptId: '' } })
      es.push({ id: nid(), from: promptId, to: agentId })
      if (cfg.model) {
        const mId = nid()
        const prov = providers.find(p => p.models.includes(cfg.model))?.key || providers[0]?.key || 'openrouter'
        ns.push({ id: mId, kind: 'model', x: 90, y: 330, data: { provider: prov, model: cfg.model } })
        es.push({ id: nid(), from: mId, to: agentId })
      }
      const toolNames: string[] = (cfg.tools || cfg.skills) || []
      if (toolNames.length) {
        const boxId = nid()
        ns.push({ id: boxId, kind: 'toolbox', x: 90, y: 330, data: { names: Array.from(new Set(toolNames)) } })
        es.push({ id: nid(), from: boxId, to: agentId })
      }
      setNodes(ns)
      setEdges(es)
      setViewport({ x: 0, y: 0, k: 1 })
      setLoadedName(name)
      setSelected(null)
      setDirty(false)
    } catch (e: any) {
      flash(false, e.message)
    }
  }

  const agentNode = nodes.find(n => n.kind === 'agent')
  const connectedSources = edges.filter(e => e.to === agentNode?.id).map(e => e.from)
  const connected = (id: string) => connectedSources.includes(id)

  // ── coordinate transforms ──
  const toWorld = (clientX: number, clientY: number) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    const v = viewRef.current
    return {
      x: ((clientX - (rect?.left || 0)) - v.x) / v.k,
      y: ((clientY - (rect?.top || 0)) - v.y) / v.k,
    }
  }

  // ── add node (palette click or drop) ──
  const addNode = (spec: string, wx: number, wy: number) => {
    const [kind, arg] = spec.split(':') as [NodeKind, string?]
    if (kind === 'agent') return
    let data: Record<string, any> = {}
    if (kind === 'prompt') data = { text: '', promptId: '' }
    if (kind === 'model') {
      const p = providers[0]
      data = { provider: p?.key || 'openrouter', model: p?.default_model || '' }
    }
    if (kind === 'memory') data = { noteId: arg || notes[0]?.id || '' }
    const node: BNode = { id: nid(), kind, x: wx - NODE_W / 2, y: wy - 20, data }
    setNodes(ns => [...ns, node])
    // auto-wire into the agent — drag the wire off to disconnect later
    if (agentNode) setEdges(es => [...es, { id: nid(), from: node.id, to: agentNode.id }])
    setSelected(node.id)
    setDirty(true)
  }

  const removeNode = (id: string) => {
    setNodes(ns => ns.filter(n => n.id !== id))
    setEdges(es => es.filter(e => e.from !== id && e.to !== id))
    if (selected === id) setSelected(null)
    setDirty(true)
  }

  const patchNode = (id: string, patch: Record<string, any>) => {
    setNodes(ns => ns.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n))
    setDirty(true)
  }

  // ── canvas pan ──
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

  // wheel zoom around the cursor (native listener — React's onWheel is passive)
  useEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      setViewport(v => {
        const k = Math.min(1.8, Math.max(0.35, v.k * (e.deltaY < 0 ? 1.08 : 0.93)))
        return { x: mx - ((mx - v.x) / v.k) * k, y: my - ((my - v.y) / v.k) * k, k }
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // ── node drag ──
  const startNodeDrag = (e: React.PointerEvent, n: BNode) => {
    e.stopPropagation()
    setSelected(n.id)
    const start = { sx: e.clientX, sy: e.clientY, nx: n.x, ny: n.y }
    const move = (ev: PointerEvent) => {
      const k = viewRef.current.k
      setNodes(ns => ns.map(x => x.id === n.id
        ? { ...x, x: start.nx + (ev.clientX - start.sx) / k, y: start.ny + (ev.clientY - start.sy) / k }
        : x))
    }
    const up = () => {
      setDirty(true)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // ── wire drag (output port → agent) ──
  const startConnect = (e: React.PointerEvent, from: BNode) => {
    e.stopPropagation()
    e.preventDefault()
    const w = toWorld(e.clientX, e.clientY)
    setConnecting({ from: from.id, mx: w.x, my: w.y })
    const move = (ev: PointerEvent) => {
      const p = toWorld(ev.clientX, ev.clientY)
      setConnecting(c => c ? { ...c, mx: p.x, my: p.y } : c)
    }
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setConnecting(null)
      const agent = nodesRef.current.find(n => n.kind === 'agent')
      if (!agent) return
      const el = nodeEls.current[agent.id]
      if (el) {
        const r = el.getBoundingClientRect()
        if (ev.clientX >= r.left - 16 && ev.clientX <= r.right + 16 && ev.clientY >= r.top - 16 && ev.clientY <= r.bottom + 16) {
          setEdges(es => es.some(x => x.from === from.id && x.to === agent.id)
            ? es : [...es, { id: nid(), from: from.id, to: agent.id }])
          setDirty(true)
        }
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // ── palette drag & drop ──
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const spec = e.dataTransfer.getData('text/agent-node')
    if (!spec) return
    const w = toWorld(e.clientX, e.clientY)
    addNode(spec, w.x, w.y)
  }

  // ── compile graph → agent config + save ──
  const compile = () => {
    const agent = nodes.find(n => n.kind === 'agent')
    if (!agent) return { error: 'no agent node' }
    const srcIds = edges.filter(e => e.to === agent.id).map(e => e.from)
    const srcs = nodes.filter(n => srcIds.includes(n.id))
    const goal = srcs.filter(n => n.kind === 'prompt').map(n => String(n.data.text || '').trim()).filter(Boolean).join('\n\n')
    // tools arrive as one TOOLBOX node holding a list — the per-tool node kind
    // was folded into it (see foldToolNodes), and reading the old kind here
    // left every saved agent with an empty tool list
    const toolNames = Array.from(new Set(srcs.filter(n => n.kind === 'toolbox')
      .flatMap(n => (n.data.names as string[]) || []).filter(Boolean)))
    const model = srcs.find(n => n.kind === 'model')?.data.model || null
    const memoryIds = srcs.filter(n => n.kind === 'memory').map(n => n.data.noteId).filter(Boolean)
    const slug = slugify(agent.data.name || '')
    return { slug, agent, goal, toolNames, model, memoryIds }
  }

  const save = async (): Promise<string | null> => {
    if (!canSave) { flash(false, 'sign in to save your agent'); return null }
    const c = compile()
    if ('error' in c) { flash(false, c.error!); return null }
    if (!c.slug) { flash(false, 'give the agent a name (on the AGENT node)'); return null }
    const existing = agents.find(a => a.value === c.slug)
    if (!isHost && (existing?.builtin || (!existing && BUILTINS.has(c.slug)))) {
      flash(false, `"${c.slug}" is a built-in — pick a new name to save your version`); return null
    }
    if (!c.goal) { flash(false, 'wire in a Prompt node with a system prompt'); return null }
    setSaving(true)
    try {
      // existing custom agent → update in place; new name → create
      const body = {
        description: c.agent!.data.description || '',
        goal: c.goal,
        icon: c.agent!.data.icon || '>_',
        ...(existing
          ? (c.toolNames.length ? { tools: c.toolNames } : { clear_tools: true })
          : { tools: c.toolNames.length ? c.toolNames : null }),
        ...(existing
          ? (c.model ? { model: c.model } : { clear_model: true })
          : { model: c.model }),
      }
      const res = existing
        ? await fetch(`${API_URL}/agents/${c.slug}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...body, key: token }),
          })
        : await fetch(`${API_URL}/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: c.slug, ...body, key: token }),
          })
      const data = await res.json()
      if (data.error) { flash(false, data.error); return null }
      persistGraph(c.slug)
      setLoadedName(c.slug)
      setDirty(false)
      fetchAgentList()
      onAgentsChanged()
      flash(true, `saved "${c.slug}" — it's now selectable in the console`)
      return c.slug
    } catch (e: any) {
      flash(false, e.message)
      return null
    } finally {
      setSaving(false)
    }
  }

  const saveAndUse = async () => {
    const c = compile()
    const name = await save()
    if (name) onUseAgent(name, 'memoryIds' in c ? (c.memoryIds as string[]) : [])
  }

  const deleteAgent = async () => {
    if (!loadedName || (BUILTINS.has(loadedName) && !isHost)) return
    try {
      const q = token ? `?key=${encodeURIComponent(token)}` : ''
      const res = await fetch(`${API_URL}/agents/${loadedName}${q}`, { method: 'DELETE' })
      const data = await res.json()
      if (data.error) { flash(false, data.error); return }
      try { const g = readGraphs(); delete g[loadedName]; localStorage.setItem(GRAPHS_KEY, JSON.stringify(g)) } catch {}
      fetchAgentList()
      onAgentsChanged()
      flash(true, `deleted "${loadedName}"`)
      starterGraph()
    } catch (e: any) {
      flash(false, e.message)
    }
  }

  // ── edge geometry ──
  const portOut = (n: BNode) => ({ x: n.x + NODE_W, y: n.y + 21 })
  const portIn = (n: BNode) => ({ x: n.x, y: n.y + 21 })
  const edgePath = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const c = Math.max(46, Math.abs(b.x - a.x) * 0.45)
    return `M ${a.x} ${a.y} C ${a.x + c} ${a.y}, ${b.x - c} ${b.y}, ${b.x} ${b.y}`
  }

  // the palette lists the default loadout; a search also asks the server for
  // fleet modules, so any of the ~300 mods can be wired onto an agent
  const filteredTools = Object.keys(tools).filter(s =>
    !paletteSearch.trim() || s.toLowerCase().includes(paletteSearch.trim().toLowerCase()))
    .concat(Object.keys(fleet).filter(f => !(f in tools)))

  const zoomBy = (f: number) => {
    const el = canvasRef.current
    const rect = el?.getBoundingClientRect()
    const mx = (rect?.width || 0) / 2
    const my = (rect?.height || 0) / 2
    setViewport(v => {
      const k = Math.min(1.8, Math.max(0.35, v.k * f))
      return { x: mx - ((mx - v.x) / v.k) * k, y: my - ((my - v.y) / v.k) * k, k }
    })
  }

  // ── node body renderers ──
  const nodeBody = (n: BNode) => {
    if (n.kind === 'agent') {
      const c = compile() as any
      return (
        <div className="p-2.5 space-y-2" onPointerDown={e => e.stopPropagation()}>
          <div className="flex gap-1.5">
            <input value={n.data.icon || ''} onChange={e => patchNode(n.id, { icon: e.target.value })} maxLength={4}
              className="w-11 bg-white/[0.05] border border-white/[0.09] rounded-md px-1.5 py-1.5 text-xs text-gray-200 outline-none text-center focus:border-emerald-500/50 transition" />
            <input value={n.data.name || ''} onChange={e => patchNode(n.id, { name: e.target.value })} placeholder="agent name"
              className="flex-1 min-w-0 bg-white/[0.05] border border-white/[0.09] rounded-md px-2 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/50 transition" />
          </div>
          <input value={n.data.description || ''} onChange={e => patchNode(n.id, { description: e.target.value })} placeholder="description"
            className="w-full bg-white/[0.05] border border-white/[0.09] rounded-md px-2 py-1.5 text-[11px] text-gray-300 outline-none placeholder:text-gray-600 focus:border-emerald-500/50 transition" />
          <div className="flex flex-wrap gap-1 pt-0.5">
            <span className={`text-[9px] px-1.5 py-0.5 rounded border ${c.goal ? 'border-amber-400/30 text-amber-300/90 bg-amber-400/[0.06]' : 'border-white/10 text-gray-600'}`}>
              ¶ prompt {c.goal ? '✓' : '—'}
            </span>
            <span className={`text-[9px] px-1.5 py-0.5 rounded border ${c.toolNames?.length ? 'border-emerald-400/30 text-emerald-300/90 bg-emerald-400/[0.06]' : 'border-white/10 text-gray-600'}`}>
              ◇ {c.toolNames?.length ? `${c.toolNames.length} tools` : 'every tool'}
            </span>
            <span className={`text-[9px] px-1.5 py-0.5 rounded border ${c.model ? 'border-sky-400/30 text-sky-300/90 bg-sky-400/[0.06]' : 'border-white/10 text-gray-600'}`}>
              ⬢ {c.model ? String(c.model).split('/').pop() : 'default model'}
            </span>
            {c.memoryIds?.length > 0 && (
              <span className="text-[9px] px-1.5 py-0.5 rounded border border-violet-400/30 text-violet-300/90 bg-violet-400/[0.06]">
                ◈ {c.memoryIds.length} notes
              </span>
            )}
          </div>
        </div>
      )
    }
    if (n.kind === 'prompt') {
      return (
        <div className="p-2.5 space-y-1.5" onPointerDown={e => e.stopPropagation()}>
          <Select
            size="sm" accent="amber" className="w-full"
            value={n.data.promptId || ''}
            onChange={v => {
              const p = prompts.find(x => x.id === v)
              patchNode(n.id, { promptId: v, ...(p ? { text: p.body || '' } : {}) })
            }}
            options={[
              { value: '', label: 'custom prompt…', icon: '✎' },
              ...prompts.map(p => ({ value: p.id, label: p.name, icon: '¶', hint: p.description })),
            ]} />
          <textarea
            value={n.data.text || ''}
            onChange={e => patchNode(n.id, { text: e.target.value, promptId: '' })}
            placeholder="You are a …"
            rows={4}
            className="w-full bg-white/[0.05] border border-white/[0.09] rounded-md px-2 py-1.5 text-[11px] text-gray-300 outline-none placeholder:text-gray-600 focus:border-amber-400/40 transition resize-none leading-relaxed" />
        </div>
      )
    }
    if (n.kind === 'model') {
      const pkey = n.data.provider || 'openrouter'
      const pinfo = providers.find(p => p.key === pkey)
      const models = pinfo?.models || []
      const keyLocked = !!pinfo?.encrypted && !pinfo?.unlocked && !pinfo?.configured
      const keyOk = !!pinfo?.configured
      return (
        <div className="p-2.5 space-y-1.5" onPointerDown={e => e.stopPropagation()}>
          <Select
            size="sm" accent="sky" className="w-full" placeholder="provider…"
            value={n.data.provider || ''}
            onChange={v => {
              const p = providers.find(x => x.key === v)
              patchNode(n.id, { provider: v, model: p?.default_model || '' })
            }}
            options={(providers.length ? providers.map(p => p.key) : ['openrouter']).map(k => ({ value: k, label: k, icon: '⬢' }))} />
          <Select
            size="sm" accent="sky" className="w-full" placeholder="model…"
            value={n.data.model || ''}
            onChange={v => patchNode(n.id, { model: v })}
            options={(n.data.model && !models.includes(n.data.model) ? [n.data.model, ...models] : models).map(mn => ({ value: mn, label: mn }))} />
          {/* the LFM providers have no key to manage — say where they run instead */}
          {pinfo?.keyless ? (
            <div className="w-full px-1.5 py-1 rounded-md text-[10px] font-medium border bg-emerald-500/[0.06] border-emerald-500/25 text-emerald-300"
                 title={pinfo.hint || ''}>
              ⌁ {pinfo.hint || 'no key needed'}
            </div>
          ) : (
          /* API key for this provider — entered here in the Builder, not the console */
          <button
            onClick={() => onManageKey?.(pkey)}
            className={`w-full flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[10px] font-medium border transition ${
              keyOk
                ? 'bg-emerald-500/[0.06] border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/15'
                : 'bg-amber-500/[0.08] border-amber-500/30 text-amber-300 hover:bg-amber-500/20'
            }`}
            title={keyOk
              ? `${pkey} key configured${pinfo?.unlocked ? ' (encrypted, unlocked)' : ''} — click to manage`
              : keyLocked
              ? `${pkey} key is encrypted — click to unlock with your passphrase`
              : `No ${pkey} API key — click to enter yours`}>
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
              <rect x="4" y="11" width="16" height="10" rx="2" />
              {keyOk ? <path d="M8 11V7a4 4 0 0 1 7.7-1.5" /> : <path d="M8 11V7a4 4 0 0 1 8 0v4" />}
            </svg>
            {keyOk ? 'key set — manage' : keyLocked ? 'key locked — unlock' : 'enter API key'}
          </button>
          )}
        </div>
      )
    }
    // memory
    const note = notes.find(x => x.id === n.data.noteId)
    return (
      <div className="p-2.5 space-y-1" onPointerDown={e => e.stopPropagation()}>
        <Select
          size="sm" accent="violet" className="w-full" placeholder="no notes in library"
          value={n.data.noteId || ''}
          onChange={v => patchNode(n.id, { noteId: v })}
          disabled={notes.length === 0}
          options={notes.map(x => ({ value: x.id, label: x.name, icon: '◈' }))} />
        <div className="text-[10px] text-gray-600 leading-snug line-clamp-2">
          {note?.content?.slice(0, 110) || 'context injected at run time'}
        </div>
      </div>
    )
  }

  const paletteItem = (spec: string, icon: string, label: string, hint: string, accent: string) => (
    <div
      key={spec}
      draggable
      onDragStart={e => { e.dataTransfer.setData('text/agent-node', spec); e.dataTransfer.effectAllowed = 'copy' }}
      onClick={() => {
        const at = clickPlace()
        addNode(spec, at.x, at.y)
      }}
      className="flex items-center gap-2 px-2.5 py-2 rounded-lg border border-white/[0.07] bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/[0.14] cursor-grab active:cursor-grabbing transition select-none group"
      title="Drag onto the canvas (or click to add)"
    >
      <span className={`${accent} text-sm w-4 text-center shrink-0`}>{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs text-gray-300 group-hover:text-gray-100 truncate">{label}</div>
        <div className="text-[9px] text-gray-600 truncate">{hint}</div>
      </div>
      <span className="text-gray-700 group-hover:text-gray-500 text-[10px] shrink-0">⠿</span>
    </div>
  )

  return (
    <div className="h-full flex min-h-0">
      {/* ── palette ── */}
      <div className="w-56 shrink-0 border-r border-white/[0.06] flex flex-col min-h-0 bg-surface-1">
        <div className="px-3 py-2.5 border-b border-white/[0.06] shrink-0">
          <div className="text-[10px] text-gray-500 uppercase tracking-wider font-medium">Nodes</div>
          <div className="text-[9px] text-gray-700 mt-0.5">drag onto the canvas → wires into the agent</div>
        </div>
        <div className="p-2 space-y-1 shrink-0">
          {paletteItem('prompt', '¶', 'Prompt', 'system prompt / persona', 'text-amber-300')}
          {paletteItem('model', '⬢', 'Model', 'provider + model override', 'text-sky-300')}
          {paletteItem('memory', '◈', 'Memory', 'library note as context', 'text-violet-300')}
        </div>
        <div className="px-3 pt-2 pb-1.5 border-t border-white/[0.06] shrink-0 flex items-center gap-2">
          <span className="text-[10px] text-emerald-300/80 uppercase tracking-wider font-medium">◇ Tools</span>
          <span className="text-[9px] text-gray-700">{Object.keys(tools).length}</span>
          {!!Object.keys(fleet).length && (
            <span className="ml-auto text-[9px] text-violet-300/70">+{Object.keys(fleet).length} fleet</span>
          )}
        </div>
        <div className="px-2 pb-1.5 shrink-0">
          <input value={paletteSearch} onChange={e => setPaletteSearch(e.target.value)}
            placeholder="filter tools + the fleet…"
            className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-[11px] text-gray-300 outline-none placeholder:text-gray-700 focus:border-emerald-500/40 transition" />
        </div>
        <div className="flex-1 overflow-y-auto min-h-0 px-2 pb-2 space-y-0.5">
          {filteredTools.map(s => {
            const meta = tools[s] || fleet[s] || {}
            const isMod = meta.kind === 'mod'
            return (
              <div key={s}
                draggable
                onDragStart={e => { e.dataTransfer.setData('text/agent-node', `tool:${s}`); e.dataTransfer.effectAllowed = 'copy' }}
                onClick={() => {
                  const at = clickPlace()
                  addNode(`tool:${s}`, at.x, at.y)
                }}
                className={`flex items-center gap-2 px-2 py-1.5 rounded-md border border-transparent cursor-grab active:cursor-grabbing transition select-none group ${
                  isMod ? 'hover:border-violet-500/20 hover:bg-violet-500/[0.05]'
                        : 'hover:border-emerald-500/20 hover:bg-emerald-500/[0.05]'
                }`}
                title={meta.description || s}
              >
                <span className={`text-[11px] shrink-0 ${isMod ? 'text-violet-400/70' : 'text-emerald-400/70'}`}>◇</span>
                <span className="text-[11px] text-gray-400 group-hover:text-gray-200 font-mono truncate">{s}</span>
                <span className="ml-auto text-gray-800 group-hover:text-gray-600 text-[10px] shrink-0">⠿</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* ── canvas column ── */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {/* toolbar */}
        <div className="border-b border-white/[0.06] px-3 py-2 flex items-center gap-2 shrink-0 bg-surface-1">
          <Select
            accent="emerald" className="max-w-[190px]"
            title="Load an existing agent into the builder"
            value={loadedName || ''}
            onChange={v => { if (v) loadAgent(v); else starterGraph() }}
            options={[
              { value: '', label: 'new agent…', icon: '✦' },
              ...agents.map(a => ({
                value: a.value,
                label: a.label,
                icon: a.icon,
                badge: (a.builtin ?? BUILTINS.has(a.value)) ? 'built-in' : undefined,
              })),
            ]} />
          <button onClick={starterGraph}
            className="px-2.5 py-1.5 rounded-md text-xs border border-white/[0.08] text-gray-500 hover:text-gray-300 hover:border-white/20 transition"
            title="Start a fresh canvas">
            new
          </button>
          {loadedName && (!BUILTINS.has(loadedName) || isHost) && (
            <button onClick={deleteAgent}
              className="px-2.5 py-1.5 rounded-md text-xs border border-red-500/20 text-red-400/80 hover:text-red-300 hover:bg-red-500/10 transition"
              title={BUILTINS.has(loadedName)
                ? `Delete built-in agent "${loadedName}" — you are the host`
                : `Delete agent "${loadedName}"`}>
              delete
            </button>
          )}
          {msg && (
            <span className={`text-[11px] px-2 py-1 rounded-md truncate min-w-0 ${msg.ok ? 'text-emerald-300 bg-emerald-500/10' : 'text-red-400 bg-red-500/10'}`}>
              {msg.text}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1.5 shrink-0">
            {dirty && <span className="text-[10px] text-amber-300/70" title="Unsaved changes">● unsaved</span>}
            {/* an agent belongs to the address that made it — no sign-in, no save */}
            {!canSave && (
              <button onClick={onSignIn}
                className="px-3 py-1.5 rounded-md text-xs font-medium border border-sky-500/30 bg-sky-500/10 text-sky-200 hover:bg-sky-500/20 transition"
                title="Agents are saved under the wallet that made them — sign in to keep this one">
                sign in to save
              </button>
            )}
            <button onClick={save} disabled={saving || !canSave}
              title={canSave ? undefined : 'Sign in to save your agent'}
              className="px-3 py-1.5 rounded-md text-xs font-medium border border-emerald-500/25 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition">
              {saving ? 'saving…' : 'save'}
            </button>
            <button onClick={saveAndUse} disabled={saving || !canSave}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-600/90 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition"
              title={canSave ? 'Save and select this agent in the console' : 'Sign in to save your agent'}>
              use in console →
            </button>
          </div>
        </div>

        {/* canvas */}
        <div
          ref={canvasRef}
          className="flex-1 min-h-0 relative overflow-hidden builder-grid cursor-grab active:cursor-grabbing"
          style={{
            backgroundPosition: `${viewport.x}px ${viewport.y}px`,
            backgroundSize: `${22 * viewport.k}px ${22 * viewport.k}px`,
          }}
          onPointerDown={onCanvasDown}
          onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
          onDrop={onDrop}
        >
          {/* edges */}
          <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ overflow: 'visible' }}>
            <g transform={`translate(${viewport.x},${viewport.y}) scale(${viewport.k})`}>
              {edges.map(e => {
                const from = nodes.find(n => n.id === e.from)
                const to = nodes.find(n => n.id === e.to)
                if (!from || !to) return null
                const color = from.kind === 'agent' ? 'rgb(var(--a-400))' : KIND_META[from.kind as Exclude<NodeKind, 'agent'>].wire
                const d = edgePath(portOut(from), portIn(to))
                return (
                  <g key={e.id} className="pointer-events-auto">
                    <path d={d} stroke="transparent" strokeWidth={14} fill="none" className="cursor-pointer"
                      onClick={() => { setEdges(es => es.filter(x => x.id !== e.id)); setDirty(true) }}>
                      <title>click to disconnect</title>
                    </path>
                    <path d={d} style={{ stroke: color }} strokeWidth={1.8} fill="none" opacity={0.65} className="pointer-events-none builder-wire" />
                  </g>
                )
              })}
              {connecting && (() => {
                const from = nodes.find(n => n.id === connecting.from)
                if (!from) return null
                const color = KIND_META[from.kind as Exclude<NodeKind, 'agent'>]?.wire || 'rgb(var(--a-400))'
                return <path d={edgePath(portOut(from), { x: connecting.mx, y: connecting.my })}
                  style={{ stroke: color }} strokeWidth={1.8} strokeDasharray="5 4" fill="none" opacity={0.85} />
              })()}
            </g>
          </svg>

          {/* nodes */}
          <div className="absolute inset-0" style={{ transform: `translate(${viewport.x}px,${viewport.y}px) scale(${viewport.k})`, transformOrigin: '0 0', pointerEvents: 'none' }}>
            {nodes.map(n => {
              const isAgent = n.kind === 'agent'
              const meta = isAgent ? null : KIND_META[n.kind as Exclude<NodeKind, 'agent'>]
              const sel = selected === n.id
              return (
                <div
                  key={n.id}
                  ref={el => { nodeEls.current[n.id] = el }}
                  className={`absolute rounded-xl border shadow-xl transition-shadow ${
                    isAgent
                      ? `bg-gradient-to-b from-emerald-500/[0.10] to-surface-1 ${sel ? 'border-emerald-400/60 shadow-[0_0_24px_rgb(var(--glow)/0.25)]' : 'border-emerald-500/35 shadow-[0_0_18px_rgb(var(--glow)/0.10)]'}`
                      : `bg-surface-2 ${sel ? 'border-white/30' : meta!.border}`
                  }`}
                  style={{ left: n.x, top: n.y, width: NODE_W, pointerEvents: 'auto' }}
                  onPointerDown={e => { e.stopPropagation(); setSelected(n.id) }}
                >
                  {/* header = drag handle */}
                  <div
                    className={`flex items-center gap-1.5 px-2.5 h-[42px] rounded-t-xl cursor-grab active:cursor-grabbing select-none border-b ${
                      isAgent ? 'border-emerald-500/20' : 'border-white/[0.06]'
                    }`}
                    onPointerDown={e => startNodeDrag(e, n)}
                  >
                    <span className={`text-sm ${isAgent ? 'text-emerald-300' : meta!.accent}`}>
                      {isAgent ? (n.data.icon || '>_') : meta!.icon}
                    </span>
                    <span className={`text-[10px] font-semibold uppercase tracking-wider ${isAgent ? 'text-emerald-200' : 'text-gray-400'}`}>
                      {isAgent ? (n.data.name || 'agent') : meta!.label}
                    </span>
                    {!isAgent && !connected(n.id) && (
                      <span className="text-[8px] px-1 py-0.5 rounded bg-white/[0.06] text-gray-600 border border-white/[0.06]" title="Not wired into the agent — drag from its output port">
                        unwired
                      </span>
                    )}
                    <span className="ml-auto" />
                    {!isAgent && (
                      <button
                        onPointerDown={e => e.stopPropagation()}
                        onClick={() => removeNode(n.id)}
                        className="text-gray-600 hover:text-red-400 transition text-xs px-1"
                        title="Remove node">
                        ✕
                      </button>
                    )}
                  </div>

                  {nodeBody(n)}

                  {/* ports */}
                  {isAgent ? (
                    <div
                      className="absolute -left-[7px] top-[14px] w-[14px] h-[14px] rounded-full bg-emerald-400 border-2 border-surface-1 shadow-[0_0_8px_rgb(var(--glow)/0.7)]"
                      title="Input — drop wires here"
                    />
                  ) : (
                    <div
                      className={`absolute -right-[7px] top-[14px] w-[14px] h-[14px] rounded-full border-2 border-surface-1 cursor-crosshair hover:scale-125 transition-transform ${connected(n.id) ? '' : 'animate-pulse'}`}
                      style={{ background: meta!.wire, boxShadow: `0 0 8px ${meta!.wire}` }}
                      onPointerDown={e => startConnect(e, n)}
                      title="Drag to the agent to connect"
                    />
                  )}
                </div>
              )
            })}
          </div>

          {/* zoom controls */}
          <div className="absolute bottom-3 right-3 flex flex-col gap-1 z-10">
            {[['+', () => zoomBy(1.2)], ['−', () => zoomBy(0.84)], ['⌂', () => setViewport({ x: 0, y: 0, k: 1 })]].map(([label, fn]: any) => (
              <button key={label} onClick={fn}
                className="w-7 h-7 rounded-md bg-surface-2 border border-white/10 text-gray-400 hover:text-gray-200 hover:border-white/20 transition text-sm flex items-center justify-center">
                {label}
              </button>
            ))}
          </div>

          {/* hints */}
          <div className="absolute bottom-3 left-3 text-[10px] text-gray-700 select-none pointer-events-none space-y-0.5">
            <div>drag nodes from the palette · wires connect into the <span className="text-emerald-500/80">agent</span></div>
            <div>drag a port ● to rewire · click a wire to disconnect · scroll to zoom · drag canvas to pan</div>
          </div>
          <div className="absolute top-3 right-3 text-[10px] text-gray-700 font-mono select-none pointer-events-none">
            {Math.round(viewport.k * 100)}%
          </div>
        </div>
      </div>
    </div>
  )
}
