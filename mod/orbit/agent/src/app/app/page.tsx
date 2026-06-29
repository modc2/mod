'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { API_URL } from './config'

type Skill = { description: string; params: Record<string, any> }
type Message = { role: 'user' | 'agent' | 'system'; text: string; steps?: any[]; chainStep?: { agent: string; prompt: string } }
type TaskEntry = { id: number; query: string; status: 'running' | 'done' | 'error'; stepCount?: number; messages: Message[]; agent_type?: string; chain?: ChainStep[] }
type Tab = 'tasks' | 'output' | 'deltas'

type ChainPreset = { name: string; description: string; steps: ChainStep[] }
type ChainStep = { agent: string; prompt: string }

type LayoutMode = 'sidebar' | 'fullscreen' | 'minimized'
type SidebarSide = 'left' | 'right'

type FileEntry = { path: string; content: string; action: 'read' | 'created' | 'modified' | 'searched' }

// ── Agent Types ─────────────────────────────────────────────────────

type AgentOption = { value: string; label: string; icon: string }

const DEFAULT_AGENTS: AgentOption[] = [
  { value: "default", label: "Default", icon: ">_" },
  { value: "architect", label: "Architect", icon: "△" },
  { value: "reviewer", label: "Reviewer", icon: "◉" },
  { value: "debugger", label: "Debugger", icon: "⬡" },
  { value: "builder", label: "Builder", icon: "◆" },
  { value: "refactorer", label: "Refactorer", icon: "⟳" },
]

const CHAIN_PRESETS: Record<string, ChainPreset> = {
  "full-review": { name: "Full Review", description: "Architecture review then code review", steps: [{ agent: "architect", prompt: "Analyze the architecture and structure of this project" }, { agent: "reviewer", prompt: "Review the code quality based on the architecture analysis" }] },
  "debug-fix": { name: "Debug & Fix", description: "Debug the issue then build the fix", steps: [{ agent: "debugger", prompt: "Find the root cause of this issue" }, { agent: "builder", prompt: "Fix the issue based on the debug analysis" }] },
  "plan-build-review": { name: "Plan, Build, Review", description: "Full pipeline: architect, build, then review", steps: [{ agent: "architect", prompt: "Design the implementation plan" }, { agent: "builder", prompt: "Implement the plan" }, { agent: "reviewer", prompt: "Review the implementation" }] },
}

