'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { API_URL } from './config'
import Library from './components/Library'
import Builder from './components/Builder'
import CreditsSidebar, { CreditsInfo } from './components/Credits'

type Skill = { description: string; params: Record<string, any> }
type Message = { role: 'user' | 'agent' | 'system'; text: string; steps?: any[]; live?: boolean }
// uid/cid/synced tie a conversation to the server-side store: uid is the
// stable cross-device id, cid the localfs pin, synced whether the server copy
// is current. Anonymous sessions only ever live in localStorage.
type TaskEntry = { id: number; query: string; status: 'running' | 'done' | 'error'; stepCount?: number; messages: Message[]; agent_type?: string; startedAt?: number; finishedAt?: number; uid?: string; cid?: string; synced?: boolean }

const genUid = () => `c-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
type Tab = 'tasks' | 'output' | 'deltas'

type LayoutMode = 'sidebar' | 'fullscreen' | 'minimized'
type SidebarSide = 'left' | 'right'

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

type AgentOption = { value: string; label: string; icon: string; description?: string; builtin?: boolean }

const DEFAULT_AGENTS: AgentOption[] = [
  { value: "default", label: "Default", icon: ">_", builtin: true },
  { value: "architect", label: "Architect", icon: "△", builtin: true },
  { value: "reviewer", label: "Reviewer", icon: "◉", builtin: true },
  { value: "debugger", label: "Debugger", icon: "⬡", builtin: true },
  { value: "builder", label: "Builder", icon: "◆", builtin: true },
  { value: "refactorer", label: "Refactorer", icon: "⟳", builtin: true },
]

// ── Sign-in (mod protocol-auth token) ───────────────────────────────
// The API verifies this statelessly via the shared auth mod: base64url of
// { data, time, key, signature } where signature = personal_sign of the
// compact JSON {"data":…,"time":…}. Identity = the recovered signer.

type AuthInfo = { address: string; token: string; isOwner: boolean }

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
}

// a prompt from the library, selectable as an agent's system prompt
type LibPrompt = { id: string; name: string; description: string; body?: string; tags: string[] }

// a memory note from the library, selectable as run context
type MemNote = { id: string; name: string; content: string; tags: string[] }

// balance + vault info for a provider API key
type KeyBalance = {
  provider: string; configured: boolean; key: string | null; supported?: boolean
  encrypted?: boolean; unlocked?: boolean; hint?: string | null; source?: string | null
  balance?: number; total_credits?: number; total_usage?: number
  balances?: Record<string, number>; error?: string
}

export default function Home() {
  // top-level view: the agent console, the visual agent builder, the library market, or the tasks page
  const [view, setView] = useState<'console' | 'builder' | 'library' | 'tasks'>('console')
  const [query, setQuery] = useState('')
  const [skills, setSkills] = useState<Record<string, Skill>>({})
  const [loading, setLoading] = useState(false)
  const [tasks, setTasks] = useState<TaskEntry[]>([])
  const [selectedTask, setSelectedTask] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<Tab>('output')
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({})
  const [composeFocused, setComposeFocused] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  let taskId = useRef(0)
  // abort handle for the in-flight run (Stop button)
  const abortRef = useRef<AbortController | null>(null)
  // 1s ticker so the elapsed-time display updates while a task runs
  const [, setClockTick] = useState(0)

  // agent state
  const [agentType, setAgentType] = useState<string>('default')
  const [agentOptions, setAgentOptions] = useState<AgentOption[]>(DEFAULT_AGENTS)
  const [showChats, setShowChats] = useState(false)
  // keeps the selected agent chip visible in the scrollable strip
  const activeChipRef = useRef<HTMLButtonElement>(null)
  // agent to preload on the visual builder canvas (null = fresh canvas)
  const [builderAgent, setBuilderAgent] = useState<string | null>(null)

  // persona picker: run with a library prompt as system prompt + memory notes as context
  const [libPrompts, setLibPrompts] = useState<LibPrompt[]>([])
  const [memNotes, setMemNotes] = useState<MemNote[]>([])
  const [promptSel, setPromptSel] = useState<LibPrompt | null>(null)
  const [memSel, setMemSel] = useState<string[]>([])
  const [showPicker, setShowPicker] = useState(false)
  const [pickerTab, setPickerTab] = useState<'agents' | 'prompts' | 'memory'>('agents')
  const [pickerSearch, setPickerSearch] = useState('')

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

  // agent strip: one scrollable row by default, expandable to a wrapped grid
  const [agentStripExpanded, setAgentStripExpanded] = useState(false)

  const providerModels = providers.find(p => p.key === provider)?.models || []

  const onProviderChange = (p: string) => {
    setProvider(p)
    localStorage.setItem('agent_provider', p)
    const def = providers.find(x => x.key === p)?.default_model || ''
    setModel(def)
    localStorage.setItem('agent_model', def)
  }

  // layout mode: sidebar (split), fullscreen (agent only), minimized (hidden)
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('sidebar')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [sidebarSide, setSidebarSide] = useState<SidebarSide>('left')

  // file viewer state
  const [viewingFile, setViewingFile] = useState<FileEntry | null>(null)
  const [detailView, setDetailView] = useState<'output' | 'files'>('files')

  // draggable sidebar width
  const [sidebarWidth, setSidebarWidth] = useState(384)
  const isDragging = useRef(false)
  const dragStartX = useRef(0)
  const dragStartWidth = useRef(384)

  // fullscreen agent mode (dedicated, not part of layout cycle)
  const [agentFullscreen, setAgentFullscreen] = useState(false)

  // drawer state for detail panel
  const [drawerOpen, setDrawerOpen] = useState(false)

  // expanded sidebar = sidebar content fills full screen
  const [sidebarExpanded, setSidebarExpanded] = useState(false)

  const cycleLayout = () => {
    setLayoutMode(m => m === 'sidebar' ? 'fullscreen' : m === 'fullscreen' ? 'minimized' : 'sidebar')
  }

  // sidebar drag resize handlers
  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDragging.current = true
    dragStartX.current = e.clientX
    dragStartWidth.current = sidebarWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMouseMove = (ev: MouseEvent) => {
      if (!isDragging.current) return
      const delta = sidebarSide === 'left'
        ? ev.clientX - dragStartX.current
        : dragStartX.current - ev.clientX
      const maxWidth = Math.floor(window.innerWidth * 0.85)
      const newWidth = Math.max(240, Math.min(maxWidth, dragStartWidth.current + delta))
      setSidebarWidth(newWidth)
    }
    const onMouseUp = () => {
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [sidebarWidth, sidebarSide])

  // toggle fullscreen agent mode
  const toggleAgentFullscreen = useCallback(() => {
    setAgentFullscreen(f => !f)
    if (!agentFullscreen) {
      setSidebarExpanded(false)
      setSidebarCollapsed(false)
    }
  }, [agentFullscreen])

  // keyboard shortcut: Escape exits fullscreen
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && agentFullscreen) setAgentFullscreen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [agentFullscreen])

  const [apiStatus, setApiStatus] = useState<'ok' | 'down' | 'loading'>('loading')

  const fetchAgents = useCallback(() => {
    fetch(`${API_URL}/agents`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => {
        if (d.schemas && typeof d.schemas === 'object') {
          const fetched: AgentOption[] = Object.entries(d.schemas).map(([key, val]: [string, any]) => ({
            value: key,
            label: val.name || key.charAt(0).toUpperCase() + key.slice(1),
            icon: val.icon || '>_',
            description: val.description || '',
            builtin: !!val.builtin,
          }))
          if (fetched.length > 0) setAgentOptions(fetched)
        }
      })
      .catch(() => {})
  }, [])

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

  const deleteAgent = async (v: string) => {
    if (!confirm(`Delete agent "${v}"? This can't be undone.`)) return
    try {
      await fetch(`${API_URL}/agents/${encodeURIComponent(v)}`, { method: 'DELETE' })
      if (agentType === v) selectAgent('default')
      fetchAgents()
    } catch {}
  }

  // jump to the visual builder canvas, optionally preloading an agent to edit
  const openBuilder = (name?: string | null) => {
    setBuilderAgent(name || null)
    setShowPicker(false)
    setView('builder')
  }

  // scroll the active chip into view when selection or the agent list changes
  useEffect(() => {
    activeChipRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [agentType, agentOptions])

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
      }))
      else localStorage.removeItem('agent_prompt_sel')
    } catch {} // shared-origin quota — selection just isn't persisted
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

  const signIn = async () => {
    const provider = eth()
    if (!provider) {
      setAuthErr('No wallet found — install MetaMask to sign in')
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
      const token = b64url({ data, time, key: address, signature })
      let isOwner = false
      try {
        const who = await fetch(`${API_URL}/whoami?key=${encodeURIComponent(token)}`,
          { signal: AbortSignal.timeout(8000) }).then(r => r.json())
        if (who?.error) throw new Error(who.error)
        isOwner = !!who?.is_owner
      } catch {} // API offline — still sign in locally, role resolves on next load
      const next = { address, token, isOwner }
      setAuth(next)
      persistAuth(next)
      setShowUserMenu(false)
    } catch (e: any) {
      if (e?.code !== 4001) setAuthErr(e?.message || 'sign-in failed') // 4001 = user rejected
    }
    setAuthBusy(false)
  }

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
      if (!v?.address || !tokenFresh(v.token)) { persistAuth(null); return }
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

  useEffect(() => {
    fetch(`${API_URL}/skills`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => { setSkills(d.schemas || {}); setApiStatus('ok') })
      .catch(() => setApiStatus('down'))
    fetchAgents()
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
      .then(d => setOwner(d.owner || null))
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
          status: t.status, messages: t.messages,
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
    const visibleSteps = allSteps.filter((s: any) => !['response', 'error'].includes(s.tool))
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
    if (!query.trim() || loading) return
    const q = query.trim()
    setQuery('')
    setLoading(true)

    const id = ++taskId.current
    const agentLabel = promptSel ? `¶ ${promptSel.name}` : (currentAgentDef?.label || agentType)
    const userMsg: Message = { role: 'user', text: `[${agentLabel}] ${q}` }
    const task: TaskEntry = { id, uid: genUid(), query: q, status: 'running', messages: [userMsg], agent_type: agentType, startedAt: Date.now() }
    setTasks(t => [task, ...t])
    setSelectedTask(id)
    setActiveTab('output')
    if (layoutMode === 'minimized') setLayoutMode('sidebar')
    if (sidebarCollapsed) setSidebarCollapsed(false)

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
      // check API is reachable before long-running request
      try {
        const ping = await fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(3000) })
        if (!ping.ok) throw new Error()
      } catch {
        throw new Error(`API not reachable at ${API_URL}. Start with: m agent/serve`)
      }

      const body: any = { query: q }
      if (auth?.token) body.key = auth.token   // signed identity rides along
      if (agentType && agentType !== 'default') body.agent_type = agentType
      if (provider) body.provider = provider
      if (model) body.model = model
      if (promptSel) {
        // prefer the freshly-fetched body — the persisted copy may be clipped
        const bodyText = libPrompts.find(p => p.id === promptSel.id)?.body || promptSel.body
        if (bodyText) body.prompt = bodyText
      }
      if (memSel.length) body.memory_ids = memSel
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
            } else {
              last.steps = [...(last.steps || []), step]
            }
            return { ...tk, messages: msgs }
          })
        } else if (ev.type === 'done') {
          finishSingle(liveSteps.length ? liveSteps : (ev.result || []))
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
      <select
        value={provider}
        onChange={(e) => onProviderChange(e.target.value)}
        className="bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-gray-300 outline-none cursor-pointer hover:border-white/20 transition-colors shrink-0"
        title="LLM provider"
      >
        {(providers.length ? providers.map(p => p.key) : ['openrouter', 'venice']).map(k => (
          <option key={k} value={k} className="bg-[#111]">{k}</option>
        ))}
      </select>
      <select
        value={model}
        onChange={(e) => { setModel(e.target.value); localStorage.setItem('agent_model', e.target.value) }}
        className="bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-gray-300 outline-none cursor-pointer hover:border-white/20 transition-colors min-w-0 flex-1"
        style={{ maxWidth: '220px' }}
        title={model || 'Model'}
      >
        {(model && !providerModels.includes(model) ? [model, ...providerModels] : providerModels).map(mn => (
          <option key={mn} value={mn} className="bg-[#111]">{mn}</option>
        ))}
        {providerModels.length === 0 && !model && <option value="" className="bg-[#111]">default</option>}
      </select>
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
  const balancePill = (
    <button
      onClick={() => { setKeyPanelProvider(provider); setView('builder'); setShowKeyPanel(true) }}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-mono font-medium transition shrink-0 border ${
        vaultLocked
          ? 'bg-amber-500/10 border-amber-500/30 text-amber-300 hover:bg-amber-500/20 pill-locked'
          : balance && typeof balance.balance === 'number' && balance.balance > 0
          ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/20'
          : balance && (!balance.configured || (typeof balance.balance === 'number' && balance.balance <= 0))
          ? 'bg-amber-500/10 border-amber-500/25 text-amber-300 hover:bg-amber-500/20'
          : 'border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20'
      }`}
      title={vaultLocked
        ? `${balance?.provider} key is encrypted — unlock it with your passphrase in the Builder`
        : balance?.key ? `${balance.provider} key ${balance.key}${balance.unlocked ? ' (encrypted, unlocked)' : ''} — manage in the Builder` : 'Add your API key in the Builder'}
    >
      {vaultLocked && lockGlyph(false)}
      {!vaultLocked && balance?.unlocked && lockGlyph(true)}
      {fmtBalance(balance)}
    </button>
  )

  // Open the key vault modal focused on a given provider (from anywhere).
  const openKeyPanel = (p: string) => { setKeyPanelProvider(p); setView('builder'); setShowKeyPanel(true) }

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

  // elapsed / duration for a server task (epoch seconds)
  const serverTaskTime = (t: ServerTask) => {
    const end = t.status === 'running' ? Date.now() / 1000 : (t.finished_at || t.started_at)
    const sec = Math.max(0, Math.floor(end - t.started_at))
    return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, '0')}s`
  }

  const tasksButton = (
    <button
      onClick={() => { if (view !== 'tasks') fetchServerTasks(); setView(view === 'tasks' ? 'console' : 'tasks') }}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium uppercase tracking-wider transition border ${
        view === 'tasks'
          ? 'border-emerald-500/40 text-emerald-200 bg-emerald-500/10'
          : runningCount > 0
          ? 'border-emerald-500/25 text-emerald-300 bg-emerald-500/[0.06] hover:bg-emerald-500/15'
          : 'border-white/[0.06] text-gray-500 hover:text-gray-300 hover:border-white/20'
      }`}
      title={runningCount > 0 ? `${runningCount} task${runningCount !== 1 ? 's' : ''} running in the background` : 'Background tasks'}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${runningCount > 0 ? 'bg-emerald-400 animate-pulse' : 'bg-gray-600'}`} />
      tasks
      {runningCount > 0 && (
        <span className="text-[9px] px-1 py-px rounded bg-emerald-500/20 border border-emerald-500/30 text-emerald-200 font-mono normal-case">
          {runningCount}
        </span>
      )}
    </button>
  )

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
                onClick={() => setExpandedServerTasks(s => ({ ...s, [t.id]: !s[t.id] }))}
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
          title={`${auth.address}${auth.isOwner ? ' · owner' : ''}`}
        >
          <span className={`w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-mono border ${
            auth.isOwner ? 'bg-emerald-500/20 border-emerald-500/35 text-emerald-200' : 'bg-sky-500/15 border-sky-500/25 text-sky-200'
          }`}>
            {auth.address.slice(2, 4).toUpperCase()}
          </span>
          <span className="text-xs text-gray-300 font-mono">{shortAddr(auth.address)}</span>
          {auth.isOwner && (
            <span className="text-[8px] px-1 py-px rounded bg-emerald-500/15 border border-emerald-500/25 text-emerald-300 uppercase tracking-wider">
              owner
            </span>
          )}
        </button>
      ) : (
        <button
          onClick={signIn}
          disabled={authBusy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 hover:border-emerald-500/50 disabled:opacity-60 transition"
          title={authErr || 'Sign in with your wallet'}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${authBusy ? 'bg-amber-400 animate-pulse' : 'bg-emerald-400'}`} />
          {authBusy ? 'Signing…' : 'Sign in'}
        </button>
      )}
      {showUserMenu && auth && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setShowUserMenu(false)} />
          <div className="absolute right-0 top-full mt-1 w-72 bg-[#141414] border border-white/10 rounded-lg z-50 shadow-2xl overflow-hidden">
            <div className="px-4 py-3 border-b border-white/[0.06] flex items-center gap-3">
              <span className={`w-9 h-9 rounded-full flex items-center justify-center text-[11px] font-mono border shrink-0 ${
                auth.isOwner ? 'bg-emerald-500/20 border-emerald-500/35 text-emerald-200' : 'bg-sky-500/15 border-sky-500/25 text-sky-200'
              }`}>
                {auth.address.slice(2, 4).toUpperCase()}
              </span>
              <div className="min-w-0">
                <div className="text-xs text-gray-200 font-mono truncate">{auth.address}</div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  {auth.isOwner ? 'module owner — full access' : 'guest — public actions'}
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
            </div>
          </div>
        </>
      )}
    </div>
  )

  // --- Credits chip — guest balance spent on the module's public key ---
  const creditsChip = auth && !auth.isOwner && (
    <button
      onClick={() => setShowCredits(true)}
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-mono transition border ${
        showCredits
          ? 'border-emerald-500/40 text-emerald-200 bg-emerald-500/10'
          : (creditsInfo?.account?.balance ?? 0) > 0
          ? 'border-emerald-500/25 text-emerald-300 bg-emerald-500/[0.06] hover:bg-emerald-500/15'
          : 'border-white/[0.06] text-gray-500 hover:text-gray-300 hover:border-white/20'
      }`}
      title="Credits — top up USDT/USDC and spend them to run on the module's public key"
    >
      <span className="text-emerald-400/80">◈</span>
      ${(creditsInfo?.account?.balance ?? 0).toFixed(2)}
      {!spendCredits && (
        <span className="text-[8px] text-gray-500 uppercase tracking-wider" title="Spending is off — runs use free models">
          free
        </span>
      )}
    </button>
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
        onClick={() => { setShowPicker(v => !v); if (!showPicker) fetchLibrary() }}
        className={`flex items-center gap-1.5 bg-white/5 border rounded-md px-2 py-1.5 text-sm outline-none cursor-pointer transition-colors min-w-0 max-w-[200px] ${
          showPicker ? 'border-emerald-500/40 text-gray-200' : 'border-white/10 text-gray-300 hover:border-white/20'
        }`}
        title={promptSel ? `Library prompt: ${promptSel.name}` : `Agent: ${currentAgentDef?.label || agentType}`}
      >
        {promptSel ? (
          <span className="text-amber-300/90 shrink-0">¶</span>
        ) : (
          <span className="shrink-0">{currentAgentDef?.icon || '>_'}</span>
        )}
        <span className="truncate">{promptSel ? promptSel.name : (currentAgentDef?.label || agentType)}</span>
        {memSel.length > 0 && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-sky-500/15 border border-sky-500/25 text-sky-300 shrink-0 font-mono"
            title={`${memSel.length} memory note${memSel.length !== 1 ? 's' : ''} in context`}>
            +{memSel.length}
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
          <div className="absolute left-0 top-full mt-1 w-80 max-h-[65vh] flex flex-col bg-[#141414] border border-white/10 rounded-lg z-50 shadow-2xl overflow-hidden">
            {/* tabs */}
            <div className="flex items-center gap-0.5 px-1.5 pt-1.5 border-b border-white/[0.06] shrink-0">
              {([
                ['agents', `agents ${agentOptions.length}`],
                ['prompts', `prompts ${libPrompts.length}`],
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
              {pickerTab === 'agents' && agentOptions.filter(a => pickerFilter(a.label, a.description)).map(a => (
                <div key={a.value} role="button" tabIndex={0}
                  onClick={() => { selectAgent(a.value); setShowPicker(false) }}
                  onKeyDown={e => { if (e.key === 'Enter') { selectAgent(a.value); setShowPicker(false) } }}
                  className={`w-full text-left px-2.5 py-2 rounded-md text-sm transition cursor-pointer group ${
                    !promptSel && agentType === a.value
                      ? 'bg-emerald-500/10 border border-emerald-500/20 text-gray-200'
                      : 'border border-transparent text-gray-400 hover:bg-white/[0.04] hover:text-gray-200'
                  }`}>
                  <div className="flex items-center gap-2">
                    <span className="w-5 text-center shrink-0">{a.icon}</span>
                    <span className="truncate">{a.label}</span>
                    {!a.builtin && (
                      <span className="text-[9px] px-1 py-0.5 rounded bg-violet-400/10 text-violet-300/90 shrink-0">custom</span>
                    )}
                    <span className="ml-auto flex items-center gap-1 shrink-0">
                      {!a.builtin && (
                        <>
                          <button title="Edit this agent"
                            onClick={e => { e.stopPropagation(); openBuilder(a.value) }}
                            className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-[10px] text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/10 transition">
                            ✎
                          </button>
                          <button title="Delete this agent"
                            onClick={e => { e.stopPropagation(); deleteAgent(a.value) }}
                            className="opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-[10px] text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition">
                            ✕
                          </button>
                        </>
                      )}
                      {!promptSel && agentType === a.value && <span className="text-[10px] text-emerald-300">active</span>}
                    </span>
                  </div>
                  {a.description && (
                    <div className="text-[10px] text-gray-600 mt-0.5 truncate pl-7">{a.description}</div>
                  )}
                </div>
              ))}
              {pickerTab === 'agents' && (
                <button
                  onClick={() => openBuilder()}
                  className="w-full text-left px-2.5 py-2 rounded-md text-xs transition border border-dashed border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 flex items-center gap-2">
                  <span className="w-5 text-center shrink-0">+</span> build a new agent
                </button>
              )}

              {pickerTab === 'prompts' && (
                <>
                  <button
                    onClick={() => { selectPrompt(null); setShowPicker(false) }}
                    className={`w-full text-left px-2.5 py-2 rounded-md text-xs transition border ${
                      !promptSel
                        ? 'bg-emerald-500/10 border-emerald-500/20 text-gray-300'
                        : 'border-transparent text-gray-500 hover:bg-white/[0.04] hover:text-gray-300'
                    }`}>
                    None — use the agent&apos;s own prompt
                  </button>
                  {libPrompts.length === 0 ? (
                    <div className="text-xs text-gray-600 text-center py-6">No prompts in the library yet</div>
                  ) : libPrompts.filter(p => pickerFilter(p.name, p.description || p.body)).map(p => (
                    <button key={p.id}
                      onClick={() => { selectPrompt(p); setShowPicker(false) }}
                      className={`w-full text-left px-2.5 py-2 rounded-md transition border ${
                        promptSel?.id === p.id
                          ? 'bg-amber-400/10 border-amber-400/30'
                          : 'border-transparent hover:bg-white/[0.04]'
                      }`}>
                      <div className="flex items-center gap-1.5">
                        <span className="text-amber-300/80 text-xs shrink-0">¶</span>
                        <span className="text-xs font-medium text-gray-200 truncate">{p.name}</span>
                        {promptSel?.id === p.id && <span className="ml-auto text-[10px] text-amber-300 shrink-0">active</span>}
                      </div>
                      <div className="text-[10px] text-gray-500 mt-0.5 line-clamp-2 leading-relaxed">
                        {p.description || p.body?.slice(0, 100) || '—'}
                      </div>
                    </button>
                  ))}
                </>
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
              <span className="text-[10px] text-gray-600">
                {pickerTab === 'memory'
                  ? 'selected notes ride along as run context'
                  : pickerTab === 'prompts'
                  ? 'a prompt overrides the agent’s system prompt'
                  : 'personas with their own prompt + skills'}
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

  // --- Compose bar (agent prompt) — rendered at the bottom of the sidebar ---
  const composeBar = (
    <div className="border-t border-white/[0.06] px-3 py-3 shrink-0">
      <div className={`flex gap-2 items-end rounded-xl border px-3 py-2 transition-all duration-200 ${
        composeFocused
          ? 'border-emerald-500/40 bg-white/[0.04] shadow-[0_0_0_3px_rgba(52,211,153,0.08)]'
          : 'border-white/[0.08] bg-white/[0.02] hover:border-white/[0.14]'
      }`}>
        <textarea
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setComposeFocused(true)}
          onBlur={() => setComposeFocused(false)}
          placeholder={`Ask ${promptSel ? promptSel.name : (currentAgentDef?.label || 'agent')}...`}
          rows={2}
          className="flex-1 bg-transparent border-none outline-none text-[15px] resize-none placeholder:text-gray-600 py-1 leading-relaxed min-w-0"
          disabled={loading}
        />
        {loading ? (
          <button onClick={stopRun}
            className="text-sm bg-red-500/15 border border-red-500/30 hover:bg-red-500/25 text-red-400 rounded-lg px-4 py-2 transition font-medium shrink-0 mb-0.5"
            title="Stop this run">
            Stop
          </button>
        ) : (
          <button onClick={run} disabled={!query.trim() || apiStatus === 'down'}
            className="text-sm lit-btn disabled:bg-white/5 disabled:text-gray-600 disabled:shadow-none rounded-lg px-4 py-2 transition font-semibold shrink-0 mb-0.5"
            title={apiStatus === 'down' ? `API offline at ${API_URL}` : ''}>
            Run
          </button>
        )}
      </div>
    </div>
  )

  // --- Sidebar content (conversations + compose + user info) ---
  const sidebarContent = (
    <div className="flex flex-col h-full">
      {/* persona picker (agents / prompts / memory) + balance */}
      <div className="border-b border-white/[0.06] px-4 py-2 flex items-center gap-2 shrink-0 min-w-0">
        {personaPicker}
        <div className="shrink-0 ml-auto flex items-center gap-1.5">
          {balancePill}
        </div>
      </div>

      {/* agent strip — every agent one tap away, + builds your own.
          your custom agents come first so they never hide off-screen.
          collapsed: one scrollable row; expanded: wraps to show every agent */}
      <div className="border-b border-white/[0.06] px-3 py-2 shrink-0">
        <div className="flex items-start gap-1.5">
          <div className={`flex items-center gap-1.5 flex-1 min-w-0 ${
            agentStripExpanded ? 'flex-wrap' : 'overflow-x-auto no-scrollbar'
          }`}>
            {[...agentOptions].sort((a, b) => Number(!!a.builtin) - Number(!!b.builtin)).map(a => {
              const active = !promptSel && agentType === a.value
              return (
                <button key={a.value}
                  ref={active ? activeChipRef : undefined}
                  onClick={() => selectAgent(a.value)}
                  title={a.description || a.label}
                  className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs whitespace-nowrap transition border shrink-0 ${
                    active
                      ? 'bg-emerald-500/15 border-emerald-500/35 text-emerald-200 shadow-[0_0_10px_rgba(52,211,153,0.15)]'
                      : 'bg-white/[0.03] border-white/[0.07] text-gray-400 hover:text-gray-200 hover:border-white/20'
                  }`}>
                  <span className="font-mono">{a.icon}</span>
                  <span>{a.label}</span>
                  {!a.builtin && <span className="w-1 h-1 rounded-full bg-violet-400/80" />}
                </button>
              )
            })}
            <button onClick={() => openBuilder()}
              title="Build your own agent — pick a prompt, skills, and a model"
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-full text-xs whitespace-nowrap border border-dashed border-emerald-500/30 text-emerald-300/90 hover:bg-emerald-500/10 hover:border-emerald-500/50 transition shrink-0">
              <span className="leading-none">+</span> new agent
            </button>
          </div>
          <button
            onClick={() => setAgentStripExpanded(v => !v)}
            title={agentStripExpanded ? 'Collapse agent list' : `Show all ${agentOptions.length} agents`}
            className={`shrink-0 w-7 h-7 mt-px rounded-full border flex items-center justify-center transition ${
              agentStripExpanded
                ? 'border-emerald-500/35 text-emerald-300 bg-emerald-500/10'
                : 'border-white/[0.07] text-gray-500 hover:text-gray-300 hover:border-white/20'
            }`}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
              className={`transition-transform ${agentStripExpanded ? 'rotate-180' : ''}`}>
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
        </div>
        {agentStripExpanded && (
          <div className="text-[9px] text-gray-600 mt-1.5 px-1">
            {agentOptions.length} agents · {agentOptions.filter(a => !a.builtin).length} custom
          </div>
        )}
      </div>

      {/* provider + model selection */}
      <div className="border-b border-white/[0.06] px-4 py-2 flex items-center gap-2 shrink-0 min-w-0 overflow-hidden">
        <span className="text-[10px] text-gray-600 uppercase tracking-wider font-medium shrink-0">model</span>
        {modelControls}
      </div>

      {/* tab bar */}
      <div className="border-b border-white/[0.06] px-2 flex items-center shrink-0">
        {/* chats dropdown (moved out of the tabs into the header) */}
        <div className="relative shrink-0 mr-1">
          <button
            onClick={() => setShowChats(v => !v)}
            className={`tab-btn flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium uppercase tracking-wider transition-colors ${
              showChats ? 'text-white' : 'text-gray-600 hover:text-gray-400'
            }`}
            title="Conversations"
          >
            chats
            <span className="text-[9px] text-gray-600 normal-case">{tasks.length || ''}</span>
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className={`transition-transform ${showChats ? 'rotate-180' : ''}`}>
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
          {showChats && (
            <div className="absolute left-0 top-full mt-1 w-72 max-h-[60vh] overflow-y-auto bg-[#141414] border border-white/10 rounded-lg z-50 shadow-2xl p-1.5">
              {tasks.length === 0 ? (
                <div className="text-center text-gray-600 py-6 px-3">
                  <p className="text-sm text-gray-500">No conversations yet</p>
                  <div className="mt-3 flex gap-1.5 justify-center flex-wrap">
                    {['map this codebase', 'find and fix a bug', 'write tests'].map(ex => (
                      <button key={ex} onClick={() => { setQuery(ex); setShowChats(false); inputRef.current?.focus() }}
                        className="px-2.5 py-1 rounded-full text-[11px] bg-white/5 border border-white/[0.06] text-gray-500 hover:text-gray-300 hover:bg-white/[0.08] transition">
                        {ex}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="space-y-0.5">
                  {tasks.map(t => (
                    <div
                      key={t.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => { setSelectedTask(t.id); setActiveTab('output'); setShowChats(false) }}
                      onKeyDown={e => { if (e.key === 'Enter') { setSelectedTask(t.id); setActiveTab('output'); setShowChats(false) } }}
                      className={`w-full text-left px-2.5 py-2 rounded-md text-sm transition group cursor-pointer ${
                        selectedTask === t.id
                          ? 'bg-emerald-500/10 border border-emerald-500/20'
                          : 'hover:bg-white/[0.04] border border-transparent'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`text-xs ${statusIcon(t.status)}`}>{statusDot(t.status)}</span>
                        {t.agent_type && t.agent_type !== 'default' && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/5 border border-white/[0.06] text-gray-500 shrink-0">
                            {agentOptions.find(a => a.value === t.agent_type)?.icon}
                          </span>
                        )}
                        <span className="truncate flex-1 text-gray-300 group-hover:text-gray-200">{t.query}</span>
                        {t.cid && (
                          <span
                            onClick={e => { e.stopPropagation(); try { navigator.clipboard?.writeText(t.cid!) } catch {} }}
                            title={`pinned to localfs — ${t.cid}\nclick to copy CID`}
                            className="text-[9px] px-1 py-px rounded font-mono bg-emerald-500/[0.08] border border-emerald-500/15 text-emerald-300/80 hover:text-emerald-200 shrink-0"
                          >⬡ {t.cid.slice(0, 6)}…</span>
                        )}
                        <span className="text-[10px] text-gray-600 shrink-0 font-mono">
                          {t.stepCount !== undefined ? `${t.stepCount}·${taskTime(t)}` : taskTime(t)}
                        </span>
                        <button
                          onClick={e => { e.stopPropagation(); deleteConversation(t) }}
                          title="Delete conversation (local + server)"
                          className="w-4 h-4 hidden group-hover:flex items-center justify-center rounded text-gray-600 hover:text-red-400 shrink-0 text-[10px]"
                        >✕</button>
                      </div>
                    </div>
                  ))}
                  <div className="px-2.5 pt-1.5 pb-1 border-t border-white/[0.05] mt-1 text-[9px] text-gray-600 flex items-center gap-1">
                    <span className="text-emerald-400/60">⬡</span>
                    {auth
                      ? `${tasks.filter(t => t.synced).length}/${tasks.length} saved to localfs · synced across devices`
                      : 'sign in to save chats to localfs across devices'}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center">
          {(['output', 'deltas'] as Tab[]).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`tab-btn px-3 py-2.5 text-xs font-medium uppercase tracking-wider transition-colors relative ${
                activeTab === tab
                  ? 'text-white'
                  : 'text-gray-600 hover:text-gray-400'
              }`}
            >
              {tab}
              {tab === 'deltas' && currentTask && getDeltas(currentTask).length > 0 && (
                <span className="ml-1 text-[9px] text-amber-400/80 normal-case">{getDeltas(currentTask).length}</span>
              )}
              {activeTab === tab && (
                <span className="absolute bottom-0 left-1 right-1 h-[1.5px] bg-emerald-500 rounded-full" />
              )}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1.5 pr-1">
          {selectedTask && currentTask && currentTask.status !== 'running' && (
            <button onClick={continueTask}
              className="w-6 h-6 flex items-center justify-center rounded text-[10px] text-emerald-300 hover:bg-emerald-500/10 transition"
              title="Continue this task">
              ↳
            </button>
          )}
          {selectedTask && currentTask?.status === 'running' && (
            <button onClick={completeTask}
              className="w-6 h-6 flex items-center justify-center rounded text-[10px] text-emerald-400 hover:bg-emerald-500/10 transition">
              ✓
            </button>
          )}
          {selectedTask && (
            <button onClick={dismissTask}
              className="w-6 h-6 flex items-center justify-center rounded text-gray-600 hover:text-gray-400 hover:bg-white/5 transition text-xs">
              ✕
            </button>
          )}
        </div>
      </div>

      {/* tab content */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* OUTPUT tab */}
        {activeTab === 'output' && (
          <div className={`p-3 space-y-2 ${!currentTask ? 'h-full' : ''}`}>
            {!currentTask ? (
              <div className="min-h-full flex flex-col items-center justify-center text-center text-gray-600 px-4 py-6">
                <div className="w-12 h-12 rounded-2xl bg-emerald-500/[0.06] border border-emerald-500/20 hero-logo flex items-center justify-center mb-4">
                  <span className="text-emerald-300 font-mono text-sm select-none">{'>'}<span className="caret-blink">_</span></span>
                </div>
                <p className="text-sm text-gray-400 font-medium">
                  {tasks.length === 0 ? 'What should we build?' : 'Select a chat or ask below'}
                </p>
                <div className="mt-4 flex gap-1.5 justify-center flex-wrap max-w-[300px]">
                  {tasks.length === 0 ? (
                    <>
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
                    </>
                  ) : (
                    <button onClick={() => setShowChats(true)}
                      className="px-3 py-1.5 rounded-full text-xs bg-white/5 border border-white/[0.06] text-gray-500 hover:text-gray-300 hover:bg-white/[0.08] transition">
                      open chats ({tasks.length})
                    </button>
                  )}
                </div>
                <p className="mt-4 text-[10px] text-gray-600 flex items-center gap-1">
                  <span className="text-emerald-400/50">⬡</span>
                  {auth ? 'your chats are pinned to localfs and follow your wallet'
                        : 'sign in to keep chats in localfs across devices'}
                </p>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-2 px-1">
                  <span className={`text-xs ${statusIcon(currentTask.status)}`}>{statusDot(currentTask.status)}</span>
                  {currentTask.agent_type && currentTask.agent_type !== 'default' && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/5 border border-white/[0.06] text-gray-500">
                      {agentOptions.find(a => a.value === currentTask.agent_type)?.icon} {agentOptions.find(a => a.value === currentTask.agent_type)?.label}
                    </span>
                  )}
                  <span className="text-sm text-gray-400 font-medium truncate">{currentTask.query}</span>
                  <span className="text-[10px] text-gray-600 font-mono ml-auto shrink-0">{taskTime(currentTask)}</span>
                </div>

                {currentTask.messages.map((msg, i) => (
                  <div key={i} className={`${msg.role === 'user' ? 'ml-auto max-w-[85%]' : 'max-w-full'}`}>
                    <div className={`rounded-lg px-3 py-2.5 msg-in ${
                      msg.role === 'user' ? 'bg-emerald-500/10 border border-emerald-500/20' :
                      msg.role === 'system' ? 'bg-red-500/10 border border-red-500/15' :
                      'bg-white/[0.03] border border-white/[0.06]'
                    }`}>
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-xs text-gray-500">{msg.role}</span>
                      </div>
                      <div className="whitespace-pre-wrap text-sm text-gray-300 leading-relaxed">{msg.text ? renderText(msg.text) : msg.live ? <span className="text-gray-600 shimmer-text">thinking…</span> : null}</div>
                      {msg.role === 'system' && detectKeyError(msg.text) && keyErrorBanner(detectKeyError(msg.text)!)}
                      {msg.steps && (
                        <div className="mt-1.5 space-y-0.5">
                          {msg.steps.map((step: any, j: number) => (
                            <div key={j} className="text-xs bg-white/[0.03] border border-white/[0.04] rounded-md px-2.5 py-1.5">
                              <button
                                className="w-full text-left flex items-center gap-2"
                                onClick={() => toggleStep(i * 1000 + j)}
                              >
                                <span className="text-gray-600">{expandedSteps[i * 1000 + j] ? '▼' : '▶'}</span>
                                <span className="text-emerald-300">{step.tool}</span>
                                {step.params?.path || step.params?.file_path ? (
                                  <span className="text-gray-600 truncate">{shortPath(step.params.path || step.params.file_path)}</span>
                                ) : null}
                                {step.error && <span className="text-red-400 ml-auto">err</span>}
                              </button>
                              {expandedSteps[i * 1000 + j] && (
                                <>
                                  {step.result && (
                                    <pre className="text-gray-500 mt-1 overflow-x-auto max-h-48 text-xs leading-relaxed">
                                      {typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2)}
                                    </pre>
                                  )}
                                  {step.error && <pre className="text-red-400 mt-1 text-xs">{step.error}</pre>}
                                </>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                {currentTask.status === 'running' && workingIndicator(currentTask)}
                <div ref={bottomRef} />
              </>
            )}
          </div>
        )}

        {/* DELTAS tab */}
        {activeTab === 'deltas' && (
          <div className="p-3">
            {!currentTask ? (
              <div className="text-center text-gray-600 mt-12">
                <p className="text-sm">Select a task</p>
              </div>
            ) : (
              <>
                {(() => {
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
                            if (file) { setViewingFile(file); setDetailView('files') }
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
              </>
            )}
          </div>
        )}
      </div>

      {/* agent prompt — sits at the bottom of the sidebar */}
      {composeBar}

      {/* user info footer — the signed-in identity, else the module owner */}
      <div className="border-t border-white/[0.06] px-4 py-2.5 shrink-0 flex items-center gap-2.5">
        {(() => {
          const ident = auth?.address || owner
          return (
            <>
              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-mono shrink-0 ${
                auth
                  ? (auth.isOwner ? 'bg-emerald-500/15 border border-emerald-500/25 text-emerald-200' : 'bg-sky-500/15 border border-sky-500/25 text-sky-200')
                  : ident ? 'bg-white/5 border border-white/10 text-gray-400' : 'bg-white/5 border border-white/10 text-gray-500'
              }`}>
                {ident ? ident.slice(2, 4).toUpperCase() : '··'}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs text-gray-300 font-mono truncate flex items-center gap-1.5"
                  title={auth ? `signed in as ${auth.address}` : (owner ? `module owned by ${owner} — sign in with your wallet` : 'no owner configured')}>
                  {auth ? shortAddr(auth.address) : 'not signed in'}
                  {auth ? (
                    <span className={`text-[8px] px-1 py-px rounded uppercase tracking-wider ${
                      auth.isOwner ? 'bg-emerald-500/15 border border-emerald-500/25 text-emerald-300' : 'bg-sky-500/15 border border-sky-500/25 text-sky-300'
                    }`}>{auth.isOwner ? 'owner' : 'guest'}</span>
                  ) : owner ? (
                    <span className="text-[8px] px-1 py-px rounded uppercase tracking-wider bg-white/5 border border-white/10 text-gray-500"
                      title={`module owner: ${owner}`}>owned by {shortAddr(owner)}</span>
                  ) : null}
                </div>
                <div className="text-[10px] text-gray-600 truncate">
                  {provider} · {model || 'default'} · {Object.keys(skills).length} skills
                  {auth && tasks.some(t => t.synced) && (
                    <span className="text-emerald-400/60" title="conversations pinned to localfs">
                      {' '}· ⬡ {tasks.filter(t => t.synced).length} in localfs
                    </span>
                  )}
                </div>
              </div>
              {!auth && (
                <button onClick={signIn} disabled={authBusy}
                  className="text-[10px] px-2 py-1 rounded-md border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-60 transition shrink-0">
                  {authBusy ? '…' : 'Sign in'}
                </button>
              )}
              <div className="flex items-center gap-1 shrink-0">
                <span className={`w-1.5 h-1.5 rounded-full ${apiStatus === 'ok' ? 'bg-emerald-400' : apiStatus === 'down' ? 'bg-red-400' : 'bg-gray-500'}`} />
                <span className="text-[10px] text-gray-600">{apiStatus === 'ok' ? 'online' : apiStatus === 'down' ? 'offline' : '…'}</span>
              </div>
            </>
          )
        })()}
      </div>
    </div>
  )

  // --- Detail panel (file viewer + output) ---
  const detailPanel = (
    <div className="flex-1 flex flex-col min-h-0 bg-[#0c0c0c]">
      {/* detail panel header */}
      <div className="border-b border-white/[0.06] px-4 py-2 flex items-center gap-2 shrink-0">
        <div className="flex items-center">
          {(['files', 'output'] as const).map(v => (
            <button key={v} onClick={() => setDetailView(v)}
              className={`px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider transition-colors relative ${
                detailView === v ? 'text-white' : 'text-gray-600 hover:text-gray-400'
              }`}>
              {v}
              {detailView === v && <span className="absolute bottom-0 left-1 right-1 h-[1.5px] bg-emerald-500 rounded-full" />}
            </button>
          ))}
        </div>
        {viewingFile && detailView === 'files' && (
          <div className="ml-3 flex items-center gap-2 min-w-0">
            <span className={`text-[10px] font-mono ${extColor(fileExt(viewingFile.path))}`}>
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
          </div>
        )}
      </div>

      {/* detail content */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {detailView === 'files' ? (
          <>
            {!currentTask ? (
              <div className="flex items-center justify-center h-full text-gray-600">
                <div className="flex flex-col items-center text-center">
                  <div className="w-14 h-14 rounded-2xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-center mb-4">
                    <span className="text-emerald-400/70 font-mono select-none">{'>'}<span className="caret-blink">_</span></span>
                  </div>
                  <p className="text-xs text-gray-600">Run a task to see file changes</p>
                </div>
              </div>
            ) : viewingFile ? (
              /* file content viewer */
              <div className="h-full flex flex-col">
                <div className="flex-1 overflow-auto">
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
                </div>
              </div>
            ) : (
              /* file list for current task */
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
                    <div className="space-y-1">
                      <div className="text-[10px] text-gray-600 uppercase tracking-wider font-medium mb-3 px-1">
                        {files.length} file{files.length !== 1 ? 's' : ''} touched
                      </div>
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
          </>
        ) : (
          /* output view - full task detail */
          <div className="p-6">
            {!currentTask ? (
              <div className="flex flex-col items-center text-center text-gray-600 mt-24">
                <div className="w-14 h-14 rounded-2xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-center mb-4">
                  <span className="text-emerald-400/70 font-mono select-none">{'>'}<span className="caret-blink">_</span></span>
                </div>
                <p className="text-sm text-gray-500">Select a task to view details</p>
              </div>
            ) : (
              <div className="space-y-3 max-w-3xl">
                <div className="flex items-center gap-2 mb-4">
                  <span className={`text-xs ${statusIcon(currentTask.status)}`}>{statusDot(currentTask.status)}</span>
                  {currentTask.agent_type && currentTask.agent_type !== 'default' && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/5 border border-white/[0.06] text-gray-500">
                      {agentOptions.find(a => a.value === currentTask.agent_type)?.icon} {agentOptions.find(a => a.value === currentTask.agent_type)?.label}
                    </span>
                  )}
                  <span className="text-sm text-gray-300 font-medium">{currentTask.query}</span>
                </div>

                {currentTask.messages.map((msg, i) => (
                  <div key={i} className={`${msg.role === 'user' ? 'ml-auto max-w-[80%]' : 'max-w-full'}`}>
                    <div className={`rounded-lg px-4 py-3 msg-in ${
                      msg.role === 'user' ? 'bg-emerald-500/10 border border-emerald-500/20' :
                      msg.role === 'system' ? 'bg-red-500/10 border border-red-500/15' :
                      'bg-white/[0.03] border border-white/[0.06]'
                    }`}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs text-gray-500">{msg.role}</span>
                      </div>
                      <div className="whitespace-pre-wrap text-sm text-gray-300 leading-relaxed">{msg.text ? renderText(msg.text) : msg.live ? <span className="text-gray-600 shimmer-text">thinking…</span> : null}</div>
                      {msg.role === 'system' && detectKeyError(msg.text) && keyErrorBanner(detectKeyError(msg.text)!)}
                      {msg.steps && (
                        <div className="mt-2 space-y-1">
                          {msg.steps.map((step: any, j: number) => (
                            <div key={j} className="text-xs bg-white/[0.03] border border-white/[0.04] rounded-md px-2 py-1">
                              <button
                                className="w-full text-left flex items-center gap-2"
                                onClick={() => toggleStep(i * 1000 + j)}
                              >
                                <span className="text-gray-600">{expandedSteps[i * 1000 + j] ? '▼' : '▶'}</span>
                                <span className="text-emerald-300">{step.tool}</span>
                                {step.error && <span className="text-red-400 ml-auto">err</span>}
                              </button>
                              {expandedSteps[i * 1000 + j] && (
                                <>
                                  {step.result && (
                                    <pre className="text-gray-500 mt-1 overflow-x-auto max-h-60 text-[11px]">
                                      {typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2)}
                                    </pre>
                                  )}
                                  {step.error && <pre className="text-red-400 mt-1 text-[11px]">{step.error}</pre>}
                                </>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                {currentTask.status === 'running' && workingIndicator(currentTask)}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )

  // --- Fullscreen main content ---
  const mainContent = (
    <div className="flex flex-col h-full">
      <header className="border-b border-white/[0.06] px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold title-gradient">Agent</h1>
          <span className="text-xs text-gray-600">mod agentic framework</span>
        </div>
        <div className="flex items-center gap-2">
          {loading && (
            <span className="flex items-center gap-1.5 text-xs text-emerald-300">
              <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
              working
            </span>
          )}
          <span className="text-xs text-gray-600">{Object.keys(skills).length} skills</span>
        </div>
      </header>

      <div className={`border-b border-white/[0.06] px-6 py-4 shrink-0 transition-colors ${composeFocused ? 'bg-white/[0.03]' : ''}`}>
        <textarea
          ref={layoutMode === 'fullscreen' ? inputRef : undefined}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setComposeFocused(true)}
          onBlur={() => setComposeFocused(false)}
          placeholder={`Task for ${promptSel ? promptSel.name : (currentAgentDef?.label || 'agent')}...`}
          rows={3}
          className="w-full bg-transparent border-none outline-none text-sm resize-none placeholder:text-gray-600"
          disabled={loading}
        />
        <div className="flex items-center justify-between mt-2 gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {personaPicker}
            {modelControls}
          </div>
          {loading ? (
            <button onClick={stopRun}
              className="px-4 py-1.5 rounded-lg bg-red-500/15 border border-red-500/30 hover:bg-red-500/25 text-red-400 text-xs font-medium transition shrink-0 ml-3">
              Stop
            </button>
          ) : (
            <button onClick={run} disabled={!query.trim()}
              className="px-4 py-1.5 rounded-lg lit-btn disabled:bg-gray-800 disabled:text-gray-600 disabled:shadow-none text-xs font-semibold transition shrink-0 ml-3">
              Run
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 p-6">
        {tasks.length === 0 ? (
          <div className="flex flex-col items-center text-center text-gray-600 mt-20">
            <div className="w-16 h-16 rounded-2xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center mb-5">
              <span className="text-gray-500 font-mono text-lg select-none">{'>'}_</span>
            </div>
            <p className="text-sm text-gray-500">No tasks yet. Select an agent and compose a task to get started.</p>
            <div className="mt-4 flex gap-2 justify-center flex-wrap">
              {['read this file', 'search for TODO', 'run ls -la', 'find all .py files'].map(ex => (
                <button key={ex} onClick={() => { setQuery(ex); inputRef.current?.focus() }}
                  className="px-3 py-1 rounded-full text-sm bg-white/5 border border-white/[0.06] hover:bg-white/[0.08] transition">
                  {ex}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {tasks.map(t => (
              <div key={t.id} className={`rounded-lg border transition cursor-pointer ${
                selectedTask === t.id ? 'border-emerald-500/20 bg-emerald-500/[0.06]' : 'border-white/[0.06] hover:border-white/10 hover:bg-white/[0.02]'
              }`}
                onClick={() => { setSelectedTask(t.id); setLayoutMode('sidebar'); setActiveTab('output') }}
              >
                <div className="px-4 py-3 flex items-center gap-3">
                  <span className={`text-xs ${statusIcon(t.status)}`}>{statusDot(t.status)}</span>
                  {t.agent_type && t.agent_type !== 'default' && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/5 border border-white/[0.06] text-gray-500 shrink-0">
                      {agentOptions.find(a => a.value === t.agent_type)?.icon} {agentOptions.find(a => a.value === t.agent_type)?.label}
                    </span>
                  )}
                  <span className="text-sm text-gray-300 truncate flex-1">{t.query}</span>
                  <span className="text-xs text-gray-600 shrink-0 font-mono">
                    {t.status === 'running' ? `Running ${taskTime(t)}` :
                     t.status === 'done' ? `${t.stepCount || 0} steps · ${taskTime(t)}` :
                     'Failed'}
                  </span>
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>
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
    </>
  )

  // agent fullscreen mode — render only the sidebar content, no chrome
  if (agentFullscreen) {
    return (
      <main className="h-screen flex flex-col bg-[#0a0a0a] agent-fullscreen">
        {/* minimal top bar */}
        <header className="border-b border-white/[0.06] px-4 py-2 flex items-center justify-between shrink-0 bg-[#0a0a0a]">
          <div className="flex items-center gap-3">
            <h1 className="text-base font-semibold tracking-tight title-gradient">Agent</h1>
            <span className="text-[10px] text-gray-600 uppercase tracking-wider">fullscreen</span>
            {loading && (
              <span className="flex items-center gap-1.5 text-xs text-emerald-300 ml-2">
                <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
                working
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 text-[10px] text-gray-500 font-mono">
            <span className={`w-1.5 h-1.5 rounded-full ${apiStatus === 'ok' ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]' : apiStatus === 'down' ? 'bg-red-400' : 'bg-gray-600'}`} />
            {Object.keys(skills).length} skills
          </span>
            {tasksButton}
            {creditsChip}
            {userChip}
            <button
              onClick={toggleAgentFullscreen}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium transition bg-emerald-500/20 border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/30"
              title="Exit fullscreen (Esc)"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="4 14 10 14 10 20" />
                <polyline points="20 10 14 10 14 4" />
                <line x1="14" y1="10" x2="21" y2="3" />
                <line x1="3" y1="21" x2="10" y2="14" />
              </svg>
              <span>Exit</span>
            </button>
          </div>
        </header>
        <div className="flex-1 min-h-0 flex flex-col">
          {sidebarContent}
        </div>
        {overlays}
      </main>
    )
  }

  return (
    <main className="h-screen flex flex-col bg-[#0a0a0a]">
      {/* top bar */}
      <header className="border-b border-white/[0.06] px-4 py-2 flex items-center gap-4 shrink-0 bg-[#0a0a0a]">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-emerald-500/25 to-teal-500/10 border border-emerald-400/25 flex items-center justify-center shadow-[0_0_16px_rgba(52,211,153,0.15)]">
            <span className="text-emerald-200 font-mono text-[10px] select-none">{'>'}_</span>
          </div>
          <div className="leading-tight">
            <h1 className="text-sm font-semibold tracking-tight title-gradient">Agent</h1>
            <div className="text-[9px] text-gray-600 uppercase tracking-widest">mod framework</div>
          </div>
        </div>

        {/* view switcher */}
        <nav className="flex items-center gap-0.5 bg-white/[0.03] border border-white/[0.07] rounded-lg p-0.5">
          {(['console', 'builder', 'library'] as const).map(v => (
            <button key={v} onClick={() => setView(v)}
              className={`px-3.5 py-1.5 rounded-md text-[11px] font-medium uppercase tracking-wider transition ${
                view === v ? 'bg-emerald-500/15 text-emerald-200 shadow-[0_0_12px_rgba(52,211,153,0.12)]' : 'text-gray-500 hover:text-gray-300'
              }`}>
              {v}
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
          <span className="flex items-center gap-1.5 text-[10px] text-gray-500 font-mono">
            <span className={`w-1.5 h-1.5 rounded-full ${apiStatus === 'ok' ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]' : apiStatus === 'down' ? 'bg-red-400' : 'bg-gray-600'}`} />
            {Object.keys(skills).length} skills
          </span>
          {apiStatus === 'down' && (
            <span className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-0.5 rounded-md">
              API offline
            </span>
          )}

          {/* background tasks (server-side registry) */}
          {tasksButton}

          {/* fullscreen agent toggle */}
          {view === 'console' && (
          <button
            onClick={toggleAgentFullscreen}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium transition border border-white/[0.06] text-gray-500 hover:text-emerald-300 hover:border-emerald-500/25 hover:bg-emerald-500/10"
            title="Fullscreen agent (double-click divider)"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 3 21 3 21 9" />
              <polyline points="9 21 3 21 3 15" />
              <line x1="21" y1="3" x2="14" y2="10" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          </button>
          )}

          {view === 'console' && (
          <button
            onClick={cycleLayout}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium transition ${
              layoutMode === 'minimized'
                ? 'bg-white/5 border border-white/[0.06] text-gray-500 hover:text-gray-300'
                : layoutMode === 'sidebar'
                ? 'bg-emerald-500/15 border border-emerald-500/25 text-emerald-300'
                : 'bg-emerald-500/25 border border-emerald-500/35 text-emerald-200'
            }`}
            title={`Layout: ${layoutMode}`}
          >
            {layoutMode === 'minimized' && (
              <>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <line x1="9" y1="3" x2="9" y2="21" />
                </svg>
                <span>Open</span>
              </>
            )}
            {layoutMode === 'sidebar' && (
              <>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                </svg>
                <span>Full</span>
              </>
            )}
            {layoutMode === 'fullscreen' && (
              <>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                <span>Hide</span>
              </>
            )}
          </button>
          )}

          {/* credits — top up USDT/USDC, spend on the public key */}
          {creditsChip}

          {/* user identity — top-right corner */}
          {userChip}
        </div>
      </header>

      {/* layout body */}
      <div className="flex-1 flex min-h-0">
        {/* background tasks — full page */}
        {view === 'tasks' && tasksPage}

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
                if (layoutMode === 'minimized') setLayoutMode('sidebar')
                if (sidebarCollapsed) setSidebarCollapsed(false)
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onAgentsChanged={fetchAgents}
              onManageKey={(p) => { setKeyPanelProvider(p); setShowKeyPanel(true) }}
              keyVersion={keyVersion}
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
                if (layoutMode === 'minimized') setLayoutMode('sidebar')
                if (sidebarCollapsed) setSidebarCollapsed(false)
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onSelectAgent={(name) => {
                selectAgent(name)
                setView('console')
                if (layoutMode === 'minimized') setLayoutMode('sidebar')
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onSelectPrompt={(item) => {
                selectPrompt({ id: item.id, name: item.name, description: item.description || '', body: item.body || '', tags: item.tags || [] })
                setView('console')
                if (layoutMode === 'minimized') setLayoutMode('sidebar')
                if (sidebarCollapsed) setSidebarCollapsed(false)
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onUseMemory={(id) => {
                toggleNote(id)
                setView('console')
                if (layoutMode === 'minimized') setLayoutMode('sidebar')
                if (sidebarCollapsed) setSidebarCollapsed(false)
                setTimeout(() => inputRef.current?.focus(), 60)
              }}
              onAgentsChanged={fetchAgents}
            />
          </div>
        )}

        {/* sidebar mode: split view */}
        {view === 'console' && layoutMode === 'sidebar' && (
          <>
            {(() => {
              const sidebarPanel = (
                <div className={`flex min-h-0 sidebar-panel relative ${sidebarExpanded ? 'flex-1' : sidebarCollapsed ? 'w-[42px] shrink-0' : 'shrink-0'}`}
                  style={sidebarCollapsed || sidebarExpanded ? undefined : { width: sidebarWidth }}>
                  {/* drag handle */}
                  {!sidebarCollapsed && !sidebarExpanded && (
                    <div
                      className={`absolute top-0 bottom-0 z-20 w-[9px] cursor-col-resize group ${sidebarSide === 'left' ? '-right-[4px]' : '-left-[4px]'}`}
                      onMouseDown={onDragStart}
                      onDoubleClick={toggleAgentFullscreen}
                    >
                      <div className={`h-full w-[1px] ml-[4px] bg-white/[0.06] group-hover:bg-emerald-500/60 group-active:bg-emerald-500 transition-colors`} />
                      {/* grab indicator — visible on hover */}
                      <div className="absolute top-1/2 -translate-y-1/2 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                        <div className="flex flex-col gap-[3px]">
                          <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
                          <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
                          <div className="w-[3px] h-[3px] rounded-full bg-emerald-400/60" />
                        </div>
                      </div>
                    </div>
                  )}
                  <div className={`flex-1 ${sidebarExpanded ? '' : sidebarSide === 'left' ? 'border-r' : 'border-l'} border-white/[0.06] flex flex-col min-h-0 min-w-0 overflow-hidden`}>
                  {sidebarCollapsed ? (
                    <div className="flex flex-col items-center py-3 gap-2 h-full">
                      <button
                        onClick={() => setSidebarCollapsed(false)}
                        className="text-gray-600 hover:text-gray-400 transition p-1"
                        title="Expand sidebar"
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points={sidebarSide === 'left' ? "9 18 15 12 9 6" : "15 18 9 12 15 6"} />
                        </svg>
                      </button>
                      <div className="flex flex-col items-center gap-1.5 mt-2">
                        {tasks.slice(0, 8).map(t => (
                          <button
                            key={t.id}
                            onClick={() => { setSelectedTask(t.id); setSidebarCollapsed(false); setActiveTab('output') }}
                            title={t.query}
                            className={`w-5 h-5 rounded flex items-center justify-center text-[9px] transition ${
                              selectedTask === t.id ? 'bg-emerald-500/20 border border-emerald-500/25' : 'hover:bg-white/[0.06]'
                            }`}
                          >
                            <span className={statusIcon(t.status)}>{statusDot(t.status)}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col h-full min-h-0">
                      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/[0.06] shrink-0">
                        <span className="text-xs text-gray-600 uppercase tracking-wider font-medium">Conversations</span>
                        <div className="flex items-center gap-0.5">
                          <button
                            onClick={() => setSidebarExpanded(e => !e)}
                            className={`text-gray-600 hover:text-gray-400 transition p-1 rounded hover:bg-white/5 ${sidebarExpanded ? 'text-emerald-300' : ''}`}
                            title={sidebarExpanded ? 'Exit fullscreen' : 'Expand to fullscreen'}
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              {sidebarExpanded ? (
                                <>
                                  <polyline points="4 14 10 14 10 20" />
                                  <polyline points="20 10 14 10 14 4" />
                                  <line x1="14" y1="10" x2="21" y2="3" />
                                  <line x1="3" y1="21" x2="10" y2="14" />
                                </>
                              ) : (
                                <>
                                  <polyline points="15 3 21 3 21 9" />
                                  <polyline points="9 21 3 21 3 15" />
                                  <line x1="21" y1="3" x2="14" y2="10" />
                                  <line x1="3" y1="21" x2="10" y2="14" />
                                </>
                              )}
                            </svg>
                          </button>
                          {!sidebarExpanded && (
                            <>
                              <button
                                onClick={() => setDrawerOpen(o => !o)}
                                className={`text-gray-600 hover:text-gray-400 transition p-1 rounded hover:bg-white/5 ${drawerOpen ? 'text-emerald-300' : ''}`}
                                title={drawerOpen ? 'Close detail drawer' : 'Open detail drawer'}
                              >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <rect x="3" y="3" width="18" height="18" rx="2" />
                                  <line x1="15" y1="3" x2="15" y2="21" />
                                  <polyline points="10 8 14 12 10 16" />
                                </svg>
                              </button>
                              <button
                                onClick={() => setSidebarSide(s => s === 'left' ? 'right' : 'left')}
                                className="text-gray-600 hover:text-gray-400 transition p-1 rounded hover:bg-white/5"
                                title={`Move to ${sidebarSide === 'left' ? 'right' : 'left'}`}
                              >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  {sidebarSide === 'left' ? (
                                    <>
                                      <rect x="3" y="3" width="18" height="18" rx="2" />
                                      <line x1="15" y1="3" x2="15" y2="21" />
                                    </>
                                  ) : (
                                    <>
                                      <rect x="3" y="3" width="18" height="18" rx="2" />
                                      <line x1="9" y1="3" x2="9" y2="21" />
                                    </>
                                  )}
                                </svg>
                              </button>
                              <button
                                onClick={() => setSidebarCollapsed(true)}
                                className="text-gray-600 hover:text-gray-400 transition p-1 rounded hover:bg-white/5"
                                title="Collapse"
                              >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <polyline points={sidebarSide === 'left' ? "15 18 9 12 15 6" : "9 18 15 12 9 6"} />
                                </svg>
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                      <div className="flex-1 min-h-0">{sidebarContent}</div>
                    </div>
                  )}
                  </div>
                </div>
              )

              const drawerPanel = (
                <>
                  {/* drawer backdrop */}
                  {drawerOpen && (
                    <div
                      className="fixed inset-0 bg-black/40 z-30 transition-opacity"
                      onClick={() => setDrawerOpen(false)}
                    />
                  )}
                  {/* drawer */}
                  <div className={`fixed top-0 bottom-0 z-40 transition-transform duration-300 ease-out ${
                    sidebarSide === 'left' ? 'right-0' : 'left-0'
                  } ${drawerOpen ? 'translate-x-0' : (sidebarSide === 'left' ? 'translate-x-full' : '-translate-x-full')}`}
                    style={{ width: 'min(600px, 50vw)' }}>
                    <div className="h-full flex flex-col bg-[#0c0c0c] border-l border-white/[0.06] shadow-2xl">
                      {/* drawer header with close */}
                      <div className="flex items-center justify-between px-3 py-2 border-b border-white/[0.06] shrink-0">
                        <span className="text-[10px] text-gray-500 uppercase tracking-wider font-medium">Detail</span>
                        <button onClick={() => setDrawerOpen(false)}
                          className="text-gray-600 hover:text-gray-300 transition p-1 rounded hover:bg-white/5">
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                          </svg>
                        </button>
                      </div>
                      {detailPanel}
                    </div>
                  </div>
                </>
              )

              if (sidebarExpanded) {
                return <>{sidebarPanel}</>
              }

              return sidebarSide === 'left' ? (
                <>{sidebarPanel}<div className="flex-1 min-h-0 relative flex flex-col">{!drawerOpen && detailPanel}{drawerPanel}</div></>
              ) : (
                <><div className="flex-1 min-h-0 relative flex flex-col">{!drawerOpen && detailPanel}{drawerPanel}</div>{sidebarPanel}</>
              )
            })()}
          </>
        )}

        {/* fullscreen mode */}
        {view === 'console' && layoutMode === 'fullscreen' && (
          <div className="flex-1 flex flex-col min-h-0">
            {mainContent}
          </div>
        )}

        {/* minimized mode */}
        {view === 'console' && layoutMode === 'minimized' && (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center text-center text-gray-700">
              <div className="w-16 h-16 rounded-2xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-center mb-4">
                <span className="text-gray-600 font-mono text-lg select-none">{'>'}_</span>
              </div>
              <p className="text-sm text-gray-600">Agent minimized</p>
              <button onClick={() => setView('library')}
                className="mt-4 px-3.5 py-1.5 rounded-full text-xs bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 hover:text-emerald-200 hover:bg-emerald-500/15 transition">
                browse library →
              </button>
            </div>
          </div>
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
      })
      if (data.error) { setErr(data.error); setSaving(false); return }
      setOk(encrypt
        ? 'Key sealed with your passphrase — encrypted on the server, unlocked for this session.'
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
      const data = await post('/key/unlock', { provider: tab, passphrase: unlockPass })
      if (data.error) { setErr(data.error === 'wrong passphrase' ? 'Wrong passphrase.' : data.error); setUnlocking(false); return }
      const r = data.result || data
      setOk(`Unlocked ${r.key || ''} — live for this session.`)
      setUnlockPass(''); setUnlocking(false)
      refresh(tab); onSaved()
    } catch (e: any) { setErr(e.message); setUnlocking(false) }
  }

  const lock = async () => {
    setErr(null); setOk(null)
    try {
      const data = await post('/key/lock', { provider: tab })
      if (data.error) { setErr(data.error); return }
      setOk('Locked — the key is wiped from server memory.')
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

  const lockIcon = (open: boolean, size = 11) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      <rect x="4" y="11" width="16" height="10" rx="2" />
      {open ? <path d="M8 11V7a4 4 0 0 1 7.7-1.5" /> : <path d="M8 11V7a4 4 0 0 1 8 0v4" />}
    </svg>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-md vault-panel bg-[#0d1211] border border-white/10 rounded-2xl shadow-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
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
                        {lockIcon(!!info.unlocked, 9)} {info.unlocked ? 'unlocked' : 'locked'}
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
              <input
                value={passphrase}
                onChange={e => setPassphrase(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') save() }}
                placeholder="passphrase (never stored, never sent to providers)…"
                type="password"
                className="mt-2 w-full bg-white/[0.04] border border-emerald-500/20 rounded-lg px-3 py-2 text-sm text-gray-200 outline-none font-mono placeholder:text-gray-600 focus:border-emerald-500/40 transition"
              />
            )}

            <button onClick={save} disabled={saving || !newKey.trim() || (encrypt && passphrase.length < 4)}
              className="mt-2.5 w-full py-2 rounded-lg text-xs font-semibold lit-btn disabled:bg-white/5 disabled:text-gray-600 disabled:shadow-none transition">
              {saving ? 'Saving…' : encrypt ? 'Encrypt & save' : 'Save'}
            </button>

            {err && <p className="text-xs text-red-400 mt-2">{err}</p>}
            {ok && !err && <p className="text-xs text-emerald-300 mt-2">{ok}</p>}
            <p className="text-[10px] text-gray-600 mt-2.5 leading-relaxed">
              {encrypt
                ? 'Sealed with AES-256-GCM under a key derived from your passphrase (PBKDF2, 600k rounds). The server stores only the encrypted file — without your passphrase nobody, including the server owner, can read it. Lock any time to wipe it from memory.'
                : `Stored in plaintext on the server (~/.mod/model/${tab}) and shared with other modules. Flip the toggle above to encrypt it instead.`}
              {' '}Get a key at {meta.hint}.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
