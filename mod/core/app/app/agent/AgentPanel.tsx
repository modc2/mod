"use client"

import { useState, useEffect, useRef, useCallback } from 'react'
import { ethers } from 'ethers'
import { useLayoutContext } from '@/context/LayoutContext'
import { userContext } from '@/context'
import { X, Send, Loader2, Bot, CheckCircle, Clock, XCircle, Play, StopCircle, ChevronDown, ChevronUp, ImageIcon, Terminal, FolderOpen, Lock, Power } from 'lucide-react'
import { MODELS, AGENT_TYPES } from '@/mod/edit/shared'
import { SidebarTerminal } from './SidebarTerminal'
import { getChainConfig } from '@/network/chainConfig'

const API_URL = process.env.NEXT_PUBLIC_CLAUDE_JOBS_URL || 'http://localhost:50117'
const OWNER_ABI = ['function owner() view returns (address)']

export const AGENT_PANEL_WIDTH = 480

interface Job {
  id: string
  prompt: string
  model: string
  work_dir: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  output: string
  error: string | null
  pid: number | null
  created_at: number
  updated_at: number
}

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',
  running: '#3b82f6',
  completed: '#10b981',
  failed: '#ef4444',
  cancelled: '#6b7280',
}

function timeSince(ts: number): string {
  const s = Math.floor(Date.now() / 1000 - ts)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

function StatusIcon({ status, size = 11 }: { status: string; size?: number }) {
  const color = STATUS_COLORS[status] || '#6b7280'
  switch (status) {
    case 'running': return <Play size={size} style={{ color }} />
    case 'completed': return <CheckCircle size={size} style={{ color }} />
    case 'failed': return <XCircle size={size} style={{ color }} />
    case 'cancelled': return <StopCircle size={size} style={{ color }} />
    default: return <Clock size={size} style={{ color }} />
  }
}

function Dropdown({ value, options, onChange, renderLabel }: {
  value: string
  options: { value: string; label: string; color?: string; icon?: string }[]
  onChange: (v: string) => void
  renderLabel: (opt: { value: string; label: string; color?: string; icon?: string }) => React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const active = options.find(o => o.value === value) || options[0]

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2.5 py-1 transition-all"
        style={{
          fontSize: '11px',
          fontWeight: 600,
          background: `${active.color || 'var(--text-tertiary)'}18`,
          border: `1px solid ${active.color || 'var(--text-tertiary)'}40`,
          color: active.color || 'var(--text-primary)',
          borderRadius: '5px',
          cursor: 'pointer',
        }}
      >
        {renderLabel(active)}
        <ChevronDown size={10} style={{ opacity: 0.6 }} />
      </button>

      {open && (
        <div
          className="absolute top-full left-0 mt-1 z-50 overflow-hidden"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-color)',
            borderRadius: '8px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            minWidth: '140px',
          }}
        >
          {options.map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className="w-full text-left px-3 py-2 transition-all flex items-center gap-2"
              style={{
                fontSize: '11px',
                fontWeight: value === opt.value ? 600 : 400,
                color: value === opt.value ? (opt.color || 'var(--text-primary)') : 'var(--text-secondary)',
                background: value === opt.value ? `${opt.color || 'var(--text-tertiary)'}12` : 'transparent',
                cursor: 'pointer',
                border: 'none',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
              onMouseLeave={e => (e.currentTarget.style.background = value === opt.value ? `${opt.color || 'var(--text-tertiary)'}12` : 'transparent')}
            >
              {renderLabel(opt)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function AgentPanel() {
  const { isAgentSidebarOpen, setAgentSidebarOpen, isTerminalMode, toggleTerminalMode, setTerminalMode } = useLayoutContext()
  const { client, user } = userContext()

  // Owner gate
  const [isOwner, setIsOwner] = useState<boolean | null>(null)

  useEffect(() => {
    if (!isAgentSidebarOpen) return
    async function checkOwner() {
      if (!user?.key) { setIsOwner(null); return }
      try {
        const chainCfg = getChainConfig()
        const treasuryAddr = chainCfg?.contracts?.Treasury?.address
        if (!treasuryAddr) { setIsOwner(true); return }
        // First try: check deployer from config (no RPC needed)
        const deployer = chainCfg?.deployer
        if (deployer && deployer.toLowerCase() === user.key.toLowerCase()) {
          setIsOwner(true)
          return
        }
        // Second try: call owner() on-chain
        const rpcUrl = chainCfg?.url || 'https://sepolia.base.org'
        const chainId = parseInt(chainCfg?.chainId || '84532')
        const provider = new ethers.JsonRpcProvider(rpcUrl, chainId, { staticNetwork: true })
        const contract = new ethers.Contract(treasuryAddr, OWNER_ABI, provider)
        const owner = await contract.owner()
        setIsOwner(owner.toLowerCase() === user.key.toLowerCase())
      } catch (err) {
        console.error('Owner check failed, allowing access:', err)
        setIsOwner(true) // fail-open: don't lock out on RPC errors
      }
    }
    checkOwner()
  }, [user?.key, isAgentSidebarOpen])

  const [jobs, setJobs] = useState<Job[]>([])
  const [selectedJob, setSelectedJob] = useState<string | null>(null)
  const [streamOutput, setStreamOutput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)

  const [prompt, setPrompt] = useState('')
  const [images, setImages] = useState<Array<{ name: string; data: string }>>([])
  const [model, setModel] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('agent_model') || 'sonnet'
    return 'sonnet'
  })
  const [agentType, setAgentType] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('agent_type') || 'default'
    return 'default'
  })
  const [submitting, setSubmitting] = useState(false)
  const [showJobs, setShowJobs] = useState(true)
  const [workDir, setWorkDir] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('agent_work_dir') || '/Users/broski/mod'
    return '/Users/broski/mod'
  })
  const [editingDir, setEditingDir] = useState(false)
  const [dirInput, setDirInput] = useState(workDir)
  const [modules, setModules] = useState<{ name: string; path: string; display: string; category: string; description?: string; fns?: string[]; has_config?: boolean }[]>([])
  const [dirSelectedIdx, setDirSelectedIdx] = useState(-1)
  const [starting, setStarting] = useState(false)
  const [startMsg, setStartMsg] = useState<string | null>(null)
  const startingRef = useRef(false)

  const inputRef = useRef<HTMLTextAreaElement>(null)
  const outputRef = useRef<HTMLDivElement>(null)
  const dirInputRef = useRef<HTMLInputElement>(null)
  const dirDropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => { localStorage.setItem('agent_model', model) }, [model])
  useEffect(() => { localStorage.setItem('agent_type', agentType) }, [agentType])
  useEffect(() => { localStorage.setItem('agent_work_dir', workDir) }, [workDir])

  // Sync agent type <-> terminal mode: "default" (>_) = terminal, anything else = AI
  useEffect(() => {
    if (agentType === 'default' && !isTerminalMode) {
      setTerminalMode(true)
    } else if (agentType !== 'default' && isTerminalMode) {
      setTerminalMode(false)
    }
  }, [agentType])

  // Close directory dropdown on click outside
  useEffect(() => {
    if (!editingDir) return
    const handler = (e: MouseEvent) => {
      if (dirDropdownRef.current && !dirDropdownRef.current.contains(e.target as Node)) {
        setEditingDir(false)
        setDirSelectedIdx(-1)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [editingDir])

  // Focus input when panel opens
  useEffect(() => {
    if (isAgentSidebarOpen) {
      setTimeout(() => inputRef.current?.focus(), 200)
    }
  }, [isAgentSidebarOpen])

  // API layer
  const agentFetch = useCallback(async (endpoint: string, options: RequestInit = {}) => {
    const token = client?.token || ''
    return fetch(`${API_URL}${endpoint}`, {
      ...options,
      headers: { ...options.headers, 'token': token, 'Content-Type': 'application/json' },
    })
  }, [client?.token])

  const fetchJobs = useCallback(async () => {
    try {
      const res = await agentFetch('/jobs')
      if (!res.ok) throw new Error('Failed')
      const data = await res.json()
      setJobs(data.jobs || [])
      setError(null)
      // Server came online - clear start state
      if (startingRef.current) {
        startingRef.current = false
        setStarting(false)
        setStartMsg(null)
      }
    } catch {
      if (!startingRef.current) {
        setError('Agent API offline')
      }
    }
  }, [agentFetch])

  useEffect(() => {
    if (!isAgentSidebarOpen) return
    fetchJobs()
    const iv = setInterval(fetchJobs, 4000)
    return () => clearInterval(iv)
  }, [fetchJobs, isAgentSidebarOpen])

  // Fetch orbit modules for directory picker
  useEffect(() => {
    if (!isAgentSidebarOpen) return
    agentFetch('/modules')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.modules) {
          const seen = new Set<string>()
          const rich = data.modules
            .map((m: any) => typeof m === 'string' ? { name: m, path: '', display: m, category: 'orbit' } : { name: m.name, path: m.path || '', display: m.display || m.path || m.name, category: m.category || 'orbit', description: m.description, fns: m.fns, has_config: m.has_config })
            .filter((m: any) => { if (seen.has(m.name + m.category)) return false; seen.add(m.name + m.category); return true })
          setModules(rich)
        }
      })
      .catch(() => {})
  }, [agentFetch, isAgentSidebarOpen])

  // Streaming
  const startStream = useCallback((jobId: string) => {
    if (esRef.current) esRef.current.close()
    const existing = jobs.find(j => j.id === jobId)
    setStreamOutput(existing?.output || '')

    const es = new EventSource(`${API_URL}/jobs/${jobId}/stream`)
    esRef.current = es

    es.onmessage = (event) => {
      if (event.data === '[DONE]' || event.data === '[CANCELLED]') {
        es.close()
        fetchJobs()
        return
      }
      setStreamOutput(prev => prev + event.data)
    }

    es.addEventListener('complete', (event: any) => {
      setStreamOutput(event.data)
      es.close()
    })

    es.onerror = () => { es.close(); fetchJobs() }
  }, [jobs, fetchJobs])

  useEffect(() => {
    return () => { if (esRef.current) esRef.current.close() }
  }, [])

  // Auto-scroll output
  useEffect(() => {
    if (outputRef.current) {
      const el = outputRef.current
      const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200
      if (isNearBottom) el.scrollTop = el.scrollHeight
    }
  }, [streamOutput])

  // Actions
  const submitJob = async () => {
    if (!prompt.trim() && images.length === 0) return
    setSubmitting(true)
    try {
      const body: any = { prompt: prompt.trim() || 'Analyze the attached image(s)', model, work_dir: workDir }

      const activeAgent = AGENT_TYPES.find(a => a.value === agentType)
      if (activeAgent?.prompt) {
        body.system_prompt = activeAgent.prompt
      }
      if (agentType !== 'default') body.agent_type = agentType
      if (images.length > 0) body.images = images

      const res = await agentFetch('/jobs', { method: 'POST', body: JSON.stringify(body) })
      if (!res.ok) throw new Error('Submit failed')
      const job = await res.json()
      setPrompt('')
      setImages([])
      setSelectedJob(job.id)
      fetchJobs()
      startStream(job.id)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  const cancelJob = async (id: string) => {
    if (esRef.current) { esRef.current.close(); esRef.current = null }
    await agentFetch(`/jobs/${id}/cancel`, { method: 'POST' })
    fetchJobs()
  }

  const deleteJob = async (id: string) => {
    await agentFetch(`/jobs/${id}`, { method: 'DELETE' })
    if (selectedJob === id) { setSelectedJob(null); setStreamOutput('') }
    fetchJobs()
  }

  const startServer = async () => {
    if (!client || starting) return
    startingRef.current = true
    setStarting(true)
    setStartMsg('Calling serve_app...')
    try {
      const result = await client.call('serve_app', { name: 'agent' })
      if (result?.error) {
        setStartMsg(result.error)
        setStarting(false)
        startingRef.current = false
        return
      }
      setStartMsg('Server starting, waiting for health...')
      // The fetchJobs interval will detect when the server comes online
      // and clear the start state. Set a timeout as fallback.
      setTimeout(() => {
        if (startingRef.current) {
          startingRef.current = false
          setStarting(false)
          setStartMsg('Timed out — check agent logs')
        }
      }, 20000)
    } catch (e: any) {
      setStartMsg(e?.message || 'Failed to call API')
      setStarting(false)
      startingRef.current = false
    }
  }

  const viewJob = (job: Job) => {
    setSelectedJob(job.id)
    if (job.status === 'running') {
      startStream(job.id)
    } else {
      setStreamOutput(job.output)
      if (esRef.current) { esRef.current.close(); esRef.current = null }
    }
  }

  // Derived
  const selectedJobData = jobs.find(j => j.id === selectedJob) || null
  const activeModel = MODELS.find(m => m.value === model) || MODELS[0]
  const activeAgent = AGENT_TYPES.find(a => a.value === agentType) || AGENT_TYPES[0]
  const runningCount = jobs.filter(j => j.status === 'running').length
  const isRunning = selectedJobData?.status === 'running'
  const output = streamOutput || selectedJobData?.output || ''

  if (!isAgentSidebarOpen) return null

  // Owner gate — show locked panel if not owner
  if (isOwner === false || isOwner === null) {
    return (
      <div
        className="fixed z-[65] flex flex-col"
        style={{
          top: '48px',
          bottom: 0,
          left: 'var(--sidebar-width, 64px)',
          width: `${AGENT_PANEL_WIDTH}px`,
          background: 'var(--bg-primary)',
          borderRight: '1px solid var(--border-color)',
          fontFamily: 'var(--font-digital), monospace',
          animation: 'slideInLeft 0.2s ease-out',
        }}
      >
        <div
          className="flex items-center px-3 py-2 shrink-0 gap-2"
          style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}
        >
          <Lock size={14} style={{ color: '#ef4444', flexShrink: 0 }} />
          <span className="text-xs font-bold uppercase tracking-wider" style={{ color: '#ef4444' }}>Locked</span>
          <div className="flex-1" />
          <button
            onClick={() => setAgentSidebarOpen(false)}
            className="flex items-center justify-center transition-all rounded shrink-0"
            style={{ width: '24px', height: '24px', color: 'var(--text-tertiary)' }}
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex-1 flex items-center justify-center px-6">
          <div className="flex flex-col items-center gap-4 text-center">
            {isOwner === null ? (
              <>
                <Loader2 size={24} className="animate-spin" style={{ color: 'var(--text-tertiary)' }} />
                <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                  Verifying ownership...
                </span>
              </>
            ) : (
              <>
                <div className="w-14 h-14 rounded-full flex items-center justify-center" style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.25)' }}>
                  <Lock size={22} style={{ color: '#ef4444' }} />
                </div>
                <span className="text-[12px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>
                  Owner Access Only
                </span>
                <span className="text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
                  The Agent is restricted to the Treasury contract owner.
                </span>
                {user?.key && (
                  <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)', opacity: 0.6 }}>
                    {user.key.slice(0, 6)}...{user.key.slice(-4)}
                  </span>
                )}
              </>
            )}
          </div>
        </div>
        <style jsx global>{`
          @keyframes slideInLeft {
            from { transform: translateX(-100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
          }
        `}</style>
      </div>
    )
  }

  return (
    <div
      className="fixed z-[65] flex flex-col"
      style={{
        top: '48px',
        bottom: 0,
        left: 'var(--sidebar-width, 64px)',
        width: `${AGENT_PANEL_WIDTH}px`,
        background: 'var(--bg-primary)',
        borderRight: '1px solid var(--border-color)',
        fontFamily: 'var(--font-digital), monospace',
        animation: 'slideInLeft 0.2s ease-out',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center px-3 py-2 shrink-0 gap-2"
        style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}
      >
        {isTerminalMode ? (
          <>
            <Terminal size={14} style={{ color: '#10b981', flexShrink: 0 }} />
            <span
              className="text-xs font-bold uppercase tracking-wider shrink-0"
              style={{ color: '#10b981', textShadow: '0 0 8px rgba(16, 185, 129, 0.4)' }}
            >
              Terminal
            </span>
          </>
        ) : (
          <>
            <Bot size={14} style={{ color: '#a78bfa', flexShrink: 0 }} />
            <span
              className="text-xs font-bold uppercase tracking-wider shrink-0"
              style={{ color: '#a78bfa', textShadow: '0 0 8px rgba(167, 139, 250, 0.4)' }}
            >
              Agent
            </span>
          </>
        )}
        {!isTerminalMode && runningCount > 0 && (
          <span className="flex items-center gap-1 text-[10px] font-bold shrink-0" style={{ color: '#3b82f6' }}>
            <span className="inline-block w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: '#3b82f6' }} />
            {runningCount} running
          </span>
        )}
        {!isTerminalMode && (error || startMsg) && (
          <div className="flex items-center gap-1.5 min-w-0">
            {starting ? (
              <span className="flex items-center gap-1 text-[10px] truncate" style={{ color: '#f59e0b' }}>
                <Loader2 size={10} className="animate-spin shrink-0" />
                {startMsg}
              </span>
            ) : startMsg ? (
              <span className="text-[10px] truncate" style={{ color: startMsg.includes('error') || startMsg.includes('Not owner') || startMsg.includes('not found') || startMsg.includes('Timed out') ? '#ef4444' : '#f59e0b' }}>
                {startMsg}
              </span>
            ) : (
              <span className="text-[10px] truncate" style={{ color: '#ef4444' }}>{error}</span>
            )}
            {error === 'Agent API offline' && !starting && !startMsg && (
              <button
                onClick={startServer}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded transition-all shrink-0"
                style={{
                  fontSize: '9px',
                  fontWeight: 700,
                  color: '#10b981',
                  border: '1px solid rgba(16,185,129,0.4)',
                  background: 'rgba(16,185,129,0.08)',
                  cursor: 'pointer',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                <Power size={9} />
                START
              </button>
            )}
          </div>
        )}
        <div className="flex-1" />
        {!isTerminalMode && (
          <Dropdown
            value={model}
            options={MODELS}
            onChange={setModel}
            renderLabel={(opt) => <span>{opt.label}</span>}
          />
        )}
        <Dropdown
          value={agentType}
          options={AGENT_TYPES.map(a => ({
            ...a,
            color: a.value === 'default' ? '#10b981' : activeModel.color,
          }))}
          onChange={setAgentType}
          renderLabel={(opt) => (
            <span className="flex items-center gap-1.5">
              <span style={{ fontSize: '12px', opacity: 0.7 }}>{opt.icon}</span>
              <span>{opt.label?.toUpperCase()}</span>
            </span>
          )}
        />
        <button
          onClick={() => setAgentSidebarOpen(false)}
          className="flex items-center justify-center transition-all rounded shrink-0"
          style={{ width: '24px', height: '24px', color: 'var(--text-tertiary)' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-tertiary)')}
        >
          <X size={14} />
        </button>
      </div>

      {/* Terminal mode */}
      {isTerminalMode && (
        <SidebarTerminal width={AGENT_PANEL_WIDTH} />
      )}

      {/* AI Agent mode */}
      {!isTerminalMode && <>

      {/* Working directory with module search */}
      <div ref={dirDropdownRef} className="relative shrink-0">
        <div
          className="flex items-center gap-1.5 px-3 py-1.5 cursor-pointer group"
          style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-primary)' }}
          onClick={() => {
            if (!editingDir) {
              setDirInput('')
              setDirSelectedIdx(-1)
              setEditingDir(true)
              setTimeout(() => dirInputRef.current?.focus(), 50)
            }
          }}
        >
          <FolderOpen size={11} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
          {editingDir ? (
            <input
              ref={dirInputRef}
              value={dirInput}
              onChange={e => { setDirInput(e.target.value); setDirSelectedIdx(-1) }}
              onKeyDown={e => {
                const searchTerm = dirInput.trim().toLowerCase()
                const filtered = searchTerm
                  ? modules.filter(m => m.name.toLowerCase().includes(searchTerm))
                  : modules
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setDirSelectedIdx(p => p < filtered.length - 1 ? p + 1 : 0)
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setDirSelectedIdx(p => p > 0 ? p - 1 : filtered.length - 1)
                } else if (e.key === 'Enter') {
                  e.preventDefault()
                  if (dirSelectedIdx >= 0 && filtered[dirSelectedIdx]) {
                    setWorkDir(filtered[dirSelectedIdx].path || filtered[dirSelectedIdx].display)
                    setEditingDir(false)
                    setDirSelectedIdx(-1)
                  } else if (dirInput.trim()) {
                    setWorkDir(dirInput.trim())
                    setEditingDir(false)
                    setDirSelectedIdx(-1)
                  }
                } else if (e.key === 'Escape') {
                  setEditingDir(false)
                  setDirSelectedIdx(-1)
                }
              }}
              placeholder="search modules..."
              className="flex-1 bg-transparent outline-none text-[10px] font-mono"
              style={{ color: 'var(--text-primary)', border: 'none', padding: 0 }}
              spellCheck={false}
            />
          ) : (
            <span
              className="flex-1 text-[10px] font-mono truncate transition-colors"
              style={{ color: 'var(--text-tertiary)' }}
              title={workDir}
            >
              {workDir.replace(/^\/Users\/\w+\//, '~/')}
            </span>
          )}
        </div>

        {/* Module search dropdown */}
        {editingDir && modules.length > 0 && (() => {
          const searchTerm = dirInput.trim().toLowerCase()
          const filtered = searchTerm
            ? modules.filter(m => m.name.toLowerCase().includes(searchTerm))
            : modules
          if (filtered.length === 0 && searchTerm) return (
            <div
              className="absolute left-0 right-0 z-50 overflow-hidden"
              style={{
                top: '100%',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-color)',
                borderTop: 'none',
                boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
              }}
            >
              <div className="px-3 py-3 text-center text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>
                No modules match "{dirInput.trim()}"
              </div>
            </div>
          )
          return (
            <div
              className="absolute left-0 right-0 z-50 overflow-y-auto"
              style={{
                top: '100%',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-color)',
                borderTop: 'none',
                boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
                maxHeight: '280px',
              }}
            >
              {filtered.map((mod, idx) => {
                const isSelected = idx === dirSelectedIdx
                const matchIdx = searchTerm ? mod.name.toLowerCase().indexOf(searchTerm) : -1
                const displayPath = mod.display.replace(/^\/Users\/\w+\//, '~/')
                const fnCount = mod.fns?.length || 0
                return (
                  <button
                    key={`${mod.category}-${mod.name}`}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      setWorkDir(mod.path || mod.display)
                      setEditingDir(false)
                      setDirSelectedIdx(-1)
                    }}
                    onMouseEnter={() => setDirSelectedIdx(idx)}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-left transition-all"
                    style={{
                      background: isSelected ? 'var(--hover-bg)' : 'transparent',
                      borderBottom: '1px solid var(--border-color)',
                    }}
                  >
                    <FolderOpen size={11} style={{ color: isSelected ? 'var(--accent-primary)' : 'var(--text-tertiary)', flexShrink: 0 }} />
                    <div className="flex flex-col min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-mono font-bold truncate" style={{ color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                          {searchTerm && matchIdx >= 0 ? (
                            <>
                              {mod.name.slice(0, matchIdx)}
                              <span style={{ color: 'var(--accent-primary)' }}>{mod.name.slice(matchIdx, matchIdx + searchTerm.length)}</span>
                              {mod.name.slice(matchIdx + searchTerm.length)}
                            </>
                          ) : mod.name}
                        </span>
                        <span className="text-[8px] font-mono px-1 py-px rounded" style={{
                          color: mod.category === 'core' ? '#f59e0b' : '#10b981',
                          background: mod.category === 'core' ? 'rgba(245,158,11,0.1)' : 'rgba(16,185,129,0.1)',
                          border: `1px solid ${mod.category === 'core' ? 'rgba(245,158,11,0.2)' : 'rgba(16,185,129,0.2)'}`,
                          flexShrink: 0,
                        }}>
                          {mod.category}
                        </span>
                        {fnCount > 0 && (
                          <span className="text-[8px] font-mono" style={{ color: 'var(--text-tertiary)', opacity: 0.5, flexShrink: 0 }}>
                            {fnCount} fn
                          </span>
                        )}
                      </div>
                      <span className="text-[9px] font-mono truncate" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>
                        {displayPath}
                      </span>
                    </div>
                  </button>
                )
              })}
            </div>
          )
        })()}
      </div>

      {/* Output area */}
      <div ref={outputRef} className="flex-1 overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
        {selectedJobData ? (
          <div className="flex flex-col h-full">
            {/* Job header */}
            <div className="px-3 py-2.5 shrink-0" style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <StatusIcon status={selectedJobData.status} />
                  <span className="text-[10px] font-bold uppercase tracking-wide" style={{ color: STATUS_COLORS[selectedJobData.status] }}>
                    {selectedJobData.status}
                  </span>
                  <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                    {selectedJobData.model.toUpperCase()}
                  </span>
                  <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)', opacity: 0.6 }}>
                    {selectedJobData.id.slice(0, 8)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  {selectedJobData.work_dir && (
                    <span className="flex items-center gap-1 text-[10px] font-mono truncate" style={{ color: 'var(--text-tertiary)', opacity: 0.7, maxWidth: '200px' }} title={selectedJobData.work_dir}>
                      <FolderOpen size={10} style={{ flexShrink: 0 }} />
                      {selectedJobData.work_dir.replace(/^\/Users\/\w+\//, '~/')}
                    </span>
                  )}
                  {isRunning && (
                    <button
                      onClick={() => cancelJob(selectedJobData.id)}
                      className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded transition-all"
                      style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}
                    >
                      STOP
                    </button>
                  )}
                  <button
                    onClick={() => { setSelectedJob(null); setStreamOutput('') }}
                    className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded transition-all"
                    style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border-color)' }}
                  >
                    BACK
                  </button>
                </div>
              </div>
              <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-secondary)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                {selectedJobData.prompt}
              </p>
            </div>

            {/* Output */}
            <div className="flex-1 overflow-y-auto p-3">
              {output ? (
                <pre className="m-0 whitespace-pre-wrap text-[11px] leading-relaxed" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-digital), monospace', wordBreak: 'break-word' }}>
                  {output}
                  {isRunning && <span className="inline-block animate-pulse" style={{ color: STATUS_COLORS.running }}>&#9610;</span>}
                </pre>
              ) : (
                <div className="flex items-center justify-center h-32">
                  {isRunning ? (
                    <div className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: '#3b82f6' }} />
                      <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: '#3b82f6', animationDelay: '0.2s' }} />
                      <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: '#3b82f6', animationDelay: '0.4s' }} />
                    </div>
                  ) : (
                    <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>No output</p>
                  )}
                </div>
              )}

              {selectedJobData.error && (
                <div className="mt-3 p-2.5 rounded-lg" style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)' }}>
                  <span className="text-[9px] font-bold uppercase" style={{ color: '#ef4444' }}>Error</span>
                  <pre className="m-0 mt-1 whitespace-pre-wrap text-[10px]" style={{ color: '#ef4444', fontFamily: 'monospace' }}>
                    {selectedJobData.error}
                  </pre>
                </div>
              )}
            </div>
          </div>
        ) : (
          /* Job list */
          <div>
            {/* Jobs header */}
            <button
              onClick={() => setShowJobs(!showJobs)}
              className="w-full flex items-center justify-between px-3 py-2 sticky top-0 z-10"
              style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-surface)' }}
            >
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>
                  Tasks
                </span>
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ backgroundColor: 'rgba(167,139,250,0.12)', color: '#a78bfa' }}>
                  {jobs.length}
                </span>
              </div>
              {showJobs ? <ChevronUp size={12} style={{ color: 'var(--text-tertiary)' }} /> : <ChevronDown size={12} style={{ color: 'var(--text-tertiary)' }} />}
            </button>

            {showJobs && (
              jobs.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 gap-3" style={{ opacity: 0.4 }}>
                  <Bot size={28} style={{ color: '#a78bfa' }} />
                  <p className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                    No tasks yet
                  </p>
                  <p className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                    Submit a prompt below
                  </p>
                </div>
              ) : (
                jobs.map(job => {
                  const color = STATUS_COLORS[job.status]
                  return (
                    <button
                      key={job.id}
                      onClick={() => viewJob(job)}
                      className="w-full text-left transition-all group"
                      style={{ borderBottom: '1px solid var(--border-color)' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                    >
                      <div className="px-3 py-2.5">
                        <div className="flex items-center justify-between mb-1">
                          <div className="flex items-center gap-1.5">
                            <StatusIcon status={job.status} />
                            <span className="text-[10px] font-bold uppercase tracking-wide" style={{ color }}>{job.status}</span>
                            <span className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>{job.model}</span>
                          </div>
                          <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>{timeSince(job.created_at)}</span>
                        </div>
                        <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-secondary)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                          {job.prompt}
                        </p>
                        {job.work_dir && (
                          <p className="flex items-center gap-1 text-[10px] font-mono mt-1" style={{ color: 'var(--text-tertiary)', opacity: 0.6 }}>
                            <FolderOpen size={9} />
                            {job.work_dir.replace(/^\/Users\/\w+\//, '~/')}
                          </p>
                        )}
                        {/* Actions */}
                        <div className="flex justify-end gap-1.5 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          {job.status === 'running' && (
                            <span
                              onClick={(e) => { e.stopPropagation(); cancelJob(job.id) }}
                              className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded cursor-pointer"
                              style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.25)' }}
                            >
                              CANCEL
                            </span>
                          )}
                          {['completed', 'failed', 'cancelled'].includes(job.status) && (
                            <span
                              onClick={(e) => { e.stopPropagation(); deleteJob(job.id) }}
                              className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded cursor-pointer"
                              style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border-color)' }}
                            >
                              DELETE
                            </span>
                          )}
                        </div>
                      </div>
                    </button>
                  )
                })
              )
            )}
          </div>
        )}
      </div>

      {/* Input bar */}
      <div className="shrink-0 px-3 py-2.5 border-t" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-surface)' }}>
        <div className="flex gap-1.5 items-end">
          <textarea
            ref={inputRef}
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitJob() } }}
            onPaste={(e) => {
              const items = e.clipboardData?.items
              if (!items) return
              for (let i = 0; i < items.length; i++) {
                if (items[i].type.startsWith('image/')) {
                  e.preventDefault()
                  const file = items[i].getAsFile()
                  if (!file) continue
                  const reader = new FileReader()
                  reader.onload = () => {
                    const base64 = reader.result as string
                    setImages(prev => [...prev, { name: file.name || `image-${Date.now()}.png`, data: base64 }])
                  }
                  reader.readAsDataURL(file)
                }
              }
            }}
            placeholder={`${activeAgent.icon}  ask ${activeAgent.label.toLowerCase()} agent... (paste images)`}
            disabled={submitting}
            rows={1}
            className="flex-1 px-2.5 py-2 rounded-lg border outline-none text-[12px] resize-none"
            style={{
              backgroundColor: 'var(--bg-input)',
              borderColor: submitting ? `${activeModel.color}40` : 'var(--border-color)',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-digital), monospace',
              minHeight: '36px',
              maxHeight: '120px',
            }}
            onFocus={e => (e.currentTarget.style.borderColor = activeModel.color)}
            onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-color)')}
            onInput={e => {
              const el = e.currentTarget
              el.style.height = 'auto'
              el.style.height = Math.min(el.scrollHeight, 120) + 'px'
            }}
          />
          <button
            onClick={submitJob}
            disabled={(!prompt.trim() && images.length === 0) || submitting}
            className="px-3 py-2 rounded-lg font-bold transition-all disabled:opacity-30"
            style={{
              backgroundColor: `${activeModel.color}18`,
              border: `1px solid ${activeModel.color}40`,
              color: activeModel.color,
              height: '36px',
            }}
          >
            {submitting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          </button>
        </div>
        {images.length > 0 && (
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
            {images.map((img, i) => (
              <div
                key={i}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded"
                style={{ background: `${activeModel.color}12`, border: `1px solid ${activeModel.color}30` }}
              >
                <ImageIcon size={10} style={{ color: activeModel.color, opacity: 0.7 }} />
                <span className="text-[9px] font-bold uppercase" style={{ color: activeModel.color, opacity: 0.8 }}>
                  {img.name.length > 16 ? img.name.slice(0, 14) + '…' : img.name}
                </span>
                <button
                  onClick={() => setImages(prev => prev.filter((_, j) => j !== i))}
                  className="ml-0.5 transition-colors"
                  style={{ color: 'var(--text-tertiary)', fontSize: '10px', lineHeight: 1 }}
                  onMouseEnter={e => (e.currentTarget.style.color = '#ef4444')}
                  onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-tertiary)')}
                >
                  ✕
                </button>
              </div>
            ))}
            {images.length > 1 && (
              <button
                onClick={() => setImages([])}
                className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded transition-colors"
                style={{ color: '#ef4444', opacity: 0.6, border: '1px solid rgba(239,68,68,0.2)' }}
                onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                onMouseLeave={e => (e.currentTarget.style.opacity = '0.6')}
              >
                Clear all
              </button>
            )}
          </div>
        )}
        <div className="flex items-center justify-between mt-1.5 px-0.5">
          <span className="text-[9px]" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>
            Enter to submit · Shift+Enter newline
          </span>
          <span className="text-[9px]" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>
            {activeAgent.value !== 'default' && <>{activeAgent.icon} {activeAgent.label} &middot; </>}
            {activeModel.label} &middot; {API_URL.replace('http://', '')}
          </span>
        </div>
      </div>

      </>}

      <style jsx global>{`
        @keyframes slideInLeft {
          from { transform: translateX(-100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
