'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { API_URL } from './config'
import Library from './components/Library'
import type { LibItem } from './components/Library'
import Market from './components/Market'
import Builder from './components/Builder'
import CreditsSidebar, { CreditsInfo } from './components/Credits'
import Select from './components/Select'
import Tools from './components/Tools'
import Arena from './components/Arena'
import MemoryPanel from './components/Memory'
import { ThemePicker, useTheme } from './components/Theme'
import { loadLocalIdentity, getOrCreateLocalIdentity, clearLocalIdentity, localSign } from './lib/localWallet'

type ToolSchema = { description: string; params: Record<string, any> }
// images: what the user pasted, as data URLs. thumbs are the tiny copies that
// survive persistence — localStorage is shared across modc2 modules, so the
// full-size data never goes in it.
type Message = { role: 'user' | 'agent' | 'system'; text: string; steps?: any[]; live?: boolean; images?: string[]; thumbs?: string[] }
// uid/cid/synced tie a conversation to the server-side store: uid is the
// stable cross-device id, cid the localfs pin, synced whether the server copy
// is current. Anonymous sessions only ever live in localStorage.
type TaskEntry = { id: number; query: string; status: 'running' | 'done' | 'error'; stepCount?: number; messages: Message[]; agent_type?: string; startedAt?: number; finishedAt?: number; uid?: string; cid?: string; synced?: boolean }

const genUid = () => `c-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
type Tab = 'tasks' | 'output' | 'tools' | 'memory' | 'deltas'
// the TOOLS tab answers two questions: what did this run call, and what can
// the agent call at all
type ToolPane = 'trace' | 'registry'

// an image staged in the composer, not yet sent
type Attachment = { id: string; name: string; url: string; thumb: string }

// Shrink a pasted image to something a model call can carry: `url` is the
// full-ish copy that rides to the API, `thumb` the few-KB copy the transcript
// keeps forever.
const MAX_EDGE = 1280
const THUMB_EDGE = 160

const scaleImage = (file: File, edge: number, quality: number): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('read failed'))
    reader.onload = () => {
      const img = new Image()
      img.onerror = () => reject(new Error('decode failed'))
      img.onload = () => {
        const scale = Math.min(1, edge / Math.max(img.width, img.height))
        const w = Math.max(1, Math.round(img.width * scale))
        const h = Math.max(1, Math.round(img.height * scale))
        const canvas = document.createElement('canvas')
        canvas.width = w; canvas.height = h
        const ctx = canvas.getContext('2d')
        if (!ctx) return reject(new Error('no canvas'))
        ctx.drawImage(img, 0, 0, w, h)
        resolve(canvas.toDataURL('image/jpeg', quality))
      }
      img.src = String(reader.result)
    }
    reader.readAsDataURL(file)
  })

const toAttachment = async (file: File): Promise<Attachment> => ({
  id: `img-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
  name: file.name || 'pasted image',
  url: await scaleImage(file, MAX_EDGE, 0.82),
  thumb: await scaleImage(file, THUMB_EDGE, 0.6),
})

// the console is a bottom dock (like a terminal panel): min = compose only,
// normal = resizable transcript, max = fills the workspace
type DockMode = 'min' | 'normal' | 'max'
type SidebarSide = 'left' | 'right'
// the rail holds two lists: the chats you've had and the agents you can run as
type RailPane = 'chats' | 'agents'

type FileEntry = { path: string; content: string; action: 'read' | 'created' | 'modified' | 'searched' }

// ── Provider key metadata + missing-key detection ───────────────────
// Shared by the KeyPanel modal and the inline "key needed" banner so the
// console can turn a raw "No X API key found" error into a one-click fix.
type ProviderMeta = { label: string; hint: string; keysUrl: string; placeholder: string }
const PROVIDER_META: Record<string, ProviderMeta> = {
  openrouter: { label: 'openrouter', hint: 'openrouter.ai/keys', keysUrl: 'https://openrouter.ai/keys', placeholder: 'sk-or-v1-…' },
  venice: { label: 'venice', hint: 'venice.ai → settings → API', keysUrl: 'https://venice.ai/settings/api', placeholder: 'venice API key…' },
}

// Sniff a task/system error for a "missing API key" condition and, if so,
// figure out which provider it's complaining about. Returns null otherwise.
const detectKeyError = (text?: string): string | null => {
  if (!text) return null
  const t = text.toLowerCase()
  const keyish = /no\s+\w+\s+api key found|api[_ ]?key|add_key\(\)|set\s+\w+_api_key/.test(t)
  if (!keyish) return null
  for (const p of Object.keys(PROVIDER_META)) {
    if (t.includes(p) || t.includes(`${p.toUpperCase()}_API_KEY`.toLowerCase())) return p
  }
  return 'openrouter'
}

// ── Agent Types ─────────────────────────────────────────────────────

// owner_source: 'item' = an address created it, 'host' = nobody did, so the
// module owner (the host) owns it and can edit/remove it
type OwnerSource = 'item' | 'host' | null
type Owned = { owner?: string | null; owner_source?: OwnerSource }

// harness: set -> the run isn't this module's loop at all, it's handed to an
// agent CLI installed on the host (claude code, codex) — owner only
type AgentOption = Owned & { value: string; label: string; icon: string; description?: string; builtin?: boolean; harness?: string | null }

const DEFAULT_AGENTS: AgentOption[] = [
  { value: "default", label: "Default", icon: ">_", builtin: true },
  { value: "architect", label: "Architect", icon: "△", builtin: true },
  { value: "reviewer", label: "Reviewer", icon: "◉", builtin: true },
  { value: "debugger", label: "Debugger", icon: "⬡", builtin: true },
  { value: "builder", label: "Builder", icon: "◆", builtin: true },
  { value: "refactorer", label: "Refactorer", icon: "⟳", builtin: true },
  { value: "claude-code", label: "Claude Code", icon: "⬡", builtin: true, harness: "claude" },
  { value: "codex", label: "Codex", icon: "◇", builtin: true, harness: "codex" },
]

// ── Sign-in (mod protocol-auth token) ───────────────────────────────
// The API verifies this statelessly via the shared auth mod: base64url of
// { data, time, key, signature } where signature = personal_sign of the
// compact JSON {"data":…,"time":…}. Identity = the recovered signer.

// local: signed by a keypair generated in this browser (no wallet extension)
type AuthInfo = { address: string; token: string; isOwner: boolean; local?: boolean }

const AUTH_KEY = 'agent_auth'
const TOKEN_TTL_MS = 23 * 3600 * 1000 // server max_age is 24h — refresh before that

const eth = () => (typeof window !== 'undefined' ? (window as any).ethereum : undefined)