export default function Home() {
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

  // agent & chain state
  const [agentType, setAgentType] = useState<string>('default')
  const [agentOptions, setAgentOptions] = useState<AgentOption[]>(DEFAULT_AGENTS)
  const [chain, setChain] = useState<ChainStep[]>([])
  const [chainMode, setChainMode] = useState(false)
  const [showChainPresets, setShowChainPresets] = useState(false)
  const [showChats, setShowChats] = useState(false)
  const [freeMode, setFreeMode] = useState(false)
  const [showCreateAgent, setShowCreateAgent] = useState(false)

  // provider + model selection
  type ProviderInfo = { key: string; models: string[]; default_model: string }
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [provider, setProvider] = useState<string>('openrouter')
  const [model, setModel] = useState<string>('')
  const [owner, setOwner] = useState<string | null>(null)

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
  const [sidebarWidth, setSidebarWidth] = useState(340)
  const isDragging = useRef(false)
  const dragStartX = useRef(0)
  const dragStartWidth = useRef(340)

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
          }))
          if (fetched.length > 0) setAgentOptions(fetched)
        }
      })
      .catch(() => {})
  }, [])

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
        setModel(savedM || pd?.default_model || '')
      })
      .catch(() => {})
    // owner / user info
    fetch(`${API_URL}/owner`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => setOwner(d.owner || null))
      .catch(() => {})
    const saved = localStorage.getItem('agent_type')
    if (saved) setAgentType(saved)
    const savedFree = localStorage.getItem('agent_free')
    if (savedFree === 'true') setFreeMode(true)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [tasks, selectedTask])

  const currentTask = tasks.find(t => t.id === selectedTask)
  const currentAgentDef = agentOptions.find(a => a.value === agentType)

  // chain management
  const addChainStep = (agentKey: string = 'default') => {
    setChain(c => [...c, { agent: agentKey, prompt: '' }])
    setChainMode(true)
  }

  const removeChainStep = (idx: number) => {
    setChain(c => c.filter((_, i) => i !== idx))
    if (chain.length <= 1) setChainMode(false)
  }

  const updateChainStep = (idx: number, field: 'agent' | 'prompt', value: string) => {
    setChain(c => c.map((s, i) => i === idx ? { ...s, [field]: value } : s))
  }

  const loadChainPreset = (key: string) => {
    const preset = CHAIN_PRESETS[key]
    if (preset) {
      setChain(preset.steps.map(s => ({ ...s })))
      setChainMode(true)
    }
  }

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

  const run = async () => {
    if (!query.trim() || loading) return
    const q = query.trim()
    setQuery('')
    setLoading(true)

    const id = ++taskId.current
    const agentLabel = currentAgentDef?.label || agentType
    const isChain = chainMode && chain.length > 0
    const userMsg: Message = {
      role: 'user',
      text: isChain ? `[chain: ${chain.map(s => agentOptions.find(a => a.value === s.agent)?.label || s.agent).join(' → ')}] ${q}` : `[${agentLabel}] ${q}`
    }
    const task: TaskEntry = { id, query: q, status: 'running', messages: [userMsg], agent_type: agentType, chain: isChain ? chain : undefined }
    setTasks(t => [task, ...t])
    setSelectedTask(id)
    setActiveTab('output')
    if (layoutMode === 'minimized') setLayoutMode('sidebar')
    if (sidebarCollapsed) setSidebarCollapsed(false)

    try {
      // check API is reachable before long-running request
      try {
        const ping = await fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(3000) })
        if (!ping.ok) throw new Error()
      } catch {
        throw new Error(`API not reachable at ${API_URL}. Start with: m agent/serve`)
      }

      const body: any = { query: q }
      if (isChain) body.chain = chain
      if (agentType && agentType !== 'default') body.agent_type = agentType
      if (provider) body.provider = provider
      if (model) body.model = model
      if (freeMode) body.free = true

      const controller = new AbortController()
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

      if (data.chain && data.results) {
        const msgs: Message[] = [...task.messages]
        let totalSteps = 0
        for (const cr of data.results) {
          const stepCount = cr.result?.length || 0
          totalSteps += stepCount
          const agentName = agentOptions.find(a => a.value === cr.agent)?.label || cr.agent
          msgs.push({
            role: 'agent',
            text: cr.error ? `[${agentName}] Error: ${cr.error}` : `[${agentName}] ${stepCount} step(s)${cr.summary ? ` — ${cr.summary}` : ''}`,
            steps: cr.result,
            chainStep: { agent: cr.agent, prompt: cr.prompt },
          })
        }
        setTasks(t => t.map(tk => tk.id === id
          ? { ...tk, status: 'done', stepCount: totalSteps, messages: msgs }
          : tk
        ))
      } else {
        const steps = data.result || []
        const stepCount = steps.length
        // extract text from response steps (LLM replied without tool calls)
        const responseText = steps
          .filter((s: any) => s.tool === 'response' && s.result)
          .map((s: any) => s.result)
          .join('\n')
        const finishSummary = steps
          .filter((s: any) => s.tool === 'finish')
          .map((s: any) => s.params?.summary)
          .filter(Boolean)
          .join('\n')
        const errorText = steps
          .filter((s: any) => s.tool === 'error' && s.error)
          .map((s: any) => s.error)
          .join('\n')
        const hasError = !!errorText || !!data.error
        const displayText = data.error
          ? `Error: ${data.error}`
          : errorText
          ? `Error: ${errorText}`
          : responseText || finishSummary || (stepCount ? `Completed ${stepCount} step(s)` : 'Done')
        const agentMsg: Message = {
          role: hasError ? 'system' : 'agent',
          text: displayText,
          steps: steps.filter((s: any) => !['response', 'error'].includes(s.tool)),
        }
        setTasks(t => t.map(tk => tk.id === id
          ? { ...tk, status: hasError ? 'error' : 'done', stepCount, messages: [...tk.messages, agentMsg] }
          : tk
        ))
      }
    } catch (e: any) {
      const msg = e.name === 'AbortError'
        ? 'Request timed out (5 min). The agent may still be running on the server.'
        : e.message === 'Load failed' || e.message === 'Failed to fetch'
        ? `API not reachable at ${API_URL}. Start with: m agent/serve`
        : e.message
      const errMsg: Message = { role: 'system', text: `Error: ${msg}` }
      setTasks(t => t.map(tk => tk.id === id
        ? { ...tk, status: 'error', messages: [...tk.messages, errMsg] }
        : tk
      ))
    }
    setLoading(false)
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

  const dismissTask = () => {
    if (!selectedTask) return
    setTasks(t => t.filter(tk => tk.id !== selectedTask))
    setSelectedTask(tasks.length > 1 ? tasks.find(t => t.id !== selectedTask)?.id || null : null)
    setActiveTab('output')
    setViewingFile(null)
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
    s === 'running' ? 'animate-spin text-blue-400' :
    s === 'done' ? 'text-emerald-400' : 'text-red-400'

  const statusDot = (s: TaskEntry['status']) =>
    s === 'running' ? '\u25D0' : s === 'done' ? '\u25CF' : '\u2715'

  const getDeltas = (task: TaskEntry | undefined) => {
    if (!task) return []
    const deltas: { tool: string; file?: string; action: string }[] = []
    for (const msg of task.messages) {
      if (!msg.steps) continue
      for (const step of msg.steps) {
        if (['read', 'write', 'edit', 'glob', 'grep'].includes(step.tool)) {
          deltas.push({
            tool: step.tool,
            file: step.params?.path || step.params?.file_path || step.params?.pattern || '\u2014',
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
      py: 'text-yellow-400', ts: 'text-blue-400', tsx: 'text-blue-400', js: 'text-yellow-300',
      rs: 'text-orange-400', sol: 'text-purple-400', json: 'text-green-400', md: 'text-gray-400',
      css: 'text-pink-400', html: 'text-orange-300', sh: 'text-green-300',
    }
    return map[ext] || 'text-gray-400'
  }

  const actionBadge = (action: string) => {
    const map: Record<string, { bg: string; text: string }> = {
      read: { bg: 'bg-blue-500/15 border-blue-500/25', text: 'text-blue-400' },
      created: { bg: 'bg-emerald-500/15 border-emerald-500/25', text: 'text-emerald-400' },
      modified: { bg: 'bg-amber-500/15 border-amber-500/25', text: 'text-amber-400' },
    }
    return map[action] || { bg: 'bg-gray-500/15 border-gray-500/25', text: 'text-gray-400' }
  }

  // provider + model selectors (used in both sidebar and fullscreen bars)
  const modelControls = (
    <div className="flex items-center gap-1.5 min-w-0">
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
        className="bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-gray-300 outline-none cursor-pointer hover:border-white/20 transition-colors min-w-0"
        style={{ maxWidth: '190px' }}
        title="Model"
      >
        {(model && !providerModels.includes(model) ? [model, ...providerModels] : providerModels).map(mn => (
          <option key={mn} value={mn} className="bg-[#111]">{mn}</option>
        ))}
        {providerModels.length === 0 && !model && <option value="" className="bg-[#111]">default</option>}
      </select>
    </div>
  )

  // --- Compose bar (agent prompt) — rendered at the bottom of the sidebar ---
  const composeBar = (
    <div className={`border-t border-white/[0.06] px-4 py-3 shrink-0 transition-colors ${composeFocused ? 'bg-white/[0.03]' : ''}`}>
      <div className="flex gap-2 items-end">
        <textarea
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setComposeFocused(true)}
          onBlur={() => setComposeFocused(false)}
          placeholder={chainMode ? 'Main query for chain...' : `Ask ${currentAgentDef?.label || 'agent'}...`}
          rows={2}
          className="flex-1 bg-transparent border-none outline-none text-[15px] resize-none placeholder:text-gray-600 py-1 leading-relaxed"
          disabled={loading}
        />
        <button onClick={run} disabled={loading || !query.trim() || apiStatus === 'down'}
          className="text-sm bg-blue-600/80 hover:bg-blue-500 disabled:bg-white/5 disabled:text-gray-600 text-white rounded-md px-4 py-2 transition font-medium shrink-0"
          title={apiStatus === 'down' ? `API offline at ${API_URL}` : ''}>
          Run
        </button>
      </div>
    </div>
  )

  // --- Sidebar content (conversations + compose + user info) ---
  const sidebarContent = (
    <div className="flex flex-col h-full">
      {/* agent selector + chain */}
      <div className="border-b border-white/[0.06] px-4 py-2 flex items-center gap-2 shrink-0">
        <select
          value={agentType}
          onChange={(e) => { setAgentType(e.target.value); localStorage.setItem('agent_type', e.target.value) }}
          className="bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-sm text-gray-300 outline-none cursor-pointer hover:border-white/20 transition-colors"
          style={{ maxWidth: '160px' }}
        >
          {agentOptions.map((a) => (
            <option key={a.value} value={a.value} className="bg-[#111]">{a.icon} {a.label}</option>
          ))}
        </select>

        <button
          onClick={() => {
            if (!chainMode) {
              setChain([{ agent: agentType, prompt: '' }])
              setChainMode(true)
            } else {
              setChainMode(false)
              setChain([])
            }
          }}
          className={`px-2 py-1.5 rounded-md text-xs font-medium transition shrink-0 ${
            chainMode
              ? 'bg-purple-500/20 border border-purple-500/30 text-purple-300'
              : 'border border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20'
          }`}
        >
          {chainMode ? 'chain on' : 'chain'}
        </button>

        <button
          onClick={() => { setFreeMode(f => !f); localStorage.setItem('agent_free', (!freeMode).toString()) }}
          className={`px-2 py-1.5 rounded-md text-xs font-medium transition shrink-0 ${
            freeMode
              ? 'bg-emerald-500/20 border border-emerald-500/30 text-emerald-300'
              : 'border border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20'
          }`}
          title={freeMode ? 'Using free OpenRouter model' : 'Click to use free models'}
        >
          {freeMode ? 'free' : '$'}
        </button>

        <div className="relative shrink-0 ml-auto flex items-center gap-1.5">
          <button
            onClick={() => setShowChainPresets(!showChainPresets)}
            className="border border-white/10 px-2 py-1.5 rounded-md text-xs text-gray-500 hover:text-gray-300 hover:border-white/20 transition"
          >
            presets
          </button>
          {showChainPresets && (
            <div className="absolute right-0 top-full mt-1 w-56 bg-[#141414] border border-white/10 rounded-lg overflow-hidden z-50 shadow-2xl">
              {Object.entries(CHAIN_PRESETS).map(([key, preset]) => (
                <button
                  key={key}
                  onClick={() => { loadChainPreset(key); setShowChainPresets(false) }}
                  className="w-full text-left px-4 py-2.5 text-xs hover:bg-white/[0.06] transition border-b border-white/5 last:border-0"
                >
                  <div className="text-gray-200 font-medium">{preset.name}</div>
                  <div className="text-gray-500 mt-0.5 text-[10px]">{preset.description}</div>
                </button>
              ))}
            </div>
          )}
          <button
            onClick={() => setShowCreateAgent(true)}
            className="border border-white/10 w-7 h-7 rounded-md text-xs text-gray-500 hover:text-gray-300 hover:border-white/20 transition flex items-center justify-center"
            title="Create new agent"
          >
            +
          </button>
        </div>
      </div>

      {/* provider + model selection */}
      <div className="border-b border-white/[0.06] px-4 py-2 flex items-center gap-2 shrink-0">
        <span className="text-[10px] text-gray-600 uppercase tracking-wider font-medium shrink-0">model</span>
        {modelControls}
      </div>

      {/* create agent modal */}
      {showCreateAgent && (
        <div className="border-b border-white/[0.06] px-4 py-3 shrink-0 bg-blue-500/[0.03]">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] text-blue-400 font-medium uppercase tracking-wider">New Agent</span>
            <button onClick={() => setShowCreateAgent(false)} className="ml-auto text-gray-600 hover:text-gray-300 text-xs transition">cancel</button>
          </div>
          <form onSubmit={async (e) => {
            e.preventDefault()
            const form = e.target as HTMLFormElement
            const name = (form.elements.namedItem('agentName') as HTMLInputElement).value.trim()
            const desc = (form.elements.namedItem('agentDesc') as HTMLInputElement).value.trim()
            const goal = (form.elements.namedItem('agentGoal') as HTMLTextAreaElement).value.trim()
            const icon = (form.elements.namedItem('agentIcon') as HTMLInputElement).value.trim() || '>_'
            if (!name) return
            try {
              const res = await fetch(`${API_URL}/agents`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description: desc, goal, icon }),
              })
              const data = await res.json()
              if (data.error) { alert(data.error); return }
              setShowCreateAgent(false)
              fetchAgents()
              setAgentType(name)
              localStorage.setItem('agent_type', name)
            } catch (err: any) { alert(err.message) }
          }} className="space-y-2">
            <div className="flex gap-2">
              <input name="agentName" placeholder="name (slug)" required
                className="flex-1 bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-gray-300 outline-none placeholder:text-gray-600" />
              <input name="agentIcon" placeholder="icon" defaultValue=">_" maxLength={4}
                className="w-14 bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-gray-300 outline-none text-center" />
            </div>
            <input name="agentDesc" placeholder="description"
              className="w-full bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-gray-300 outline-none placeholder:text-gray-600" />
            <textarea name="agentGoal" placeholder="system prompt / goal..." rows={3}
              className="w-full bg-white/5 border border-white/10 rounded-md px-2 py-1.5 text-xs text-gray-300 outline-none placeholder:text-gray-600 resize-none" />
            <button type="submit"
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-blue-600/80 hover:bg-blue-500 text-white transition">
              Create
            </button>
          </form>
        </div>
      )}

      {/* chain builder */}
      {chainMode && (
        <div className="border-b border-white/[0.06] px-4 py-3 shrink-0 bg-purple-500/[0.03]">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] text-purple-400 font-medium uppercase tracking-wider">Chain</span>
            <span className="text-[10px] text-gray-600">{chain.length} step{chain.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="space-y-2">
            {chain.map((step, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-[10px] text-gray-600 w-4 shrink-0">{i + 1}.</span>
                <select
                  value={step.agent}
                  onChange={e => updateChainStep(i, 'agent', e.target.value)}
                  className="bg-white/5 border border-white/10 rounded-md px-2 py-1 text-[10px] text-gray-300 outline-none w-24 shrink-0"
                >
                  {agentOptions.map(a => (
                    <option key={a.value} value={a.value} className="bg-[#111]">{a.icon} {a.label}</option>
                  ))}
                </select>
                <input
                  type="text"
                  value={step.prompt}
                  onChange={e => updateChainStep(i, 'prompt', e.target.value)}
                  placeholder="Step prompt..."
                  className="flex-1 bg-transparent border border-white/10 rounded-md px-2 py-1 text-[10px] text-gray-300 outline-none placeholder:text-gray-600 min-w-0"
                />
                {i > 0 && (
                  <button
                    onClick={() => {
                      const c = [...chain];
                      [c[i - 1], c[i]] = [c[i], c[i - 1]];
                      setChain(c)
                    }}
                    className="text-gray-600 hover:text-gray-300 text-xs transition"
                  >
                    ↑
                  </button>
                )}
                <button
                  onClick={() => removeChainStep(i)}
                  className="text-gray-600 hover:text-red-400 text-xs transition"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
          <button
            onClick={() => addChainStep(agentType)}
            className="mt-2 text-[10px] text-purple-400 hover:text-purple-300 transition"
          >
            + add step
          </button>
        </div>
      )}

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
                    {['read this file', 'search for TODO', 'run ls -la'].map(ex => (
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
                    <button
                      key={t.id}
                      onClick={() => { setSelectedTask(t.id); setActiveTab('output'); setShowChats(false) }}
                      className={`w-full text-left px-2.5 py-2 rounded-md text-sm transition group ${
                        selectedTask === t.id
                          ? 'bg-blue-500/10 border border-blue-500/20'
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
                        {t.chain && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-purple-500/10 border border-purple-500/20 text-purple-400 shrink-0">
                            chain
                          </span>
                        )}
                        <span className="truncate flex-1 text-gray-300 group-hover:text-gray-200">{t.query}</span>
                        {t.stepCount !== undefined && (
                          <span className="text-[10px] text-gray-600 shrink-0">{t.stepCount}s</span>
                        )}
                      </div>
                    </button>
                  ))}
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
              {activeTab === tab && (
                <span className="absolute bottom-0 left-1 right-1 h-[1.5px] bg-blue-500 rounded-full" />
              )}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1.5 pr-1">
          {selectedTask && currentTask && currentTask.status !== 'running' && (
            <button onClick={continueTask}
              className="w-6 h-6 flex items-center justify-center rounded text-[10px] text-blue-400 hover:bg-blue-500/10 transition"
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
          <div className="p-3 space-y-2">
            {!currentTask ? (
              <div className="text-center text-gray-600 mt-12 px-4">
                <p className="text-2xl mb-3 text-gray-700">{'>'}_ </p>
                <p className="text-sm text-gray-500">
                  {tasks.length === 0 ? 'No conversations yet' : 'Select a chat or ask below'}
                </p>
                <div className="mt-4 flex gap-1.5 justify-center flex-wrap">
                  {tasks.length === 0 ? (
                    ['read this file', 'search for TODO', 'run ls -la'].map(ex => (
                      <button key={ex} onClick={() => { setQuery(ex); inputRef.current?.focus() }}
                        className="px-3 py-1.5 rounded-full text-xs bg-white/5 border border-white/[0.06] text-gray-500 hover:text-gray-300 hover:bg-white/[0.08] transition">
                        {ex}
                      </button>
                    ))
                  ) : (
                    <button onClick={() => setShowChats(true)}
                      className="px-3 py-1.5 rounded-full text-xs bg-white/5 border border-white/[0.06] text-gray-500 hover:text-gray-300 hover:bg-white/[0.08] transition">
                      open chats ({tasks.length})
                    </button>
                  )}
                </div>
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
                </div>

                {currentTask.chain && (
                  <div className="flex items-center gap-1 mb-2 overflow-x-auto px-1">
                    {currentTask.chain.map((step, i) => {
                      const a = agentOptions.find(ao => ao.value === step.agent)
                      const chainMsgs = currentTask.messages.filter(m => m.chainStep)
                      const isDone = i < chainMsgs.length
                      const isActive = i === chainMsgs.length && currentTask.status === 'running'
                      return (
                        <div key={i} className="flex items-center gap-1">
                          {i > 0 && <span className="text-gray-700 text-xs">→</span>}
                          <span className={`px-1.5 py-0.5 rounded-md text-[10px] font-medium transition ${
                            isDone ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/25' :
                            isActive ? 'bg-blue-500/15 text-blue-400 border border-blue-500/25 animate-pulse' :
                            'bg-white/5 border border-white/[0.06] text-gray-500'
                          }`}>
                            {a?.icon || '?'} {a?.label || step.agent}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}

                {currentTask.messages.map((msg, i) => (
                  <div key={i} className={`${msg.role === 'user' ? 'ml-auto max-w-[85%]' : 'max-w-full'}`}>
                    <div className={`rounded-lg px-3 py-2.5 ${
                      msg.role === 'user' ? 'bg-blue-500/10 border border-blue-500/20' :
                      msg.role === 'system' ? 'bg-red-500/10 border border-red-500/15' :
                      'bg-white/[0.03] border border-white/[0.06]'
                    }`}>
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-xs text-gray-500">{msg.role}</span>
                        {msg.chainStep && (
                          <span className="text-[10px] px-1 py-0.5 rounded bg-purple-500/10 text-purple-400">
                            {agentOptions.find(a => a.value === msg.chainStep?.agent)?.icon} {agentOptions.find(a => a.value === msg.chainStep?.agent)?.label || msg.chainStep?.agent}
                          </span>
                        )}
                      </div>
                      <div className="whitespace-pre-wrap text-sm text-gray-300 leading-relaxed">{msg.text}</div>
                      {msg.steps && (
                        <div className="mt-1.5 space-y-0.5">
                          {msg.steps.map((step: any, j: number) => (
                            <div key={j} className="text-xs bg-white/[0.03] border border-white/[0.04] rounded-md px-2.5 py-1.5">
                              <button
                                className="w-full text-left flex items-center gap-2"
                                onClick={() => toggleStep(i * 1000 + j)}
                              >
                                <span className="text-gray-600">{expandedSteps[i * 1000 + j] ? '\u25BC' : '\u25B6'}</span>
                                <span className="text-blue-400">{step.tool}</span>
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
                {loading && selectedTask === currentTask.id && (
                  <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg px-3 py-2">
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
                      <span className="text-gray-500 text-sm">Working...</span>
                    </div>
                  </div>
                )}
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
                            d.action === 'read' ? 'text-blue-400' :
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

      {/* user info footer */}
      <div className="border-t border-white/[0.06] px-4 py-2.5 shrink-0 flex items-center gap-2.5">
        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-mono shrink-0 ${owner ? 'bg-blue-500/15 border border-blue-500/25 text-blue-300' : 'bg-white/5 border border-white/10 text-gray-500'}`}>
          {owner ? owner.slice(2, 4).toUpperCase() : '··'}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-xs text-gray-300 font-mono truncate" title={owner || 'no owner configured'}>
            {owner ? `${owner.slice(0, 6)}…${owner.slice(-4)}` : 'no owner'}
          </div>
          <div className="text-[10px] text-gray-600 truncate">
            {provider} · {model || 'default'} · {Object.keys(skills).length} skills
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <span className={`w-1.5 h-1.5 rounded-full ${apiStatus === 'ok' ? 'bg-emerald-400' : apiStatus === 'down' ? 'bg-red-400' : 'bg-gray-500'}`} />
          <span className="text-[10px] text-gray-600">{apiStatus === 'ok' ? 'online' : apiStatus === 'down' ? 'offline' : '…'}</span>
        </div>
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
              {detailView === v && <span className="absolute bottom-0 left-1 right-1 h-[1.5px] bg-blue-500 rounded-full" />}
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
                <div className="text-center">
                  <p className="text-3xl mb-3 text-gray-700">{'>'}_ </p>
                  <p className="text-xs">Run a task to see file changes</p>
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
                            <div className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
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
              <div className="text-center text-gray-600 mt-20">
                <p className="text-3xl mb-3 text-gray-700">{'>'}_ </p>
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

                {currentTask.chain && (
                  <div className="flex items-center gap-1 mb-3 overflow-x-auto">
                    {currentTask.chain.map((step, i) => {
                      const a = agentOptions.find(ao => ao.value === step.agent)
                      const chainMsgs = currentTask.messages.filter(m => m.chainStep)
                      const isDone = i < chainMsgs.length
                      const isActive = i === chainMsgs.length && currentTask.status === 'running'
                      return (
                        <div key={i} className="flex items-center gap-1">
                          {i > 0 && <span className="text-gray-700 text-xs">→</span>}
                          <span className={`px-2 py-1 rounded-md text-[10px] font-medium transition ${
                            isDone ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/25' :
                            isActive ? 'bg-blue-500/15 text-blue-400 border border-blue-500/25 animate-pulse' :
                            'bg-white/5 border border-white/[0.06] text-gray-500'
                          }`}>
                            {a?.icon || '?'} {a?.label || step.agent}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}

                {currentTask.messages.map((msg, i) => (
                  <div key={i} className={`${msg.role === 'user' ? 'ml-auto max-w-[80%]' : 'max-w-full'}`}>
                    <div className={`rounded-lg px-4 py-3 ${
                      msg.role === 'user' ? 'bg-blue-500/10 border border-blue-500/20' :
                      msg.role === 'system' ? 'bg-red-500/10 border border-red-500/15' :
                      'bg-white/[0.03] border border-white/[0.06]'
                    }`}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs text-gray-500">{msg.role}</span>
                        {msg.chainStep && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400">
                            {agentOptions.find(a => a.value === msg.chainStep?.agent)?.icon} {agentOptions.find(a => a.value === msg.chainStep?.agent)?.label || msg.chainStep?.agent}
                            {msg.chainStep?.prompt && `: ${msg.chainStep.prompt}`}
                          </span>
                        )}
                      </div>
                      <div className="whitespace-pre-wrap text-sm text-gray-300 leading-relaxed">{msg.text}</div>
                      {msg.steps && (
                        <div className="mt-2 space-y-1">
                          {msg.steps.map((step: any, j: number) => (
                            <div key={j} className="text-xs bg-white/[0.03] border border-white/[0.04] rounded-md px-2 py-1">
                              <button
                                className="w-full text-left flex items-center gap-2"
                                onClick={() => toggleStep(i * 1000 + j)}
                              >
                                <span className="text-gray-600">{expandedSteps[i * 1000 + j] ? '\u25BC' : '\u25B6'}</span>
                                <span className="text-blue-400">{step.tool}</span>
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
                {loading && selectedTask === currentTask.id && (
                  <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
                      <span className="text-gray-500 text-sm">Agent working...</span>
                    </div>
                  </div>
                )}
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
          <h1 className="text-xl font-bold">Agent</h1>
          <span className="text-xs text-gray-600">mod agentic framework</span>
        </div>
        <div className="flex items-center gap-2">
          {loading && (
            <span className="flex items-center gap-1.5 text-xs text-blue-400">
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
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
          placeholder={chainMode ? 'Main query for chain...' : `Task for ${currentAgentDef?.label || 'agent'}...`}
          rows={3}
          className="w-full bg-transparent border-none outline-none text-sm resize-none placeholder:text-gray-600"
          disabled={loading}
        />
        <div className="flex items-center justify-between mt-2 gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <select
              value={agentType}
              onChange={(e) => { setAgentType(e.target.value); localStorage.setItem('agent_type', e.target.value) }}
              className="bg-white/5 border border-white/10 rounded-md px-2 py-1 text-xs text-gray-300 outline-none cursor-pointer hover:border-white/20 transition-colors shrink-0"
              style={{ maxWidth: '160px' }}
            >
              {agentOptions.map((a) => (
                <option key={a.value} value={a.value} className="bg-[#111]">{a.icon} {a.label}</option>
              ))}
            </select>
            {modelControls}
          </div>
          <button onClick={run} disabled={loading || !query.trim()}
            className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-gray-800 disabled:text-gray-600 text-xs font-medium transition shrink-0 ml-3">
            Run
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 p-6">
        {tasks.length === 0 ? (
          <div className="text-center text-gray-600 mt-20">
            <p className="text-4xl mb-4 text-gray-700">{'>'}_ </p>
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
                selectedTask === t.id ? 'border-blue-500/20 bg-blue-500/[0.06]' : 'border-white/[0.06] hover:border-white/10 hover:bg-white/[0.02]'
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
                  {t.chain && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-purple-500/10 border border-purple-500/20 text-purple-400 shrink-0">
                      chain {t.chain.length}
                    </span>
                  )}
                  <span className="text-sm text-gray-300 truncate flex-1">{t.query}</span>
                  <span className="text-xs text-gray-600 shrink-0">
                    {t.status === 'running' ? 'Running...' :
                     t.status === 'done' ? `${t.stepCount || 0} steps` :
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

  // agent fullscreen mode — render only the sidebar content, no chrome
  if (agentFullscreen) {
    return (
      <main className="h-screen flex flex-col bg-[#0a0a0a] agent-fullscreen">
        {/* minimal top bar */}
        <header className="border-b border-white/[0.06] px-4 py-2 flex items-center justify-between shrink-0 bg-[#0a0a0a]">
          <div className="flex items-center gap-3">
            <h1 className="text-base font-semibold tracking-tight">Agent</h1>
            <span className="text-[10px] text-gray-600 uppercase tracking-wider">fullscreen</span>
            {loading && (
              <span className="flex items-center gap-1.5 text-xs text-blue-400 ml-2">
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
                working
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-gray-600">{Object.keys(skills).length} skills</span>
            <button
              onClick={toggleAgentFullscreen}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium transition bg-blue-500/20 border border-blue-500/30 text-blue-400 hover:bg-blue-500/30"
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
      </main>
    )
  }

  return (
    <main className="h-screen flex flex-col bg-[#0a0a0a]">
      {/* top bar */}
      <header className="border-b border-white/[0.06] px-4 py-2 flex items-center justify-between shrink-0 bg-[#0a0a0a]">
        <div className="flex items-center gap-3">
          <h1 className="text-base font-semibold tracking-tight">Agent</h1>
          <span className="text-[10px] text-gray-600 uppercase tracking-wider">mod</span>
        </div>
        <div className="flex items-center gap-3">
          {loading && (
            <span className="flex items-center gap-1.5 text-xs text-blue-400">
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
              working
            </span>
          )}
          <span className="text-[10px] text-gray-600">{Object.keys(skills).length} skills</span>
          {apiStatus === 'down' && (
            <span className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-0.5 rounded-md">
              API offline
            </span>
          )}

          {/* fullscreen agent toggle */}
          <button
            onClick={toggleAgentFullscreen}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium transition border border-white/[0.06] text-gray-500 hover:text-blue-400 hover:border-blue-500/25 hover:bg-blue-500/10"
            title="Fullscreen agent (double-click divider)"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 3 21 3 21 9" />
              <polyline points="9 21 3 21 3 15" />
              <line x1="21" y1="3" x2="14" y2="10" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          </button>

          <button
            onClick={cycleLayout}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[10px] font-medium transition ${
              layoutMode === 'minimized'
                ? 'bg-white/5 border border-white/[0.06] text-gray-500 hover:text-gray-300'
                : layoutMode === 'sidebar'
                ? 'bg-blue-500/15 border border-blue-500/25 text-blue-400'
                : 'bg-blue-500/25 border border-blue-500/35 text-blue-300'
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
        </div>
      </header>

      {/* layout body */}
      <div className="flex-1 flex min-h-0">
        {/* sidebar mode: split view */}
        {layoutMode === 'sidebar' && (
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
                      <div className={`h-full w-[1px] ml-[4px] bg-white/[0.06] group-hover:bg-blue-500/60 group-active:bg-blue-500 transition-colors`} />
                      {/* grab indicator — visible on hover */}
                      <div className="absolute top-1/2 -translate-y-1/2 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                        <div className="flex flex-col gap-[3px]">
                          <div className="w-[3px] h-[3px] rounded-full bg-blue-400/60" />
                          <div className="w-[3px] h-[3px] rounded-full bg-blue-400/60" />
                          <div className="w-[3px] h-[3px] rounded-full bg-blue-400/60" />
                        </div>
                      </div>
                    </div>
                  )}
                  <div className={`flex-1 ${sidebarExpanded ? '' : sidebarSide === 'left' ? 'border-r' : 'border-l'} border-white/[0.06] flex flex-col min-h-0`}>
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
                              selectedTask === t.id ? 'bg-blue-500/20 border border-blue-500/25' : 'hover:bg-white/[0.06]'
                            }`}
                          >
                            <span className={statusIcon(t.status)}>{statusDot(t.status)}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col h-full">
                      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/[0.06] shrink-0">
                        <span className="text-xs text-gray-600 uppercase tracking-wider font-medium">Conversations</span>
                        <div className="flex items-center gap-0.5">
                          <button
                            onClick={() => setSidebarExpanded(e => !e)}
                            className={`text-gray-600 hover:text-gray-400 transition p-1 rounded hover:bg-white/5 ${sidebarExpanded ? 'text-blue-400' : ''}`}
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
                                className={`text-gray-600 hover:text-gray-400 transition p-1 rounded hover:bg-white/5 ${drawerOpen ? 'text-blue-400' : ''}`}
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
                      {sidebarContent}
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
                <>{sidebarPanel}<div className="flex-1 min-h-0 relative">{!drawerOpen && detailPanel}{drawerPanel}</div></>
              ) : (
                <><div className="flex-1 min-h-0 relative">{!drawerOpen && detailPanel}{drawerPanel}</div>{sidebarPanel}</>
              )
            })()}
          </>
        )}

        {/* fullscreen mode */}
        {layoutMode === 'fullscreen' && (
          <div className="flex-1 flex flex-col min-h-0">
            {mainContent}
          </div>
        )}

        {/* minimized mode */}
        {layoutMode === 'minimized' && (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-gray-700">
              <p className="text-5xl mb-4">{'>'}_ </p>
              <p className="text-sm text-gray-600">Agent minimized</p>
            </div>
          </div>
        )}
      </div>
    </main>
  )
}
