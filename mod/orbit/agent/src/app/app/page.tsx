'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { API_URL } from './config'
import Library from './components/Library'
import type { LibItem } from './components/Library'
import Market from './components/Market'
import Builder from './components/Builder'
import AgentEditor from './components/AgentEditor'
import CreditsSidebar, { CreditsInfo } from './components/Credits'
import Select from './components/Select'
import Tools from './components/Tools'
import Arena from './components/Arena'
import MemoryPanel from './components/Memory'
import { ThemePicker, useTheme } from './components/Theme'
import { loadLocalIdentity, getOrCreateLocalIdentity, clearLocalIdentity, localSign } from './lib/localWallet'
import { BrowserModel, serveModelRequest, type BrowserState } from './lib/browserModel'

type ToolSchema = { description: string; params: Record<string, any> }
// images: what the user pasted, as data URLs. thumbs are the tiny copies that
// survive persistence — localStorage is shared across modc2 modules, so the
// full-size data never goes in it.
// usage: what this one call to the agent cost on the provider key — filled in
// live from the run's `usage` events, and finalised by its `done` event.
type Usage = { cost?: number | null; tokens?: number; calls?: number; model?: string
               priced?: boolean; charged?: number | null; per_call?: any[] }
// draft/running exist only while a run streams: draft is the model's output
// landing token by token, running is the tool call in flight right now. Both
// are superseded by the real step events and dropped when the run finishes.
type RunningTool = { tool: string; params?: any; i?: number; n?: number }
type Message = { role: 'user' | 'agent' | 'system'; text: string; steps?: any[]; live?: boolean; images?: string[]; thumbs?: string[]; usage?: Usage; draft?: string; running?: RunningTool | null }
// uid/cid/synced tie a conversation to the server-side store: uid is the
// stable cross-device id, cid the localfs pin, synced whether the server copy
// is current. Anonymous sessions only ever live in localStorage.
type TaskEntry = { id: number; query: string; status: 'running' | 'done' | 'error'; stepCount?: number; messages: Message[]; agent_type?: string; startedAt?: number; finishedAt?: number; uid?: string; cid?: string; synced?: boolean }

const genUid = () => `c-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`

// What of the streaming draft is fit to show. The raw stream is the loop's
// own output: prose, a thinking model's <think> scratchpad, and the
// <STEP>{...}</STEP> tool-call JSON. The prose streams as-is; an unfinished
// STEP block folds into "writing a <tool> call…" (the tool name is readable
// long before the JSON closes); a finished one is about to arrive as a real
// step event, so it just disappears.
const draftView = (draft: string) => {
  let t = draft.replace(/<\/?PLAN>/g, '')
  let composing: string | null = null
  const i = t.indexOf('<STEP>')
  if (i >= 0) {
    const rest = t.slice(i)
    if (!rest.includes('</STEP>')) {
      const m = /"tool"\s*:\s*"([\w./-]+)"/.exec(rest)
      composing = m ? m[1] : ''
    }
    t = t.slice(0, i)
  }
  const prose = t.replace(/<\/?think>/gi, '').replace(/<\|[^|]{0,40}\|>/g, '').trimStart()
  return { prose, composing }
}

// ── what a call cost, in words a person reads ──
// Sub-cent runs are the normal case here, so a $0.00 that means "nearly free"
// and a $0.00 that means "nothing was metered" have to look different: the
// first keeps digits until it says something, the second is not printed at all.
const fmtUSD = (c?: number | null) => {
  if (c == null) return null
  if (c === 0) return '$0'
  if (c >= 0.01) return `$${c.toFixed(3)}`
  if (c >= 0.0001) return `$${c.toFixed(5)}`
  return `<$0.0001`
}
const fmtTok = (t?: number) => !t ? null : t >= 1000 ? `${(t / 1000).toFixed(1)}k` : `${t}`
// the per-call breakdown, as the footer's tooltip: "cost per call" literally
const callBreakdown = (u?: Usage) => (u?.per_call || [])
  .map((c: any, i: number) => `#${c.call ?? i + 1}  ${fmtUSD(c.cost) ?? '—'}  ${
    fmtTok((c.prompt_tokens || 0) + (c.completion_tokens || 0)) ?? '0'} tok`)
  .join('\n')