const b64url = (obj: unknown): string => {
  const s = JSON.stringify(obj)
  const b64 = btoa(unescape(encodeURIComponent(s)))
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

// a restored token past the server's freshness window would just 401 —
// drop it up front so the UI shows "sign in" instead of failing requests
const tokenFresh = (token: string | null | undefined): boolean => {
  if (!token) return false
  try {
    const b64 = token.replace(/-/g, '+').replace(/_/g, '/')
    const env = JSON.parse(decodeURIComponent(escape(atob(b64))))
    return Date.now() - Number(env.time) * 1000 < TOKEN_TTL_MS
  } catch { return false }
}

const shortAddr = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

// a run registered in the server-side task registry (GET /tasks)
type ServerTask = {
  id: string; query: string; agent_type: string; provider?: string; model?: string | null
  user?: string | null; status: 'running' | 'done' | 'error'; steps: number
  tool?: string | null; started_at: number; finished_at?: number | null
  summary?: string | null; chain?: boolean
  images?: number            // attachments the run carried — previews are a separate fetch
}

// a prompt from the library, selectable as an agent's system prompt
type LibPrompt = Owned & { id: string; name: string; description: string; body?: string; tags: string[] }

// ── Personas: agents and library prompts, one list ──────────────────
// Both answer the same question — "what prompt does this run use?" — so the
// console treats them as one selectable thing. An agent is a persona with its
// own tools/model; a library prompt is a persona that overrides the goal.
type Persona = Owned & {
  key: string                 // 'agent:dev' | 'prompt:p-1f2e'
  kind: 'agent' | 'prompt'
  id: string                  // agent slug or prompt id
  label: string
  icon: string
  description?: string
  builtin?: boolean
  harness?: string | null     // agent runs on an external CLI, not our loop
  prompt?: LibPrompt          // set for kind 'prompt'
}

// a memory note from the library, selectable as run context
type MemNote = { id: string; name: string; content: string; tags: string[] }

// balance + vault info for a provider API key
type KeyBalance = {
  provider: string; configured: boolean; key: string | null; supported?: boolean
  encrypted?: boolean; unlocked?: boolean; hint?: string | null; source?: string | null
  remembered?: boolean; remember_expires?: number | null
  balance?: number; total_credits?: number; total_usage?: number
  balances?: Record<string, number>; error?: string
}

export default function Home() {
  // top-level view: the agent console, the visual agent builder, the arena
  // board, the library market, or the tasks page
  const [view, setView] = useState<'console' | 'builder' | 'arena' | 'library' | 'tasks'>('console')
  // the look: palette + skin, persisted and applied to <html>
  const [theme, setTheme] = useTheme()
  const [query, setQuery] = useState('')
  const [toolSchemas, setToolSchemas] = useState<Record<string, ToolSchema>>({})
  const [loading, setLoading] = useState(false)
  const [tasks, setTasks] = useState<TaskEntry[]>([])
  const [selectedTask, setSelectedTask] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<Tab>('output')
  const [toolPane, setToolPane] = useState<ToolPane>('trace')
  // {total, custom} from /tools — feeds the header's tool count
  const [toolCounts, setToolCounts] = useState<{ total: number; custom: number } | null>(null)
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({})
  const [composeFocused, setComposeFocused] = useState(false)
  // images staged in the composer + the group open in the viewer
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [attachErr, setAttachErr] = useState<string | null>(null)
  // the viewer holds the whole group it was opened from, so arrow keys can
  // step through the images of one message instead of just the one clicked
  const [lightbox, setLightbox] = useState<{ srcs: string[]; i: number } | null>(null)
  const openLightbox = (srcs: string[], i = 0) => setLightbox({ srcs, i })
  const stepLightbox = (d: number) =>
    setLightbox(l => l && { ...l, i: (l.i + d + l.srcs.length) % l.srcs.length })
  const fileRef = useRef<HTMLInputElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  let taskId = useRef(0)
  // abort handle for the in-flight run (Stop button)
  const abortRef = useRef<AbortController | null>(null)
  // 1s ticker so the elapsed-time display updates while a task runs
  const [, setClockTick] = useState(0)

  // agent state
  const [agentType, setAgentType] = useState<string>('default')
  // where a run lands when you haven't picked — the server decides per caller
  // (Claude Code for the host, the native loop for guests), see fetchAgents
  const [defaultAgent, setDefaultAgent] = useState<string>('default')
  const [agentOptions, setAgentOptions] = useState<AgentOption[]>(DEFAULT_AGENTS)
  // agent to preload on the visual builder canvas (null = fresh canvas)
  const [builderAgent, setBuilderAgent] = useState<string | null>(null)

  // persona picker: run with a library prompt as system prompt + memory notes as context
  const [libPrompts, setLibPrompts] = useState<LibPrompt[]>([])
  const [memNotes, setMemNotes] = useState<MemNote[]>([])
  const [promptSel, setPromptSel] = useState<LibPrompt | null>(null)
  const [memSel, setMemSel] = useState<string[]>([])
  // tool documents installed from Discover, attached to the run as instructions
  const [toolSel, setToolSel] = useState<string[]>([])
  const [showPicker, setShowPicker] = useState(false)
  const [pickerTab, setPickerTab] = useState<'prompts' | 'memory'>('prompts')
  const [pickerSearch, setPickerSearch] = useState('')
  // last refusal from an owner-gated edit/delete, shown in the picker footer
  const [personaErr, setPersonaErr] = useState<string | null>(null)

  // api key + balance — keys are entered from the Builder (model node), not the console
  const [balance, setBalance] = useState<KeyBalance | null>(null)
  const [showKeyPanel, setShowKeyPanel] = useState(false)
  const [keyPanelProvider, setKeyPanelProvider] = useState<string | null>(null)
  // bumped after any key save/unlock so the Builder refreshes provider key state
  const [keyVersion, setKeyVersion] = useState(0)

  // provider + model selection
  type ProviderInfo = { key: string; models: string[]; default_model: string; configured?: boolean; encrypted?: boolean; unlocked?: boolean }
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [provider, setProvider] = useState<string>('openrouter')
  const [model, setModel] = useState<string>('')
  // the host — the module owner. null until the API answers ('' = no owner
  // configured, which makes every visitor the host)
  const [owner, setOwner] = useState<string | null>(null)

  // sign-in (wallet personal_sign → protocol-auth token)
  const [auth, setAuth] = useState<AuthInfo | null>(null)
  const [authBusy, setAuthBusy] = useState(false)
  const [authErr, setAuthErr] = useState<string | null>(null)
  const [showUserMenu, setShowUserMenu] = useState(false)

  // credits — prepaid USDT/USDC balance spent on the module's public key
  const [creditsInfo, setCreditsInfo] = useState<CreditsInfo | null>(null)
  const [showCredits, setShowCredits] = useState(false)
  const [spendCredits, setSpendCredits] = useState(true)

  // server-side task registry (background runs)
  const [serverTasks, setServerTasks] = useState<ServerTask[]>([])
  const [taskFilter, setTaskFilter] = useState<'all' | 'running' | 'done' | 'error'>('all')
  const [taskSearch, setTaskSearch] = useState('')
  const [expandedServerTasks, setExpandedServerTasks] = useState<Record<string, boolean>>({})
  // attachment previews per task id — base64, so they're fetched only when a
  // task is opened and never ride along with the 4s registry poll
  const [taskImages, setTaskImages] = useState<Record<string, string[]>>({})

  const providerModels = providers.find(p => p.key === provider)?.models || []

  const onProviderChange = (p: string) => {
    setProvider(p)
    localStorage.setItem('agent_provider', p)
    const def = providers.find(x => x.key === p)?.default_model || ''
    setModel(def)
    localStorage.setItem('agent_model', def)
  }

  // workspace layout: chats + agents in the side rail, the market on the other
  // side, the console docked at the bottom
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [sidebarSide, setSidebarSide] = useState<SidebarSide>('left')
  const [railPane, setRailPane] = useState<RailPane>('chats')
  const [dock, setDock] = useState<DockMode>('normal')
  const [dockHeight, setDockHeight] = useState(400)
  const [chatSearch, setChatSearch] = useState('')
  const [agentSearch, setAgentSearch] = useState('')

  // the market rail — the library, docked opposite the chats rail
  const [marketOpen, setMarketOpen] = useState(true)
  const [marketWidth, setMarketWidth] = useState(300)
  // bumped whenever the console changes the library, so the market refetches
  const [libVersion, setLibVersion] = useState(0)
  const marketSide: SidebarSide = sidebarSide === 'left' ? 'right' : 'left'

  // phone layout: the rail becomes a slide-over drawer, the dock header wraps
  const [isMobile, setIsMobile] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const apply = () => setIsMobile(mq.matches)
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [])

  // the prompt is docked at the bottom by default, but can pop out into a
  // floating panel — drag it anywhere, resize its width, dock it back
  const [promptFloat, setPromptFloat] = useState(false)
  const [promptPos, setPromptPos] = useState({ x: 0, y: 0 })
  const [promptW, setPromptW] = useState(560)

  // keep the floating prompt on screen — reachable header, reachable handle
  const clampPromptPos = (p: { x: number; y: number }, w: number) => ({
    x: Math.max(8, Math.min(window.innerWidth - Math.min(w, window.innerWidth - 16) - 8, p.x)),
    y: Math.max(52, Math.min(window.innerHeight - 110, p.y)),
  })

  // file viewer state
  const [viewingFile, setViewingFile] = useState<FileEntry | null>(null)

  // draggable rail width / dock height
  const [sidebarWidth, setSidebarWidth] = useState(280)
  const isDragging = useRef(false)
  const dragStartX = useRef(0)
  const dragStartWidth = useRef(280)
  const dragStartY = useRef(0)
  const dragStartHeight = useRef(400)

  // restore the workspace geometry the user last dragged into place
  useEffect(() => {
    try {
      const w = Number(localStorage.getItem('agent_rail_w'))
      if (w >= 200) setSidebarWidth(w)
      const h = Number(localStorage.getItem('agent_dock_h'))
      if (h >= 120) setDockHeight(h)
      const d = localStorage.getItem('agent_dock') as DockMode | null
      if (d === 'min' || d === 'normal' || d === 'max') setDock(d)
      setSidebarCollapsed(localStorage.getItem('agent_rail_closed') === '1')
      const pane = localStorage.getItem('agent_rail_pane')
      if (pane === 'chats' || pane === 'agents') setRailPane(pane)
      const mw = Number(localStorage.getItem('agent_market_w'))
      if (mw >= 220) setMarketWidth(mw)
      // open on a first visit — collapsed only if it was collapsed on purpose.
      // Three columns need room: under 1100px the workspace between the rails
      // shrinks to a gutter, so a first visit in a narrow window starts with
      // the market as a strip (and under 900px the chats rail too).
      const marketPref = localStorage.getItem('agent_market_open')
      const wide = window.innerWidth
      setMarketOpen(marketPref ? marketPref !== '0' : wide >= 1100)
      if (!localStorage.getItem('agent_rail_closed') && wide < 900) setSidebarCollapsed(true)
      // floating prompt: width, position, and whether it was left undocked
      const pw = Number(localStorage.getItem('agent_prompt_w'))
      const pwv = pw >= 300 ? pw : 560
      if (pw >= 300) setPromptW(pw)
      const pp = localStorage.getItem('agent_prompt_pos')
      if (pp) setPromptPos(clampPromptPos(JSON.parse(pp), pwv))
      if (localStorage.getItem('agent_prompt_float') === '1' && pp) setPromptFloat(true)
      if (window.matchMedia('(max-width: 767px)').matches) {
        // phones: the rail is a drawer (start closed) and, unless the user
        // picked a dock size, the console fills the screen — chat-app feel,
        // prompt at the very bottom
        setSidebarCollapsed(true)
        setMarketOpen(false)
        if (!d) setDock('max')
      }
    } catch {}
  }, [])
  const setDockPersist = (d: DockMode) => {
    setDock(d)
    try { localStorage.setItem('agent_dock', d) } catch {}
  }

  // rail drag resize (horizontal) — pointer events so touch drags work too
  const onDragStart = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    isDragging.current = true
    dragStartX.current = e.clientX
    dragStartWidth.current = sidebarWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    let last = sidebarWidth
    const onPointerMove = (ev: PointerEvent) => {
      if (!isDragging.current) return
      const delta = sidebarSide === 'left'
        ? ev.clientX - dragStartX.current
        : dragStartX.current - ev.clientX
      const maxWidth = Math.floor(window.innerWidth * 0.5)
      last = Math.max(200, Math.min(maxWidth, dragStartWidth.current + delta))
      setSidebarWidth(last)
    }
    const onPointerUp = () => {
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      // the width we ended on, not the one we started from
      try { localStorage.setItem('agent_rail_w', String(last)) } catch {}
      document.removeEventListener('pointermove', onPointerMove)
      document.removeEventListener('pointerup', onPointerUp)
    }
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', onPointerUp)
  }, [sidebarWidth, sidebarSide])

  // market rail drag resize — same handle, mirrored, since it sits on the
  // opposite edge of the workspace
  const onMarketDragStart = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = marketWidth
    let last = startW
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onPointerMove = (ev: PointerEvent) => {
      const delta = marketSide === 'left' ? ev.clientX - startX : startX - ev.clientX
      const maxWidth = Math.floor(window.innerWidth * 0.5)
      last = Math.max(220, Math.min(maxWidth, startW + delta))
      setMarketWidth(last)
    }
    const onPointerUp = () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      try { localStorage.setItem('agent_market_w', String(last)) } catch {}
      document.removeEventListener('pointermove', onPointerMove)
      document.removeEventListener('pointerup', onPointerUp)
    }
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', onPointerUp)
  }, [marketWidth, marketSide])

  // dock drag resize (vertical — drag the console's top edge)
  const onDockDragStart = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    isDragging.current = true
    dragStartY.current = e.clientY
    dragStartHeight.current = dockHeight
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'

    let last = dockHeight
    const onPointerMove = (ev: PointerEvent) => {
      if (!isDragging.current) return
      const maxHeight = Math.floor(window.innerHeight * 0.85)
      last = Math.max(140, Math.min(maxHeight, dragStartHeight.current + (dragStartY.current - ev.clientY)))
      setDockHeight(last)
    }
    const onPointerUp = () => {
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      try { localStorage.setItem('agent_dock_h', String(last)) } catch {}
      document.removeEventListener('pointermove', onPointerMove)
      document.removeEventListener('pointerup', onPointerUp)
    }
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', onPointerUp)
  }, [dockHeight])

  // ── floating prompt: undock, drag, resize ─────────────────────────
  const togglePromptFloat = useCallback(() => {
    const next = !promptFloat
    if (next) {
      const w = Math.min(promptW, window.innerWidth - 16)
      let saved: { x: number; y: number } | null = null
      try { const raw = localStorage.getItem('agent_prompt_pos'); if (raw) saved = JSON.parse(raw) } catch {}
      setPromptPos(clampPromptPos(saved || { x: (window.innerWidth - w) / 2, y: window.innerHeight - 220 }, w))
    }
    setPromptFloat(next)
    try { localStorage.setItem('agent_prompt_float', next ? '1' : '0') } catch {}
    setTimeout(() => inputRef.current?.focus(), 40)
  }, [promptFloat, promptW])

  // drag the floating prompt by its header bar (mouse or touch)
  const onPromptDragStart = useCallback((e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('button')) return // the dock-back button still clicks
    e.preventDefault()
    const sx = e.clientX, sy = e.clientY
    const bx = promptPos.x, by = promptPos.y
    let last = promptPos
    const onPointerMove = (ev: PointerEvent) => {
      last = clampPromptPos({ x: bx + ev.clientX - sx, y: by + ev.clientY - sy }, promptW)
      setPromptPos(last)
    }
    const onPointerUp = () => {
      try { localStorage.setItem('agent_prompt_pos', JSON.stringify(last)) } catch {}
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
    }
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
  }, [promptPos, promptW])

  // adjust the floating prompt's width from its right edge
  const onPromptResizeStart = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const sx = e.clientX
    const bw = promptW
    let last = bw
    const onPointerMove = (ev: PointerEvent) => {
      last = Math.max(300, Math.min(window.innerWidth - 16, bw + ev.clientX - sx))
      setPromptW(last)
      setPromptPos(p => clampPromptPos(p, last))
    }
    const onPointerUp = () => {
      try { localStorage.setItem('agent_prompt_w', String(last)) } catch {}
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
    }
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
  }, [promptW])

  // keep the floating prompt reachable when the window shrinks
  useEffect(() => {
    if (!promptFloat) return
    const onResize = () => setPromptPos(p => clampPromptPos(p, promptW))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [promptFloat, promptW])

  // console maximize toggle (dock fills the workspace)
  const toggleDockMax = useCallback(() => {
    setDockPersist(dock === 'max' ? 'normal' : 'max')
  }, [dock])

  // keyboard shortcut: Escape closes the prompt picker, else leaves a maximized console
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (showPicker) { setShowPicker(false); return }
      if (dock === 'max') setDockPersist('normal')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dock, showPicker])

  const [apiStatus, setApiStatus] = useState<'ok' | 'down' | 'loading'>('loading')

  // token: the server answers with the default agent for THIS caller — the
  // host gets Claude Code, a guest gets the native loop it's allowed to run
  const fetchAgents = useCallback((token?: string) => {
    const q = token ? `?key=${encodeURIComponent(token)}` : ''
    fetch(`${API_URL}/agents${q}`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => {
        // a pick of your own always wins — the default is only where you land
        if (d.default) {
          setDefaultAgent(d.default)
          setAgentType(() => localStorage.getItem('agent_type') || d.default)
        }
        if (d.schemas && typeof d.schemas === 'object') {
          const fetched: AgentOption[] = Object.entries(d.schemas).map(([key, val]: [string, any]) => ({
            value: key,
            label: val.name || key.charAt(0).toUpperCase() + key.slice(1),
            icon: val.icon || '>_',
            description: val.description || '',
            builtin: !!val.builtin,
            harness: val.harness || null,
            owner: val.owner || null,
            owner_source: (val.owner_source || null) as OwnerSource,
          }))
          if (fetched.length > 0) setAgentOptions(fetched)
          if (d.host) setOwner((o: string | null) => o === null ? d.host : o)
        }
      })
      .catch(() => {})
  }, [])

  // signing in changes who the caller is, and so which agent is the default:
  // the host lands on Claude Code, a guest on the native loop
  useEffect(() => { fetchAgents(auth?.token) }, [auth?.token, fetchAgents])

  // prompts + memory notes for the persona picker
  const fetchLibrary = useCallback(() => {
    fetch(`${API_URL}/library?kind=prompt`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => setLibPrompts((d.items || []).filter((i: any) => i.kind === 'prompt')))
      .catch(() => {})
    fetch(`${API_URL}/memory`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => setMemNotes(d.memory || []))
      .catch(() => {})
  }, [])

  // the market rail keeps its own copy of the catalogue, so anything the
  // console changes has to tell it to refetch
  const libChanged = useCallback(() => setLibVersion(v => v + 1), [])

  // the library page can create and delete anything — refetch on the way back
  const prevView = useRef(view)
  useEffect(() => {
    if (prevView.current !== view && view === 'console') { libChanged(); fetchLibrary() }
    prevView.current = view
  }, [view, libChanged, fetchLibrary])

  // remove an agent or a library prompt — the server only lets its owner or
  // the host through, so a refusal comes back as a 403 to surface
  const deletePersona = async (p: Persona) => {
    const what = p.kind === 'agent' ? 'agent' : 'prompt'
    if (!confirm(`Delete ${what} "${p.label}"? This can't be undone.`)) return
    const q = auth?.token ? `?key=${encodeURIComponent(auth.token)}` : ''
    const route = p.kind === 'agent'
      ? `agents/${encodeURIComponent(p.id)}`
      : `prompts/${encodeURIComponent(p.id)}`
    try {
      const r = await fetch(`${API_URL}/${route}${q}`, { method: 'DELETE' }).then(x => x.json())
      if (r?.error) { setPersonaErr(r.error); return }
      setPersonaErr(null)
      libChanged()
      if (p.kind === 'agent') {
        if (agentType === p.id) selectAgent(defaultAgent)
        fetchAgents(auth?.token)
      } else {
        if (promptSel?.id === p.id) selectPrompt(null)
        fetchLibrary()
      }
    } catch (e: any) {
      setPersonaErr(e?.message || 'delete failed')
    }
  }

  // jump to the visual builder canvas, optionally preloading an agent to edit
  const openBuilder = (name?: string | null) => {
    setBuilderAgent(name || null)
    setShowPicker(false)
    setView('builder')
  }

  const selectAgent = (v: string) => {
    setAgentType(v)
    localStorage.setItem('agent_type', v)
    // an agent and a library prompt are exclusive — the prompt would override the agent's goal
    setPromptSel(null)
    try { localStorage.removeItem('agent_prompt_sel') } catch {}
  }

  const selectPrompt = (p: LibPrompt | null) => {
    setPromptSel(p)
    try {
      if (p) localStorage.setItem('agent_prompt_sel', JSON.stringify({
        id: p.id, name: p.name, description: p.description || '',
        body: (p.body || '').slice(0, 8000), tags: p.tags || [],
        owner: p.owner || null, owner_source: p.owner_source || null,
      }))
      else localStorage.removeItem('agent_prompt_sel')
    } catch {} // shared-origin quota — selection just isn't persisted
  }

  // ── one persona list: agents + library prompts ────────────────────
  // Yours first, then the rest — your own work never hides off-screen.
  const personas: Persona[] = [
    ...agentOptions.map(a => ({
      key: `agent:${a.value}`, kind: 'agent' as const, id: a.value,
      label: a.label, icon: a.icon, description: a.description,
      builtin: a.builtin, harness: a.harness,
      owner: a.owner, owner_source: a.owner_source,
    })),
    ...libPrompts.map(p => ({
      key: `prompt:${p.id}`, kind: 'prompt' as const, id: p.id,
      label: p.name, icon: '¶', description: p.description || p.body?.slice(0, 90),
      builtin: false, owner: p.owner, owner_source: p.owner_source, prompt: p,
    })),
  ]

  const activePersonaKey = promptSel ? `prompt:${promptSel.id}` : `agent:${agentType}`
  const activePersona = personas.find(p => p.key === activePersonaKey)

  const selectPersona = (p: Persona) => {
    if (p.kind === 'agent') selectAgent(p.id)
    else selectPrompt(p.prompt || null)
  }

  // ── who may administer a persona ──────────────────────────────────
  // You are the host until something says otherwise: signed in, we trust the
  // answer the server already gave us; signed out, we assume yes and let the
  // API be the gate (a refusal shows up in the picker footer). The host owns
  // anything nobody else does — including the shipped agents.
  const isHost = auth ? auth.isOwner || owner === '' : true
  const ownedByMe = (p: Owned) =>
    !!auth && p.owner_source === 'item' &&
    (p.owner || '').toLowerCase() === auth.address.toLowerCase()
  const canManage = (p: Persona) => isHost || ownedByMe(p)
  // '' while the API hasn't answered yet — no owner claim beats a wrong one
  const ownerLabel = (p: Owned) =>
    p.owner_source === 'host' ? 'host' : p.owner ? shortAddr(p.owner) : ''
  const ownerTitle = (p: Owned) =>
    p.owner_source === 'host'
      ? `no owner recorded — the host${p.owner ? ` (${p.owner})` : ''} owns it`
      : p.owner ? `owned by ${p.owner}` : 'no owner'

  const editPersona = (p: Persona) => {
    if (p.kind === 'agent') openBuilder(p.id)
    else { setShowPicker(false); setView('library') }
  }

  // one row for an agent or a library prompt — the dropdown picker and the
  // rail's AGENTS pane show the same thing, so they share this
  const personaRow = (p: Persona, onPicked?: () => void) => {
    const active = p.key === activePersonaKey
    const mine = ownedByMe(p)
    return (
      <div key={p.key} role="button" tabIndex={0}
        onClick={() => { selectPersona(p); onPicked?.() }}
        onKeyDown={e => { if (e.key === 'Enter') { selectPersona(p); onPicked?.() } }}
        className={`w-full text-left px-2.5 py-2 rounded-md text-sm transition cursor-pointer group ${
          active
            ? p.kind === 'prompt'
              ? 'bg-amber-400/10 border border-amber-400/30 text-gray-200'
              : 'bg-emerald-500/10 border border-emerald-500/20 text-gray-200'
            : 'border border-transparent text-gray-400 hover:bg-white/[0.04] hover:text-gray-200'
        }`}>
        <div className="flex items-center gap-2">
          <span className={`w-5 text-center shrink-0 ${p.kind === 'prompt' ? 'text-amber-300/80' : ''}`}>{p.icon}</span>
          <span className="truncate">{p.label}</span>
          <span className={`text-[9px] px-1 py-0.5 rounded shrink-0 ${
            p.kind === 'prompt' ? 'bg-amber-400/10 text-amber-300/90' : 'bg-white/[0.06] text-gray-500'
          }`}>{p.kind}</span>
          {p.harness && (
            <span className="text-[9px] px-1 py-0.5 rounded shrink-0 bg-violet-400/10 border border-violet-400/25 text-violet-300/90"
              title={`runs on the ${p.harness} CLI installed on this host — host owner only`}>
              {p.harness}
            </span>
          )}
          <span className="ml-auto flex items-center gap-1 shrink-0">
            {canManage(p) && (
              <>
                <button title={`Edit this ${p.kind}`}
                  onClick={e => { e.stopPropagation(); editPersona(p) }}
                  className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-[10px] text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/10 transition">
                  ✎
                </button>
                <button title={p.builtin
                  ? `Delete built-in agent "${p.label}" — host only`
                  : `Delete this ${p.kind}`}
                  onClick={e => { e.stopPropagation(); deletePersona(p) }}
                  className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-[10px] text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition">
                  ✕
                </button>
              </>
            )}
            {active && <span className={`text-[10px] ${p.kind === 'prompt' ? 'text-amber-300' : 'text-emerald-300'}`}>active</span>}
          </span>
        </div>
        <div className="flex items-center gap-1.5 mt-0.5 pl-7">
          {/* every persona shows who owns it — unowned means the host does */}
          <span className={`text-[9px] font-mono shrink-0 ${
            mine ? 'text-emerald-300/80' : p.owner_source === 'host' ? 'text-gray-600' : 'text-violet-300/80'
          }`} title={ownerTitle(p)}>
            {mine ? 'you' : ownerLabel(p)}
          </span>
          {p.description && (
            <span className="text-[10px] text-gray-600 truncate">· {p.description}</span>
          )}
        </div>
      </div>
    )
  }

  const toggleNote = (id: string) => {
    setMemSel(sel => {
      const next = sel.includes(id) ? sel.filter(x => x !== id) : [...sel, id]
      try { localStorage.setItem('agent_mem_sel', JSON.stringify(next)) } catch {}
      return next
    })
  }

  const fetchBalance = useCallback(() => {
    fetch(`${API_URL}/balance?provider=${encodeURIComponent(provider)}`, { signal: AbortSignal.timeout(15000) })
      .then(r => r.json())
      .then(d => setBalance(d))
      .catch(() => {})
  }, [provider])

  useEffect(() => { fetchBalance() }, [fetchBalance])

  // ── credits (top up USDT/USDC, spend on the public key) ──────────
  const fetchCredits = useCallback(() => {
    const q = auth?.token ? `?key=${encodeURIComponent(auth.token)}` : ''
    fetch(`${API_URL}/credits${q}`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => { if (d && d.deposit) setCreditsInfo(d) })
      .catch(() => {})
  }, [auth?.token])

  useEffect(() => { fetchCredits() }, [fetchCredits])

  useEffect(() => {
    try {
      const v = localStorage.getItem('agent_spend_credits')
      if (v !== null) setSpendCredits(v === '1')
    } catch {}
  }, [])

  const setSpendCreditsPersist = (v: boolean) => {
    setSpendCredits(v)
    try { localStorage.setItem('agent_spend_credits', v ? '1' : '0') } catch {}
  }

  // ── sign-in ───────────────────────────────────────────────────────
  const persistAuth = (v: AuthInfo | null) => {
    // modc2 modules share one localStorage origin — never let quota crash sign-in
    try {
      if (v) localStorage.setItem(AUTH_KEY, JSON.stringify(v))
      else localStorage.removeItem(AUTH_KEY)
    } catch {}
  }

  // resolve the role server-side and persist — shared by both sign-in paths
  const finishSignIn = async (address: string, token: string, local: boolean) => {
    let isOwner = false
    try {
      const who = await fetch(`${API_URL}/whoami?key=${encodeURIComponent(token)}`,
        { signal: AbortSignal.timeout(8000) }).then(r => r.json())
      if (who?.error) throw new Error(who.error)
      isOwner = !!who?.is_owner
    } catch {} // API offline — still sign in locally, role resolves on next load
    const next = { address, token, isOwner, local }
    setAuth(next)
    persistAuth(next)
    setShowUserMenu(false)
  }

  const signInWallet = async () => {
    const provider = eth()
    if (!provider) {
      setAuthErr('No wallet found — install MetaMask, or use a local wallet')
      return
    }
    setAuthBusy(true)
    setAuthErr(null)
    try {
      const accounts = await provider.request({ method: 'eth_requestAccounts' })
      const address = String(accounts?.[0] || '').toLowerCase()
      if (!address) throw new Error('no account selected')
      const data = { scope: 'agent' }
      const time = (Date.now() / 1000).toString()
      // sig payload must match the server's sig_data: compact {"data":…,"time":…}
      const signature: string = await provider.request({
        method: 'personal_sign',
        params: [JSON.stringify({ data, time }), address],
      })
      await finishSignIn(address, b64url({ data, time, key: address, signature }), false)
    } catch (e: any) {
      if (e?.code !== 4001) setAuthErr(e?.message || 'sign-in failed') // 4001 = user rejected
    }
    setAuthBusy(false)
  }

  // sign in with a keypair generated and kept in this browser — no extension
  // needed. Same EIP-191 signature a wallet would produce, so the server
  // verifies it unchanged; the address is a device-local pseudonym.
  const signInLocal = async () => {
    setAuthBusy(true)
    setAuthErr(null)
    try {
      const id = getOrCreateLocalIdentity()
      const data = { scope: 'agent' }
      const time = (Date.now() / 1000).toString()
      const signature = await localSign(id, JSON.stringify({ data, time }))
      await finishSignIn(id.address, b64url({ data, time, key: id.address, signature }), true)
    } catch (e: any) {
      setAuthErr(e?.message || 'local sign-in failed')
    }
    setAuthBusy(false)
  }

  // default entry point (components' "sign in" buttons): wallet when one is
  // installed, otherwise fall back to the local browser wallet
  const signIn = () => (eth() ? signInWallet() : signInLocal())

  const signOut = () => {
    setAuth(null)
    setShowUserMenu(false)
    persistAuth(null)
  }

  // restore session; a stale token is dropped so the UI shows "sign in"
  useEffect(() => {
    try {
      const raw = localStorage.getItem(AUTH_KEY)
      if (!raw) return
      const v: AuthInfo = JSON.parse(raw)
      if (!v?.address) { persistAuth(null); return }
      if (!tokenFresh(v.token)) {
        // a local session re-signs silently — the key never left this browser
        if (v.local && loadLocalIdentity()) void signInLocal()
        else persistAuth(null)
        return
      }
      setAuth(v)
      // re-check the role server-side (owner may have changed)
      fetch(`${API_URL}/whoami?key=${encodeURIComponent(v.token)}`, { signal: AbortSignal.timeout(8000) })
        .then(r => r.json())
        .then(who => {
          if (who?.signed_in) setAuth(a => a ? { ...a, isOwner: !!who.is_owner } : a)
        })
        .catch(() => {})
    } catch {}
  }, [])

  // ── background tasks (server-side registry) ───────────────────────
  const fetchServerTasks = useCallback(() => {
    fetch(`${API_URL}/tasks?limit=100`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => setServerTasks(Array.isArray(d.tasks) ? d.tasks : []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchServerTasks()
    const iv = setInterval(fetchServerTasks, 4000)
    return () => clearInterval(iv)
  }, [fetchServerTasks])

  // the registry is also the API heartbeat — one call for the tool schemas,
  // the tab counts and the online light
  const loadTools = useCallback(() => {
    fetch(`${API_URL}/tools`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => {
        const list: any[] = d.tools || []
        setToolSchemas(Object.fromEntries(list.map(t => [t.name, t])))
        setToolCounts({ total: list.length,
                        custom: list.filter(t => t.kind === 'custom').length })
        setApiStatus('ok')
      })
      .catch(() => setApiStatus('down'))
  }, [])

  // an API that went down usually comes back — a pm2 restart is a few seconds.
  // Keep beating while it's out so the light and the Run button recover on
  // their own; before this, a bounce during page load meant a hard reload.
  useEffect(() => {
    if (apiStatus !== 'down') return
    const iv = setInterval(loadTools, 10000)
    return () => clearInterval(iv)
  }, [apiStatus, loadTools])

  useEffect(() => {
    loadTools()
    // providers + models for the selector
    fetch(`${API_URL}/providers`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => {
        const list: ProviderInfo[] = d.providers || []
        setProviders(list)
        const savedP = localStorage.getItem('agent_provider')
        const savedM = localStorage.getItem('agent_model')
        const p = (savedP && list.find(x => x.key === savedP)) ? savedP : (d.default || list[0]?.key || 'openrouter')
        setProvider(p)
        const pd = list.find(x => x.key === p)
        // drop saved models the provider no longer offers (stale slugs 404 on run)
        const validSaved = savedM && (pd?.models || []).includes(savedM) ? savedM : null
        setModel(validSaved || pd?.default_model || '')
        if (!validSaved && savedM) localStorage.removeItem('agent_model')
      })
      .catch(() => {})
    // owner / user info
    fetch(`${API_URL}/owner`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => setOwner(d.owner || ''))
      .catch(() => {})
    const saved = localStorage.getItem('agent_type')
    if (saved) setAgentType(saved)
    // restore persona picker selections (library prompt + memory notes)
    fetchLibrary()
    try {
      const savedPrompt = localStorage.getItem('agent_prompt_sel')
      if (savedPrompt) setPromptSel(JSON.parse(savedPrompt))
      const savedMem = localStorage.getItem('agent_mem_sel')
      if (savedMem) setMemSel(JSON.parse(savedMem))
    } catch {}
    // restore conversations from the last session
    try {
      const rawTasks = localStorage.getItem('agent_tasks_v1')
      if (rawTasks) {
        const savedTasks: TaskEntry[] = JSON.parse(rawTasks)
        // anything that was mid-flight when the page closed is orphaned;
        // pre-uid entries get one so they can sync to the server store
        const restored = savedTasks.map(t => {
          const base = t.uid ? t : { ...t, uid: genUid() }
          return base.status === 'running'
            ? { ...base, status: 'error' as const, messages: [...base.messages, { role: 'system' as const, text: 'Interrupted — page closed while this task was running.' }] }
            : base
        })
        if (restored.length) {
          setTasks(restored)
          setSelectedTask(restored[0].id)
          taskId.current = restored.reduce((mx, t) => Math.max(mx, t.id), 0)
        }
      }
    } catch {}
  }, [])

  // persist conversations (trimmed — modc2 modules share one localStorage origin, keep it small)
  useEffect(() => {
    if (tasks.length === 0) return
    try {
      const clip = (v: any) => typeof v === 'string' && v.length > 4000 ? v.slice(0, 4000) + '\n…[truncated]' : v
      const slim = tasks.slice(0, 20).map(t => ({
        ...t,
        messages: t.messages.map(msg => ({
          ...msg,
          text: clip(msg.text),
          // only the thumbnails survive a reload — full-size data URLs would
          // eat the whole shared quota in a couple of chats
          images: undefined,
          steps: msg.steps?.slice(0, 60).map((s: any) => ({ ...s, result: clip(s.result) })),
        })),
      }))
      localStorage.setItem('agent_tasks_v1', JSON.stringify(slim))
    } catch {} // quota-full is non-fatal: worst case the session just isn't saved
  }, [tasks])

  // ── conversation sync (server-side store, pinned to localfs) ──────
  // Signed-in sessions persist every finished conversation to the API,
  // which pins each one to localfs and returns its CID — history survives
  // browsers, devices, and the shared modc2 localStorage origin.
  const syncBusy = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!auth?.token) return
    const pending = tasks.filter(t =>
      t.uid && t.status !== 'running' && !t.synced && !syncBusy.current.has(t.uid))
    pending.forEach(t => {
      const uid = t.uid!
      syncBusy.current.add(uid)
      fetch(`${API_URL}/conversations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: uid, query: t.query, agent_type: t.agent_type || 'default',
          // thumbnails only — a pinned conversation shouldn't carry megabytes
          // of base64 across the wire
          status: t.status, messages: t.messages.map(msg => ({ ...msg, images: undefined })),
          started: t.startedAt ? t.startedAt / 1000 : undefined,
          key: auth.token,
        }),
        signal: AbortSignal.timeout(20000),
      })
        .then(r => r.json())
        .then(d => {
          if (d?.id) setTasks(ts => ts.map(x => x.uid === uid ? { ...x, synced: true, cid: d.cid } : x))
        })
        .catch(() => {}) // offline — retried on the next tasks change
        .finally(() => syncBusy.current.delete(uid))
    })
  }, [tasks, auth])

  // pull the server-side history once per sign-in and merge (uid-deduped)
  useEffect(() => {
    if (!auth?.token) return
    fetch(`${API_URL}/conversations?key=${encodeURIComponent(auth.token)}`,
      { signal: AbortSignal.timeout(10000) })
      .then(r => r.json())
      .then(d => {
        const convs: any[] = Array.isArray(d?.conversations) ? d.conversations : []
        if (!convs.length) return
        setTasks(ts => {
          const have = new Set(ts.map(t => t.uid).filter(Boolean))
          const merged = [...ts]
          for (const c of convs) {
            if (!c?.id || have.has(c.id)) continue
            merged.push({
              id: ++taskId.current,
              uid: c.id, cid: c.cid, synced: true,
              query: c.query || '(untitled)',
              status: c.status === 'error' ? 'error' as const : 'done' as const,
              messages: Array.isArray(c.messages) ? c.messages : [],
              agent_type: c.agent_type,
              startedAt: c.started ? c.started * 1000 : undefined,
              finishedAt: c.updated ? c.updated * 1000 : undefined,
            })
          }
          return merged
        })
      })
      .catch(() => {})
  }, [auth?.address])

  // tick every second while running so elapsed timers move
  useEffect(() => {
    if (!loading) return
    const iv = setInterval(() => setClockTick(t => t + 1), 1000)
    return () => clearInterval(iv)
  }, [loading])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [tasks, selectedTask])

  // the composer grows with its content (up to ~a third of the screen) —
  // "adjustable" without a second drag handle
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, Math.floor(window.innerHeight * 0.35))}px`
  }, [query, promptFloat])

  const currentTask = tasks.find(t => t.id === selectedTask)
  const currentAgentDef = agentOptions.find(a => a.value === agentType)

  // Extract files touched by a task
  const getTaskFiles = useCallback((task: TaskEntry | undefined): FileEntry[] => {
    if (!task) return []
    const files: FileEntry[] = []
    const seen = new Set<string>()
    for (const msg of task.messages) {
      if (!msg.steps) continue
      for (const step of msg.steps) {
        const path = step.params?.path || step.params?.file_path || ''
        if (!path || seen.has(path + step.tool)) continue
        seen.add(path + step.tool)
        if (['read', 'write', 'edit'].includes(step.tool)) {
          files.push({
            path,
            content: typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2),
            action: step.tool === 'read' ? 'read' : step.tool === 'write' ? 'created' : 'modified',
          })
        }
      }
    }
    return files
  }, [])

  // derive the final display message from a full step list
  const finalizeSteps = (allSteps: any[], apiError?: string) => {
    const responseText = allSteps.filter((s: any) => s.tool === 'response' && s.result).map((s: any) => s.result).join('\n')
    const finishSummary = allSteps.filter((s: any) => s.tool === 'finish').map((s: any) => s.params?.summary).filter(Boolean).join('\n')
    const errorText = allSteps.filter((s: any) => s.tool === 'error' && s.error).map((s: any) => s.error).join('\n')
    const hasError = !!errorText || !!apiError
    // 'invalid' = a step the model malformed and the loop retried — internal noise
    const visibleSteps = allSteps.filter((s: any) => !['response', 'error', 'invalid'].includes(s.tool))
    const displayText = apiError ? `Error: ${apiError}`
      : errorText ? `Error: ${errorText}`
      : responseText || finishSummary || (visibleSteps.length ? `Completed ${visibleSteps.length} step(s)` : 'Done')
    return { hasError, displayText, visibleSteps }
  }

  // consume the SSE step stream; returns true if a terminal (done/error) event arrived
  const streamRun = async (body: any, onEvent: (ev: any) => void, signal: AbortSignal, onFirstEvent: () => void) => {
    const res = await fetch(`${API_URL}/run/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    if (!res.ok || !res.body || !(res.headers.get('content-type') || '').includes('text/event-stream')) {
      throw new Error('stream-unavailable')
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let terminal = false
    let first = true
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const frames = buf.split('\n\n')
      buf = frames.pop() || ''
      for (const frame of frames) {
        const dataLine = frame.split('\n').find(l => l.startsWith('data: '))
        if (!dataLine) continue
        try {
          const ev = JSON.parse(dataLine.slice(6))
          if (first) { first = false; onFirstEvent() }
          onEvent(ev)
          if (ev.type === 'done' || ev.type === 'error') terminal = true
        } catch {}
      }
    }
    return terminal
  }

  const stopRun = () => {
    abortRef.current?.abort()
  }

  const run = async () => {
    const shots = attachments
    if ((!query.trim() && shots.length === 0) || loading) return
    const q = query.trim() || 'look at the attached image(s)'
    setQuery('')
    setAttachments([])
    setAttachErr(null)
    setLoading(true)

    const id = ++taskId.current
    const agentLabel = promptSel ? `¶ ${promptSel.name}` : (currentAgentDef?.label || agentType)
    const userMsg: Message = {
      role: 'user', text: `[${agentLabel}] ${q}`,
      ...(shots.length ? { images: shots.map(a => a.url), thumbs: shots.map(a => a.thumb) } : {}),
    }
    const task: TaskEntry = { id, uid: genUid(), query: q, status: 'running', messages: [userMsg], agent_type: agentType, startedAt: Date.now() }
    setTasks(t => [task, ...t])
    setSelectedTask(id)
    setActiveTab('output')
    setViewingFile(null)
    if (dock === 'min') setDockPersist('normal')

    const patchTask = (patch: Partial<TaskEntry> | ((tk: TaskEntry) => TaskEntry)) => {
      setTasks(t => t.map(tk => tk.id === id
        ? (typeof patch === 'function' ? patch(tk) : { ...tk, ...patch })
        : tk))
    }
    const finishSingle = (allSteps: any[], apiError?: string) => {
      const fin = finalizeSteps(allSteps, apiError)
      const agentMsg: Message = { role: fin.hasError ? 'system' : 'agent', text: fin.displayText, steps: fin.visibleSteps }
      patchTask(tk => ({
        ...tk,
        status: fin.hasError ? 'error' : 'done',
        stepCount: fin.visibleSteps.length,
        messages: [...tk.messages.filter(msg => !msg.live), agentMsg],
        finishedAt: Date.now(),
      }))
    }

    try {
      // check API is reachable before long-running request. One failed ping
      // isn't proof it's gone — a restart bounces the socket for a couple of
      // seconds, and telling someone to `m agent/serve` a server that is
      // already coming back up sends them the wrong way. Beat twice.
      let alive = false
      for (let attempt = 0; attempt < 2 && !alive; attempt++) {
        if (attempt) await new Promise(res => setTimeout(res, 2000))
        try {
          alive = (await fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(3000) })).ok
        } catch {}
      }
      if (!alive) throw new Error(`API not reachable at ${API_URL}. Start with: m agent/serve`)

      const body: any = { query: q }
      if (shots.length) {
        body.images = shots.map(a => a.url)      // vision-capable models only
        body.thumbs = shots.map(a => a.thumb)    // previews for the task registry
      }
      if (auth?.token) body.key = auth.token   // signed identity rides along
      // always named: an omitted agent means "server's default", which would
      // turn an explicit pick of the native agent into a Claude Code run
      if (agentType) body.agent_type = agentType
      if (provider) body.provider = provider
      if (model) body.model = model
      if (promptSel) {
        // prefer the freshly-fetched body — the persisted copy may be clipped
        const bodyText = libPrompts.find(p => p.id === promptSel.id)?.body || promptSel.body
        if (bodyText) body.prompt = bodyText
      }
      if (memSel.length) body.memory_ids = memSel
      if (toolSel.length) body.tool_ids = toolSel
      // guests pick how runs are powered: spend credits on the module's
      // public key, or stay on free models (never charged)
      if (auth && !auth.isOwner && !spendCredits) body.free = true

      const controller = new AbortController()
      abortRef.current = controller

      // ── live streaming path ──
      const liveSteps: any[] = []
      let receivedAny = false

      const onEvent = (ev: any) => {
        if (apiStatus !== 'ok') setApiStatus('ok')
        if (ev.type === 'step' && ev.step) {
          liveSteps.push(ev.step)
          const step = ev.step
          patchTask(tk => {
            const msgs = [...tk.messages]
            let last = msgs[msgs.length - 1]
            if (!last || !last.live) {
              last = { role: 'agent', text: '', steps: [], live: true }
              msgs.push(last)
            } else {
              last = { ...last, steps: [...(last.steps || [])] }
              msgs[msgs.length - 1] = last
            }
            if (step.tool === 'response' && step.result) {
              last.text = last.text ? `${last.text}\n${step.result}` : String(step.result)
            } else if (step.tool === 'finish') {
              last.text = step.params?.summary || last.text
            } else if (step.tool !== 'invalid') {
              last.steps = [...(last.steps || []), step]
            }
            return { ...tk, messages: msgs }
          })
        } else if (ev.type === 'done') {
          finishSingle(liveSteps.length ? liveSteps : (ev.result || []))
          // a billed run just moved the balance — the run cost what it cost
          // on the module's key plus the margin, so re-read it
          if (ev.charged?.charged) fetchCredits()
        } else if (ev.type === 'error') {
          finishSingle(liveSteps, ev.error || 'Unknown error')
        }
      }

      let streamed = false
      try {
        const terminal = await streamRun(body, onEvent, controller.signal, () => { receivedAny = true })
        streamed = true
        if (!terminal) {
          // stream cut mid-run — the server is likely still working, don't silently re-run
          patchTask(tk => ({
            ...tk,
            status: 'error',
            finishedAt: Date.now(),
            messages: [...tk.messages.map(msg => msg.live ? { ...msg, live: undefined } : msg),
              { role: 'system', text: 'Stream interrupted — the agent may still be running on the server.' }],
          }))
        }
      } catch (streamErr: any) {
        if (streamErr?.name === 'AbortError') throw streamErr
        if (receivedAny) throw streamErr
        // stream endpoint unavailable (older API) — fall back to the blocking call
      }

      if (!streamed && !receivedAny) {
        const timeout = setTimeout(() => controller.abort(), 5 * 60 * 1000) // 5 min timeout
        const res = await fetch(`${API_URL}/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })
        clearTimeout(timeout)
        if (apiStatus !== 'ok') setApiStatus('ok')
        const data = await res.json()
        finishSingle(data.result || [], data.error)
      }
    } catch (e: any) {
      const msg = e.name === 'AbortError'
        ? 'Stopped — the agent may still finish on the server.'
        : e.message === 'Load failed' || e.message === 'Failed to fetch'
        ? `API not reachable at ${API_URL}. Start with: m agent/serve`
        : e.message
      const errMsg: Message = { role: 'system', text: `Error: ${msg}` }
      setTasks(t => t.map(tk => tk.id === id
        ? { ...tk, status: 'error', finishedAt: Date.now(), messages: [...tk.messages.filter(msg => !msg.live), errMsg] }
        : tk
      ))
    }
    abortRef.current = null
    setLoading(false)
    fetchBalance()
    fetchCredits()
    fetchServerTasks()
    inputRef.current?.focus()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      run()
    }
  }

  const completeTask = () => {
    if (!selectedTask) return
    setTasks(t => t.map(tk => tk.id === selectedTask ? { ...tk, status: 'done' } : tk))
  }

  // remove a conversation locally AND from the server-side store
  const deleteConversation = (task: TaskEntry) => {
    setTasks(t => t.filter(tk => tk.id !== task.id))
    if (selectedTask === task.id) {
      setSelectedTask(tasks.find(t => t.id !== task.id)?.id || null)
      setActiveTab('output')
      setViewingFile(null)
    }
    if (task.uid && task.synced && auth?.token) {
      fetch(`${API_URL}/conversations/${encodeURIComponent(task.uid)}?key=${encodeURIComponent(auth.token)}`,
        { method: 'DELETE', signal: AbortSignal.timeout(10000) }).catch(() => {})
    }
  }

  const dismissTask = () => {
    const t = tasks.find(tk => tk.id === selectedTask)
    if (t) deleteConversation(t)
  }

  const continueTask = () => {
    if (!currentTask || currentTask.status === 'running') return
    // extract the last agent message output as context
    const agentMsgs = currentTask.messages.filter(m => m.role === 'agent')
    const lastOutput = agentMsgs.length > 0 ? agentMsgs[agentMsgs.length - 1].text : ''
    const prefix = `Continue from previous task "${currentTask.query}":\n\nPrevious output:\n${lastOutput}\n\nNext step: `
    setQuery(prefix)
    inputRef.current?.focus()
  }

  const toggleStep = (idx: number) => {
    setExpandedSteps(s => ({ ...s, [idx]: !s[idx] }))
  }

  // ── composer attachments — paste, drop, or pick an image ───────────
  const addFiles = async (files: File[]) => {
    const imgs = files.filter(f => f.type.startsWith('image/'))
    if (!imgs.length) return
    setAttachErr(null)
    try {
      const next = await Promise.all(imgs.slice(0, 4).map(toAttachment))
      setAttachments(a => [...a, ...next].slice(0, 4))
    } catch {
      setAttachErr("Couldn't read that image")
    }
  }

  const onPasteCompose = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.items || [])
      .filter(it => it.kind === 'file' && it.type.startsWith('image/'))
      .map(it => it.getAsFile())
      .filter((f): f is File => !!f)
    if (files.length) { e.preventDefault(); addFiles(files) }
  }

  // the open viewer owns the keyboard: Esc closes it, arrows walk the group
  useEffect(() => {
    if (!lightbox) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLightbox(null)
      else if (e.key === 'ArrowLeft') stepLightbox(-1)
      else if (e.key === 'ArrowRight') stepLightbox(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [lightbox])

  const statusIcon = (s: TaskEntry['status']) =>
    s === 'running' ? 'animate-spin text-emerald-300' :
    s === 'done' ? 'text-emerald-400' : 'text-red-400'

  const statusDot = (s: TaskEntry['status']) =>
    s === 'running' ? '◐' : s === 'done' ? '●' : '✕'

  // elapsed / duration label for a task ("12s", "2m 05s")
  const taskTime = (t: TaskEntry) => {
    if (!t.startedAt) return ''
    const end = t.status === 'running' ? Date.now() : (t.finishedAt || t.startedAt)
    const sec = Math.max(0, Math.floor((end - t.startedAt) / 1000))
    return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, '0')}s`
  }

  // markdown-lite: render ``` code fences and `inline code` in agent text
  const renderText = (text: string) => {
    if (!text.includes('`')) return text
    return text.split('```').map((seg, i) => {
      if (i % 2 === 1) {
        const nl = seg.indexOf('\n')
        const lang = nl > -1 ? seg.slice(0, nl).trim() : ''
        const code = (nl > -1 ? seg.slice(nl + 1) : seg).replace(/\n$/, '')
        return (
          <span key={i} className="code-block block">
            {lang && <span className="code-lang block">{lang}</span>}
            {code}
          </span>
        )
      }
      const bits = seg.split('`')
      return (
        <span key={i}>
          {bits.map((b, j) => j % 2 === 1 ? <code key={j} className="inline-code">{b}</code> : b)}
        </span>
      )
    })
  }

  // animated "agent is working" row with live elapsed time
  const workingIndicator = (t: TaskEntry) => (
    <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg px-3 py-2.5 msg-in">
      <div className="flex items-center gap-2.5">
        <span className="flex items-center gap-1">
          <span className="dot w-1.5 h-1.5 bg-emerald-400 rounded-full" />
          <span className="dot w-1.5 h-1.5 bg-emerald-400 rounded-full" />
          <span className="dot w-1.5 h-1.5 bg-emerald-400 rounded-full" />
        </span>
        <span className="shimmer-text text-sm font-medium">Working</span>
        <span className="text-xs text-gray-600 ml-auto font-mono">{taskTime(t)}</span>
      </div>
    </div>
  )

  // every tool call the run made, in order — the TOOLS tab reads this
  const getSteps = (task: TaskEntry | undefined) =>
    task ? task.messages.flatMap(msg => msg.steps || []) : []

  const getDeltas = (task: TaskEntry | undefined) => {
    if (!task) return []
    const deltas: { tool: string; file?: string; action: string }[] = []
    for (const msg of task.messages) {
      if (!msg.steps) continue
      for (const step of msg.steps) {
        if (['read', 'write', 'edit', 'glob', 'grep'].includes(step.tool)) {
          deltas.push({
            tool: step.tool,
            file: step.params?.path || step.params?.file_path || step.params?.pattern || '—',
            action: step.tool === 'read' ? 'read' : step.tool === 'write' ? 'created' : step.tool === 'edit' ? 'modified' : 'searched',
          })
        }
      }
    }
    return deltas
  }

  const shortPath = (p: string) => {
    const parts = p.split('/')
    return parts.length > 3 ? '.../' + parts.slice(-3).join('/') : p
  }

  const fileExt = (p: string) => {
    const ext = p.split('.').pop()?.toLowerCase() || ''
    return ext
  }

  const extColor = (ext: string) => {
    const map: Record<string, string> = {
      py: 'text-yellow-400', ts: 'text-sky-400', tsx: 'text-sky-400', js: 'text-yellow-300',
      rs: 'text-orange-400', sol: 'text-purple-400', json: 'text-green-400', md: 'text-gray-400',
      css: 'text-pink-400', html: 'text-orange-300', sh: 'text-green-300',
    }
    return map[ext] || 'text-gray-400'
  }

  const actionBadge = (action: string) => {
    const map: Record<string, { bg: string; text: string }> = {
      read: { bg: 'bg-sky-500/15 border-sky-500/25', text: 'text-sky-400' },
      created: { bg: 'bg-emerald-500/15 border-emerald-500/25', text: 'text-emerald-400' },
      modified: { bg: 'bg-amber-500/15 border-amber-500/25', text: 'text-amber-400' },
    }
    return map[action] || { bg: 'bg-gray-500/15 border-gray-500/25', text: 'text-gray-400' }
  }

  // provider + model selectors (used in both sidebar and fullscreen bars)
  const modelControls = (
    <div className="flex items-center gap-1.5 min-w-0 flex-1">
      <Select
        accent="emerald" className="shrink-0" title="LLM provider"
        value={provider}
        onChange={onProviderChange}
        options={(providers.length ? providers.map(p => p.key) : ['openrouter', 'venice']).map(k => ({ value: k, label: k, icon: '⬢' }))} />
      <Select
        accent="emerald" className="min-w-0 flex-1 max-w-[220px]" title={model || 'Model'}
        value={model}
        onChange={(v) => { setModel(v); localStorage.setItem('agent_model', v) }}
        options={
          providerModels.length === 0 && !model
            ? [{ value: '', label: 'default' }]
            : (model && !providerModels.includes(model) ? [model, ...providerModels] : providerModels).map(mn => ({ value: mn, label: mn }))
        } />
    </div>
  )

  // balance pill — live credit + vault state for the active provider; click to manage keys
  const vaultLocked = !!balance && !!balance.encrypted && !balance.unlocked && !balance.configured
  const fmtBalance = (b: KeyBalance | null) => {
    if (!b) return '···'
    if (vaultLocked) return 'locked'
    if (!b.configured) return 'no key'
    if (typeof b.balance !== 'number') return b.error ? '$ ?' : '···'
    return `$${b.balance.toFixed(2)}`
  }
  const lockGlyph = (open: boolean) => (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      <rect x="4" y="11" width="16" height="10" rx="2" />
      {open ? <path d="M8 11V7a4 4 0 0 1 7.7-1.5" /> : <path d="M8 11V7a4 4 0 0 1 8 0v4" />}
    </svg>
  )
  // one colour scheme for the key, wherever it shows up
  const keyTone = vaultLocked
    ? 'bg-amber-500/10 border-amber-500/30 text-amber-300 hover:bg-amber-500/20 pill-locked'
    : balance && typeof balance.balance === 'number' && balance.balance > 0
    ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/20'
    : balance && (!balance.configured || (typeof balance.balance === 'number' && balance.balance <= 0))
    ? 'bg-amber-500/10 border-amber-500/25 text-amber-300 hover:bg-amber-500/20'
    : 'border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20'
  const keyTitle = vaultLocked
    ? `${balance?.provider} key is encrypted — click to unlock it with your passphrase`
    : balance?.key
    ? `${balance.provider} key ${balance.key}${balance.unlocked ? ' (encrypted, unlocked)' : ''} — click to manage`
    : 'Add your API key'

  // Open the key vault modal focused on a given provider (from anywhere).
  // It's an overlay, so it opens over whatever you were doing.
  const openKeyPanel = (p: string) => { setKeyPanelProvider(p); setShowKeyPanel(true) }

  // the tool registry lives in the console's TOOLS tab — open it from anywhere
  const openToolRegistry = () => {
    setView('console'); setActiveTab('tools'); setToolPane('registry')
    if (dock === 'min') setDockPersist('normal')
  }

  const balancePill = (
    <button
      onClick={() => openKeyPanel(provider)}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-mono font-medium transition shrink-0 border ${keyTone}`}
      title={keyTitle}
    >
      {vaultLocked && lockGlyph(false)}
      {!vaultLocked && balance?.unlocked && lockGlyph(true)}
      {fmtBalance(balance)}
    </button>
  )

  // the same key, squeezed into a 42px strip — with the rail collapsed and the
  // console docked shut there'd otherwise be no way to reach a locked vault
  const keyStripButton = (
    <button
      onClick={() => openKeyPanel(provider)}
      className={`w-8 py-1 flex flex-col items-center gap-0.5 rounded-md border font-mono transition ${keyTone}`}
      title={keyTitle}
    >
      {lockGlyph(!vaultLocked)}
      <span className="text-[8px] leading-none">
        {!balance ? '·' : vaultLocked ? 'lock'
          : !balance.configured ? 'add'
          : typeof balance.balance !== 'number' ? 'key'
          : balance.balance >= 10 ? `$${Math.round(balance.balance)}` : `$${balance.balance.toFixed(1)}`}
      </span>
    </button>
  )

  // Friendly "you need an API key" call-to-action, shown in place of the raw
  // provider error inside a task's output. One tap to add a key, one to go get one.
  const keyErrorBanner = (prov: string) => {
    const meta = PROVIDER_META[prov] || PROVIDER_META.openrouter
    return (
      <div className="mt-2 rounded-lg border border-amber-500/25 bg-amber-500/[0.06] p-3">
        <div className="flex items-start gap-2.5">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5 text-amber-300">
            <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3" />
          </svg>
          <div className="min-w-0">
            <div className="text-sm font-medium text-amber-200">Add your {meta.label} API key to run this model</div>
            <div className="text-xs text-amber-200/70 mt-0.5">Keys are encrypted in your browser-side vault — never shared.</div>
          </div>
        </div>
        <div className="flex items-center gap-2 mt-2.5">
          <button
            onClick={() => openKeyPanel(prov)}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 transition">
            Enter API key
          </button>
          <a
            href={meta.keysUrl} target="_blank" rel="noopener noreferrer"
            className="px-3 py-1.5 rounded-md text-xs font-medium border border-white/10 text-gray-300 hover:text-white hover:border-white/25 transition flex items-center gap-1.5">
            Get a {meta.label} key
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 17L17 7M7 7h10v10" />
            </svg>
          </a>
        </div>
      </div>
    )
  }

  // --- Background tasks — live server-side registry, visible from anywhere ---
  const runningCount = serverTasks.filter(t => t.status === 'running').length

  // previews for a task, pulled the first time it's opened
  const loadTaskImages = (t: ServerTask) => {
    if (!t.images || taskImages[t.id]) return
    fetch(`${API_URL}/tasks/${t.id}/images`, { signal: AbortSignal.timeout(10000) })
      .then(r => r.json())
      .then(d => setTaskImages(m => ({ ...m, [t.id]: Array.isArray(d.images) ? d.images : [] })))
      .catch(() => {})
  }

  // elapsed / duration for a server task (epoch seconds)
  const serverTaskTime = (t: ServerTask) => {
    const end = t.status === 'running' ? Date.now() / 1000 : (t.finished_at || t.started_at)
    const sec = Math.max(0, Math.floor(end - t.started_at))
    return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, '0')}s`
  }

  // --- Tasks page — the server-side registry as a full view ---
  const taskCounts = {
    all: serverTasks.length,
    running: runningCount,
    done: serverTasks.filter(t => t.status === 'done').length,
    error: serverTasks.filter(t => t.status === 'error').length,
  }
  const visibleServerTasks = serverTasks.filter(t => {
    if (taskFilter !== 'all' && t.status !== taskFilter) return false
    const s = taskSearch.trim().toLowerCase()
    if (!s) return true
    return t.query.toLowerCase().includes(s)
      || (t.summary || '').toLowerCase().includes(s)
      || (t.user || '').toLowerCase().includes(s)
      || t.agent_type.toLowerCase().includes(s)
  })

  const tasksPage = (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="text-lg font-semibold tracking-tight text-gray-100">Background tasks</h2>
          <span className="text-xs text-gray-500 font-mono">
            {runningCount > 0 ? `${runningCount} running` : 'idle'}
          </span>
          <span className="ml-auto text-[10px] text-gray-600">server-side registry · survives page close · everyone&apos;s runs</span>
        </div>

        <div className="flex items-center gap-2 mt-5 flex-wrap">
          {(['all', 'running', 'done', 'error'] as const).map(f => (
            <button key={f} onClick={() => setTaskFilter(f)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition border ${
                taskFilter === f
                  ? f === 'error'
                    ? 'border-red-500/40 text-red-300 bg-red-500/10'
                    : 'border-emerald-500/40 text-emerald-200 bg-emerald-500/10'
                  : 'border-white/[0.07] text-gray-500 hover:text-gray-300 hover:border-white/20'
              }`}>
              {f}
              <span className="font-mono text-[10px] opacity-70">{taskCounts[f]}</span>
            </button>
          ))}
          <input
            value={taskSearch}
            onChange={e => setTaskSearch(e.target.value)}
            placeholder="Filter tasks…"
            className="ml-auto w-56 bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition"
          />
        </div>

        <div className="mt-4 space-y-1.5 pb-10">
          {visibleServerTasks.length === 0 ? (
            <div className="text-sm text-gray-600 text-center py-24 border border-dashed border-white/[0.06] rounded-xl">
              {serverTasks.length === 0
                ? 'No tasks yet — runs show up here, even ones started elsewhere'
                : 'Nothing matches this filter'}
            </div>
          ) : visibleServerTasks.map(t => {
            const expanded = !!expandedServerTasks[t.id]
            return (
              <div key={t.id}
                onClick={() => { setExpandedServerTasks(s => ({ ...s, [t.id]: !s[t.id] })); if (!expanded) loadTaskImages(t) }}
                className={`px-4 py-3 rounded-lg border cursor-pointer transition ${
                  t.status === 'running'
                    ? 'bg-emerald-500/[0.05] border-emerald-500/15'
                    : expanded ? 'bg-white/[0.04] border-white/10' : 'bg-white/[0.015] border-white/[0.05] hover:bg-white/[0.04] hover:border-white/10'
                }`}>
                <div className="flex items-center gap-3">
                  <span className={`text-sm shrink-0 ${statusIcon(t.status)}`}>{statusDot(t.status)}</span>
                  <span className="text-xs shrink-0 font-mono text-gray-500" title={t.agent_type}>
                    {agentOptions.find(a => a.value === t.agent_type)?.icon || '>_'}
                  </span>
                  <span className={`flex-1 text-gray-200 text-sm min-w-0 ${expanded ? 'whitespace-pre-wrap break-words' : 'truncate'}`}>
                    {t.query}
                  </span>
                  {!!t.images && (
                    <span className="text-[10px] shrink-0 px-1.5 py-px rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-300/80"
                      title={`${t.images} image${t.images !== 1 ? 's' : ''} attached — open the task to see them`}>
                      ▣ {t.images}
                    </span>
                  )}
                  <span className="text-[11px] text-gray-500 shrink-0 font-mono" title={`${t.steps} steps`}>
                    {t.steps} step{t.steps !== 1 ? 's' : ''} · {serverTaskTime(t)}
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-1.5 pl-8 min-w-0">
                  {t.status === 'running' && t.tool && (
                    <span className="text-[11px] text-emerald-300/80 font-mono shimmer-text shrink-0">{t.tool}…</span>
                  )}
                  {t.status !== 'running' && t.summary && (
                    <span className={`text-[11px] text-gray-500 min-w-0 ${expanded ? 'whitespace-pre-wrap break-words' : 'truncate'}`}>
                      {t.summary}
                    </span>
                  )}
                  <span className="ml-auto flex items-center gap-2 shrink-0">
                    {t.chain && (
                      <span className="text-[9px] px-1.5 py-px rounded bg-sky-500/10 border border-sky-500/20 text-sky-300 uppercase tracking-wider">chain</span>
                    )}
                    {(t.model || t.provider) && (
                      <span className="text-[10px] text-gray-600 font-mono truncate max-w-[180px]" title={`${t.provider || ''} ${t.model || ''}`.trim()}>
                        {t.model || t.provider}
                      </span>
                    )}
                    {t.user && (
                      <span className="text-[10px] text-gray-600 font-mono" title={t.user}>
                        {auth && t.user.toLowerCase() === auth.address.toLowerCase() ? 'you' : shortAddr(t.user)}
                      </span>
                    )}
                  </span>
                </div>
                {expanded && !!t.images && (
                  <div className="mt-2 pl-8 flex items-center gap-1.5 flex-wrap">
                    {(taskImages[t.id] || []).map((src, k) => (
                      <img key={k} src={src} alt="attachment" title="Click to view"
                        onClick={e => { e.stopPropagation(); openLightbox(taskImages[t.id], k) }}
                        className="h-16 w-16 object-cover rounded-md border border-white/10 cursor-zoom-in hover:border-emerald-500/40 transition" />
                    ))}
                    {!taskImages[t.id] && <span className="text-[10px] text-gray-600 font-mono">loading images…</span>}
                  </div>
                )}
                {expanded && (
                  <div className="mt-2 pl-8 flex items-center gap-4 text-[10px] text-gray-600 font-mono flex-wrap">
                    <span>id {t.id}</span>
                    <span>agent {t.agent_type}</span>
                    {t.provider && <span>provider {t.provider}</span>}
                    <span>started {new Date(t.started_at * 1000).toLocaleString()}</span>
                    {t.finished_at ? <span>finished {new Date(t.finished_at * 1000).toLocaleString()}</span> : null}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )

  // --- User chip — sign-in state, top-right corner ---
  const userChip = (
    <div className="relative">
      {auth ? (
        <button
          onClick={() => setShowUserMenu(v => !v)}
          className={`flex items-center gap-2 pl-1 pr-2.5 py-1 rounded-full border transition ${
            showUserMenu ? 'border-emerald-500/40 bg-emerald-500/10' : 'border-white/10 bg-white/[0.03] hover:border-white/20'
          }`}
          title={auth.address}
        >
          <span className="w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-mono border bg-emerald-500/20 border-emerald-500/35 text-emerald-200">
            {auth.address.slice(2, 4).toUpperCase()}
          </span>
          <span className="text-xs text-gray-300 font-mono">{shortAddr(auth.address)}</span>
        </button>
      ) : (
        <button
          onClick={() => setShowUserMenu(v => !v)}
          disabled={authBusy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 hover:border-emerald-500/50 disabled:opacity-60 transition"
          title={authErr || 'Sign in — browser wallet or a local key'}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${authBusy ? 'bg-amber-400 animate-pulse' : 'bg-emerald-400'}`} />
          {authBusy ? 'Signing…' : 'Sign in'}
        </button>
      )}
      {showUserMenu && !auth && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setShowUserMenu(false)} />
          <div className="absolute right-0 top-full mt-1 w-72 bg-surface-2 border border-white/10 rounded-lg z-50 shadow-2xl overflow-hidden">
            <div className="p-1.5">
              <button
                onClick={signInWallet}
                disabled={authBusy}
                className="w-full text-left px-2.5 py-2 rounded-md text-xs hover:bg-emerald-500/[0.06] disabled:opacity-60 transition"
              >
                <div className="text-gray-200">Browser wallet</div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  {eth() ? 'sign with your MetaMask address' : 'no wallet extension found'}
                </div>
              </button>
              <button
                onClick={signInLocal}
                disabled={authBusy}
                className="w-full text-left px-2.5 py-2 rounded-md text-xs hover:bg-emerald-500/[0.06] disabled:opacity-60 transition"
              >
                <div className="text-gray-200">Local wallet</div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  {(() => {
                    const id = loadLocalIdentity()
                    return id ? `resume ${shortAddr(id.address)} — key stays in this browser`
                      : 'generate a key in this browser — no extension, no chain link'
                  })()}
                </div>
              </button>
            </div>
            {authErr && (
              <div className="px-3 py-2 border-t border-white/[0.06] text-[10px] text-red-400/90">{authErr}</div>
            )}
          </div>
        </>
      )}
      {showUserMenu && auth && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setShowUserMenu(false)} />
          <div className="absolute right-0 top-full mt-1 w-72 bg-surface-2 border border-white/10 rounded-lg z-50 shadow-2xl overflow-hidden">
            <div className="px-4 py-3 border-b border-white/[0.06] flex items-center gap-3">
              <span className="w-9 h-9 rounded-full flex items-center justify-center text-[11px] font-mono border shrink-0 bg-emerald-500/20 border-emerald-500/35 text-emerald-200">
                {auth.address.slice(2, 4).toUpperCase()}
              </span>
              <div className="min-w-0">
                <div className="text-xs text-gray-200 font-mono truncate">{auth.address}</div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  {auth.local ? 'local key — held in this browser' : 'signed in with your wallet'}
                </div>
              </div>
            </div>
            <div className="p-1.5">
              <button
                onClick={() => { setShowCredits(true); setShowUserMenu(false) }}
                className="w-full flex items-center px-2.5 py-2 rounded-md text-xs text-gray-400 hover:bg-emerald-500/[0.06] hover:text-emerald-200 transition">
                <span className="text-emerald-400/80 mr-2">◈</span>
                Credits &amp; top-up
                <span className="ml-auto font-mono text-emerald-300">
                  ${(creditsInfo?.account?.balance ?? 0).toFixed(2)}
                </span>
              </button>
              <button
                onClick={() => { navigator.clipboard?.writeText(auth.address).catch(() => {}); setShowUserMenu(false) }}
                className="w-full text-left px-2.5 py-2 rounded-md text-xs text-gray-400 hover:bg-white/[0.04] hover:text-gray-200 transition">
                Copy address
              </button>
              <button
                onClick={signOut}
                className="w-full text-left px-2.5 py-2 rounded-md text-xs text-red-400/90 hover:bg-red-500/10 hover:text-red-300 transition">
                Sign out
              </button>
              {auth.local && (
                <button
                  onClick={() => { clearLocalIdentity(); signOut() }}
                  title="Delete the browser-held key — this identity (and anything stored under it) is gone for good"
                  className="w-full text-left px-2.5 py-2 rounded-md text-xs text-red-400/90 hover:bg-red-500/10 hover:text-red-300 transition">
                  Forget local wallet
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )

  // --- Persona picker — choose an agent, a library prompt, and memory notes ---
  const pickerFilter = (name: string, desc?: string) => {
    const s = pickerSearch.trim().toLowerCase()
    if (!s) return true
    return name.toLowerCase().includes(s) || (desc || '').toLowerCase().includes(s)
  }

  const personaPicker = (
    <div className="relative min-w-0">
      <button
        onClick={() => { setShowPicker(v => !v); setPersonaErr(null); if (!showPicker) fetchLibrary() }}
        className={`flex items-center gap-1.5 bg-white/5 border rounded-md px-2 py-1.5 text-sm outline-none cursor-pointer transition-colors min-w-0 max-w-[200px] ${
          showPicker ? 'border-emerald-500/40 text-gray-200' : 'border-white/10 text-gray-300 hover:border-white/20'
        }`}
        title={activePersona
          ? `${activePersona.kind === 'prompt' ? 'Prompt' : 'Agent'}: ${activePersona.label} · ${ownerTitle(activePersona)}`
          : `Agent: ${currentAgentDef?.label || agentType}`}
      >
        {promptSel ? (
          <span className="text-amber-300/90 shrink-0">¶</span>
        ) : (
          <span className="shrink-0">{currentAgentDef?.icon || '>_'}</span>
        )}
        <span className="truncate">{promptSel ? promptSel.name : (currentAgentDef?.label || agentType)}</span>
        {activePersona?.owner_source && (
          <span className={`text-[9px] px-1 py-0.5 rounded shrink-0 font-mono ${
            activePersona.owner_source === 'host'
              ? 'bg-white/[0.06] text-gray-500'
              : ownedByMe(activePersona)
              ? 'bg-emerald-500/15 text-emerald-300/90'
              : 'bg-violet-400/10 text-violet-300/90'
          }`} title={ownerTitle(activePersona)}>
            {ownedByMe(activePersona) ? 'you' : ownerLabel(activePersona)}
          </span>
        )}
        {memSel.length > 0 && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-sky-500/15 border border-sky-500/25 text-sky-300 shrink-0 font-mono"
            title={`${memSel.length} memory note${memSel.length !== 1 ? 's' : ''} in context`}>
            +{memSel.length}
          </span>
        )}
        {toolSel.length > 0 && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/25 text-emerald-300 shrink-0 font-mono"
            title={`${toolSel.length} installed tool doc${toolSel.length !== 1 ? 's' : ''} in context`}
            onClick={e => { e.stopPropagation(); setToolSel([]) }}>
            ⌘{toolSel.length}
          </span>
        )}
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
          className={`shrink-0 text-gray-600 transition-transform ${showPicker ? 'rotate-180' : ''}`}>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {showPicker && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setShowPicker(false)} />
          <div className="absolute left-0 top-full mt-1 w-80 max-h-[65vh] flex flex-col bg-surface-2 border border-white/10 rounded-lg z-50 shadow-2xl overflow-hidden">
            {/* tabs */}
            <div className="flex items-center gap-0.5 px-1.5 pt-1.5 border-b border-white/[0.06] shrink-0">
              {([
                ['prompts', `prompts ${personas.length}`],
                ['memory', memSel.length ? `memory ${memSel.length}/${memNotes.length}` : `memory ${memNotes.length}`],
              ] as const).map(([t, label]) => (
                <button key={t} onClick={() => setPickerTab(t)}
                  className={`px-2.5 py-2 text-[10px] font-medium uppercase tracking-wider transition-colors relative ${
                    pickerTab === t ? 'text-white' : 'text-gray-600 hover:text-gray-400'
                  }`}>
                  {label}
                  {pickerTab === t && <span className="absolute bottom-0 left-1 right-1 h-[1.5px] bg-emerald-500 rounded-full" />}
                </button>
              ))}
            </div>
            {/* search */}
            <div className="px-2 py-2 border-b border-white/[0.06] shrink-0">
              <input
                value={pickerSearch}
                onChange={e => setPickerSearch(e.target.value)}
                placeholder={`Search ${pickerTab}…`}
                className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition"
              />
            </div>
            {/* list */}
            <div className="flex-1 overflow-y-auto min-h-0 p-1.5 space-y-0.5">
              {pickerTab === 'prompts' && personas
                .filter(p => pickerFilter(p.label, p.description))
                .map(p => personaRow(p, () => setShowPicker(false)))}
              {pickerTab === 'prompts' && (
                <button
                  onClick={() => openBuilder()}
                  className="w-full text-left px-2.5 py-2 rounded-md text-xs transition border border-dashed border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 flex items-center gap-2">
                  <span className="w-5 text-center shrink-0">+</span> build a new agent
                </button>
              )}

              {pickerTab === 'memory' && (
                memNotes.length === 0 ? (
                  <div className="text-xs text-gray-600 text-center py-6">
                    No memory notes yet — add them in the library
                  </div>
                ) : memNotes.filter(n => pickerFilter(n.name, n.content)).map(n => {
                  const on = memSel.includes(n.id)
                  return (
                    <button key={n.id}
                      onClick={() => toggleNote(n.id)}
                      className={`w-full text-left px-2.5 py-2 rounded-md transition border ${
                        on ? 'bg-sky-500/10 border-sky-500/25' : 'border-transparent hover:bg-white/[0.04]'
                      }`}>
                      <div className="flex items-center gap-2">
                        <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center text-[9px] shrink-0 ${
                          on ? 'bg-sky-500/30 border-sky-400/50 text-sky-200' : 'border-white/20 text-transparent'
                        }`}>✓</span>
                        <span className="text-xs font-medium text-gray-200 truncate">{n.name}</span>
                      </div>
                      <div className="text-[10px] text-gray-500 mt-0.5 line-clamp-2 leading-relaxed pl-[22px]">
                        {n.content?.slice(0, 120) || '—'}
                      </div>
                    </button>
                  )
                })
              )}
            </div>
            {/* footer */}
            <div className="border-t border-white/[0.06] px-2.5 py-2 flex items-center gap-2 shrink-0">
              <span className={`text-[10px] truncate ${personaErr ? 'text-red-400' : 'text-gray-600'}`}
                title={personaErr || undefined}>
                {personaErr
                  ? personaErr
                  : pickerTab === 'memory'
                  ? 'selected notes ride along as run context'
                  : 'agents bring tools + a model, prompts just set the goal'}
              </span>
              <div className="ml-auto flex items-center gap-2">
                {pickerTab === 'memory' && memSel.length > 0 && (
                  <button onClick={() => { setMemSel([]); try { localStorage.removeItem('agent_mem_sel') } catch {} }}
                    className="text-[10px] text-gray-500 hover:text-gray-300 transition">
                    clear
                  </button>
                )}
                <button onClick={() => { setShowPicker(false); setView('builder') }}
                  className="text-[10px] text-violet-300/90 hover:text-violet-200 transition">
                  builder →
                </button>
                <button onClick={() => { setShowPicker(false); setView('library') }}
                  className="text-[10px] text-emerald-300/90 hover:text-emerald-200 transition">
                  library →
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )

  // --- Compose row — the agent prompt. Lives docked at the bottom by
  // default, or pops out into a floating, draggable, resizable panel. ---
  const composeCore = (
    <div
      onDragOver={e => e.preventDefault()}
      onDrop={e => { e.preventDefault(); addFiles(Array.from(e.dataTransfer?.files || [])) }}
      className={`rounded-xl border px-3 py-2 transition-all duration-200 ${
      composeFocused
        ? 'border-emerald-500/40 bg-white/[0.04] shadow-[0_0_0_3px_rgb(var(--glow)/0.08)]'
        : 'border-white/[0.08] bg-white/[0.02] hover:border-white/[0.14]'
    }`}>
      {/* staged images — paste, drop, or pick them; they ride with the next run */}
      {(attachments.length > 0 || attachErr) && (
        <div className="flex items-center gap-1.5 flex-wrap pb-2">
          {attachments.map((a, k) => (
            <div key={a.id} className="relative group">
              <img src={a.thumb} alt={a.name} title={a.name}
                onClick={() => openLightbox(attachments.map(x => x.url), k)}
                className="h-12 w-12 object-cover rounded-md border border-emerald-500/20 cursor-zoom-in" />
              <button onClick={() => setAttachments(l => l.filter(x => x.id !== a.id))}
                title="Remove"
                className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-black/80 border border-white/15 text-[9px] text-gray-400 hover:text-red-400 opacity-0 group-hover:opacity-100 transition">✕</button>
            </div>
          ))}
          {attachErr && <span className="text-[10px] text-red-400">{attachErr}</span>}
        </div>
      )}
      <div className="flex gap-2 items-end">
      <input ref={fileRef} type="file" accept="image/*" multiple className="hidden"
        onChange={e => { addFiles(Array.from(e.target.files || [])); e.target.value = '' }} />
      <button onClick={() => fileRef.current?.click()} disabled={loading}
        title="Attach an image — or just paste one"
        className="w-8 h-8 shrink-0 mb-0.5 flex items-center justify-center rounded-lg border border-white/[0.08] text-gray-600 hover:text-emerald-300 hover:border-emerald-500/30 disabled:opacity-40 transition">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <polyline points="21 15 16 10 5 21" />
        </svg>
      </button>
      <textarea
        ref={inputRef}
        value={query}
        onChange={e => setQuery(e.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={onPasteCompose}
        onFocus={() => setComposeFocused(true)}
        onBlur={() => setComposeFocused(false)}
        placeholder={`Ask ${promptSel ? promptSel.name : (currentAgentDef?.label || 'agent')}...`}
        rows={2}
        className="flex-1 bg-transparent border-none outline-none text-[15px] resize-none placeholder:text-gray-600 py-1 leading-relaxed min-w-0"
        disabled={loading}
      />
      <button onClick={togglePromptFloat}
        title={promptFloat ? 'Dock the prompt back to the bottom' : 'Pop the prompt out — drag it anywhere, resize it'}
        className={`w-8 h-8 shrink-0 mb-0.5 flex items-center justify-center rounded-lg border transition ${
          promptFloat
            ? 'border-emerald-500/30 text-emerald-300 bg-emerald-500/10'
            : 'border-white/[0.08] text-gray-600 hover:text-emerald-300 hover:border-emerald-500/30'
        }`}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          {promptFloat ? (
            /* dock back down */
            <>
              <polyline points="8 11 12 15 16 11" />
              <line x1="12" y1="3" x2="12" y2="15" />
              <line x1="4" y1="20" x2="20" y2="20" />
            </>
          ) : (
            /* pop out */
            <>
              <rect x="3" y="9" width="12" height="12" rx="2" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="21" y1="3" x2="12" y2="12" />
            </>
          )}
        </svg>
      </button>
      {loading ? (
        <button onClick={stopRun}
          className="text-sm bg-red-500/15 border border-red-500/30 hover:bg-red-500/25 text-red-400 rounded-lg px-4 py-2 transition font-medium shrink-0 mb-0.5"
          title="Stop this run">
          Stop
        </button>
      ) : (
        <button onClick={run} disabled={(!query.trim() && attachments.length === 0) || apiStatus === 'down'}
          className="text-sm lit-btn disabled:bg-white/5 disabled:text-gray-600 disabled:shadow-none rounded-lg px-4 py-2 transition font-semibold shrink-0 mb-0.5"
          title={apiStatus === 'down' ? `API offline at ${API_URL}` : ''}>
          Run
        </button>
      )}
      </div>
    </div>
  )

  // docked shell — bottom of the console, safe-area padded for phones
  const composeBar = (
    <div className="border-t border-white/[0.06] px-3 py-2.5 shrink-0 compose-safe">
      {/* no persona strip here — the picker in the console toolbar owns that
          choice, and a second overflowing chip row only crowded the composer. */}
      {composeCore}
    </div>
  )

  // floating shell — drag by the header bar, resize from the right edge
  const floatingPrompt = promptFloat ? (
    <div
      className="fixed z-40 bg-surface-1/95 backdrop-blur-sm border border-white/15 rounded-xl shadow-2xl"
      style={{ left: promptPos.x, top: promptPos.y, width: Math.min(promptW, (typeof window !== 'undefined' ? window.innerWidth : 9999) - 16) }}
    >
      <div
        onPointerDown={onPromptDragStart}
        className="flex items-center gap-2 px-3 py-1.5 border-b border-white/[0.06] cursor-move touch-none select-none"
        title="Drag to move the prompt"
      >
        <span className="flex gap-[3px]">
          <span className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
          <span className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
          <span className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
        </span>
        <span className="text-[9px] text-gray-500 uppercase tracking-wider">prompt</span>
        <button onClick={togglePromptFloat}
          title="Dock the prompt back to the bottom"
          className="ml-auto w-5 h-5 flex items-center justify-center rounded text-gray-600 hover:text-emerald-300 hover:bg-emerald-500/10 transition">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="8 11 12 15 16 11" />
            <line x1="12" y1="3" x2="12" y2="15" />
            <line x1="4" y1="20" x2="20" y2="20" />
          </svg>
        </button>
      </div>
      <div className="px-2.5 py-2">{composeCore}</div>
      <div
        onPointerDown={onPromptResizeStart}
        className="absolute top-0 bottom-0 -right-[5px] w-[10px] cursor-ew-resize touch-none group"
        title="Drag to resize"
      >
        <div className="h-full w-[1px] ml-[4px] bg-transparent group-hover:bg-emerald-500/60 group-active:bg-emerald-500 transition-colors" />
      </div>
    </div>
  ) : null

  // --- Tool trace — every step the run took, params and result in full ---
  const traceBody = (
    <div className="p-3 max-w-4xl mx-auto w-full">
      {!currentTask ? (
        <p className="text-sm text-gray-600 text-center mt-8">Select a chat to see what it ran</p>
      ) : (() => {
        const steps = getSteps(currentTask)
        if (steps.length === 0) return (
          <p className="text-sm text-gray-600 text-center mt-8">
            {currentTask.status === 'running' ? 'No tool calls yet…' : 'No tool calls — the agent answered directly'}
          </p>
        )
        return (
          <div className="space-y-0.5">
            {steps.map((step: any, j: number) => {
              const target = step.params?.path || step.params?.file_path || step.params?.pattern || step.params?.command || ''
              const open = !!expandedSteps[j]
              return (
                <div key={j} className={`text-xs rounded-md border transition ${
                  step.error ? 'bg-red-500/[0.04] border-red-500/15' : 'bg-white/[0.02] border-white/[0.05] hover:border-white/[0.1]'
                }`}>
                  <button className="w-full text-left flex items-center gap-2 px-2.5 py-2" onClick={() => toggleStep(j)}>
                    <span className="text-gray-700 w-6 shrink-0 font-mono text-[10px]">{String(j + 1).padStart(2, '0')}</span>
                    <span className="text-gray-600">{open ? '▼' : '▶'}</span>
                    <span className="text-emerald-300 font-mono shrink-0">{step.tool}</span>
                    {target && <span className="text-gray-600 truncate font-mono">{shortPath(String(target))}</span>}
                    {step.error && <span className="text-red-400 ml-auto shrink-0">err</span>}
                  </button>
                  {open && (
                    <div className="px-2.5 pb-2 space-y-1">
                      {step.params && Object.keys(step.params).length > 0 && (
                        <pre className="text-gray-500 overflow-x-auto max-h-40 text-[11px] leading-relaxed border-l border-white/[0.06] pl-2">
                          {JSON.stringify(step.params, null, 2)}
                        </pre>
                      )}
                      {step.result != null && step.result !== '' && (
                        <pre className="text-gray-400 overflow-x-auto max-h-72 text-[11px] leading-relaxed border-l border-emerald-500/20 pl-2">
                          {typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2)}
                        </pre>
                      )}
                      {step.error && (
                        <pre className="text-red-400 overflow-x-auto max-h-40 text-[11px] leading-relaxed border-l border-red-500/30 pl-2">{step.error}</pre>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      })()}
    </div>
  )

  // TRACE is this run's history; REGISTRY is the whole tool surface, and
  // where a new tool gets added
  const toolTrace = (
    <div>
      <div className="px-3 pt-3 max-w-4xl mx-auto w-full flex items-center gap-1.5">
        {(['trace', 'registry'] as ToolPane[]).map(p => (
          <button key={p} onClick={() => setToolPane(p)}
            className={`px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider transition border ${
              toolPane === p ? 'bg-emerald-500/15 border-emerald-500/25 text-emerald-300'
                             : 'bg-white/[0.03] border-white/[0.06] text-gray-600 hover:text-gray-300'
            }`}>
            {p}
            {p === 'trace' && currentTask && getSteps(currentTask).length > 0 && (
              <span className="ml-1 text-gray-500 normal-case">{getSteps(currentTask).length}</span>
            )}
            {p === 'registry' && toolCounts && (
              <span className="ml-1 text-gray-500 normal-case">{toolCounts.total}</span>
            )}
          </button>
        ))}
      </div>
      {toolPane === 'registry'
        ? <Tools token={auth?.token} isHost={isHost} onCount={setToolCounts} />
        : traceBody}
    </div>
  )

  // --- Transcript — the console body: the run's messages, its tool trace, or its file deltas ---
  const transcript = (
    <div className="h-full overflow-y-auto min-h-0">
      {activeTab === 'tools' ? toolTrace : activeTab === 'memory' ? (
        <MemoryPanel token={auth?.token} memSel={memSel} onToggleMem={toggleNote}
          onNotesChanged={() => { fetchLibrary(); libChanged() }} />
      ) : activeTab === 'deltas' ? (
        <div className="p-3 max-w-4xl mx-auto w-full">
          {!currentTask ? (
            <p className="text-sm text-gray-600 text-center mt-8">Select a chat to see what it touched</p>
          ) : (() => {
            const deltas = getDeltas(currentTask)
            if (deltas.length === 0) return (
              <p className="text-sm text-gray-600 text-center mt-8">No file operations</p>
            )
            return (
              <div className="space-y-0.5">
                {deltas.map((d, i) => (
                  <div key={i} className="flex items-center gap-2 px-2.5 py-2 rounded-md hover:bg-white/[0.03] transition text-sm cursor-pointer"
                    onClick={() => {
                      const files = getTaskFiles(currentTask)
                      const file = files.find(f => f.path === d.file)
                      if (file) { setViewingFile(file); if (dock === 'max') setDockPersist('normal') }
                    }}>
                    <span className={`font-mono text-xs w-16 shrink-0 ${
                      d.action === 'created' ? 'text-emerald-400' :
                      d.action === 'modified' ? 'text-amber-400' :
                      d.action === 'read' ? 'text-sky-400' :
                      'text-gray-500'
                    }`}>{d.action}</span>
                    <span className="text-gray-400 truncate font-mono">{shortPath(d.file || '')}</span>
                  </div>
                ))}
              </div>
            )
          })()}
        </div>
      ) : !currentTask ? (
        <div className="h-full flex flex-col items-center justify-center text-center text-gray-600 px-4 py-6">
          <div className="w-12 h-12 rounded-2xl bg-emerald-500/[0.06] border border-emerald-500/20 hero-logo flex items-center justify-center mb-4">
            <span className="text-emerald-300 font-mono text-sm select-none">{'>'}<span className="caret-blink">_</span></span>
          </div>
          <p className="text-sm text-gray-400 font-medium">
            {tasks.length === 0 ? 'What should we build?' : 'New chat — ask below, or pick one from the rail'}
          </p>
          <div className="mt-4 flex gap-1.5 justify-center flex-wrap max-w-[520px]">
            {['map this codebase', 'find and fix a bug', 'write tests for recent changes'].map(ex => (
              <button key={ex} onClick={() => { setQuery(ex); inputRef.current?.focus() }}
                className="px-3 py-1.5 rounded-full text-xs bg-white/5 border border-white/[0.06] text-gray-500 hover:text-gray-300 hover:bg-white/[0.08] transition">
                {ex}
              </button>
            ))}
            <button onClick={() => setView('library')}
              className="px-3 py-1.5 rounded-full text-xs bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 hover:text-emerald-200 hover:bg-emerald-500/15 transition">
              browse library →
            </button>
          </div>
          <p className="mt-4 text-[10px] text-gray-600 flex items-center gap-1">
            <span className="text-emerald-400/50">⬡</span>
            {auth ? 'your chats are pinned to localfs and follow your wallet'
                  : 'sign in to keep chats in localfs across devices'}
          </p>
        </div>
      ) : (
        <div className="p-3 space-y-2 max-w-4xl mx-auto w-full">
          {currentTask.messages.map((msg, i) => {
            // the transcript is the conversation, not the trace: tool calls are
            // one line here and the whole story in the TOOLS tab
            // show the thumbnails, open the full-size copy — after a reload
            // only the thumbnails survived, so they stand in for both
            const shots = msg.thumbs || msg.images || []
            const full = msg.images || msg.thumbs || []
            const lastStep = msg.steps?.[msg.steps.length - 1]
            return (
            <div key={i} className={`${msg.role === 'user' ? 'ml-auto max-w-[85%]' : 'max-w-full'}`}>
              <div className={`rounded-lg px-3 py-2.5 msg-in ${
                msg.role === 'user' ? 'bg-emerald-500/10 border border-emerald-500/20' :
                msg.role === 'system' ? 'bg-red-500/10 border border-red-500/15' :
                'bg-white/[0.03] border border-white/[0.06]'
              }`}>
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-xs text-gray-500">{msg.role}</span>
                </div>
                {shots.length > 0 && (
                  <div className="flex gap-1.5 flex-wrap mb-1.5">
                    {shots.map((src, k) => (
                      <img key={k} src={src} alt="attachment" title="Click to view"
                        onClick={() => openLightbox(full.length === shots.length ? full : shots, k)}
                        className="h-20 w-20 object-cover rounded-md border border-white/10 cursor-zoom-in hover:border-emerald-500/40 transition" />
                    ))}
                  </div>
                )}
                <div className="whitespace-pre-wrap text-sm text-gray-300 leading-relaxed">
                  {msg.text ? renderText(msg.text) : msg.live ? (
                    <span className="text-gray-600 shimmer-text">
                      {lastStep ? `${lastStep.tool}${lastStep.params?.path || lastStep.params?.file_path ? ` ${shortPath(lastStep.params.path || lastStep.params.file_path)}` : ''}…` : 'thinking…'}
                    </span>
                  ) : null}
                </div>
                {msg.role === 'system' && detectKeyError(msg.text) && keyErrorBanner(detectKeyError(msg.text)!)}
                {msg.steps && msg.steps.length > 0 && (
                  <button onClick={() => setActiveTab('tools')}
                    title="Open the tool trace"
                    className="mt-1.5 inline-flex items-center gap-1.5 text-[10px] text-gray-600 hover:text-emerald-300 transition">
                    <span className="text-emerald-400/60">⚙</span>
                    {msg.steps.length} tool call{msg.steps.length === 1 ? '' : 's'}
                    {msg.steps.some((s: any) => s.error) && <span className="text-red-400/80">· errors</span>}
                    <span className="text-gray-700">→</span>
                  </button>
                )}
              </div>
            </div>
          )})}
          {currentTask.status === 'running' && workingIndicator(currentTask)}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  )

  // --- Chats rail — every conversation, always visible on the side ---
  const newChat = () => {
    setSelectedTask(null)
    setViewingFile(null)
    setActiveTab('output')
    if (dock === 'min') setDockPersist('normal')
    setTimeout(() => inputRef.current?.focus(), 40)
  }

  const setRailClosed = (closed: boolean) => {
    setSidebarCollapsed(closed)
    try { localStorage.setItem('agent_rail_closed', closed ? '1' : '0') } catch {}
  }

  const setPane = (p: RailPane) => {
    setRailPane(p)
    try { localStorage.setItem('agent_rail_pane', p) } catch {}
    if (p === 'agents') { fetchAgents(auth?.token); fetchLibrary() }
  }

  const setMarketOpenPersist = (open: boolean) => {
    setMarketOpen(open)
    try { localStorage.setItem('agent_market_open', open ? '1' : '0') } catch {}
  }

  // chats bucketed by day, so a long history stays scannable
  const chatBucket = (t: TaskEntry) => {
    if (!t.startedAt) return 'earlier'
    const d = new Date(t.startedAt)
    const now = new Date()
    if (d.toDateString() === now.toDateString()) return 'today'
    if (d.toDateString() === new Date(now.getTime() - 86400000).toDateString()) return 'yesterday'
    return 'earlier'
  }
  const visibleChats = tasks.filter(t => {
    const s = chatSearch.trim().toLowerCase()
    return !s || t.query.toLowerCase().includes(s)
  })

  const chatRow = (t: TaskEntry) => (
    <div
      key={t.id}
      role="button"
      tabIndex={0}
      onClick={() => { setSelectedTask(t.id); setActiveTab('output'); setViewingFile(null); if (dock === 'min') setDockPersist('normal') }}
      onKeyDown={e => { if (e.key === 'Enter') { setSelectedTask(t.id); setActiveTab('output') } }}
      className={`w-full text-left px-2.5 py-2 rounded-lg text-sm transition group cursor-pointer border ${
        selectedTask === t.id
          ? 'bg-emerald-500/10 border-emerald-500/20'
          : 'hover:bg-white/[0.04] border-transparent'
      }`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span className={`text-[11px] shrink-0 ${statusIcon(t.status)}`}>{statusDot(t.status)}</span>
        {t.agent_type && t.agent_type !== 'default' && (
          <span className="text-[10px] text-gray-500 shrink-0 font-mono" title={t.agent_type}>
            {agentOptions.find(a => a.value === t.agent_type)?.icon}
          </span>
        )}
        <span className="truncate flex-1 text-[13px] text-gray-300 group-hover:text-gray-200">{t.query}</span>
        <button
          onClick={e => { e.stopPropagation(); deleteConversation(t) }}
          title="Delete conversation (local + server)"
          className="w-4 h-4 hidden group-hover:flex items-center justify-center rounded text-gray-600 hover:text-red-400 shrink-0 text-[10px]"
        >✕</button>
      </div>
      {/* a chat's attachments, so an image conversation is recognisable in the rail */}
      {(() => {
        const shots = t.messages.flatMap(m => m.thumbs || m.images || [])
        return shots.length > 0 && (
          <div className="flex items-center gap-1 mt-1 pl-[18px]">
            {shots.slice(0, 4).map((src, k) => (
              <img key={k} src={src} alt="" title="Click to view"
                onClick={e => { e.stopPropagation(); openLightbox(shots, k) }}
                className="h-7 w-7 object-cover rounded border border-white/10 hover:border-emerald-500/40 cursor-zoom-in transition" />
            ))}
            {shots.length > 4 && <span className="text-[9px] text-gray-600 font-mono">+{shots.length - 4}</span>}
          </div>
        )
      })()}
      <div className="flex items-center gap-1.5 mt-0.5 pl-[18px] text-[10px] text-gray-600 font-mono">
        <span>{t.stepCount !== undefined ? `${t.stepCount} step${t.stepCount !== 1 ? 's' : ''} · ` : ''}{taskTime(t)}</span>
        {t.cid && (
          <span
            onClick={e => { e.stopPropagation(); try { navigator.clipboard?.writeText(t.cid!) } catch {} }}
            title={`pinned to localfs — ${t.cid}\nclick to copy CID`}
            className="ml-auto px-1 py-px rounded bg-emerald-500/[0.08] border border-emerald-500/15 text-emerald-300/80 hover:text-emerald-200 shrink-0"
          >⬡ {t.cid.slice(0, 6)}…</span>
        )}
      </div>
    </div>
  )

  // foot of the open rail: who you are, what the module can do, and the key —
  // the same three things the strip carries when the rail is collapsed
  const identityFooter = (
    <div className="border-t border-white/[0.06] px-3 py-2.5 shrink-0 flex items-center gap-2.5">
      <div className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-mono shrink-0 bg-emerald-500/15 border border-emerald-500/25 text-emerald-200">
        {auth ? auth.address.slice(2, 4).toUpperCase() : '··'}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-xs text-gray-300 font-mono truncate"
          title={auth ? `signed in as ${auth.address}` : 'not signed in — sign in to save your work under your address'}>
          {auth ? shortAddr(auth.address) : 'not signed in'}
        </div>
        <div className="text-[10px] text-gray-600 truncate flex items-center gap-1.5">
          <button onClick={openToolRegistry} className="hover:text-emerald-300 transition" title="Open the tool registry">
            {toolCounts?.total ?? Object.keys(toolSchemas).length} tools
          </button>
          {tasks.some(t => t.synced) && (
            <span className="text-emerald-400/60" title="conversations pinned to localfs">
              ⬡ {tasks.filter(t => t.synced).length}
            </span>
          )}
          <span className={`ml-auto w-1.5 h-1.5 rounded-full shrink-0 ${
            apiStatus === 'ok' ? 'bg-emerald-400' : apiStatus === 'down' ? 'bg-red-400' : 'bg-gray-500'
          }`} title={apiStatus === 'ok' ? 'API online' : apiStatus === 'down' ? 'API offline' : 'checking the API'} />
        </div>
      </div>
      {!auth && (
        <button onClick={signIn} disabled={authBusy}
          className="text-[10px] px-2 py-1 rounded-md border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-60 transition shrink-0">
          {authBusy ? '…' : 'Sign in'}
        </button>
      )}
      {keyStripButton}
    </div>
  )

  // --- Agents pane — the personas you can run as, beside the chats ---
  const agentsPane = (
    <>
      <div className="px-2.5 py-2 border-b border-white/[0.06] shrink-0">
        <input
          value={agentSearch}
          onChange={e => setAgentSearch(e.target.value)}
          placeholder="Filter agents…"
          className="w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition"
        />
      </div>
      <div className="flex-1 overflow-y-auto min-h-0 p-1.5 space-y-0.5">
        {(() => {
          const s = agentSearch.trim().toLowerCase()
          const shown = personas.filter(p => !s ||
            p.label.toLowerCase().includes(s) || (p.description || '').toLowerCase().includes(s))
          if (shown.length === 0) {
            return <div className="text-center text-xs text-gray-500 py-10 px-3">Nothing matches</div>
          }
          // agents bring tools and a model; prompts only set the goal — keep
          // the two apart so the distinction stays visible
          return (['agent', 'prompt'] as const).map(kind => {
            const rows = shown.filter(p => p.kind === kind)
            if (rows.length === 0) return null
            return (
              <div key={kind}>
                <div className="px-2 pt-2 pb-1 text-[9px] text-gray-600 uppercase tracking-wider">
                  {kind === 'agent' ? 'agents' : 'prompts'}
                </div>
                {rows.map(p => personaRow(p))}
              </div>
            )
          })
        })()}
      </div>
      <div className="px-2 pb-2 shrink-0">
        <button onClick={() => openBuilder()}
          className="w-full text-left px-2.5 py-2 rounded-md text-xs transition border border-dashed border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 flex items-center gap-2">
          <span className="w-5 text-center shrink-0">+</span> build a new agent
        </button>
      </div>
    </>
  )

  const railContent = (
    <div className="flex flex-col h-full min-h-0">
      {/* rail header — two panes: the chats you've had, the agents you can be */}
      <div className="px-2 py-2 border-b border-white/[0.06] flex items-center gap-1 shrink-0">
        <div className="flex items-center gap-0.5 bg-white/[0.03] border border-white/[0.07] rounded-md p-0.5 min-w-0">
          {([['chats', tasks.length], ['agents', personas.length]] as const).map(([pane, n]) => (
            <button key={pane} onClick={() => setPane(pane as RailPane)}
              className={`px-2 py-1 rounded text-[10px] uppercase tracking-wider transition ${
                railPane === pane ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-500 hover:text-gray-300'
              }`}>
              {pane} <span className="opacity-60 font-mono">{n || ''}</span>
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-0.5">
          <button onClick={() => railPane === 'chats' ? newChat() : openBuilder()}
            className="w-6 h-6 flex items-center justify-center rounded-md text-emerald-300/90 hover:bg-emerald-500/10 border border-emerald-500/25 transition text-sm leading-none"
            title={railPane === 'chats' ? 'New chat' : 'Build a new agent'}>+</button>
          <button onClick={() => setSidebarSide(s => s === 'left' ? 'right' : 'left')}
            className="w-6 h-6 flex items-center justify-center rounded-md text-gray-600 hover:text-gray-300 hover:bg-white/5 transition"
            title={`Swap sides — rail to the ${sidebarSide === 'left' ? 'right' : 'left'}, market opposite`}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <line x1={sidebarSide === 'left' ? '9' : '15'} y1="3" x2={sidebarSide === 'left' ? '9' : '15'} y2="21" />
            </svg>
          </button>
          <button onClick={() => setRailClosed(true)}
            className="w-6 h-6 flex items-center justify-center rounded-md text-gray-600 hover:text-gray-300 hover:bg-white/5 transition"
            title="Collapse rail">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points={sidebarSide === 'left' ? '15 18 9 12 15 6' : '9 18 15 12 9 6'} />
            </svg>
          </button>
        </div>
      </div>

      {railPane === 'agents' ? agentsPane : <>
      {/* search — only worth the space once there's a history */}
      {tasks.length > 4 && (
        <div className="px-2.5 py-2 border-b border-white/[0.06] shrink-0">
          <input
            value={chatSearch}
            onChange={e => setChatSearch(e.target.value)}
            placeholder="Filter chats…"
            className="w-full bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition"
          />
        </div>
      )}

      {/* the list */}
      <div className="flex-1 overflow-y-auto min-h-0 p-1.5 space-y-0.5">
        {visibleChats.length === 0 ? (
          <div className="text-center text-gray-600 py-10 px-3">
            <p className="text-xs text-gray-500">{tasks.length === 0 ? 'No chats yet' : 'Nothing matches'}</p>
            {tasks.length === 0 && (
              <p className="text-[10px] text-gray-600 mt-1.5 leading-relaxed">
                Ask something in the console below — every run lands here.
              </p>
            )}
          </div>
        ) : (['today', 'yesterday', 'earlier'] as const).map(bucket => {
          const rows = visibleChats.filter(t => chatBucket(t) === bucket)
          if (rows.length === 0) return null
          return (
            <div key={bucket}>
              <div className="px-2 pt-2 pb-1 text-[9px] text-gray-600 uppercase tracking-wider">{bucket}</div>
              {rows.map(chatRow)}
            </div>
          )
        })}
      </div>
      </>}

      {/* library + builder shortcuts, then who you are */}
      <div className="px-2 py-1.5 border-t border-white/[0.06] shrink-0 flex items-center gap-1.5">
        <button onClick={() => setView('library')}
          className="flex-1 px-2 py-1.5 rounded-md text-[10px] uppercase tracking-wider text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/[0.07] border border-white/[0.06] transition">
          library
        </button>
        <button onClick={() => openBuilder()}
          className="flex-1 px-2 py-1.5 rounded-md text-[10px] uppercase tracking-wider text-gray-500 hover:text-violet-300 hover:bg-violet-500/[0.07] border border-white/[0.06] transition">
          builder
        </button>
      </div>
      {identityFooter}
    </div>
  )

  // --- Workspace — files touched by the selected run, above the console ---
  const filesPanel = (
    <div className="flex-1 flex flex-col min-h-0 bg-surface-1">
      <div className="border-b border-white/[0.06] px-4 py-2 flex items-center gap-2 shrink-0">
        {viewingFile ? (
          <>
            <button onClick={() => setViewingFile(null)}
              className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-gray-500 hover:text-gray-300 transition">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 18 9 12 15 6" />
              </svg>
              files
            </button>
            <span className={`text-[10px] font-mono ml-2 ${extColor(fileExt(viewingFile.path))}`}>
              .{fileExt(viewingFile.path)}
            </span>
            <span className="text-xs text-gray-400 font-mono truncate">{shortPath(viewingFile.path)}</span>
            {(() => {
              const b = actionBadge(viewingFile.action)
              return (
                <span className={`text-[9px] px-1.5 py-0.5 rounded-md border ${b.bg} ${b.text} shrink-0`}>
                  {viewingFile.action}
                </span>
              )
            })()}
          </>
        ) : (
          <>
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-400">files</span>
            {currentTask && (
              <span className="text-[10px] text-gray-600 font-mono">
                {getTaskFiles(currentTask).length} touched
              </span>
            )}
            {currentTask && (
              <span className="text-xs text-gray-600 truncate ml-2 min-w-0">{currentTask.query}</span>
            )}
          </>
        )}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {!currentTask ? (
          <div className="flex items-center justify-center h-full text-gray-600">
            <div className="flex flex-col items-center text-center">
              <div className="w-14 h-14 rounded-2xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-center mb-4">
                <span className="text-emerald-400/70 font-mono select-none">{'>'}<span className="caret-blink">_</span></span>
              </div>
              <p className="text-xs text-gray-600">Run a task in the console below to see file changes</p>
            </div>
          </div>
        ) : viewingFile ? (
          <pre className="text-[12px] leading-[1.6] font-mono text-gray-300 p-4 whitespace-pre-wrap">
            {viewingFile.content ? (
              viewingFile.content.split('\n').map((line, i) => (
                <div key={i} className="flex hover:bg-white/[0.02] transition-colors">
                  <span className="text-gray-700 select-none w-12 shrink-0 text-right pr-4 text-[11px]">{i + 1}</span>
                  <span className="flex-1 min-w-0">{line || ' '}</span>
                </div>
              ))
            ) : (
              <span className="text-gray-600">No content available</span>
            )}
          </pre>
        ) : (
          <div className="p-4">
            {(() => {
              const files = getTaskFiles(currentTask)
              if (files.length === 0) return (
                <div className="flex items-center justify-center h-64 text-gray-600">
                  <div className="text-center">
                    <p className="text-xs">No files touched yet</p>
                    {currentTask.status === 'running' && (
                      <div className="flex items-center gap-2 mt-3 justify-center">
                        <div className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
                        <span className="text-[10px] text-gray-500">Agent working...</span>
                      </div>
                    )}
                  </div>
                </div>
              )
              return (
                <div className="space-y-1 max-w-3xl">
                  {files.map((f, i) => {
                    const b = actionBadge(f.action)
                    return (
                      <button key={i}
                        onClick={() => setViewingFile(f)}
                        className="w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-white/[0.04] transition group border border-transparent hover:border-white/[0.06]">
                        <span className={`text-sm ${extColor(fileExt(f.path))}`}>
                          {fileExt(f.path) === 'py' ? '◆' : fileExt(f.path) === 'ts' || fileExt(f.path) === 'tsx' ? '◇' : '○'}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="text-xs text-gray-300 group-hover:text-gray-200 font-mono truncate">
                            {f.path.split('/').pop()}
                          </div>
                          <div className="text-[10px] text-gray-600 font-mono truncate mt-0.5">
                            {shortPath(f.path)}
                          </div>
                        </div>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded-md border ${b.bg} ${b.text} shrink-0`}>
                          {f.action}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )
            })()}
          </div>
        )}
      </div>
    </div>
  )

  // --- The console dock — bottom of the screen, full width, resizable ---
  const consoleDock = (
    <div
      className={`relative flex flex-col min-h-0 bg-surface-0 border-t border-white/[0.06] ${dock === 'max' ? 'flex-1' : 'shrink-0'}`}
      style={dock === 'normal' ? { height: dockHeight } : undefined}
    >
      {/* drag the console's top edge to resize; double-click to maximize */}
      {dock === 'normal' && (
        <div
          className="absolute -top-[5px] left-0 right-0 h-[10px] z-20 cursor-row-resize touch-none group"
          onPointerDown={onDockDragStart}
          onDoubleClick={toggleDockMax}
        >
          <div className="w-full h-[1px] mt-[4px] bg-transparent group-hover:bg-emerald-500/60 group-active:bg-emerald-500 transition-colors" />
          <div className="absolute left-1/2 -translate-x-1/2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none flex gap-[3px]">
            <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
            <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
            <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
          </div>
        </div>
      )}

      {/* dock header — two lines: tabs + what's running on top, the run
          controls (persona, model, credit) on their own line below, so neither
          crowds the other in a narrow window. */}
      <div className="border-b border-white/[0.06] shrink-0 min-w-0">
      <div className="px-2 flex items-center gap-x-2 py-0.5 min-w-0">
        <div className="flex items-center shrink-0">
          {(['output', 'tools', 'memory', 'deltas'] as Tab[]).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`tab-btn px-3 py-2 text-[10px] font-medium uppercase tracking-wider transition-colors relative ${
                activeTab === tab ? 'text-white' : 'text-gray-600 hover:text-gray-400'
              }`}
            >
              {tab === 'output' ? 'console' : tab}
              {tab === 'tools' && (currentTask && getSteps(currentTask).length > 0 ? (
                <span className="ml-1 text-[9px] text-emerald-400/80 normal-case">{getSteps(currentTask).length}</span>
              ) : toolCounts ? (
                <span className="ml-1 text-[9px] text-gray-600 normal-case">{toolCounts.total}</span>
              ) : null)}
              {tab === 'memory' && memSel.length > 0 && (
                <span className="ml-1 text-[9px] text-sky-400/80 normal-case">{memSel.length}</span>
              )}
              {tab === 'deltas' && currentTask && getDeltas(currentTask).length > 0 && (
                <span className="ml-1 text-[9px] text-amber-400/80 normal-case">{getDeltas(currentTask).length}</span>
              )}
              {activeTab === tab && (
                <span className="absolute bottom-0 left-1 right-1 h-[1.5px] bg-emerald-500 rounded-full" />
              )}
            </button>
          ))}
        </div>

        {/* what this console is currently on */}
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {currentTask ? (
            <>
              <span className={`text-[11px] shrink-0 ${statusIcon(currentTask.status)}`}>{statusDot(currentTask.status)}</span>
              <span className="text-xs text-gray-400 truncate min-w-0">{currentTask.query}</span>
              <span className="text-[10px] text-gray-600 font-mono shrink-0">{taskTime(currentTask)}</span>
              {currentTask.status !== 'running' && (
                <button onClick={continueTask}
                  className="w-6 h-6 flex items-center justify-center rounded text-[10px] text-emerald-300 hover:bg-emerald-500/10 transition shrink-0"
                  title="Continue this chat">↳</button>
              )}
              {currentTask.status === 'running' && (
                <button onClick={completeTask}
                  className="w-6 h-6 flex items-center justify-center rounded text-[10px] text-emerald-400 hover:bg-emerald-500/10 transition shrink-0"
                  title="Mark done">✓</button>
              )}
              <button onClick={dismissTask}
                className="w-6 h-6 flex items-center justify-center rounded text-gray-600 hover:text-gray-400 hover:bg-white/5 transition text-xs shrink-0"
                title="Delete this chat">✕</button>
            </>
          ) : tasks.length > 0 ? (
            <span className="text-[11px] text-gray-700">· new chat</span>
          ) : null}
        </div>

        {/* dock size controls */}
        <div className="flex items-center gap-0.5 shrink-0 pl-1">
          <button
            onClick={() => setDockPersist(dock === 'min' ? 'normal' : 'min')}
            className={`w-6 h-6 flex items-center justify-center rounded transition ${
              dock === 'min' ? 'text-emerald-300 bg-emerald-500/10' : 'text-gray-600 hover:text-gray-300 hover:bg-white/5'
            }`}
            title={dock === 'min' ? 'Show the transcript' : 'Collapse the console'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points={dock === 'min' ? '18 15 12 9 6 15' : '6 9 12 15 18 9'} />
            </svg>
          </button>
          <button
            onClick={toggleDockMax}
            className={`w-6 h-6 flex items-center justify-center rounded transition ${
              dock === 'max' ? 'text-emerald-300 bg-emerald-500/10' : 'text-gray-600 hover:text-gray-300 hover:bg-white/5'
            }`}
            title={dock === 'max' ? 'Restore the split (Esc)' : 'Maximize the console'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              {dock === 'max' ? (
                <>
                  <polyline points="4 14 10 14 10 20" />
                  <polyline points="20 10 14 10 14 4" />
                </>
              ) : (
                <>
                  <polyline points="15 3 21 3 21 9" />
                  <polyline points="9 21 3 21 3 15" />
                </>
              )}
            </svg>
          </button>
        </div>
      </div>

      {/* second line — run controls */}
      <div className="px-2 pb-1 flex items-center gap-1.5 min-w-0 overflow-x-auto no-scrollbar">
        {personaPicker}
        {modelControls}
        {balancePill}
      </div>
      </div>

      {dock !== 'min' && <div className="flex-1 min-h-0">{transcript}</div>}
      {composeBar}
    </div>
  )

  // overlays shared by both layouts (fullscreen + normal)
  const overlays = (
    <>
      {showKeyPanel && (
        <KeyPanel
          initialProvider={keyPanelProvider || provider}
          onClose={() => { setShowKeyPanel(false); setKeyPanelProvider(null) }}
          onSaved={() => { fetchBalance(); setKeyVersion(v => v + 1) }}
        />
      )}
      <CreditsSidebar
        open={showCredits}
        onClose={() => setShowCredits(false)}
        auth={auth}
        info={creditsInfo}
        onRefresh={fetchCredits}
        spend={spendCredits}
        onSpendChange={setSpendCreditsPersist}
        onSignIn={signIn}
      />
      {lightbox && (
        <div onClick={() => setLightbox(null)}
          className="fixed inset-0 z-[100] bg-black/85 backdrop-blur-sm flex items-center justify-center p-8 cursor-zoom-out">
          <img src={lightbox.srcs[lightbox.i]} alt="attachment"
            onClick={e => e.stopPropagation()}
            className="max-h-full max-w-full rounded-lg border border-white/10 cursor-default" />
          <button onClick={() => setLightbox(null)} title="Close (Esc)"
            className="absolute top-4 right-5 w-8 h-8 rounded-lg bg-black/60 border border-white/10 text-gray-400 hover:text-gray-100 hover:border-white/25 transition">✕</button>
          {lightbox.srcs.length > 1 && (
            <>
              {([['‹', -1, 'left-5'], ['›', 1, 'right-5']] as const).map(([glyph, d, side]) => (
                <button key={side} onClick={e => { e.stopPropagation(); stepLightbox(d) }}
                  className={`absolute ${side} top-1/2 -translate-y-1/2 w-9 h-14 rounded-lg bg-black/60 border border-white/10 text-xl text-gray-400 hover:text-gray-100 hover:border-white/25 transition`}>{glyph}</button>
              ))}
              <span className="absolute bottom-5 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded-full bg-black/60 border border-white/10 text-[10px] font-mono text-gray-400">
                {lightbox.i + 1} / {lightbox.srcs.length}
              </span>
            </>
          )}
        </div>
      )}
    </>
  )

  // the chats rail — collapses to a strip of status dots
  const railPanel = (
    <div
      className={`flex min-h-0 sidebar-panel relative ${sidebarCollapsed ? 'w-[42px] shrink-0' : 'shrink-0'}`}
      style={sidebarCollapsed ? undefined : { width: sidebarWidth }}
    >
      {!sidebarCollapsed && (
        <div
          className={`absolute top-0 bottom-0 z-20 w-[9px] cursor-col-resize touch-none group ${sidebarSide === 'left' ? '-right-[4px]' : '-left-[4px]'}`}
          onPointerDown={onDragStart}
        >
          <div className="h-full w-[1px] ml-[4px] bg-white/[0.06] group-hover:bg-emerald-500/60 group-active:bg-emerald-500 transition-colors" />
          <div className="absolute top-1/2 -translate-y-1/2 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
            <div className="flex flex-col gap-[3px]">
              <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
              <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
              <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
            </div>
          </div>
        </div>
      )}
      <div className={`flex-1 ${sidebarSide === 'left' ? 'border-r' : 'border-l'} border-white/[0.06] flex flex-col min-h-0 min-w-0 overflow-hidden`}>
        {sidebarCollapsed ? (
          <div className="flex flex-col items-center py-3 gap-2 h-full">
            <button
              onClick={() => setRailClosed(false)}
              className="text-gray-600 hover:text-gray-400 transition p-1"
              title="Show the rail"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points={sidebarSide === 'left' ? '9 18 15 12 9 6' : '15 18 9 12 15 6'} />
              </svg>
            </button>
            <button
              onClick={() => { setRailClosed(false); setPane('chats'); newChat() }}
              className="w-6 h-6 flex items-center justify-center rounded-md text-emerald-300/90 border border-emerald-500/25 hover:bg-emerald-500/10 transition text-sm leading-none"
              title="New chat"
            >+</button>
            <button
              onClick={() => { setRailClosed(false); setPane('agents') }}
              className="w-6 h-6 flex items-center justify-center rounded-md text-gray-500 hover:text-emerald-300 hover:bg-white/[0.06] transition text-[11px] leading-none"
              title={`Agents — running as ${activePersona?.label || agentType}`}
            >{promptSel ? '¶' : (currentAgentDef?.icon || '>_')}</button>
            <div className="flex flex-col items-center gap-1.5 mt-2 overflow-y-auto min-h-0">
              {tasks.slice(0, 12).map(t => (
                <button
                  key={t.id}
                  onClick={() => { setSelectedTask(t.id); setActiveTab('output'); setPane('chats'); setRailClosed(false) }}
                  title={t.query}
                  className={`w-5 h-5 rounded flex items-center justify-center text-[9px] shrink-0 transition ${
                    selectedTask === t.id ? 'bg-emerald-500/20 border border-emerald-500/25' : 'hover:bg-white/[0.06]'
                  }`}
                >
                  <span className={statusIcon(t.status)}>{statusDot(t.status)}</span>
                </button>
              ))}
            </div>
            {/* the key sits at the foot of the strip, where the identity
                footer sits when the rail is open */}
            <div className="mt-auto pt-2">{keyStripButton}</div>
          </div>
        ) : railContent}
      </div>
    </div>
  )

  // the market rail — the library docked on the far side of the workspace,
  // so a run can be dressed with a prompt, a note or a tool without leaving
  // the console. Collapses to a strip like the chats rail.
  const marketPanel = (
    <div
      className={`flex min-h-0 sidebar-panel relative ${marketOpen ? 'shrink-0' : 'w-[42px] shrink-0'}`}
      style={marketOpen ? { width: marketWidth } : undefined}
    >
      {marketOpen && (
        <div
          className={`absolute top-0 bottom-0 z-20 w-[9px] cursor-col-resize touch-none group ${marketSide === 'left' ? '-right-[4px]' : '-left-[4px]'}`}
          onPointerDown={onMarketDragStart}
        >
          <div className="h-full w-[1px] ml-[4px] bg-white/[0.06] group-hover:bg-emerald-500/60 group-active:bg-emerald-500 transition-colors" />
        </div>
      )}
      <div className={`flex-1 ${marketSide === 'left' ? 'border-r' : 'border-l'} border-white/[0.06] flex flex-col min-h-0 min-w-0 overflow-hidden`}>
        {marketOpen ? (
          <Market
            auth={auth}
            host={owner}
            activeAgent={agentType}
            activePromptId={promptSel?.id ?? null}
            memSel={memSel}
            toolSel={toolSel}
            refreshKey={libVersion}
            onSelectAgent={(name) => { selectAgent(name); fetchAgents(auth?.token) }}
            onSelectPrompt={(item: LibItem) => selectPrompt({
              id: item.id, name: item.name, description: item.description || '',
              body: item.body || '', tags: item.tags || [],
              owner: item.owner ?? null, owner_source: (item.owner_source ?? null) as OwnerSource,
            })}
            onToggleMemory={toggleNote}
            onToggleTool={(id) => setToolSel(prev =>
              prev.includes(id) ? prev.filter(s => s !== id) : [...prev, id])}
            onOpenLibrary={() => setView('library')}
            onClose={() => setMarketOpenPersist(false)}
            onCreated={(kind) => { if (kind === 'agent') fetchAgents(auth?.token); fetchLibrary(); libChanged() }}
            onSignIn={signIn}
          />
        ) : (
          <div className="flex flex-col items-center py-3 gap-2 h-full">
            <button
              onClick={() => setMarketOpenPersist(true)}
              className="text-gray-600 hover:text-gray-400 transition p-1"
              title="Show the market"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points={marketSide === 'left' ? '9 18 15 12 9 6' : '15 18 9 12 15 6'} />
              </svg>
            </button>
            <button
              onClick={() => setMarketOpenPersist(true)}
              title="Market — prompts, tools, memory and agents"
              className="[writing-mode:vertical-rl] text-[10px] uppercase tracking-widest text-gray-600 hover:text-emerald-300 transition py-2"
            >
              market
            </button>
          </div>
        )}
      </div>
    </div>
  )

  // workspace: files on top, console docked along the bottom
  const workspace = (
    <div className="flex-1 flex flex-col min-h-0 min-w-0">
      {dock !== 'max' && <div className="flex-1 min-h-0 flex flex-col">{filesPanel}</div>}
      {consoleDock}
    </div>
  )

  return (
    <main className="h-screen flex flex-col bg-surface-0">
      {/* top bar — the four views and who you are. Everything else lives where
          it's used: the rails carry their own collapse, the dock its own size,
          the key and the tool count sit in the rail's foot. */}
      <header className="border-b border-white/[0.06] px-3 h-11 flex items-center gap-3 shrink-0 bg-surface-0">
        <div className="flex items-center gap-2.5 shrink-0" title="Agent — mod framework">
          <div className="brand-mark w-7 h-7 flex items-center justify-center shrink-0">
            <span className="select-none">{'>'}_</span>
          </div>
          {/* wordmark — first thing the eye lands on, so it carries the theme's
              accent. Drops out under 640px, where the bar needs the room. */}
          <span className="title-gradient uppercase select-none hidden sm:block">agent</span>
        </div>

        <nav className="flex items-center gap-0.5 bg-white/[0.03] border border-white/[0.07] rounded-lg p-0.5">
          {(['console', 'builder', 'arena', 'library', 'tasks'] as const).map(v => (
            <button key={v}
              onClick={() => { if (v === 'tasks' && view !== 'tasks') fetchServerTasks(); setView(v) }}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] font-medium uppercase tracking-wider transition ${
                view === v ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-500 hover:text-gray-300'
              }`}
              title={v === 'tasks' && runningCount > 0 ? `${runningCount} running in the background` : undefined}>
              {v}
              {v === 'tasks' && runningCount > 0 && (
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              )}
            </button>
          ))}
        </nav>

        <div className="flex items-center gap-3 ml-auto">
          {loading && (
            <span className="flex items-center gap-1.5 text-xs text-emerald-300">
              <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
              working
            </span>
          )}
          {apiStatus === 'down' && (
            <span className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-0.5 rounded-md">
              API offline
            </span>
          )}
          <ThemePicker theme={theme} onPick={setTheme} />
          {userChip}
        </div>
      </header>

      {/* layout body */}
      <div className="flex-1 flex min-h-0">
        {/* background tasks — full page */}
        {view === 'tasks' && tasksPage}

        {/* arena — every agent on the same tasks, one ranked board */}
        {view === 'arena' && (
          <div className="flex-1 min-h-0 flex">
            <Arena token={auth?.token} isHost={isHost} />
          </div>
        )}

        {/* visual agent builder */}
        {view === 'builder' && (
          <div className="flex-1 min-h-0">
            <Builder
              key={builderAgent || 'new'}
              initialAgent={builderAgent}
              onUseAgent={(name, memoryIds) => {
                selectAgent(name)
                if (memoryIds.length) {
                  setMemSel(memoryIds)
                  try { localStorage.setItem('agent_mem_sel', JSON.stringify(memoryIds)) } catch {}
                }
                setView('console')
                if (dock === 'min') setDockPersist('normal')
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onAgentsChanged={() => { fetchAgents(auth?.token); libChanged() }}
              onManageKey={(p) => { setKeyPanelProvider(p); setShowKeyPanel(true) }}
              keyVersion={keyVersion}
              token={auth?.token}
              isHost={isHost}
              onSignIn={signIn}
            />
          </div>
        )}

        {/* library market view */}
        {view === 'library' && (
          <div className="flex-1 min-h-0">
            <Library
              onUsePrompt={(text) => {
                setView('console')
                setQuery(text)
                if (dock === 'min') setDockPersist('normal')
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onSelectAgent={(name) => {
                selectAgent(name)
                setView('console')
                if (dock === 'min') setDockPersist('normal')
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onSelectPrompt={(item) => {
                selectPrompt({ id: item.id, name: item.name, description: item.description || '',
                  body: item.body || '', tags: item.tags || [],
                  owner: item.owner ?? null, owner_source: (item.owner_source ?? null) as OwnerSource })
                setView('console')
                if (dock === 'min') setDockPersist('normal')
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onUseMemory={(id) => {
                toggleNote(id)
                setView('console')
                if (dock === 'min') setDockPersist('normal')
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onUseTool={(id) => {
                setToolSel(prev => prev.includes(id) ? prev.filter(s => s !== id) : [...prev, id])
                setView('console')
                if (dock === 'min') setDockPersist('normal')
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onAgentsChanged={() => { fetchAgents(auth?.token); libChanged() }}
              onSignIn={signIn}
              auth={auth}
              host={owner}
            />
          </div>
        )}

        {/* console: chats + agents rail on one side, the market on the other,
            console docked at the bottom */}
        {view === 'console' && (
          sidebarSide === 'left'
            ? <>{railPanel}{workspace}{marketPanel}</>
            : <>{marketPanel}{workspace}{railPanel}</>
        )}
      </div>
      {overlays}
    </main>
  )
}

// ── API Key panel — balance + set your own key ──────────────────────

function KeyPanel({ initialProvider, onClose, onSaved }: {
  initialProvider: string
  onClose: () => void
  onSaved: () => void
}) {
  const tabs = Object.keys(PROVIDER_META)
  const [tab, setTab] = useState(tabs.includes(initialProvider) ? initialProvider : 'openrouter')
  const [info, setInfo] = useState<KeyBalance | null>(null)
  const [loadingInfo, setLoadingInfo] = useState(true)

  // add / replace key
  const [newKey, setNewKey] = useState('')
  const [encrypt, setEncrypt] = useState(true)
  const [passphrase, setPassphrase] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)

  // unlock
  const [unlockPass, setUnlockPass] = useState('')
  const [unlocking, setUnlocking] = useState(false)
  const [confirmForget, setConfirmForget] = useState(false)
  // ON by default: unlock once, stay unlocked — retyping the passphrase after
  // every restart is what made the vault annoying to actually live with
  const [remember, setRemember] = useState(true)

  const refresh = useCallback((p: string) => {
    setLoadingInfo(true)
    fetch(`${API_URL}/balance?provider=${encodeURIComponent(p)}`, { signal: AbortSignal.timeout(15000) })
      .then(r => r.json())
      .then(d => { setInfo(d); setLoadingInfo(false) })
      .catch(() => setLoadingInfo(false))
  }, [])

  useEffect(() => {
    setErr(null); setOk(null); setConfirmForget(false); setUnlockPass('')
    refresh(tab)
  }, [tab, refresh])

  const post = async (path: string, body: any) => {
    const res = await fetch(`${API_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return res.json()
  }

  const save = async () => {
    if (!newKey.trim()) return
    if (encrypt && passphrase.length < 4) { setErr('passphrase must be at least 4 characters'); return }
    setSaving(true); setErr(null); setOk(null)
    try {
      const data = await post('/key', {
        api_key: newKey.trim(), provider: tab,
        passphrase: encrypt ? passphrase : undefined,
        remember,
      })
      if (data.error) { setErr(data.error); setSaving(false); return }
      setOk(encrypt
        ? (remember
          ? 'Key sealed with your passphrase — unlocked and kept unlocked on this server.'
          : 'Key sealed with your passphrase — unlocked for this session only.')
        : 'Key saved.')
      setNewKey(''); setPassphrase('')
      setSaving(false)
      refresh(tab); onSaved()
    } catch (e: any) { setErr(e.message); setSaving(false) }
  }

  const unlock = async () => {
    if (!unlockPass) return
    setUnlocking(true); setErr(null); setOk(null)
    try {
      const data = await post('/key/unlock', { provider: tab, passphrase: unlockPass, remember })
      if (data.error) { setErr(data.error === 'wrong passphrase' ? 'Wrong passphrase.' : data.error); setUnlocking(false); return }
      const r = data.result || data
      setOk(`Unlocked ${r.key || ''} — ${r.remembered
        ? 'staying unlocked on this server, no passphrase next time.'
        : 'live for this session.'}`)
      setUnlockPass(''); setUnlocking(false)
      refresh(tab); onSaved()
    } catch (e: any) { setErr(e.message); setUnlocking(false) }
  }

  const lock = async () => {
    setErr(null); setOk(null)
    try {
      const data = await post('/key/lock', { provider: tab })
      if (data.error) { setErr(data.error); return }
      setOk('Locked — key wiped from memory; your passphrase is needed again.')
      refresh(tab); onSaved()
    } catch (e: any) { setErr(e.message) }
  }

  const forget = async () => {
    if (!confirmForget) { setConfirmForget(true); return }
    setErr(null); setOk(null)
    try {
      const res = await fetch(`${API_URL}/key?provider=${encodeURIComponent(tab)}`, { method: 'DELETE' })
      const data = await res.json()
      if (data.error) { setErr(data.error); return }
      setOk('Encrypted key deleted.')
      setConfirmForget(false)
      refresh(tab); onSaved()
    } catch (e: any) { setErr(e.message) }
  }

  const locked = !!info?.encrypted && !info?.unlocked
  const meta = PROVIDER_META[tab]

  // one control, shown wherever a passphrase is asked for
  const rememberToggle = (tone: 'amber' | 'emerald') => (
    <button onClick={() => setRemember(v => !v)}
      className="mt-2.5 flex items-center gap-2 text-[11px] text-gray-500 hover:text-gray-300 transition text-left">
      <span className={`w-8 rounded-full p-0.5 transition-colors flex items-center h-[18px] shrink-0 ${
        remember ? (tone === 'amber' ? 'bg-amber-500/70' : 'bg-emerald-500/70') : 'bg-white/10'
      }`}>
        <span className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${remember ? 'translate-x-3.5' : ''}`} />
      </span>
      stay unlocked on this server — don't ask for my passphrase again
    </button>
  )

  const lockIcon = (open: boolean, size = 11) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      <rect x="4" y="11" width="16" height="10" rx="2" />
      {open ? <path d="M8 11V7a4 4 0 0 1 7.7-1.5" /> : <path d="M8 11V7a4 4 0 0 1 8 0v4" />}
    </svg>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-md vault-panel bg-surface-1 border border-white/10 rounded-2xl shadow-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
        {/* header */}
        <div className="px-5 py-4 border-b border-white/[0.06] flex items-center gap-3">
          <span className="vault-ring w-7 h-7 rounded-lg flex items-center justify-center text-emerald-300">
            {lockIcon(!locked, 13)}
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-100 tracking-tight">API keys</div>
            <div className="text-[10px] text-gray-600">bring your own key · encrypted vault</div>
          </div>
          <button onClick={onClose} className="ml-auto text-gray-600 hover:text-gray-300 transition p-1">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* provider tabs */}
        <div className="px-5 pt-3 flex items-center gap-1.5">
          {tabs.map(p => (
            <button key={p} onClick={() => setTab(p)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition border ${
                tab === p
                  ? 'bg-emerald-500/12 border-emerald-500/35 text-emerald-200'
                  : 'bg-white/[0.03] border-white/[0.07] text-gray-500 hover:text-gray-300 hover:border-white/20'
              }`}>
              {PROVIDER_META[p].label}
            </button>
          ))}
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* status / balance card */}
          <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
            {loadingInfo ? (
              <div className="text-sm text-gray-600 shimmer-text w-fit">checking key…</div>
            ) : info?.configured ? (
              <>
                <div className="flex items-baseline gap-2">
                  <span className={`text-2xl font-mono font-semibold ${
                    typeof info.balance === 'number' && info.balance > 0 ? 'text-emerald-300' : 'text-amber-300'
                  }`}>
                    {typeof info.balance === 'number' ? `$${info.balance.toFixed(2)}` : '—'}
                  </span>
                  <span className="text-[10px] text-gray-600 uppercase tracking-wider">remaining</span>
                  <span className="ml-auto flex items-center gap-1">
                    {info.encrypted && (
                      <span className={`flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded font-mono uppercase tracking-wider border ${
                        info.unlocked
                          ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                          : 'bg-amber-500/10 border-amber-500/30 text-amber-300'
                      }`}>
                        {lockIcon(!!info.unlocked, 9)}
                        {info.unlocked ? (info.remembered ? 'stays unlocked' : 'unlocked') : 'locked'}
                      </span>
                    )}
                  </span>
                </div>
                {typeof info.total_credits === 'number' && (
                  <div className="text-[11px] text-gray-500 mt-1.5 font-mono">
                    ${info.total_usage?.toFixed(2)} used of ${info.total_credits.toFixed(2)} credits
                  </div>
                )}
                {info.balances && !('balance' in info && typeof info.balance === 'number') && (
                  <div className="text-[11px] text-gray-500 mt-1.5 font-mono">
                    {Object.entries(info.balances).map(([k, v]) => `${k} ${Number(v).toFixed(2)}`).join(' · ')}
                  </div>
                )}
                <div className="text-[11px] text-gray-600 mt-1.5 font-mono truncate">
                  key: {info.key}{info.source ? ` · via ${info.source}` : ''}
                </div>
                {info.remembered && (
                  <div className="text-[10px] text-gray-600 mt-1">
                    unlocked on this server{info.remember_expires
                      ? ` until ${new Date(info.remember_expires * 1000).toLocaleDateString()}`
                      : ''} — "Lock now" ends that.
                  </div>
                )}
                {info.error && <div className="text-[11px] text-red-400/80 mt-1.5">{info.error}</div>}
              </>
            ) : locked ? (
              <div className="flex items-center gap-2 text-sm text-amber-300">
                {lockIcon(false, 13)}
                <span>Encrypted key on file{info?.hint ? ` (${info.hint})` : ''} — unlock it below.</span>
              </div>
            ) : (
              <div className="text-sm text-gray-500">No {meta.label} key configured — add yours below.</div>
            )}
          </div>

          {/* unlock — vault exists but key not in memory */}
          {locked && (
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-3.5">
              <div className="text-[10px] text-amber-300/80 uppercase tracking-wider font-medium mb-2 flex items-center gap-1.5">
                {lockIcon(false, 10)} unlock your key
              </div>
              <div className="flex gap-2">
                <input
                  value={unlockPass}
                  onChange={e => setUnlockPass(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') unlock() }}
                  placeholder="passphrase…"
                  type="password"
                  autoFocus
                  className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-gray-200 outline-none font-mono placeholder:text-gray-600 focus:border-amber-500/40 transition"
                />
                <button onClick={unlock} disabled={unlocking || !unlockPass}
                  className="px-4 py-2 rounded-lg text-xs font-medium bg-amber-500/90 hover:bg-amber-400 disabled:bg-white/5 disabled:text-gray-600 text-black transition shrink-0">
                  {unlocking ? 'Unlocking…' : 'Unlock'}
                </button>
              </div>
              {rememberToggle('amber')}
            </div>
          )}

          {/* vault controls — key currently unlocked */}
          {info?.encrypted && info?.unlocked && (
            <div className="flex items-center gap-2">
              <button onClick={lock}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/[0.04] border border-white/[0.08] text-gray-300 hover:border-amber-500/40 hover:text-amber-300 transition">
                {lockIcon(false, 10)} Lock now
              </button>
              <button onClick={forget}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
                  confirmForget
                    ? 'bg-red-500/15 border-red-500/40 text-red-300'
                    : 'bg-white/[0.02] border-white/[0.06] text-gray-600 hover:text-red-300 hover:border-red-500/30'
                }`}>
                {confirmForget ? 'Really delete?' : 'Delete encrypted key'}
              </button>
            </div>
          )}

          {/* add / replace key */}
          <div>
            <div className="text-[10px] text-gray-600 uppercase tracking-wider font-medium mb-2">
              {info?.configured || info?.encrypted ? 'replace key' : 'add your key'}
            </div>
            <input
              value={newKey}
              onChange={e => { setNewKey(e.target.value); setOk(null) }}
              onKeyDown={e => { if (e.key === 'Enter' && !encrypt) save() }}
              placeholder={meta.placeholder}
              type="password"
              className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-gray-200 outline-none font-mono placeholder:text-gray-600 focus:border-emerald-500/40 transition"
            />

            {/* encrypt toggle */}
            <button onClick={() => setEncrypt(v => !v)}
              className="mt-2.5 flex items-center gap-2 text-xs text-gray-400 hover:text-gray-200 transition group">
              <span className={`w-8 h-4.5 rounded-full p-0.5 transition-colors flex items-center h-[18px] ${encrypt ? 'bg-emerald-500/70' : 'bg-white/10'}`}>
                <span className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${encrypt ? 'translate-x-3.5' : ''}`} />
              </span>
              <span className="flex items-center gap-1.5">
                {lockIcon(false, 10)}
                encrypt with a passphrase only I know
              </span>
            </button>

            {encrypt && (
              <>
                <input
                  value={passphrase}
                  onChange={e => setPassphrase(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') save() }}
                  placeholder="passphrase (never stored, never sent to providers)…"
                  type="password"
                  className="mt-2 w-full bg-white/[0.04] border border-emerald-500/20 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none font-mono placeholder:text-gray-600 focus:border-emerald-500/40 transition"
                />
                {rememberToggle('emerald')}
              </>
            )}

            <button onClick={save} disabled={saving || !newKey.trim() || (encrypt && passphrase.length < 4)}
              className="mt-2.5 w-full py-2 rounded-lg text-xs font-semibold lit-btn disabled:bg-white/5 disabled:text-gray-600 disabled:shadow-none transition">
              {saving ? 'Saving…' : encrypt ? 'Encrypt & save' : 'Save'}
            </button>

            {err && <p className="text-xs text-red-400 mt-2">{err}</p>}
            {ok && !err && <p className="text-xs text-emerald-300 mt-2">{ok}</p>}
            <p className="text-[10px] text-gray-600 mt-2.5 leading-relaxed">
              {encrypt
                ? `Sealed with AES-256-GCM under a key derived from your passphrase (PBKDF2, 600k rounds). The server stores only the encrypted file — without your passphrase nobody, including the server owner, can read it.${
                    remember
                      ? ' Staying unlocked keeps a copy sealed under this server\'s own device key so restarts don\'t ask again; "Lock now" wipes both.'
                      : ' It stays unlocked only until the API restarts.'}`
                : `Stored in plaintext on the server (~/.mod/model/${tab}) and shared with other modules. Flip the toggle above to encrypt it instead.`}
              {' '}Get a key at {meta.hint}.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