// The console session — one id per browser, kept forever. It rides along with
// every run so the agent's memory module can file the exchange and read the
// earlier ones back into the next prompt: a signed-in visitor is remembered by
// their address across devices, an anonymous one by this id alone. Each run is
// its own conversation in the UI, so without this the agent would meet the
// same person as a stranger every single message.
const SESSION_KEY = 'agent_session_v1'
const sessionId = (): string => {
  try {
    const saved = localStorage.getItem(SESSION_KEY)
    if (saved) return saved
    const fresh = `s-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(SESSION_KEY, fresh)
    return fresh
  } catch {
    return 's-ephemeral'    // private mode: this tab remembers, nothing else
  }
}
type Tab = 'tasks' | 'output' | 'tools' | 'memory' | 'deltas'
// the TOOLS tab answers two questions: what did this run call, and what can
// the agent call at all
type ToolPane = 'trace' | 'registry'
// the three shelves inside HUB — the things a chat pulls from, and the runs
// it left going, all under one top-level tab
type HubPane = 'agents' | 'library' | 'tasks'

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

type SidebarSide = 'left' | 'right'
// the rail holds two lists: the chats you've had and the agents you can run as
type RailPane = 'chats' | 'agents'

type FileEntry = { path: string; content: string; action: 'read' | 'created' | 'modified' | 'searched' }

// ── Provider key metadata + missing-key detection ───────────────────
// Shared by the KeyPanel modal and the inline "key needed" banner so the
// console can turn a raw "No X API key found" error into a one-click fix.
// topUpUrl: where credits are bought. Neither provider sells them over an
// API (OpenRouter's Coinbase endpoint answers 410 Gone), so a top-up is
// always a trip to their page — the owner's treasury panel books what lands.
type ProviderMeta = { label: string; hint: string; keysUrl: string; placeholder: string; topUpUrl?: string }
const PROVIDER_META: Record<string, ProviderMeta> = {
  openrouter: { label: 'openrouter', hint: 'openrouter.ai/keys', keysUrl: 'https://openrouter.ai/keys', placeholder: 'sk-or-v1-…', topUpUrl: 'https://openrouter.ai/settings/credits' },
  venice: { label: 'venice', hint: 'venice.ai → settings → API', keysUrl: 'https://venice.ai/settings/api', placeholder: 'venice API key…', topUpUrl: 'https://venice.ai/settings/api' },
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

// ── Empty-console starters ──────────────────────────────────────────
// The three things a coding agent is asked to do first. Clicking one only
// fills the composer — the run is still yours to press — so the label is
// the prompt verbatim rather than a summary of one.

const STARTERS: { q: string; s: string; icon: JSX.Element }[] = [
  {
    q: 'map this codebase',
    s: 'walk the tree, name what lives where',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 5h6l1.5 2H20v12H4z" />
        <path d="M9 12h7M9 16h4" />
      </svg>
    ),
  },
  {
    q: 'find and fix a bug',
    s: 'reproduce it first, then patch it',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="6" />
        <path d="M20 20l-4.5-4.5M9 11h4" />
      </svg>
    ),
  },
  {
    q: 'write tests for recent changes',
    s: 'cover what the last commits touched',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 6L9 17l-5-5" />
      </svg>
    ),
  },
]

// ── Sign-in (mod protocol-auth token) ───────────────────────────────
// The API verifies this statelessly via the shared auth mod: base64url of
// { data, time, key, signature } where signature = personal_sign of the
// compact JSON {"data":…,"time":…}. Identity = the recovered signer.

// local: signed by a keypair generated in this browser (no wallet extension)
// harnesses: the CLI harness names /whoami says this caller may run — the
// host's, plus any a console module (claude, codex, build, chain) vouches for
type AuthInfo = { address: string; token: string; isOwner: boolean; local?: boolean; harnesses?: string[] }

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
  keyless?: boolean          // LFM providers: local/browser compute, no key, no bill
  balance?: number | null; total_credits?: number; total_usage?: number
  balances?: Record<string, number>; error?: string
}

export default function Home() {
  // top-level view: three places, not five. CHAT is the console you talk to an
  // agent in. HUB is everything you keep — the agents you wired, the library
  // you pull from, the runs still going — behind one door instead of three
  // tabs competing with the one that matters. ARENA is the ranked board.
  const [view, setView] = useState<'chat' | 'hub' | 'arena'>('chat')
  // which shelf of the hub is open
  const [hubPane, setHubPane] = useState<HubPane>('agents')
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
  // which tool calls are open, keyed by where they're drawn — the same step
  // appears inline in the transcript and again in the trace, and opening one
  // shouldn't open the other
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({})
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
  // your own pick, if you made one: the server holds it per address (signed
  // in) and localStorage holds it otherwise, so an anonymous visitor still
  // gets to say where their runs land — it just doesn't follow the wallet.
  const [defaultPick, setDefaultPick] = useState<string | null>(null)
  const [defaultSource, setDefaultSource] = useState<'you' | 'host'>('host')
  // the one-time "which agent should your runs land on?" card
  const [showDefaultPick, setShowDefaultPick] = useState(false)
  const [defaultErr, setDefaultErr] = useState<string | null>(null)
  const [agentOptions, setAgentOptions] = useState<AgentOption[]>(DEFAULT_AGENTS)
  // agent to preload on the visual builder canvas (null = fresh canvas)
  const [builderAgent, setBuilderAgent] = useState<string | null>(null)
  // the rail's inline agent editor: null = the list, {name: null} = a new
  // agent, {name} = editing that one. Creating and changing an agent is a
  // sidebar job — the canvas is where you go for the wiring, not the naming.
  // `from` = a fork: a new agent prefilled from that one, so an agent you
  // can't change is still somewhere to start.
  const [agentEdit, setAgentEdit] = useState<{ name: string | null; from?: string | null } | null>(null)

  // persona picker: run with a library prompt as system prompt + memory notes as context
  const [libPrompts, setLibPrompts] = useState<LibPrompt[]>([])
  const [memNotes, setMemNotes] = useState<MemNote[]>([])
  const [promptSel, setPromptSel] = useState<LibPrompt | null>(null)
  const [memSel, setMemSel] = useState<string[]>([])
  // tool documents installed from Discover, attached to the run as instructions
  const [toolSel, setToolSel] = useState<string[]>([])
  const [showPicker, setShowPicker] = useState(false)
  // The picker's trigger sits in the runbar, which scrolls horizontally on a
  // narrow dock — and a scroll box clips in BOTH axes, so an absolutely
  // positioned dropdown inside it was drawn into a 40px-tall strip and
  // couldn't be seen, let alone clicked. The panel is `fixed` and measured
  // off the button instead, which no ancestor can clip.
  const pickerBtnRef = useRef<HTMLButtonElement>(null)
  const [pickerPos, setPickerPos] = useState<{ left: number; top: number } | null>(null)
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
  type ProviderInfo = { key: string; models: string[]; default_model: string; configured?: boolean; encrypted?: boolean; unlocked?: boolean; keyless?: boolean; runtime?: string | null; hint?: string | null; free?: boolean }
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [provider, setProvider] = useState<string>('openrouter')
  const [model, setModel] = useState<string>('')

  // the `browser` provider: the model runs in this tab, so the console owns
  // one worker and answers the run's generation requests from it
  const browserRef = useRef<BrowserModel | null>(null)
  const [browserState, setBrowserState] = useState<BrowserState>({ phase: 'idle' })
  const browserModel = () => {
    if (!browserRef.current) {
      const bm = new BrowserModel()
      bm.onState = setBrowserState
      browserRef.current = bm
    }
    return browserRef.current
  }
  // the host — the module owner. null until the API answers ('' = no owner
  // configured, which makes every visitor the host)
  const [owner, setOwner] = useState<string | null>(null)

  // sign-in (wallet personal_sign → protocol-auth token)
  const [auth, setAuth] = useState<AuthInfo | null>(null)
  const [authBusy, setAuthBusy] = useState(false)
  const [authErr, setAuthErr] = useState<string | null>(null)
  const [showUserMenu, setShowUserMenu] = useState(false)

  // Escape closes the account menu. The click-catcher behind it already
  // handled the pointer; the keyboard had no way out.
  useEffect(() => {
    if (!showUserMenu) return
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowUserMenu(false) }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [showUserMenu])

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
    if (p !== 'browser' && browserRef.current) {
      // a resident LFM is hundreds of MB of tab memory — don't keep it warm
      // for a provider that isn't going to ask it anything
      browserRef.current.dispose()
      setBrowserState({ phase: 'idle' })
    }
    const def = providers.find(x => x.key === p)?.default_model || ''
    setModel(def)
    localStorage.setItem('agent_model', def)
  }

  // workspace layout: chats + agents in the side rail, the market on the other
  // side, the console docked at the bottom
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [sidebarSide, setSidebarSide] = useState<SidebarSide>('left')
  const [railPane, setRailPane] = useState<RailPane>('chats')
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

  // draggable rail width
  const [sidebarWidth, setSidebarWidth] = useState(280)
  // a rail narrower than its default cannot hold the header's counts as well
  // as the two pane names and the three buttons — below this it sheds the
  // counts, which are the only part of that row nothing depends on
  const tightRail = sidebarWidth < 280
  const isDragging = useRef(false)
  const dragStartX = useRef(0)
  const dragStartWidth = useRef(280)

  // restore the workspace geometry the user last dragged into place
  useEffect(() => {
    try {
      const w = Number(localStorage.getItem('agent_rail_w'))
      // a width stored before the floor moved is raised to it, not honoured
      if (w >= 100) setSidebarWidth(Math.max(244, w))
      setSidebarCollapsed(localStorage.getItem('agent_rail_closed') === '1')
      const pane = localStorage.getItem('agent_rail_pane')
      if (pane === 'chats' || pane === 'agents') setRailPane(pane)
      const hp = localStorage.getItem('agent_hub_pane')
      if (hp === 'agents' || hp === 'library' || hp === 'tasks') setHubPane(hp)
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
        // phones: both rails start as drawers so the console has the screen
        setSidebarCollapsed(true)
        setMarketOpen(false)
      }
    } catch {}
  }, [])

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
      // 244 is a measurement, not a taste: below it the header's own two pane
      // names stop fitting beside its buttons, and a rail whose tabs read
      // "CH… AGE…" is narrower than the thing it is a rail for
      last = Math.max(244, Math.min(maxWidth, dragStartWidth.current + delta))
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

  // keyboard shortcut: Escape closes the prompt picker, then the file viewer
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (showPicker) { setShowPicker(false); return }
      setViewingFile(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showPicker])

  // the picker is positioned off its button, so anything that moves the
  // button — a resize, a rail drag, opening it from the hero chip rather
  // than the toolbar — has to re-measure
  useEffect(() => {
    if (!showPicker) return
    placePicker()
    const on = () => placePicker()
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showPicker])

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
          // signed out there is no address to file a pick under, so the
          // browser holds it; signed in the server's answer is the truth
          const local = (() => { try { return localStorage.getItem('agent_default') } catch { return null } })()
          const pick = d.default_pick || (token ? null : local)
          const landing = pick || d.default
          setDefaultPick(pick || null)
          setDefaultSource(pick ? 'you' : 'host')
          setDefaultAgent(landing)
          setAgentType(() => localStorage.getItem('agent_type') || landing)
          // nobody has said where their runs should land — ask, once
          if (!pick) setShowDefaultPick(() => {
            try { return !localStorage.getItem('agent_default_asked') } catch { return false }
          })
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
    if (prevView.current !== view && view === 'chat') { libChanged(); fetchLibrary() }
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
        // an editor open on what just went is a form that can't save
        setAgentEdit(e => e && e.name === p.id ? null : e)
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

  // open the hub on one of its shelves. Everything that used to be its own
  // top-level tab comes through here, so a caller still says where it wants to
  // land — it just lands inside HUB instead of beside it.
  const openHub = (pane: HubPane) => {
    if (pane === 'tasks') fetchServerTasks()
    setHubPane(pane)
    setView('hub')
    try { localStorage.setItem('agent_hub_pane', pane) } catch {}
  }

  // jump to the AGENTS canvas, optionally preloading an agent to edit
  const openBuilder = (name?: string | null) => {
    setBuilderAgent(name || null)
    setShowPicker(false)
    openHub('agents')
  }

  // open the rail's inline editor — the sidebar is where an agent is made and
  // changed, so every ✎ and + lands here rather than on the canvas. Called
  // from the rail itself and from the console's persona dropdown, so it puts
  // the rail on screen and on the right pane first.
  const openAgentEditor = (name?: string | null) => {
    setShowPicker(false)
    setView('chat')
    setRailClosed(false)
    setPane('agents')
    setAgentEdit({ name: name || null })
  }

  // Make an agent the one your runs land on. Signed in it is filed against
  // your address on the server, so it follows the wallet; signed out the
  // browser keeps it, which is as far as an anonymous pick can travel.
  // Resolves to an error string, or null when it took.
  const setDefaultAgentPick = useCallback(async (name: string): Promise<string | null> => {
    try { localStorage.setItem('agent_default_asked', '1') } catch {}
    if (!auth?.token) {
      try { localStorage.setItem('agent_default', name) } catch {}
      setDefaultPick(name); setDefaultSource('you'); setDefaultAgent(name)
      setDefaultErr(null); setShowDefaultPick(false)
      return null
    }
    try {
      const r = await fetch(`${API_URL}/agents/default`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, key: auth.token }),
      }).then(x => x.json())
      if (r?.error) { setDefaultErr(r.error); return r.error }
      setDefaultPick(r.pick || null)
      setDefaultSource(r.source === 'you' ? 'you' : 'host')
      setDefaultAgent(r.default || name)
      try { localStorage.setItem('agent_default', name) } catch {}
      setDefaultErr(null); setShowDefaultPick(false)
      return null
    } catch (e: any) {
      const msg = e?.message || 'could not save the default'
      setDefaultErr(msg)
      return msg
    }
  }, [auth?.token])

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
  // Running a harness agent is a different question from managing one, and it
  // can't be answered optimistically: the run leaves this loop for a CLI on
  // the host's own shell, so the server allows the module owner — and, per
  // harness, whoever the console behind it (claude, codex, build, chain)
  // vouches for; /whoami hands that list back with the role. Signed out with
  // an owner on record, we already know the answer is no — saying so up front
  // beats offering a run that comes back a refusal.
  // `owner` is null until /owner answers; unknown stays permissive.
  const canRunHarness = (harness?: string | null) =>
    owner === null || owner === '' || !!auth?.isOwner ||
    (!!harness && !!auth?.harnesses?.includes(harness))
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
    if (p.kind === 'agent') openAgentEditor(p.id)
    else { setShowPicker(false); openHub('library') }
  }

  // open the builder on a COPY of this agent — the way to change one that
  // isn't yours without asking whoever owns it
  const forkPersona = (p: Persona) => {
    if (p.kind !== 'agent') { setShowPicker(false); openHub('library'); return }
    setShowPicker(false)
    setView('chat')
    setRailClosed(false)
    setPane('agents')
    setAgentEdit({ name: null, from: p.id })
  }

  // the agent an unnamed run lands on — your own pick if you made one
  const isDefaultAgent = (id: string) => id === (defaultPick || defaultAgent)

  // asked once. Answering it or waving it away both count as asked, so the
  // card never stands between anyone and a second run.
  const dismissDefaultPick = () => {
    setShowDefaultPick(false)
    setDefaultErr(null)
    try { localStorage.setItem('agent_default_asked', '1') } catch {}
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
          {/* the name owns the line. It used to sit between two shrink-0 chips
              in a narrow rail, and since `truncate` lets a flex item collapse
              to zero it was the one thing that disappeared. Nothing else that
              is not the name may hold width here while the rail is idle: the
              harness chip moved down to the meta line, and the row's buttons
              are display:none until the row is hovered — as `opacity-0` they
              were invisible and still ate 40px of every name. */}
          <span className="truncate flex-1 min-w-0" title={p.label}>{p.label}</span>
          <span className="flex items-center gap-1 shrink-0">
            {/* the default lives on the row it belongs to: ★ says where an
                unnamed run lands, and clicking one moves it there */}
            {p.kind === 'agent' && (
              <button title={isDefaultAgent(p.id)
                ? 'your default — unnamed runs land here'
                : `Make "${p.label}" the agent your runs land on`}
                onClick={e => { e.stopPropagation(); if (!isDefaultAgent(p.id)) setDefaultAgentPick(p.id) }}
                className={`w-5 h-5 items-center justify-center rounded text-[10px] transition ${
                  isDefaultAgent(p.id)
                    ? 'flex text-emerald-300'
                    : 'hidden group-hover:flex group-focus-within:flex text-gray-600 hover:text-emerald-300 hover:bg-emerald-500/10'
                }`}>
                ★
              </button>
            )}
            {/* an agent you can't change is still a starting point — ⧉ opens
                it as a new agent of your own */}
            {p.kind === 'agent' && (
              <button title={`Open a copy of "${p.label}" as a new agent`}
                onClick={e => { e.stopPropagation(); forkPersona(p) }}
                className="hidden group-hover:flex group-focus-within:flex w-5 h-5 items-center justify-center rounded text-[10px] text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/10 transition">
                ⧉
              </button>
            )}
            {canManage(p) && (
              <>
                <button title={`Edit this ${p.kind}`}
                  onClick={e => { e.stopPropagation(); editPersona(p) }}
                  className="hidden group-hover:flex group-focus-within:flex w-5 h-5 items-center justify-center rounded text-[10px] text-gray-500 hover:text-emerald-300 hover:bg-emerald-500/10 transition">
                  ✎
                </button>
                <button title={p.builtin
                  ? `Delete built-in agent "${p.label}" — host only`
                  : `Delete this ${p.kind}`}
                  onClick={e => { e.stopPropagation(); deletePersona(p) }}
                  className="hidden group-hover:flex group-focus-within:flex w-5 h-5 items-center justify-center rounded text-[10px] text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition">
                  ✕
                </button>
              </>
            )}
            {active && <span className={`text-[10px] ${p.kind === 'prompt' ? 'text-amber-300' : 'text-emerald-300'}`}>active</span>}
          </span>
        </div>
        <div className="flex items-center gap-1.5 mt-0.5 pl-7">
          {/* kind sits here rather than beside the name: it's what the row IS,
              not what it's called, and the picker mixes both kinds in one list */}
          <span className={`text-[9px] px-1 py-0.5 rounded shrink-0 ${
            p.kind === 'prompt' ? 'bg-amber-400/10 text-amber-300/90' : 'bg-white/[0.06] text-gray-500'
          }`}>{p.kind}</span>
          {/* the harness belongs with the kind — both say what the row is, not
              what it is called — and down here it costs the name nothing */}
          {p.harness && (
            <span className={`text-[9px] px-1 py-0.5 rounded shrink-0 flex items-center gap-0.5 ${
              canRunHarness(p.harness)
                ? 'bg-violet-400/10 border border-violet-400/25 text-violet-300/90'
                : 'bg-white/[0.03] border border-white/[0.08] text-gray-600'
            }`}
              title={canRunHarness(p.harness)
                ? `runs on the ${p.harness} CLI installed on this host`
                : `runs on the ${p.harness} CLI on the host's own shell — sign in as the host or that console's owner to start it`}>
              {/* a padlock rather than a word: the chip is already the name of
                  the CLI, and there is no emoji font on this host */}
              {!canRunHarness(p.harness) && (
                <svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                  <rect x="4" y="11" width="16" height="10" rx="2" />
                  <path d="M8 11V7a4 4 0 0 1 8 0v4" />
                </svg>
              )}
              {p.harness}
            </span>
          )}
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
      // the first load asks twice — once for the provider this component
      // started on, then again for the one the server actually defaults to —
      // and the slower answer used to win, leaving a hosted balance pinned to
      // a local provider ("-$0.15" beside weights that can't be billed)
      .then(d => setBalance(b => (d && d.provider && d.provider !== provider) ? b : d))
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
    let harnesses: string[] = []
    try {
      const who = await fetch(`${API_URL}/whoami?key=${encodeURIComponent(token)}`,
        { signal: AbortSignal.timeout(8000) }).then(r => r.json())
      if (who?.error) throw new Error(who.error)
      isOwner = !!who?.is_owner
      harnesses = Array.isArray(who?.harnesses) ? who.harnesses : []
    } catch {} // API offline — still sign in locally, role resolves on next load
    const next = { address, token, isOwner, local, harnesses }
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
          if (who?.signed_in) setAuth(a => a ? { ...a, isOwner: !!who.is_owner,
            harnesses: Array.isArray(who.harnesses) ? who.harnesses : [] } : a)
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

  // Dismissing a finished run. The row goes immediately rather than waiting
  // for the next poll — a list you can't clear is a list you stop reading.
  const dismissServerTask = useCallback((id: string) => {
    setServerTasks(ts => ts.filter(t => t.id !== id))
    fetch(`${API_URL}/tasks/${id}`, { method: 'DELETE' })
      .catch(() => {}).finally(fetchServerTasks)
  }, [fetchServerTasks])

  const clearServerTasks = useCallback((status: 'error' | 'done' | 'finished') => {
    setServerTasks(ts => ts.filter(t => t.status === 'running'
      || (status !== 'finished' && t.status !== status)))
    fetch(`${API_URL}/tasks?status=${status}`, { method: 'DELETE' })
      .catch(() => {}).finally(fetchServerTasks)
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
          // the totals are worth keeping across a reload; the per-call rows
          // are a tooltip, and localStorage here is shared with every other
          // module on the origin
          usage: msg.usage ? { ...msg.usage, per_call: undefined } : undefined,
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
    // what the agent said, in order — a response step's text or a finish
    // step's summary. The LAST one is the answer: a run can now speak twice,
    // because a model that signed off on a promise without doing anything is
    // sent back to work, and what it wrote before doing the job is not it.
    const said = allSteps
      .map((s: any) => s.tool === 'response' ? String(s.result || '')
        : s.tool === 'finish' ? String(s.params?.summary || '') : '')
      .filter(Boolean)
    const answerText = said[said.length - 1] || ''
    const errorText = allSteps.filter((s: any) => s.tool === 'error' && s.error).map((s: any) => s.error).join('\n')
    const hasError = !!errorText || !!apiError
    // 'invalid' = a step the model malformed and the loop retried — internal noise
    const visibleSteps = allSteps.filter((s: any) => !['response', 'error', 'invalid'].includes(s.tool))
    const displayText = apiError ? `Error: ${apiError}`
      : errorText ? `Error: ${errorText}`
      : answerText || (visibleSteps.length ? `Completed ${visibleSteps.length} step(s)` : 'Done')
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

    // the run's price as it is spent: each model call lands here the moment it
    // resolves, so the footer counts up instead of appearing at the end. Held
    // out here because the finish path reads it when the stream cut early.
    let liveUsage: Usage | null = null
    const patchTask = (patch: Partial<TaskEntry> | ((tk: TaskEntry) => TaskEntry)) => {
      setTasks(t => t.map(tk => tk.id === id
        ? (typeof patch === 'function' ? patch(tk) : { ...tk, ...patch })
        : tk))
    }
    const finishSingle = (allSteps: any[], apiError?: string, usage?: Usage) => {
      const fin = finalizeSteps(allSteps, apiError)
      const agentMsg: Message = { role: fin.hasError ? 'system' : 'agent', text: fin.displayText,
                                  steps: fin.visibleSteps,
                                  // the server's final tally wins over the one
                                  // assembled live — it is the one that was billed
                                  ...(usage?.calls || liveUsage ? { usage: usage || liveUsage! } : {}) }
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
      // a browser run generates in this tab: the id ties the run's stream to
      // the worker below, so the server knows where to send each step
      if (provider === 'browser') body.browser_session = genUid()
      if (promptSel) {
        // prefer the freshly-fetched body — the persisted copy may be clipped
        const bodyText = libPrompts.find(p => p.id === promptSel.id)?.body || promptSel.body
        if (bodyText) body.prompt = bodyText
      }
      // the conversation this run belongs to — the memory module keys the
      // remembered exchange off it (and off the signed-in address, if any)
      body.session = sessionId()
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

      // patch the trailing live message (creating it if the run hasn't
      // spoken yet) — the streaming events all land on the same bubble
      const patchLive = (fn: (last: Message) => Message) => {
        patchTask(tk => {
          const msgs = [...tk.messages]
          const last = msgs[msgs.length - 1]
          if (!last || !last.live) msgs.push(fn({ role: 'agent', text: '', steps: [], live: true }))
          else msgs[msgs.length - 1] = fn(last)
          return { ...tk, messages: msgs }
        })
      }

      const onEvent = (ev: any) => {
        if (apiStatus !== 'ok') setApiStatus('ok')
        if (ev.type === 'token' && ev.text) {
          // the model's answer landing token by token. Clipped from the
          // front: only the tail is on screen, and a mid-run persist of an
          // unbounded draft would eat the shared localStorage quota
          patchLive(last => ({ ...last, draft: ((last.draft || '') + ev.text).slice(-8000) }))
        } else if (ev.type === 'model_start') {
          // a fresh model call — whatever streamed before belongs to the
          // last step now, and the tool that was running has returned
          patchLive(last => ({ ...last, draft: '', running: null }))
        } else if (ev.type === 'tool_start' && ev.tool) {
          patchLive(last => ({ ...last, draft: '',
            running: { tool: ev.tool, params: ev.params, i: ev.i, n: ev.n } }))
        } else if (ev.type === 'step' && ev.step) {
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
            // the executed step supersedes the live indicators for it
            last.draft = undefined
            last.running = null
            if (step.tool === 'response' && step.result) {
              last.text = last.text ? `${last.text}\n${step.result}` : String(step.result)
            } else if (step.tool === 'finish') {
              last.text = step.params?.summary || last.text
            } else if (step.tool !== 'invalid') {
              last.steps = [...(last.steps || []), step]
            }
            return { ...tk, messages: msgs }
          })
        } else if (ev.type === 'usage' && ev.usage) {
          const u = ev.usage
          liveUsage = {
            cost: u.total ?? liveUsage?.cost,
            tokens: (liveUsage?.tokens || 0) + (u.prompt_tokens || 0) + (u.completion_tokens || 0),
            calls: (liveUsage?.calls || 0) + 1,
            model: u.model || liveUsage?.model,
            priced: u.priced !== false && (liveUsage?.priced !== false),
            per_call: [...(liveUsage?.per_call || []), u],
          }
          const snap = liveUsage
          patchTask(tk => {
            const msgs = [...tk.messages]
            const last = msgs[msgs.length - 1]
            if (!last || !last.live) return tk
            msgs[msgs.length - 1] = { ...last, usage: snap }
            return { ...tk, messages: msgs }
          })
        } else if (ev.type === 'model_request') {
          // the run is blocked on this tab — generate and hand the text back.
          // Deliberately not awaited: the stream reader has to keep draining
          // while the worker runs, or the next event would queue behind it.
          serveModelRequest(browserModel(), ev, API_URL)
        } else if (ev.type === 'done') {
          finishSingle(liveSteps.length ? liveSteps : (ev.result || []), undefined, ev.usage)
          // a billed run just moved the balance — the run cost what it cost
          // on the module's key plus the margin, so re-read it
          if (ev.charged?.charged) fetchCredits()
        } else if (ev.type === 'error') {
          finishSingle(liveSteps, ev.error || 'Unknown error', ev.usage)
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
            messages: [...tk.messages.map(msg => msg.live ? { ...msg, live: undefined, draft: undefined, running: null } : msg),
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
        finishSingle(data.result || [], data.error, data.usage)
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

  const toggleStep = (key: string) => {
    setExpandedSteps(s => ({ ...s, [key]: !s[key] }))
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

  // where the weights are, for the `browser` provider — a run can't start
  // until they're in the tab, and a 300 MB download deserves a real bar
  const browserPill = provider !== 'browser' ? null : (() => {
    const s = browserState
    const tone = s.phase === 'error' ? 'border-red-500/30 text-red-300'
      : s.phase === 'ready' || s.phase === 'generating' ? 'border-emerald-500/25 text-emerald-300'
      : 'border-white/10 text-gray-400'
    const label = s.phase === 'loading' ? `loading ${s.pct ?? 0}%`
      : s.phase === 'generating' ? `${s.tokens ?? 0} tok`
      : s.phase === 'ready' ? (s.device || 'ready')
      : s.phase === 'error' ? 'failed'
      : 'not loaded'
    const title = s.phase === 'error' ? s.error
      : s.phase === 'idle' ? `click to download ${model} into this tab (${BrowserModel.device})`
      : `${s.repo || model} — ${s.device || BrowserModel.device}${s.dtype ? ` · ${s.dtype}` : ''}`
    return (
      <button
        onClick={() => model && browserModel().load(model).catch(() => {})}
        title={title}
        className={`shrink-0 px-2 py-1.5 rounded-md text-[10px] font-mono border transition hover:border-white/25 ${tone}`}
      >⌁ {label}</button>
    )
  })()

  // where this provider's compute lives, and who pays for it. The console
  // defaults to a local one, so the difference has to be visible at a glance
  // rather than buried in the key panel.
  const activeProvider = providers.find(p => p.key === provider) || null
  const isFree = !!activeProvider?.free
  const providerOptions = (providers.length
    ? providers
    : [{ key: 'openrouter' }, { key: 'venice' }] as ProviderInfo[]
  ).map(p => ({
    value: p.key,
    label: p.key,
    // ⌂ the weights are here (this box, or your tab) · ⬢ someone's API
    icon: p.free ? '⌂' : '⬢',
    ...(p.free ? { badge: 'free' } : {}),
    ...(p.hint ? { hint: p.hint } : {}),
  }))

  // provider + model selectors (used in both sidebar and fullscreen bars)
  const modelControls = (
    <div className="flex items-center gap-1.5 min-w-0 flex-1">
      <Select
        accent="emerald" className="shrink-0"
        title={isFree
          ? `${provider}: ${activeProvider?.hint || 'runs locally'} — never billed`
          : `${provider}: hosted, billed at cost`}
        value={provider}
        onChange={onProviderChange}
        options={providerOptions} />
      <Select
        accent="emerald" className="min-w-0 flex-1 max-w-[220px]" title={model || 'Model'}
        value={model}
        onChange={(v) => { setModel(v); localStorage.setItem('agent_model', v) }}
        options={
          providerModels.length === 0 && !model
            ? [{ value: '', label: 'default' }]
            : (model && !providerModels.includes(model) ? [model, ...providerModels] : providerModels).map(mn => ({
                value: mn,
                // the repo id carries the whole story; the vendor prefix is
                // the same for every row, so it only costs width
                label: mn.includes('/') ? mn.split('/').slice(1).join('/') : mn,
                ...(mn === activeProvider?.default_model ? { badge: 'default' } : {}),
              }))
        } />
      {browserPill}
    </div>
  )

  // balance pill — live credit + vault state for the active provider; click to manage keys
  const vaultLocked = !!balance && !!balance.encrypted && !balance.unlocked && !balance.configured
  const fmtBalance = (b: KeyBalance | null) => {
    if (!b) return '···'
    if (b.keyless) return 'free'    // nothing to bill: local or browser compute
    if (vaultLocked) return 'locked'
    if (!b.configured) return 'no key'
    if (typeof b.balance !== 'number') return b.error ? '$ ?' : '···'
    // the sign belongs outside the unit — "$-0.15" reads as a broken number
    return b.balance < 0 ? `-$${Math.abs(b.balance).toFixed(2)}` : `$${b.balance.toFixed(2)}`
  }
  const lockGlyph = (open: boolean) => (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      <rect x="4" y="11" width="16" height="10" rx="2" />
      {open ? <path d="M8 11V7a4 4 0 0 1 7.7-1.5" /> : <path d="M8 11V7a4 4 0 0 1 8 0v4" />}
    </svg>
  )
  // one colour scheme for the key, wherever it shows up
  const keyTone = balance?.keyless
    ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/20'
    : vaultLocked
    ? 'bg-amber-500/10 border-amber-500/30 text-amber-300 hover:bg-amber-500/20 pill-locked'
    : balance && typeof balance.balance === 'number' && balance.balance > 0
    ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/20'
    : balance && (!balance.configured || (typeof balance.balance === 'number' && balance.balance <= 0))
    ? 'bg-amber-500/10 border-amber-500/25 text-amber-300 hover:bg-amber-500/20'
    : 'border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20'
  const keyTitle = balance?.keyless
    ? `${balance.provider}: ${providers.find(p => p.key === balance.provider)?.hint || 'no key needed'} — runs on it are never billed`
    : vaultLocked
    ? `${balance?.provider} key is encrypted — click to unlock it with your passphrase`
    : balance?.key
    ? `${balance.provider} key ${balance.key}${balance.unlocked ? ' (encrypted, unlocked)' : ''} — click to manage`
    : 'Add your API key'

  // Open the key vault modal focused on a given provider (from anywhere).
  // It's an overlay, so it opens over whatever you were doing.
  const openKeyPanel = (p: string) => {
    // the LFM providers have no key to enter — the panel has no tab for them,
    // and the pill's tooltip already says why
    if (!PROVIDER_META[p]) return
    setKeyPanelProvider(p); setShowKeyPanel(true)
  }

  // the tool registry lives in the console's TOOLS tab — open it from anywhere
  const openToolRegistry = () => {
    setView('chat'); setActiveTab('tools'); setToolPane('registry')
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
      className={`w-9 py-1 flex flex-col items-center gap-0.5 rounded-md border font-mono transition overflow-hidden ${keyTone}`}
      title={keyTitle}
    >
      {lockGlyph(!vaultLocked)}
      <span className="text-[8px] leading-none max-w-full truncate tracking-tighter">
        {!balance ? '·' : vaultLocked ? 'lock'
          : !balance.configured ? 'add'
          : typeof balance.balance !== 'number' ? 'key'
          /* same sign convention as the pill — `$-0.1` reads as a broken number */
          : balance.balance < 0 ? `-$${Math.abs(balance.balance).toFixed(2)}`
          : balance.balance >= 10 ? `$${Math.round(balance.balance)}` : `$${balance.balance.toFixed(1)}`}
      </span>
    </button>
  )

  // Friendly "you need an API key" call-to-action, shown in place of the raw
  // provider error inside a task's output. One tap to add a key, one to go get one.
  const keyErrorBanner = (prov: string) => {
    const meta = PROVIDER_META[prov] || PROVIDER_META.openrouter
    return (
      <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] p-3">
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

  // A "you're not allowed to run this" wall, said as a sentence with the way
  // through it attached. The raw form — `Permission denied: 'run' requires
  // admin access.` — is the server telling itself off; it tells the visitor
  // nothing about what to do, and there are exactly three things to do.
  const accessBanner = (text: string) => {
    const action = text.match(/'([^']+)'/)?.[1] || 'this'
    const ownerOnly = /owner[-\s]only/i.test(text)
    return (
      <div className="rounded-lg border border-violet-400/25 bg-violet-400/[0.06] p-3">
        <div className="flex items-start gap-2.5">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5 text-violet-300">
            <rect x="4" y="11" width="16" height="10" rx="2" />
            <path d="M8 11V7a4 4 0 0 1 8 0v4" />
          </svg>
          <div className="min-w-0">
            <div className="text-sm font-medium text-violet-100">
              {ownerOnly ? `“${action}” is the host's to run` : `You don't have access to “${action}” yet`}
            </div>
            <div className="text-xs text-violet-200/70 mt-0.5 leading-relaxed">
              {ownerOnly
                ? 'This one touches the host itself, so only the module owner can call it. Everything else in the console is open to you.'
                : !auth
                ? 'Runs are billed to whoever made them. Sign in with your wallet, then add credits — or ask the owner to grant your address access.'
                : 'Your address is signed in but has no credit and no grant. Top up to run on the module\'s key, or ask the owner to grant you access.'}
            </div>
          </div>
        </div>
        {!ownerOnly && (
          <div className="flex items-center gap-2 mt-2.5 flex-wrap">
            {!auth ? (
              <button onClick={signIn} disabled={authBusy}
                className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-60 transition">
                {authBusy ? 'Signing in…' : 'Sign in'}
              </button>
            ) : (
              <button onClick={() => setShowCredits(true)}
                className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 transition">
                Add credits
              </button>
            )}
            {auth && (
              <button onClick={() => { try { navigator.clipboard?.writeText(auth.address) } catch {} }}
                title="Copy your address — the owner needs it to grant you access"
                className="px-3 py-1.5 rounded-md text-xs font-mono border border-white/10 text-gray-300 hover:text-white hover:border-white/25 transition">
                {shortAddr(auth.address)} ⧉
              </button>
            )}
          </div>
        )}
      </div>
    )
  }

  // A harness agent refused. This is the one wall with nothing behind it to
  // buy or sign up for: the run would leave this module for a CLI on the
  // host's own shell, and no amount of credit changes who owns that shell.
  // So the way through is a different agent, offered as a button.
  // Both wordings are matched — chats live in localStorage, so transcripts
  // written before the server said it this way are still on screen.
  const HARNESS_REFUSAL = /hands the run to the .+ CLI|runs a coding CLI on this host/i

  const harnessBanner = (text: string) => {
    const said = text.match(/'([^']+)' hands the run to the (\S+) CLI/)
    const picked = said?.[1]
    const label = (picked && agentOptions.find(a => a.value === picked)?.label) || picked
    const cli = said?.[2]
    // the console's own fallback matches the server's: an unnamed run lands on
    // the native default, so that is what we offer to switch to
    const fallback = agentOptions.find(a => a.value === 'default' && !a.harness)
      || agentOptions.find(a => !a.harness)
    return (
      <div className="rounded-lg border border-violet-400/25 bg-violet-400/[0.06] p-3">
        <div className="flex items-start gap-2.5">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5 text-violet-300">
            <rect x="4" y="11" width="16" height="10" rx="2" />
            <path d="M8 11V7a4 4 0 0 1 8 0v4" />
          </svg>
          <div className="min-w-0">
            <div className="text-sm font-medium text-violet-100">
              {label ? `“${label}” runs on the host's own machine` : 'That agent runs on the host\'s own machine'}
            </div>
            <div className="text-xs text-violet-200/70 mt-0.5 leading-relaxed">
              It hands the whole run to the {cli || 'agent'} CLI installed on this
              host — that CLI brings its own tools and answers to nobody here, so
              only the module owner can start one. Every other agent runs on this
              module&apos;s own loop, sandboxed to your directory, and is open to you.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 mt-2.5 flex-wrap">
          {fallback && (
            <button onClick={() => selectAgent(fallback.value)}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 transition">
              Switch to {fallback.label}
            </button>
          )}
          {!auth && (
            <button onClick={signIn} disabled={authBusy}
              className="px-3 py-1.5 rounded-md text-xs font-medium border border-white/10 text-gray-300 hover:text-white hover:border-white/25 disabled:opacity-60 transition">
              {authBusy ? 'Signing in…' : "I'm the host — sign in"}
            </button>
          )}
        </div>
      </div>
    )
  }

  // "credit balance spent" is about the caller's prepaid credits — a different
  // pot of money from the provider-key balance in the header pill, which is the
  // host's. Read as raw text the two flatly contradict each other ($5.38 up top,
  // "out of credits" in the transcript), so say which is which.
  const creditsBanner = (text: string) => {
    const empty = /no account credits/i.test(text)
    return (
      <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] p-3">
        <div className="flex items-start gap-2.5">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5 text-amber-300">
            <circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" />
          </svg>
          <div className="min-w-0">
            <div className="text-sm font-medium text-amber-100">
              {empty ? 'This run needs credits on your account' : 'Your credits ran out mid-run'}
            </div>
            <div className="text-xs text-amber-200/70 mt-0.5 leading-relaxed">
              {balance && typeof balance.balance === 'number' && balance.balance > 0 ? (
                <>The <span className="font-mono">{fmtBalance(balance)}</span> in the header is the host&apos;s{' '}
                  {balance.provider} key, not your money. Paid models run on that key and are billed to your
                  own credit balance{creditsInfo?.account ? <> — currently <span className="font-mono">{`$${creditsInfo.account.balance.toFixed(2)}`}</span></> : null}.</>
              ) : (
                <>Paid models run on the host&apos;s provider key and are billed to your own credit balance.</>
              )}
              {' '}Add credits, or switch to a free model — those never touch it.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 mt-2.5 flex-wrap">
          {!auth ? (
            <button onClick={signIn} disabled={authBusy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-60 transition">
              {authBusy ? 'Signing in…' : 'Sign in'}
            </button>
          ) : (
            <button onClick={() => setShowCredits(true)}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 transition">
              Add credits
            </button>
          )}
        </div>
      </div>
    )
  }

  // What a system message actually means, when we can tell. Anything we can't
  // read stays exactly as the server said it — nothing is ever swallowed.
  const runNotice = (text?: string) => {
    const prov = detectKeyError(text)
    if (prov) return keyErrorBanner(prov)
    if (text && /no account credits|credit balance spent/i.test(text)) return creditsBanner(text)
    if (text && HARNESS_REFUSAL.test(text)) return harnessBanner(text)
    if (text && /permission denied|requires admin access|owner[-\s]only/i.test(text)) return accessBanner(text)
    return null
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
          {(taskCounts.done + taskCounts.error) > 0 && (
            <button
              onClick={() => clearServerTasks(taskFilter === 'error' || taskFilter === 'done' ? taskFilter : 'finished')}
              title="Drop finished rows from the registry — running tasks stay"
              className="px-2.5 py-1 rounded-full text-[11px] font-medium border border-white/[0.07] text-gray-500 hover:text-red-300 hover:border-red-500/30 transition">
              clear {taskFilter === 'error' || taskFilter === 'done' ? taskFilter : 'finished'}
            </button>
          )}
          <input
            value={taskSearch}
            onChange={e => setTaskSearch(e.target.value)}
            placeholder="Filter tasks…"
            className="ml-auto w-56 bg-white/[0.03] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-emerald-500/40 transition"
          />
        </div>

        <div className="mt-4 space-y-1.5 pb-10">
          {visibleServerTasks.length === 0 ? (
            <div className="text-center py-24 border border-dashed border-white/[0.06] rounded-xl space-y-2">
              <p className="text-sm text-gray-500">
                {serverTasks.length === 0 ? 'No tasks yet' : 'Nothing matches this filter'}
              </p>
              {serverTasks.length === 0 && (
                <p className="text-xs text-gray-600 max-w-[380px] mx-auto leading-relaxed">
                  Every run lands here — the ones started in the console, and the ones started
                  somewhere else against this module.
                </p>
              )}
            </div>
          ) : visibleServerTasks.map(t => {
            const expanded = !!expandedServerTasks[t.id]
            return (
              <div key={t.id}
                onClick={() => { setExpandedServerTasks(s => ({ ...s, [t.id]: !s[t.id] })); if (!expanded) loadTaskImages(t) }}
                className={`group relative px-4 py-3 rounded-lg border cursor-pointer transition ${
                  t.status === 'running'
                    ? 'bg-emerald-500/[0.05] border-emerald-500/15'
                    : expanded ? 'bg-white/[0.04] border-white/10' : 'bg-white/[0.015] border-white/[0.05] hover:bg-white/[0.04] hover:border-white/10'
                }`}>
                {t.status !== 'running' && (
                  <button
                    onClick={e => { e.stopPropagation(); dismissServerTask(t.id) }}
                    title="Dismiss this run"
                    className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-black/80 border border-white/15 text-[10px] text-gray-500 hover:text-red-400 hover:border-red-500/40 opacity-0 group-hover:opacity-100 transition">✕</button>
                )}
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

  // --- Host row — who runs this module. Every agent and prompt shows its
  // owner; the host that owns everything unowned was the one address the
  // console never named. null = the API hasn't answered, '' = no owner set.
  const hostRow = owner === null ? null : (
    <div className="pop__foot">
      <span className="text-[10px] text-gray-600 uppercase tracking-wider shrink-0">host</span>
      {owner ? (
        <button
          onClick={() => { navigator.clipboard?.writeText(owner).catch(() => {}) }}
          title={`This module is hosted by ${owner} — click to copy. The host owns every agent, prompt and note nobody else made.`}
          className="min-w-0 flex items-center gap-1.5 font-mono text-[10px] text-gray-400 hover:text-gray-200 transition"
        >
          <span className="truncate">{shortAddr(owner)}</span>
          {auth && auth.address.toLowerCase() === owner.toLowerCase() && (
            <span className="text-[9px] px-1 py-px rounded bg-emerald-500/10 border border-emerald-500/25 text-emerald-300 uppercase tracking-wider shrink-0">
              you
            </span>
          )}
        </button>
      ) : (
        <span className="font-mono text-[10px] text-gray-600" title="No owner recorded — this module is unowned, so everyone is the host">
          unowned
        </span>
      )}
    </div>
  )

  // --- User chip — sign-in state, top-right corner ---
  // The menu's furniture: an icon tile per way in, and the chevron that says
  // the row goes somewhere. Stroked so they take the row's colour on hover.
  const svg = (d: React.ReactNode) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{d}</svg>
  )
  const iconWallet = svg(<><rect x="2.5" y="5.5" width="19" height="14" rx="2.5" /><path d="M2.5 10h19" /><circle cx="17.5" cy="14.5" r="1.2" /></>)
  const iconKey = svg(<><circle cx="8" cy="14" r="4" /><path d="M11 11.5 20 3" /><path d="M17 6l2.5 2.5" /></>)
  const chevron = <span className="pop__go">{svg(<polyline points="9 6 15 12 9 18" />)}</span>

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
          <div className="pop" role="menu" aria-label="Sign in">
            {/* the head says what this menu is for — the two rows below only
                made sense once you knew runs are billed to whoever signs. */}
            <div className="pop__head">
              sign in
              <span className="pop__note">Runs are billed to the address you sign in with.</span>
            </div>
            <div className="pop__body">
              <button
                onClick={signInWallet}
                disabled={authBusy || !eth()}
                role="menuitem"
                title={eth() ? 'Sign a message with your browser wallet' : 'Install MetaMask (or another EIP-1193 wallet) to use this'}
                className="pop__item"
              >
                <span className="pop__i">{iconWallet}</span>
                <span className="min-w-0">
                  <span className="pop__t">Browser wallet</span>
                  <span className="pop__s">
                    {eth() ? 'sign a message with your MetaMask address' : 'no wallet extension found in this browser'}
                  </span>
                </span>
                {eth() && chevron}
              </button>
              <button
                onClick={signInLocal}
                disabled={authBusy}
                role="menuitem"
                className="pop__item"
              >
                <span className="pop__i">{iconKey}</span>
                <span className="min-w-0">
                  <span className="pop__t">{loadLocalIdentity() ? 'Local wallet' : 'Create a local wallet'}</span>
                  <span className="pop__s">
                    {(() => {
                      const id = loadLocalIdentity()
                      return id ? `resume ${shortAddr(id.address)} — key stays in this browser`
                        : 'generate a key in this browser — no extension, no chain link'
                    })()}
                  </span>
                </span>
                {chevron}
              </button>
            </div>
            {hostRow}
            {authErr && <div className="pop__err">{authErr}</div>}
          </div>
        </>
      )}
      {showUserMenu && auth && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setShowUserMenu(false)} />
          <div className="pop" role="menu" aria-label="Account">
            <div className="pop__id">
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
            {/* balance first — it's the one number that decides whether the
                next run happens, so it gets the card rather than a text row */}
            <div className="pop__body">
              <button
                role="menuitem"
                onClick={() => { setShowCredits(true); setShowUserMenu(false) }}
                className="pop__item">
                <span className="pop__i text-[13px] leading-none">◈</span>
                {/* the balance rides the title line, not the row's right edge:
                    parked outside, it collided with the wrapped second line */}
                <span className="min-w-0 flex-1">
                  <span className="pop__t pop__t--line">
                    {auth.isOwner ? 'Credit desk' : 'Credits'}
                    {/* the owner never buys credits — their runs are on their
                        own key — so the number worth showing them is what the
                        guests are holding, not their own empty balance */}
                    <span className="pop__amt">
                      ${(auth.isOwner
                        ? (creditsInfo?.accounts || []).reduce((n, a) => n + (a.balance || 0), 0)
                        : (creditsInfo?.account?.balance ?? 0)).toFixed(2)}
                    </span>
                  </span>
                  <span className="pop__s">
                    {auth.isOwner
                      ? 'give or take credit from any address'
                      : 'top up, or see what runs have cost'}
                  </span>
                </span>
              </button>
            </div>
            <div className="px-1.5 pb-1.5 flex flex-col gap-px">
              <button
                role="menuitem"
                onClick={() => { navigator.clipboard?.writeText(auth.address).catch(() => {}); setShowUserMenu(false) }}
                className="pop__row">
                Copy address
              </button>
              {/* Sign out is neutral — you sign back in. Only forgetting the
                  key is destructive, so only that one wears the red. */}
              <button role="menuitem" onClick={signOut} className="pop__row">
                Sign out
              </button>
              {auth.local && (
                <button
                  role="menuitem"
                  onClick={() => { clearLocalIdentity(); signOut() }}
                  title="Delete the browser-held key — this identity (and anything stored under it) is gone for good"
                  className="pop__row pop__row--warn">
                  Forget local wallet
                </button>
              )}
            </div>
            {hostRow}
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

  // where the dropdown lands: under the button, and never off the right edge
  const placePicker = () => {
    const r = pickerBtnRef.current?.getBoundingClientRect()
    if (!r) return
    const w = 320  // w-80
    setPickerPos({ left: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)), top: r.bottom + 4 })
  }

  const personaPicker = (
    <div className="relative min-w-0">
      <button
        ref={pickerBtnRef}
        onClick={() => { placePicker(); setShowPicker(v => !v); setPersonaErr(null); if (!showPicker) fetchLibrary() }}
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
          <div style={pickerPos ? { left: pickerPos.left, top: pickerPos.top } : undefined}
            className="fixed w-80 max-h-[65vh] flex flex-col bg-surface-2 border border-white/10 rounded-lg z-50 shadow-2xl overflow-hidden">
            {/* tabs */}
            <div className="tab-strip gap-0.5 px-1.5 pt-1.5 border-b border-white/[0.06] shrink-0">
              {([
                ['prompts', `prompts ${personas.length}`],
                ['memory', memSel.length ? `memory ${memSel.length}/${memNotes.length}` : `memory ${memNotes.length}`],
              ] as const).map(([t, label]) => (
                <button key={t} onClick={() => setPickerTab(t)}
                  className={`tab-btn px-2.5 py-2 font-medium uppercase tracking-wider transition-colors relative ${
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
                  onClick={() => openAgentEditor()}
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
            {/* the default, spelled out: which agent a run lands on when you
                haven't picked one, and one click to move it to the one you
                are looking at */}
            {pickerTab === 'prompts' && (
              <div className="border-t border-white/[0.06] px-2.5 py-1.5 flex items-center gap-2 shrink-0">
                <span className="text-[10px] text-gray-600 truncate min-w-0"
                  title={defaultSource === 'you'
                    ? (auth ? 'your pick, kept against your address' : 'your pick, kept in this browser')
                    : 'this module picked it — you have not chosen one'}>
                  <span className="text-emerald-300/70">★</span>{' '}
                  default: {agentOptions.find(a => a.value === (defaultPick || defaultAgent))?.label || defaultPick || defaultAgent}
                  <span className="text-gray-700"> · {defaultSource === 'you' ? 'yours' : 'module'}</span>
                </span>
                {!promptSel && !isDefaultAgent(agentType) && (
                  <button onClick={() => setDefaultAgentPick(agentType)}
                    className="ml-auto shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 transition"
                    title={`Make "${currentAgentDef?.label || agentType}" the agent every unnamed run lands on`}>
                    make {currentAgentDef?.label || agentType} default
                  </button>
                )}
              </div>
            )}
            {/* footer */}
            <div className="border-t border-white/[0.06] px-2.5 py-2 flex items-center gap-2 shrink-0">
              <span className={`text-[10px] truncate min-w-0 ${personaErr || defaultErr ? 'text-red-400' : 'text-gray-600'}`}
                title={personaErr || defaultErr || undefined}>
                {personaErr || defaultErr
                  ? (personaErr || defaultErr)
                  : pickerTab === 'memory'
                  ? 'selected notes ride along as run context'
                  : 'agents bring tools + a model, prompts just set the goal'}
              </span>
              <div className="ml-auto flex items-center gap-2 shrink-0">
                {pickerTab === 'memory' && memSel.length > 0 && (
                  <button onClick={() => { setMemSel([]); try { localStorage.removeItem('agent_mem_sel') } catch {} }}
                    className="text-[10px] text-gray-500 hover:text-gray-300 transition whitespace-nowrap">
                    clear
                  </button>
                )}
                {/* the builder, on the agent you're actually running as — one
                    click from the chat to the form that made it. Three links
                    is the ceiling here: a fourth wrapped the row in two. */}
                <button onClick={() => { const p = personas.find(x => x.key === `agent:${agentType}`)
                    ; if (p && !canManage(p)) forkPersona(p); else openAgentEditor(agentType) }}
                  className="text-[10px] text-emerald-300/90 hover:text-emerald-200 transition whitespace-nowrap"
                  title={`Open ${currentAgentDef?.label || agentType} in the builder`}>
                  build →
                </button>
                <button onClick={() => { setShowPicker(false); openHub('library') }}
                  className="text-[10px] text-emerald-300/90 hover:text-emerald-200 transition whitespace-nowrap"
                  title="The whole library — prompts, tool documents, memory notes and agents">
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
      {/* The selected agent runs on a CLI this visitor can't start. Say so on
          the composer, before a prompt gets written into a refusal. */}
      {!promptSel && currentAgentDef?.harness && !canRunHarness(currentAgentDef.harness) && (
        <div className="flex items-center gap-2 pb-2 text-[11px] text-violet-200/70">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-violet-300/80">
            <rect x="4" y="11" width="16" height="10" rx="2" />
            <path d="M8 11V7a4 4 0 0 1 8 0v4" />
          </svg>
          <span className="min-w-0 truncate">
            {currentAgentDef.label} runs on the host&apos;s own {currentAgentDef.harness} CLI — sign in as the host or that console&apos;s owner.
          </span>
          <button onClick={() => selectAgent('default')}
            className="shrink-0 px-1.5 py-0.5 rounded border border-violet-400/25 text-violet-200/90 hover:bg-violet-400/10 transition">
            use a native agent
          </button>
        </div>
      )}
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
        placeholder={`Ask ${promptSel ? promptSel.name : (currentAgentDef?.label || 'agent')}…`}
        rows={1}
        className="flex-1 bg-transparent border-none outline-none text-[15px] resize-none placeholder:text-gray-600 py-1.5 leading-relaxed min-w-0 overflow-y-auto"
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
      {/* the keys, once you're actually typing — an idle composer stays clean */}
      {(composeFocused || query.trim()) && !loading && (
        <div className="compose-hint">
          <span><kbd>↵</kbd> run</span>
          <span><kbd>⇧↵</kbd> newline</span>
          {/* no key chip here — the paste chord differs per platform, and the
              point is that pasting works at all, not which key does it */}
          <span className="hidden sm:inline">· images paste straight in</span>
          <span className="ml-auto truncate pl-2">
            {promptSel ? promptSel.name : (currentAgentDef?.label || agentType)}
            {model ? ` · ${model.split('/').pop()}` : ''}
          </span>
        </div>
      )}
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

  // What a step was aimed at — the one argument worth putting on the row
  // itself, so a call reads as "read foo.ts" and not just "read". The
  // distinguishing argument wins: a search is its pattern, a shell call its
  // command, and only a tool with neither falls back to the path it touched.
  const stepTarget = (step: any) =>
    step?.params?.pattern || step?.params?.query || step?.params?.command ||
    step?.params?.url || step?.params?.path || step?.params?.file_path || ''

  // One tool call, drawn the same way wherever it appears: a row you can open
  // for the arguments it went out with and what came back. `n` numbers it in
  // the trace; the transcript leaves it off and leads with the tool name.
  // one line under a message: total, model calls, tokens, and — when the
  // caller is a guest paying for it — what was actually charged. The per-call
  // breakdown is the tooltip, because the total is what is read at a glance
  // and the calls are what is read when it looks wrong.
  const costFooter = (msg: Message) => {
    const u = msg.usage
    if (!u || !u.calls) return null
    const cost = u.priced === false ? null : fmtUSD(u.cost)
    const toks = fmtTok(u.tokens)
    return (
      <div className="flex items-center gap-1.5 flex-wrap pt-1.5 mt-1.5 border-t border-white/[0.05] text-[10px] text-gray-600"
        title={callBreakdown(u) || undefined}>
        <span className={cost ? 'text-emerald-300/60' : 'text-gray-600'}>
          {cost || 'unpriced model'}
        </span>
        <span>· {u.calls} model call{u.calls === 1 ? '' : 's'}</span>
        {toks && <span>· {toks} tok</span>}
        {msg.live && <span className="text-gray-700">· so far</span>}
        {u.charged != null && (
          <span className="text-amber-300/60" title="taken from your credit balance — provider cost plus the module's margin">
            · charged {fmtUSD(u.charged)}
          </span>
        )}
        {u.model && <span className="text-gray-700 truncate max-w-[40%]">· {u.model}</span>}
      </div>
    )
  }

  const stepRow = (step: any, key: string, n?: number) => {
    const target = stepTarget(step)
    const open = !!expandedSteps[key]
    return (
      <div key={key} className={`text-xs rounded-md border transition ${
        step.error ? 'bg-red-500/[0.04] border-red-500/15' : 'bg-white/[0.02] border-white/[0.05] hover:border-white/[0.1]'
      }`}>
        <button className="w-full text-left flex items-center gap-2 px-2.5 py-1.5" onClick={() => toggleStep(key)}
          title={open ? 'Hide the arguments and the result' : 'Show the arguments and the result'}>
          {n != null
            ? <span className="text-gray-700 w-6 shrink-0 font-mono text-[10px]">{String(n).padStart(2, '0')}</span>
            : <span className="text-emerald-400/50 shrink-0 text-[10px]">⚙</span>}
          <span className="text-gray-600 shrink-0">{open ? '▼' : '▶'}</span>
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
  }

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
            {steps.map((step: any, j: number) => stepRow(step, `trace-${j}`, j + 1))}
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
        <MemoryPanel token={auth?.token} session={sessionId()} memSel={memSel} onToggleMem={toggleNote}
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
                      if (file) setViewingFile(file)
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
        // Auto margins, not justify-center: a centred flex child overflows in
        // BOTH directions when it outgrows the box, and the half above the fold
        // is unreachable — which is how a short window ate the question.
        <div className="h-full overflow-y-auto flex flex-col items-center px-5">
          <div className="hero my-auto flex flex-col items-center text-center">
            <div className="hero-halo">
              <div className="hero-mark rounded-2xl bg-emerald-500/[0.06] border border-emerald-500/20 hero-logo flex items-center justify-center relative">
                <span className="text-emerald-300 font-mono select-none">{'>'}<span className="caret-blink">_</span></span>
              </div>
            </div>

            <h2 className="hero-q">
              {tasks.length === 0 ? 'What should we build?' : 'New chat'}
            </h2>
            <p className="hero-sub max-w-[440px]">
              {isFree
                ? 'This run generates on weights that never leave the box — no key, no bill. Every step it takes is kept in the trace beside this transcript.'
                : 'Every step the run takes is kept in the trace beside this transcript.'}
            </p>

            {/* what the next run is carrying — and the way back to each of them */}
            <div className="hero-ctx flex items-center justify-center gap-1.5 flex-wrap">
              <button className="ctx-chip" onClick={() => setShowPicker(true)}
                title="Change the agent or prompt this runs as">
                <span className="ctx-chip__d bg-emerald-400/70" />
                <span className="ctx-chip__n">{promptSel?.name || currentAgentDef?.label || agentType}</span>
                {!promptSel && isDefaultAgent(agentType) && (
                  <span className="text-emerald-300/70" title="your default agent">★</span>
                )}
              </button>
              {/* the default is a choice, so it is a control rather than a
                  fact printed somewhere — this is the way back to it */}
              <button className="ctx-chip" onClick={() => { setDefaultErr(null); setShowDefaultPick(true) }}
                title={defaultSource === 'you'
                  ? 'The agent every new chat starts on — yours to change'
                  : 'No default picked yet — this module chose one for you'}>
                <span className="text-emerald-300/70">★</span>
                default
                {/* the name only when it isn't the agent already named on the
                    chip beside it — two chips saying "Default" is one chip */}
                {!(!promptSel && isDefaultAgent(agentType)) && (
                  <span className="ctx-chip__n">
                    {agentOptions.find(a => a.value === (defaultPick || defaultAgent))?.label || defaultAgent}
                  </span>
                )}
              </button>
              <button className="ctx-chip" onClick={() => { setActiveTab('tools'); setToolPane('registry') }}
                title="The tools this run gets — open the registry">
                <span className="ctx-chip__n">{toolSel.length || toolCounts?.total || ''}</span>
                {toolSel.length ? 'tools' : toolCounts?.total ? 'tools' : 'full toolbox'}
              </button>
              {memSel.length > 0 && (
                <button className="ctx-chip" onClick={() => setActiveTab('memory')}
                  title="Memory notes riding along with this run">
                  <span className="ctx-chip__d bg-sky-400/70" />
                  <span className="ctx-chip__n">{memSel.length}</span>
                  note{memSel.length > 1 ? 's' : ''}
                </button>
              )}
              {model && (
                <button className="ctx-chip" onClick={() => openKeyPanel(provider)}
                  title={isFree
                    ? `${provider} · ${model} — ${activeProvider?.hint || 'local compute'}, never billed`
                    : `${provider} · ${model} — hosted, billed at cost`}>
                  {isFree && <span className="ctx-chip__d bg-emerald-400/70" />}
                  {model.split('/').pop()}
                  <span className="ctx-chip__n">{isFree ? 'free' : provider}</span>
                </button>
              )}
            </div>

            {/* four ways in */}
            <div className="suggest">
              {STARTERS.map(s => (
                <button key={s.q} className="suggest-card" onClick={() => { setQuery(s.q); inputRef.current?.focus() }}>
                  <span className="suggest-card__i">{s.icon}</span>
                  <span className="min-w-0">
                    <span className="suggest-card__t block">{s.q}</span>
                    <span className="suggest-card__s block">{s.s}</span>
                  </span>
                </button>
              ))}
              <button className="suggest-card suggest-card--go" onClick={() => openHub('library')}>
                <span className="suggest-card__i">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4 19.5V5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2z" />
                    <path d="M9 3v14" />
                  </svg>
                </span>
                <span className="min-w-0">
                  <span className="suggest-card__t block">browse the library →</span>
                  <span className="suggest-card__s block">prompts, tools, memory, agents</span>
                </span>
              </button>
            </div>

            <p className="hero-foot">
              <span className="text-emerald-400/50">⬡</span>
              {auth ? 'your chats are pinned to localfs and follow your wallet'
                    : 'sign in to keep chats in localfs across devices'}
            </p>
          </div>
        </div>
      ) : (
        <div className="p-3 space-y-2 max-w-4xl mx-auto w-full">
          {currentTask.messages.map((msg, i) => {
            // the transcript carries the calls themselves — each one a row you
            // can open right here; the TOOLS tab is the same steps end to end,
            // numbered, without the conversation between them
            // show the thumbnails, open the full-size copy — after a reload
            // only the thumbnails survived, so they stand in for both
            const shots = msg.thumbs || msg.images || []
            const full = msg.images || msg.thumbs || []
            // a system message we can read gets said properly, and the server's
            // own words move under a disclosure rather than leading with them
            const notice = msg.role === 'system' ? runNotice(msg.text) : null
            return (
            <div key={i} className={`${msg.role === 'user' ? 'ml-auto max-w-[85%]' : 'max-w-full'}`}>
              <div className={`rounded-lg px-3 py-2.5 msg-in ${
                msg.role === 'user' ? 'bg-emerald-500/10 border border-emerald-500/20' :
                msg.role === 'system' ? notice ? 'bg-white/[0.02] border border-white/[0.07]'
                                              : 'bg-red-500/10 border border-red-500/15' :
                'bg-white/[0.03] border border-white/[0.06]'
              }`}>
                {/* a notice names itself — "system" above it is just noise */}
                {!notice && (
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs text-gray-500">{msg.role}</span>
                  </div>
                )}
                {shots.length > 0 && (
                  <div className="flex gap-1.5 flex-wrap mb-1.5">
                    {shots.map((src, k) => (
                      <img key={k} src={src} alt="attachment" title="Click to view"
                        onClick={() => openLightbox(full.length === shots.length ? full : shots, k)}
                        className="h-20 w-20 object-cover rounded-md border border-white/10 cursor-zoom-in hover:border-emerald-500/40 transition" />
                    ))}
                  </div>
                )}
                {notice ? (
                  <>
                    {notice}
                    <details className="mt-2 group/raw">
                      <summary className="text-[10px] text-gray-600 hover:text-gray-400 cursor-pointer select-none transition list-none">
                        <span className="inline-block w-3 group-open/raw:rotate-90 transition-transform">▸</span>
                        what the server said
                      </summary>
                      <pre className="mt-1.5 pl-2 border-l border-white/[0.08] text-[11px] leading-relaxed text-gray-500 whitespace-pre-wrap">{msg.text}</pre>
                    </details>
                  </>
                ) : (
                  <div className="whitespace-pre-wrap text-sm text-gray-300 leading-relaxed">
                    {msg.text ? renderText(msg.text) : null}
                    {/* the model's output, landing as it streams. Prose shows
                        with a caret; a tool call being written folds into an
                        indicator naming the tool it is shaping up to be */}
                    {msg.live && msg.draft ? (() => {
                      const d = draftView(msg.draft)
                      return (
                        <>
                          {d.prose && (
                            <span className="text-gray-400">
                              {msg.text ? '\n' : ''}{d.prose}
                              <span className="text-emerald-400 animate-pulse">▋</span>
                            </span>
                          )}
                          {d.composing != null && (
                            <div className="flex items-center gap-2 mt-1 text-xs">
                              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse shrink-0" />
                              <span className="text-gray-600 shimmer-text">
                                writing a <span className="text-emerald-300/80 font-mono">{d.composing || 'tool'}</span> call…
                              </span>
                            </div>
                          )}
                          {!d.prose && d.composing == null && !msg.text && !msg.steps?.length && (
                            <span className="text-gray-600 shimmer-text">thinking…</span>
                          )}
                        </>
                      )
                    })() : msg.live && !msg.text && !msg.steps?.length && !msg.running ? (
                      <span className="text-gray-600 shimmer-text">thinking…</span>
                    ) : null}
                  </div>
                )}
                {/* the calls themselves, in the transcript. What the agent
                    reached for is half of what it did, and it used to be a
                    count you had to leave the conversation to read. */}
                {((msg.steps && msg.steps.length > 0) || (msg.live && msg.running)) && (
                  <div className="mt-2 space-y-0.5">
                    {(msg.steps || []).map((step: any, j: number) => stepRow(step, `m${i}-${j}`))}
                    {/* still going: the call in flight, by name, while it
                        runs — or a plain pulse when nothing is executing */}
                    {msg.live && (msg.running ? (
                      <div className="flex items-center gap-2 px-2.5 py-1.5 text-xs rounded-md border border-emerald-500/20 bg-emerald-500/[0.04]">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse shrink-0" />
                        <span className="text-emerald-300 font-mono shrink-0 shimmer-text">{msg.running.tool}</span>
                        {stepTarget(msg.running) && (
                          <span className="text-gray-600 truncate font-mono">{shortPath(String(stepTarget(msg.running)))}</span>
                        )}
                        {(msg.running.n || 0) > 1 && (
                          <span className="text-gray-700 ml-auto shrink-0 text-[10px]">{msg.running.i}/{msg.running.n}</span>
                        )}
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse shrink-0" />
                        <span className="text-gray-600 shimmer-text">working…</span>
                      </div>
                    ))}
                    {(msg.steps?.length || 0) > 0 && (
                      <button onClick={() => setActiveTab('tools')}
                        title="Open the full trace"
                        className="inline-flex items-center gap-1.5 pt-0.5 px-2.5 text-[10px] text-gray-600 hover:text-emerald-300 transition">
                        {msg.steps!.length} call{msg.steps!.length === 1 ? '' : 's'}
                        {msg.steps!.some((s: any) => s.error) && <span className="text-red-400/80">· errors</span>}
                        <span className="text-gray-700">· full trace →</span>
                      </button>
                    )}
                  </div>
                )}
                {/* what this call to the agent cost on the provider key. Every
                    run has a price whether or not anyone is billed for it, and
                    a price you only learn from the treasury is a price you
                    don't watch — so it sits under the answer that spent it,
                    counting up while the run is still going. */}
                {costFooter(msg)}
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
      onClick={() => { setSelectedTask(t.id); setActiveTab('output'); setViewingFile(null) }}
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
          {/* "signed out" rather than "not signed in": three characters
              shorter, which is the difference between a word and "not sig…"
              in a rail at its floor, and the button beside it already says
              what to do about it */}
          {auth ? shortAddr(auth.address) : 'signed out'}
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
  // Two states in one pane: the list, and the editor for whichever agent you
  // opened. Making one never leaves the console — you write it here, hit
  // "save + use", and the next message runs as it.
  const agentsPane = agentEdit ? (
    <AgentEditor
      key={agentEdit.name || (agentEdit.from ? `from:${agentEdit.from}` : 'new')}
      name={agentEdit.name}
      from={agentEdit.from}
      token={auth?.token}
      isHost={isHost}
      address={auth?.address}
      defaultAgent={defaultPick || defaultAgent}
      onMakeDefault={setDefaultAgentPick}
      onSaved={(name) => { fetchAgents(auth?.token); libChanged(); setAgentEdit({ name }) }}
      onClose={() => setAgentEdit(null)}
      onOpenCanvas={(name) => { setAgentEdit(null); openBuilder(name) }}
      onUse={(name) => {
        selectAgent(name)
        setAgentEdit(null)
        setTimeout(() => inputRef.current?.focus(), 60)
      }}
    />
  ) : (
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
      {/* new agent — written right here; the canvas is one click further on
          for the graph view of the same thing */}
      {/* a top edge, like the identity footer below it — without one the row
          the list happens to be cut through bleeds straight into these buttons */}
      <div className="px-2 py-2 shrink-0 flex items-center gap-1.5 border-t border-white/[0.06]">
        <button onClick={() => setAgentEdit({ name: null })}
          className="flex-1 min-w-0 text-left px-2.5 py-2 rounded-md text-xs whitespace-nowrap transition border border-dashed border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 flex items-center gap-2">
          <span className="w-5 text-center shrink-0">+</span> new agent
        </button>
        <button onClick={() => openBuilder()}
          title="Open the canvas — wire an agent as a graph"
          className="px-2 py-2 rounded-md text-[10px] uppercase tracking-wider transition border border-white/[0.08] text-gray-500 hover:text-violet-300 hover:border-violet-400/30 shrink-0">
          canvas
        </button>
      </div>
    </>
  )

  const railContent = (
    <div className="flex flex-col h-full min-h-0">
      {/* rail header — two panes: the chats you've had, the agents you can be */}
      <div className="px-2 py-2 border-b border-white/[0.06] flex items-center gap-1 shrink-0">
        {/* the two pane names are the header's floor — the counts beside them
            are not, so a rail dragged down near its minimum drops the counts
            rather than growing wider than the rail and sliding underneath the
            buttons on the right, which is what it used to do */}
        <div className="flex items-center gap-0.5 bg-white/[0.03] border border-white/[0.07] rounded-md p-0.5 min-w-0 overflow-hidden">
          {([['chats', tasks.length], ['agents', personas.length]] as const).map(([pane, n]) => (
            <button key={pane} onClick={() => { setPane(pane as RailPane); if (pane === 'agents') setAgentEdit(null) }}
              title={`${n} ${pane}`}
              className={`px-2 py-1 rounded text-[10px] uppercase tracking-wider whitespace-nowrap truncate min-w-0 transition ${
                railPane === pane ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-500 hover:text-gray-300'
              }`}>
              {pane}{n && !tightRail ? <span className="opacity-60 font-mono"> {n}</span> : null}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-0.5 shrink-0">
          <button onClick={() => railPane === 'chats' ? newChat() : setAgentEdit({ name: null })}
            className="w-6 h-6 flex items-center justify-center rounded-md text-emerald-300/90 hover:bg-emerald-500/10 border border-emerald-500/25 transition text-sm leading-none"
            title={railPane === 'chats' ? 'New chat' : 'New agent'}>+</button>
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
      <div className="flex-1 overflow-y-auto min-h-0 p-1.5 space-y-0.5 flex flex-col">
        {visibleChats.length === 0 ? (
          /* centred rather than pinned to the top of an empty column — the rail
             is the full height of the window and a card floating at the top of
             it read as a layout that had failed */
          <div className="my-auto px-4 py-6 text-center flex flex-col items-center gap-2">
            <div className="w-9 h-9 rounded-xl border border-white/[0.07] bg-white/[0.02] flex items-center justify-center text-gray-700 text-sm font-mono">
              {tasks.length === 0 ? '>_' : '⌕'}
            </div>
            <p className="text-xs text-gray-400">{tasks.length === 0 ? 'No chats yet' : 'Nothing matches'}</p>
            <p className="text-[10px] text-gray-600 leading-relaxed max-w-[190px]">
              {tasks.length === 0
                ? 'Every run in the console lands here, and each one is its own chat.'
                : 'No chat in this history matches that filter.'}
            </p>
            {tasks.length === 0 && (
              <button onClick={newChat}
                className="mt-1 px-2.5 py-1.5 rounded-md text-[10px] uppercase tracking-wider border border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 transition">
                Start one
              </button>
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

      {/* no LIBRARY / AGENTS pair down here — both are tabs in the header two
          rows up, and the second one collided with this rail's own AGENTS pane,
          which opens something else entirely. Who you are is the only thing the
          foot of the rail owes you. */}
      {identityFooter}
    </div>
  )

  // --- File viewer — one file the run touched, opened from the DELTAS tab.
  //     It takes over the console body rather than owning a panel of its own:
  //     an empty file list is not worth half a screen. ---
  const fileViewer = viewingFile && (
    <div className="h-full flex flex-col min-h-0">
      <div className="border-b border-white/[0.06] px-3 py-2 flex items-center gap-2 shrink-0 bg-surface-1">
        <button onClick={() => setViewingFile(null)}
          className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-gray-500 hover:text-gray-200 transition"
          title="Back to the run (Esc)">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          back
        </button>
        <span className={`text-[10px] font-mono ml-1 ${extColor(fileExt(viewingFile.path))}`}>
          .{fileExt(viewingFile.path)}
        </span>
        <span className="text-xs text-gray-400 font-mono truncate min-w-0">{shortPath(viewingFile.path)}</span>
        {(() => {
          const b = actionBadge(viewingFile.action)
          return (
            <span className={`text-[9px] px-1.5 py-0.5 rounded-md border ${b.bg} ${b.text} shrink-0 ml-auto uppercase tracking-wider`}>
              {viewingFile.action}
            </span>
          )
        })()}
      </div>
      <div className="flex-1 overflow-y-auto min-h-0">
        <pre className="text-[12px] leading-[1.6] font-mono text-gray-300 p-3 whitespace-pre-wrap">
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
  )

  // --- The console — the workspace itself, full height ---
  const consoleDock = (
    <div className="relative flex-1 flex flex-col min-h-0 console-bg">
      {/* console header — one line where it fits: the panes on the left, what's
          running in the middle, the run controls (persona, model, credit) on
          the right. It was always two rows, which stacked a third bar of
          chrome under the top bar for no gain on a normal window; wrapping
          means the narrow case still gets its own line, and only then. */}
      <div className="border-b border-white/[0.06] shrink-0 min-w-0">
      <div className="px-2 flex flex-wrap items-center gap-x-2 gap-y-1 py-1 min-w-0">
        <div className="tab-strip shrink-0 max-w-full">
          {(['output', 'tools', 'memory', 'deltas'] as Tab[]).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`tab-btn px-3 py-2 font-medium uppercase tracking-wider transition-colors relative ${
                activeTab === tab ? 'text-white' : 'text-gray-600 hover:text-gray-400'
              }`}
            >
              {/* "console" here sat directly under CONSOLE in the view
                  switcher — same word, two different scopes. This pane is the
                  transcript, and the rail beside it already calls those chats. */}
              {tab === 'output' ? 'chat' : tab}
              {tab === 'tools' && (currentTask && getSteps(currentTask).length > 0 ? (
                <span className="tab-badge ml-1 text-emerald-400/80 normal-case">{getSteps(currentTask).length}</span>
              ) : toolCounts ? (
                <span className="tab-badge ml-1 text-gray-600 normal-case">{toolCounts.total}</span>
              ) : null)}
              {tab === 'memory' && memSel.length > 0 && (
                <span className="tab-badge ml-1 text-sky-400/80 normal-case">{memSel.length}</span>
              )}
              {tab === 'deltas' && currentTask && getDeltas(currentTask).length > 0 && (
                <span className="tab-badge ml-1 text-amber-400/80 normal-case">{getDeltas(currentTask).length}</span>
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

        {/* run controls, boxed so they read as one thing: what the next run
            runs as. `ml-auto` holds them at the right edge on a wide dock;
            when the tabs and the title leave no room, the box wraps whole
            onto its own line instead of scrolling a control out of reach. */}
        <div className="runbar shrink-0 ml-auto max-w-full overflow-x-auto no-scrollbar">
          {personaPicker}
          <span className="runbar__sep" />
          {modelControls}
          <span className="runbar__sep" />
          {balancePill}
        </div>

      </div>
      </div>

      <div className="flex-1 min-h-0">{viewingFile ? fileViewer : transcript}</div>
      {composeBar}
    </div>
  )

  // overlays shared by both layouts (fullscreen + normal)
  // --- Pick a default agent — asked once, before the first run ---
  // Which agent a run lands on used to be decided for you and never said out
  // loud. It's the one choice that colours every run, so it gets asked: a
  // card, the agents you can actually run, one click, and it's remembered.
  const defaultAgentDialog = showDefaultPick && agentOptions.length > 0 ? (
    <div className="fixed inset-0 z-[95] bg-black/70 backdrop-blur-sm flex items-center justify-center p-5"
      onClick={dismissDefaultPick}>
      <div onClick={e => e.stopPropagation()}
        className="w-full max-w-md max-h-[80vh] flex flex-col bg-surface-2 border border-white/10 rounded-xl shadow-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-white/[0.06]">
          <div className="text-sm text-gray-100 font-medium">Pick your default agent</div>
          <div className="text-[11px] text-gray-500 mt-1 leading-relaxed">
            It&apos;s the one every chat starts on — you can still switch per message, and
            change this any time from the agent menu.
            {auth ? ' Kept against your address, so it follows your wallet.'
                  : ' Sign in to keep it across devices; for now this browser holds it.'}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0 p-1.5 space-y-0.5">
          {agentOptions.map(a => {
            const locked = !!a.harness && !canRunHarness(a.harness)
            return (
              <button key={a.value} disabled={locked}
                onClick={() => { selectAgent(a.value); setDefaultAgentPick(a.value) }}
                className={`w-full text-left px-2.5 py-2 rounded-md transition border ${
                  locked
                    ? 'border-transparent opacity-40 cursor-not-allowed'
                    : a.value === defaultAgent
                    ? 'bg-emerald-500/10 border-emerald-500/25 hover:bg-emerald-500/15'
                    : 'border-transparent hover:bg-white/[0.05]'
                }`}
                title={locked ? `${a.label} runs on the host's own ${a.harness} CLI — host only` : a.description}>
                <div className="flex items-center gap-2">
                  <span className="w-5 text-center shrink-0 text-gray-300">{a.icon}</span>
                  <span className="text-xs text-gray-200 truncate min-w-0">{a.label}</span>
                  {a.value === defaultAgent && (
                    <span className="text-[9px] px-1 py-0.5 rounded bg-white/[0.06] text-gray-500 shrink-0">
                      suggested
                    </span>
                  )}
                  {locked && <span className="text-[9px] text-gray-600 shrink-0 ml-auto">host only</span>}
                </div>
                {a.description && (
                  <div className="text-[10px] text-gray-600 mt-0.5 pl-7 line-clamp-2 leading-relaxed">
                    {a.description}
                  </div>
                )}
              </button>
            )
          })}
        </div>
        <div className="px-3 py-2 border-t border-white/[0.06] flex items-center gap-2">
          <span className={`text-[10px] truncate min-w-0 ${defaultErr ? 'text-red-400' : 'text-gray-600'}`}>
            {defaultErr || 'or build one of your own'}
          </span>
          <button onClick={() => { dismissDefaultPick(); openAgentEditor() }}
            className="ml-auto shrink-0 text-[10px] px-2 py-1 rounded-md border border-emerald-500/25 text-emerald-300/90 hover:bg-emerald-500/10 transition">
            build an agent
          </button>
          <button onClick={dismissDefaultPick}
            className="shrink-0 text-[10px] px-2 py-1 rounded-md border border-white/10 text-gray-500 hover:text-gray-300 transition">
            not now
          </button>
        </div>
      </div>
    </div>
  ) : null

  const overlays = (
    <>
      {defaultAgentDialog}
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
            onOpenLibrary={() => openHub('library')}
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

  // workspace: the console, floor to ceiling, between the two rails
  const workspace = (
    <div className="flex-1 flex flex-col min-h-0 min-w-0">
      {consoleDock}
    </div>
  )

  // --- Hub: the agents canvas — the graph an agent is wired on, and the
  //     TASK mode that writes what agents are scored on. The rail's AGENTS
  //     pane covers making and editing one; this is the graph view of it. ---
  const agentsCanvas = (
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
          setView('chat')
          setTimeout(() => inputRef.current?.focus(), 60)
        }}
        onRunAgent={(name, prompt, memoryIds) => {
          selectAgent(name)
          if (memoryIds.length) {
            setMemSel(memoryIds)
            try { localStorage.setItem('agent_mem_sel', JSON.stringify(memoryIds)) } catch {}
          }
          setQuery(prompt)
          setView('chat')
          setTimeout(() => inputRef.current?.focus(), 60)
        }}
        onAgentsChanged={() => { fetchAgents(auth?.token); libChanged() }}
        onManageKey={(p) => { setKeyPanelProvider(p); setShowKeyPanel(true) }}
        keyVersion={keyVersion}
        token={auth?.token}
        isHost={isHost}
        onSignIn={signIn}
        address={auth?.address}
        onOpenArena={() => setView('arena')}
      />
    </div>
  )

  // --- Hub: the library market — prompts, tools, memory and agents, each of
  //     which lands back in the chat when you take it ---
  const libraryPage = (
    <div className="flex-1 min-h-0">
      <Library
        onUsePrompt={(text) => {
          setView('chat')
          setQuery(text)
          setTimeout(() => inputRef.current?.focus(), 60)
        }}
        onSelectAgent={(name) => {
          selectAgent(name)
          setView('chat')
          setTimeout(() => inputRef.current?.focus(), 60)
        }}
        onSelectPrompt={(item) => {
          selectPrompt({ id: item.id, name: item.name, description: item.description || '',
            body: item.body || '', tags: item.tags || [],
            owner: item.owner ?? null, owner_source: (item.owner_source ?? null) as OwnerSource })
          setView('chat')
          setTimeout(() => inputRef.current?.focus(), 60)
        }}
        onUseMemory={(id) => {
          toggleNote(id)
          setView('chat')
          setTimeout(() => inputRef.current?.focus(), 60)
        }}
        onUseTool={(id) => {
          setToolSel(prev => prev.includes(id) ? prev.filter(s => s !== id) : [...prev, id])
          setView('chat')
          setTimeout(() => inputRef.current?.focus(), 60)
        }}
        onAgentsChanged={() => { fetchAgents(auth?.token); libChanged() }}
        onSignIn={signIn}
        auth={auth}
        host={owner}
      />
    </div>
  )

  return (
    <main className="h-screen flex flex-col bg-surface-0">
      {/* top bar — the three views and who you are. Everything else lives where
          it's used: the rails carry their own collapse, the dock its own size,
          the key and the tool count sit in the rail's foot. */}
      {/* The view switcher is the one thing that must always be readable in
          full, so below lg it drops to its own row (order-last + basis-full)
          rather than being squeezed or scrolled off behind the sign-in
          cluster — the bar grows a line instead of hiding a tab. */}
      <header className="border-b border-white/[0.06] px-3 min-h-12 py-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 shrink-0 bg-surface-0">
        <div className="flex items-center gap-2.5 shrink-0" title="Agent — mod framework">
          <div className="brand-mark w-7 h-7 flex items-center justify-center shrink-0">
            <span className="select-none">{'>'}_</span>
          </div>
          {/* wordmark — first thing the eye lands on, so it carries the theme's
              accent. Drops out under 640px, where the bar needs the room. */}
          <span className="title-gradient uppercase select-none hidden sm:block">agent</span>
        </div>

        <nav className="tab-strip order-last basis-full lg:order-none lg:basis-auto gap-0.5 bg-white/[0.03] border border-white/[0.07] rounded-lg p-0.5">
          {(['chat', 'hub', 'arena'] as const).map(v => (
            <button key={v}
              onClick={() => { if (v === 'hub') openHub(hubPane); else setView(v) }}
              className={`tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium uppercase tracking-wider transition ${
                view === v ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-500 hover:text-gray-300'
              }`}
              title={v === 'hub'
                ? runningCount > 0
                  ? `Agents, library and the ${runningCount} runs still going`
                  : 'Agents, library and background runs'
                : v === 'chat' ? 'The console — talk to an agent'
                : 'Every agent on the same tasks, one ranked board'}>
              {v}
              {/* the hub is where a background run lives now, so its dot is the
                  only thing the top bar still has to say about one */}
              {v === 'hub' && runningCount > 0 && (
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              )}
            </button>
          ))}
        </nav>

        <div className="flex items-center gap-3 ml-auto shrink-0">
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
        {/* HUB — the agents canvas, the library and the background runs, one
            shelf strip instead of three tabs crowding the chat */}
        {view === 'hub' && (
          <div className="flex-1 min-h-0 flex flex-col">
            <div className="border-b border-white/[0.06] px-3 py-1.5 shrink-0 flex items-center gap-1.5 flex-wrap">
              {(['agents', 'library', 'tasks'] as HubPane[]).map(p => (
                <button key={p} onClick={() => openHub(p)}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider transition border ${
                    hubPane === p ? 'bg-emerald-500/15 border-emerald-500/25 text-emerald-300'
                                  : 'bg-white/[0.03] border-white/[0.06] text-gray-600 hover:text-gray-300'
                  }`}>
                  {p}
                  {p === 'tasks' && runningCount > 0 && (
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  )}
                </button>
              ))}
              {/* the tasks shelf says this in its own header a line down, so
                  the strip stays quiet there */}
              {hubPane !== 'tasks' && (
                <span className="ml-auto text-[10px] text-gray-600 hidden sm:block">
                  {hubPane === 'agents' ? 'wire an agent — nodes, tools, model'
                    : 'prompts, tools, memory, agents — pull one into a chat'}
                </span>
              )}
            </div>
            <div className="flex-1 min-h-0 flex">
              {hubPane === 'tasks' && tasksPage}
              {hubPane === 'agents' && agentsCanvas}
              {hubPane === 'library' && libraryPage}
            </div>
          </div>
        )}

        {/* arena — every agent on the same tasks, one ranked board */}
        {view === 'arena' && (
          <div className="flex-1 min-h-0 flex">
            <Arena token={auth?.token} isHost={isHost} />
          </div>
        )}

        {/* console: chats + agents rail on one side, the market on the other,
            console docked at the bottom */}
        {view === 'chat' && (
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
        <div className="tab-strip px-5 pt-3 gap-1.5">
          {tabs.map(p => (
            <button key={p} onClick={() => setTab(p)}
              className={`tab-btn px-3 py-1.5 rounded-lg font-medium transition border ${
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
                  {/* an empty key is the usual reason a run just failed —
                      the place to fix it belongs next to the number */}
                  {PROVIDER_META[tab]?.topUpUrl && (
                    <a href={PROVIDER_META[tab].topUpUrl} target="_blank" rel="noreferrer"
                      className={`text-[10px] px-1.5 py-0.5 rounded border transition ${
                        typeof info.balance === 'number' && info.balance <= 0
                          ? 'border-amber-500/40 text-amber-200 hover:bg-amber-500/10'
                          : 'border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20'
                      }`}>
                      top up ↗
                    </a>
                  )}
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
