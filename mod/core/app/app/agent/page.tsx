"use client"

import { useState, useEffect, useRef, useCallback } from 'react'
import { ethers } from 'ethers'
import { userContext } from '@/context'
import AgentSidebar from './AgentSidebar'
import AgentChat from './AgentChat'
import ServerPanel from '@/components/ServerPanel'
import { MODELS, AGENT_TYPES } from '@/mod/edit/shared'
import { getChainConfig } from '@/network/chainConfig'

export const dynamic = 'force-dynamic'

const API_URL = process.env.NEXT_PUBLIC_CLAUDE_JOBS_URL || 'http://localhost:50117'

// Minimal ABI for owner() check
const OWNER_ABI = ['function owner() view returns (address)']

// ── Types ──────────────────────────────────────────────────

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

interface Personality {
  id: string
  name: string
  icon: string
  prompt: string
  custom?: boolean
}

interface OrbitModule {
  name: string
  has_mod: boolean
}

const DEFAULT_PERSONALITIES: Personality[] = AGENT_TYPES.map(a => ({ id: a.value, name: a.label, icon: a.icon, prompt: a.prompt }))

function loadCustomAgents(): Personality[] {
  if (typeof window === 'undefined') return []
  try {
    return JSON.parse(localStorage.getItem('custom_agents') || '[]')
  } catch { return [] }
}

function saveCustomAgents(agents: Personality[]) {
  localStorage.setItem('custom_agents', JSON.stringify(agents))
}

// ── Main Component ─────────────────────────────────────────

export default function AgentPage() {
  const { client, user } = userContext()

  // Owner gate
  const [isOwner, setIsOwner] = useState<boolean | null>(null)
  const [ownerCheckError, setOwnerCheckError] = useState<string | null>(null)

  useEffect(() => {
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
      } catch (err: any) {
        console.error('Owner check failed, allowing access:', err)
        setIsOwner(true) // fail-open: don't lock out on RPC errors
      }
    }
    checkOwner()
  }, [user?.key])

  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedJob, setSelectedJob] = useState<string | null>(null)
  const [streamOutput, setStreamOutput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)

  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('agent_model') || 'sonnet'
    return 'sonnet'
  })
  const [personality, setPersonality] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('agent_personality') || 'default'
    return 'default'
  })
  const [workDir, setWorkDir] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [modules, setModules] = useState<string[]>([])
  const [showModules, setShowModules] = useState(false)
  const [customAgents, setCustomAgents] = useState<Personality[]>(loadCustomAgents)
  const [showAddAgent, setShowAddAgent] = useState(false)
  const [newAgentName, setNewAgentName] = useState('')
  const [newAgentIcon, setNewAgentIcon] = useState('')
  const [newAgentPrompt, setNewAgentPrompt] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const moduleDropdownRef = useRef<HTMLDivElement>(null)

  // Module picker / code viewer state
  const [selectedModule, setSelectedModule] = useState('')
  const [moduleCode, setModuleCode] = useState('')
  const [moduleCodePath, setModuleCodePath] = useState('')
  const [moduleCodeLoading, setModuleCodeLoading] = useState(false)
  const [orbitModules, setOrbitModules] = useState<OrbitModule[]>([])
  const [moduleSearch, setModuleSearch] = useState('')

  const allPersonalities = [...DEFAULT_PERSONALITIES, ...customAgents]

  useEffect(() => { localStorage.setItem('agent_model', model) }, [model])
  useEffect(() => { localStorage.setItem('agent_personality', personality) }, [personality])

  // ── Fetch orbit modules from filesystem ────────────────

  useEffect(() => {
    fetch('/api/mod-code')
      .then(r => r.json())
      .then(data => {
        if (data.modules) setOrbitModules(data.modules)
      })
      .catch(() => {})
  }, [])

  // ── Fetch module code when selected ────────────────────

  useEffect(() => {
    if (!selectedModule) {
      setModuleCode('')
      setModuleCodePath('')
      return
    }
    setModuleCodeLoading(true)
    fetch(`/api/mod-code?name=${encodeURIComponent(selectedModule)}`)
      .then(r => r.json())
      .then(data => {
        setModuleCode(data.code || '')
        setModuleCodePath(data.path || '')
      })
      .catch(() => setModuleCode(''))
      .finally(() => setModuleCodeLoading(false))
  }, [selectedModule])

  // ── API Layer ────────────────────────────────────────────

  const agentFetch = useCallback(async (endpoint: string, options: RequestInit = {}) => {
    const token = client?.token || ''
    return fetch(`${API_URL}${endpoint}`, {
      ...options,
      headers: {
        ...options.headers,
        'token': token,
        'Content-Type': 'application/json',
      },
    })
  }, [client?.token])

  const fetchJobs = useCallback(async () => {
    try {
      const res = await agentFetch('/jobs')
      if (!res.ok) throw new Error('Failed')
      const data = await res.json()
      setJobs(data.jobs || [])
      setError(null)
    } catch {
      setError('Agent API offline')
    } finally {
      setLoading(false)
    }
  }, [agentFetch])

  useEffect(() => {
    fetchJobs()
    const iv = setInterval(fetchJobs, 4000)
    return () => clearInterval(iv)
  }, [fetchJobs])

  useEffect(() => {
    agentFetch('/modules')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.modules) setModules([...new Set(data.modules.map((m: any) => typeof m === 'string' ? m : m.name))] as string[])
      })
      .catch(() => {})
  }, [agentFetch])

  // ── Streaming ────────────────────────────────────────────

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

    es.onerror = () => {
      es.close()
      fetchJobs()
    }
  }, [jobs, fetchJobs])

  useEffect(() => {
    return () => { if (esRef.current) esRef.current.close() }
  }, [])

  // ── Custom Agents ──────────────────────────────────────

  const addAgent = () => {
    if (!newAgentName.trim()) return
    const id = newAgentName.trim().toLowerCase().replace(/\s+/g, '-')
    const agent: Personality = {
      id,
      name: newAgentName.trim(),
      icon: newAgentIcon.trim() || '\u25CF',
      prompt: newAgentPrompt.trim(),
      custom: true,
    }
    const updated = [...customAgents, agent]
    setCustomAgents(updated)
    saveCustomAgents(updated)
    setNewAgentName('')
    setNewAgentIcon('')
    setNewAgentPrompt('')
    setShowAddAgent(false)
    setPersonality(id)
  }

  const deleteAgent = (id: string) => {
    const updated = customAgents.filter(a => a.id !== id)
    setCustomAgents(updated)
    saveCustomAgents(updated)
    if (personality === id) setPersonality('default')
  }

  // ── Job Actions ──────────────────────────────────────────

  const submitJob = async () => {
    if (!prompt.trim()) return
    setSubmitting(true)
    try {
      const body: any = { prompt: prompt.trim(), model }

      const activeP = allPersonalities.find(p => p.id === personality)
      if (activeP?.prompt) body.system_prompt = activeP.prompt
      if (personality !== 'default') body.agent_type = personality
      if (workDir.trim()) body.work_dir = workDir.trim()

      // forward-style: include module_name and creation_mode when a module is selected
      if (selectedModule) {
        body.module_name = selectedModule
        body.creation_mode = 'edit'
      }

      const res = await agentFetch('/jobs', {
        method: 'POST',
        body: JSON.stringify(body),
      })

      if (!res.ok) throw new Error('Submit failed')
      const job = await res.json()
      setPrompt('')
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

  const viewJob = (job: Job) => {
    setSelectedJob(job.id)
    if (job.status === 'running') {
      startStream(job.id)
    } else {
      setStreamOutput(job.output)
      if (esRef.current) { esRef.current.close(); esRef.current = null }
    }
  }

  // ── Module selector ──────────────────────────────────────

  const selectModule = (name: string) => {
    setSelectedModule(name)
    setWorkDir(`~/mod/mod/orbit/${name}`)
    setShowModules(false)
    setModuleSearch('')
  }

  const clearModule = () => {
    setSelectedModule('')
    setWorkDir('')
    setModuleCode('')
    setModuleCodePath('')
  }

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (moduleDropdownRef.current && !moduleDropdownRef.current.contains(e.target as Node)) {
        setShowModules(false)
      }
    }
    if (showModules) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showModules])

  // ── Derived ──────────────────────────────────────────────

  const selectedJobData = jobs.find(j => j.id === selectedJob) || null
  const activePersonality = allPersonalities.find(p => p.id === personality) || allPersonalities[0]
  const activeModel = MODELS.find(m => m.value === model) || MODELS[0]
  const runningCount = jobs.filter(j => j.status === 'running').length

  const filteredModules = orbitModules
    .filter(m => m.has_mod)
    .filter(m => !moduleSearch || m.name.toLowerCase().includes(moduleSearch.toLowerCase()))

  // Owner gate UI
  if (isOwner === null) {
    return (
      <div className="h-full w-full flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
        <div className="flex flex-col items-center gap-4">
          <div className="w-3 h-3 rounded-full animate-pulse" style={{ background: 'var(--accent-primary)' }} />
          <span style={{ fontSize: '14px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
            Verifying ownership...
          </span>
        </div>
      </div>
    )
  }

  if (isOwner === false) {
    return (
      <div className="h-full w-full flex items-center justify-center" style={{ background: 'var(--bg-primary)' }}>
        <div className="flex flex-col items-center gap-5 max-w-md text-center p-8 rounded-xl" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
          <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)' }}>
            <span style={{ fontSize: '28px' }}>&#x1F512;</span>
          </div>
          <div>
            <h2 style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '8px' }}>
              Owner Access Only
            </h2>
            <p style={{ fontSize: '14px', color: 'var(--text-tertiary)', lineHeight: '1.6' }}>
              The Agent is restricted to the Treasury contract owner.
              {user?.key && (
                <span style={{ display: 'block', marginTop: '8px', fontSize: '12px', fontFamily: 'monospace', color: 'var(--text-tertiary)', opacity: 0.7 }}>
                  Connected: {user.key.slice(0, 6)}...{user.key.slice(-4)}
                </span>
              )}
            </p>
            {ownerCheckError && (
              <p style={{ fontSize: '12px', color: '#ef4444', marginTop: '12px', fontFamily: 'monospace' }}>
                {ownerCheckError}
              </p>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full w-full flex flex-col overflow-hidden" style={{ fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {/* Top bar */}
      <div
        className="px-5 py-3 flex items-center justify-between shrink-0"
        style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-secondary)' }}
      >
        <div className="flex items-center gap-3">
          <span style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.01em' }}>
            Agent
          </span>
          {runningCount > 0 && (
            <span
              className="px-2.5 py-1 flex items-center gap-1.5"
              style={{
                fontSize: '12px',
                fontWeight: 600,
                background: 'rgba(59, 130, 246, 0.1)',
                border: '1px solid rgba(59, 130, 246, 0.25)',
                color: '#60a5fa',
                borderRadius: '6px',
              }}
            >
              <span className="inline-block w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: '#60a5fa' }} />
              {runningCount} running
            </span>
          )}
        </div>

        {error && (
          <span style={{ fontSize: '11px', color: '#ef4444', maxWidth: '500px' }} className="truncate">
            {error}
          </span>
        )}
      </div>

      {/* Server status */}
      <div className="px-4 pt-3 shrink-0" style={{ background: 'var(--bg-secondary)' }}>
        <ServerPanel moduleName="agent" apiUrl="http://localhost:50117" appUrl="http://localhost:3117" color="#a78bfa" />
      </div>

      {/* Main layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar with agents + tasks */}
        <div className="h-full flex flex-col" style={{
          width: sidebarOpen ? '340px' : '40px',
          minWidth: sidebarOpen ? '340px' : '40px',
          borderRight: '1px solid var(--border-color)',
          background: 'var(--bg-secondary)',
          transition: 'width 0.15s, min-width 0.15s',
        }}>
          {!sidebarOpen ? (
            <div
              className="h-full flex flex-col items-center py-3 cursor-pointer"
              onClick={() => setSidebarOpen(true)}
            >
              <span style={{ fontSize: '13px', color: 'var(--text-secondary)', writingMode: 'vertical-rl', fontWeight: 600, letterSpacing: '0.1em' }}>
                AGENTS
              </span>
            </div>
          ) : (
            <>
              {/* Agents section */}
              <div className="shrink-0" style={{ borderBottom: '1px solid var(--border-color)' }}>
                <div className="px-4 py-3 flex items-center justify-between">
                  <span style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.02em' }}>
                    Agents
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setShowAddAgent(!showAddAgent)}
                      className="w-7 h-7 flex items-center justify-center rounded-md transition-colors"
                      style={{ color: 'var(--text-tertiary)', background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '16px' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      title="Add agent"
                    >
                      +
                    </button>
                    <button
                      onClick={() => setSidebarOpen(false)}
                      className="w-7 h-7 flex items-center justify-center rounded-md transition-colors"
                      style={{ color: 'var(--text-tertiary)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="15 18 9 12 15 6" />
                      </svg>
                    </button>
                  </div>
                </div>

                {/* Add agent form */}
                {showAddAgent && (
                  <div className="px-4 pb-3 space-y-2">
                    <div className="flex gap-2">
                      <input
                        value={newAgentIcon}
                        onChange={e => setNewAgentIcon(e.target.value)}
                        placeholder="Icon"
                        maxLength={2}
                        style={{
                          width: '48px', fontSize: '14px', textAlign: 'center',
                          background: 'var(--bg-input)', border: '1px solid var(--border-color)',
                          color: 'var(--text-primary)', padding: '6px', outline: 'none', borderRadius: '6px',
                        }}
                      />
                      <input
                        value={newAgentName}
                        onChange={e => setNewAgentName(e.target.value)}
                        placeholder="Agent name"
                        style={{
                          flex: 1, fontSize: '13px',
                          background: 'var(--bg-input)', border: '1px solid var(--border-color)',
                          color: 'var(--text-primary)', padding: '6px 10px', outline: 'none', borderRadius: '6px',
                        }}
                      />
                    </div>
                    <textarea
                      value={newAgentPrompt}
                      onChange={e => setNewAgentPrompt(e.target.value)}
                      placeholder="System prompt..."
                      rows={2}
                      style={{
                        width: '100%', fontSize: '13px', fontFamily: '"JetBrains Mono", "Fira Code", monospace',
                        background: 'var(--bg-input)', border: '1px solid var(--border-color)',
                        color: 'var(--text-primary)', padding: '8px 10px', outline: 'none',
                        borderRadius: '6px', resize: 'none',
                      }}
                    />
                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => setShowAddAgent(false)}
                        style={{
                          fontSize: '12px', padding: '4px 12px', background: 'transparent',
                          border: '1px solid var(--border-color)', color: 'var(--text-tertiary)',
                          borderRadius: '5px', cursor: 'pointer',
                        }}
                      >
                        Cancel
                      </button>
                      <button
                        onClick={addAgent}
                        disabled={!newAgentName.trim()}
                        style={{
                          fontSize: '12px', padding: '4px 12px',
                          background: newAgentName.trim() ? 'var(--accent-primary)' : 'var(--hover-bg)',
                          border: 'none',
                          color: newAgentName.trim() ? '#000' : 'var(--text-tertiary)',
                          borderRadius: '5px', cursor: newAgentName.trim() ? 'pointer' : 'not-allowed',
                          fontWeight: 600,
                        }}
                      >
                        Add
                      </button>
                    </div>
                  </div>
                )}

                {/* Agent list */}
                <div className="px-3 pb-3 flex flex-col gap-1">
                  {allPersonalities.map(p => {
                    const isActive = personality === p.id
                    return (
                      <div
                        key={p.id}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-all group"
                        style={{
                          background: isActive ? 'var(--hover-bg)' : 'transparent',
                          border: isActive ? '1px solid var(--border-color)' : '1px solid transparent',
                        }}
                        onClick={() => setPersonality(p.id)}
                        onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--hover-bg)' }}
                        onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
                      >
                        <span style={{ fontSize: '14px', opacity: 0.8, width: '20px', textAlign: 'center' }}>{p.icon}</span>
                        <span style={{
                          fontSize: '13px',
                          fontWeight: isActive ? 600 : 400,
                          color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                          flex: 1,
                        }}>
                          {p.name}
                        </span>
                        {p.custom && (
                          <button
                            onClick={e => { e.stopPropagation(); deleteAgent(p.id) }}
                            className="w-5 h-5 flex items-center justify-center rounded transition-colors opacity-0 group-hover:opacity-100"
                            style={{
                              color: 'var(--text-tertiary)', background: 'transparent',
                              border: 'none', cursor: 'pointer', fontSize: '12px',
                            }}
                            onMouseEnter={e => { e.currentTarget.style.color = '#ef4444'; e.currentTarget.style.background = 'rgba(239,68,68,0.1)' }}
                            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-tertiary)'; e.currentTarget.style.background = 'transparent' }}
                            title="Delete agent"
                          >
                            &#215;
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Tasks section */}
              <AgentSidebar
                jobs={jobs}
                selectedJob={selectedJob}
                onSelectJob={viewJob}
                onCancelJob={cancelJob}
                onDeleteJob={deleteJob}
                collapsed={false}
                onToggle={() => {}}
                embedded
                modules={modules}
                workDir={workDir}
                onSelectModule={selectModule}
                onClearModule={clearModule}
                prompt={prompt}
                onPromptChange={setPrompt}
                onSubmit={submitJob}
                submitting={submitting}
                model={model}
                personalityIcon={activePersonality.icon}
                personalityName={activePersonality.name}
              />
            </>
          )}
        </div>

        {/* Main area */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Controls bar */}
          <div
            className="shrink-0 px-4 py-2.5 flex items-center gap-3 flex-wrap"
            style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-secondary)' }}
          >
            {/* Model selector */}
            <div className="flex gap-1.5">
              {MODELS.map(m => (
                <button
                  key={m.value}
                  onClick={() => setModel(m.value)}
                  className="px-3 py-1.5 transition-all"
                  style={{
                    fontSize: '13px',
                    fontWeight: model === m.value ? 600 : 400,
                    background: model === m.value ? `${m.color}18` : 'transparent',
                    border: model === m.value ? `1px solid ${m.color}40` : '1px solid var(--border-color)',
                    color: model === m.value ? m.color : 'var(--text-tertiary)',
                    borderRadius: '6px',
                    cursor: 'pointer',
                  }}
                >
                  {m.label}
                </button>
              ))}
            </div>

            <div style={{ width: '1px', height: '20px', background: 'var(--border-color)' }} />

            {/* Module selector */}
            <div className="relative flex items-center gap-1.5" ref={moduleDropdownRef}>
              <button
                onClick={() => setShowModules(!showModules)}
                className="px-3 py-1.5 transition-all flex items-center gap-1.5"
                style={{
                  fontSize: '13px',
                  fontWeight: selectedModule ? 600 : 400,
                  background: selectedModule ? 'rgba(16, 185, 129, 0.08)' : 'transparent',
                  border: selectedModule ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid var(--border-color)',
                  color: selectedModule ? '#10b981' : 'var(--text-tertiary)',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  maxWidth: '200px',
                }}
              >
                {selectedModule || 'Module'}
              </button>
              {selectedModule && (
                <button
                  onClick={clearModule}
                  className="w-6 h-6 flex items-center justify-center rounded-md transition-colors"
                  style={{ color: 'var(--text-tertiary)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  &#215;
                </button>
              )}

              {showModules && (
                <div
                  className="absolute top-full left-0 mt-2 z-50 overflow-y-auto"
                  style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '10px',
                    boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
                    maxHeight: '320px',
                    minWidth: '220px',
                  }}
                >
                  <div className="p-2.5" style={{ borderBottom: '1px solid var(--border-color)' }}>
                    <input
                      value={workDir}
                      onChange={e => setWorkDir(e.target.value)}
                      placeholder="Custom path..."
                      style={{
                        width: '100%', fontSize: '13px', fontFamily: 'monospace',
                        background: 'var(--bg-input)', border: '1px solid var(--border-color)',
                        color: 'var(--text-primary)', padding: '8px 10px', outline: 'none', borderRadius: '6px',
                      }}
                      onKeyDown={e => { if (e.key === 'Enter') setShowModules(false) }}
                    />
                  </div>
                  {(modules.length > 0 ? modules : orbitModules.filter(m => m.has_mod).map(m => m.name)).map(m => (
                    <div
                      key={m}
                      onClick={() => selectModule(m)}
                      className="px-4 py-2.5 cursor-pointer transition-colors"
                      style={{
                        fontSize: '13px', fontFamily: 'monospace', color: 'var(--text-primary)',
                        background: selectedModule === m ? 'rgba(16, 185, 129, 0.08)' : 'transparent',
                      }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
                      onMouseLeave={e => (e.currentTarget.style.background = selectedModule === m ? 'rgba(16, 185, 129, 0.08)' : 'transparent')}
                    >
                      {m}
                    </div>
                  ))}
                  {modules.length === 0 && orbitModules.length === 0 && (
                    <div className="px-4 py-3" style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
                      No modules loaded
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="ml-auto flex items-center gap-3">
              <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', opacity: 0.6 }}>
                {activePersonality.id !== 'default' && (
                  <span style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>
                    {activePersonality.icon} {activePersonality.name} &middot;{' '}
                  </span>
                )}
                {activeModel.label} &middot; {API_URL.replace('http://', '')}
              </span>
            </div>
          </div>


          {/* Content area: Agent Chat / Code Viewer / Module Picker */}
          {selectedJobData ? (
            <AgentChat
              job={selectedJobData}
              streamOutput={streamOutput}
              onCancel={cancelJob}
            />
          ) : selectedModule ? (
            /* ── Code Viewer ──────────────────────────────────── */
            <div className="flex-1 flex flex-col overflow-hidden" style={{ background: 'var(--bg-primary)' }}>
              {/* Code header */}
              <div
                className="px-4 py-2 flex items-center justify-between shrink-0"
                style={{ borderBottom: '1px solid var(--border-color)', background: 'rgba(16, 185, 129, 0.03)' }}
              >
                <div className="flex items-center gap-2.5">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ background: '#10b981', boxShadow: '0 0 6px rgba(16, 185, 129, 0.4)' }}
                  />
                  <span style={{ fontSize: '13px', fontWeight: 700, color: '#10b981' }}>
                    {selectedModule}
                  </span>
                  <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                    {moduleCodePath}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  {moduleCode && (
                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                      {moduleCode.split('\n').length} lines
                    </span>
                  )}
                  <button
                    onClick={() => { setSelectedJob(null); setStreamOutput(''); clearModule() }}
                    className="px-2.5 py-1 transition-all"
                    style={{
                      fontSize: '11px', fontWeight: 500,
                      background: 'transparent',
                      border: '1px solid var(--border-color)',
                      color: 'var(--text-tertiary)',
                      borderRadius: '5px', cursor: 'pointer',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.color = 'var(--text-primary)' }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-tertiary)' }}
                  >
                    Change module
                  </button>
                </div>
              </div>

              {/* Code content */}
              {moduleCodeLoading ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: '#10b981' }} />
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>Loading code...</span>
                  </div>
                </div>
              ) : moduleCode ? (
                <div className="flex-1 overflow-auto" style={{ background: 'var(--bg-primary)' }}>
                  <table style={{ borderCollapse: 'collapse', width: '100%', fontFamily: '"JetBrains Mono", "Fira Code", monospace' }}>
                    <tbody>
                      {moduleCode.split('\n').map((line, i) => (
                        <tr
                          key={i}
                          style={{ lineHeight: '1.6' }}
                          onMouseEnter={e => (e.currentTarget.style.background = 'var(--hover-bg)')}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                        >
                          <td style={{
                            padding: '0 12px 0 16px',
                            textAlign: 'right',
                            userSelect: 'none',
                            color: 'var(--text-tertiary)',
                            opacity: 0.3,
                            fontSize: '11px',
                            width: '1%',
                            whiteSpace: 'nowrap',
                            verticalAlign: 'top',
                          }}>
                            {i + 1}
                          </td>
                          <td style={{
                            padding: '0 16px 0 8px',
                            fontSize: '12px',
                            color: 'var(--text-primary)',
                            whiteSpace: 'pre',
                          }}>
                            {line || ' '}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center">
                  <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                    No mod.py found for this module
                  </span>
                </div>
              )}
            </div>
          ) : (
            /* ── Module Picker ────────────────────────────────── */
            <div className="flex-1 flex flex-col overflow-hidden" style={{ background: 'var(--bg-primary)' }}>
              {/* Search */}
              <div className="px-5 pt-4 pb-3 shrink-0">
                <input
                  value={moduleSearch}
                  onChange={e => setModuleSearch(e.target.value)}
                  placeholder="Search modules..."
                  autoFocus
                  style={{
                    width: '100%',
                    fontSize: '13px',
                    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border-color)',
                    color: 'var(--text-primary)',
                    padding: '10px 14px',
                    outline: 'none',
                    borderRadius: '8px',
                    transition: 'border-color 0.15s',
                  }}
                  onFocus={e => (e.currentTarget.style.borderColor = '#10b981')}
                  onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-color)')}
                />
              </div>

              {/* Module grid */}
              <div className="flex-1 overflow-auto px-5 pb-5">
                <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
                  {filteredModules.map(mod => (
                    <button
                      key={mod.name}
                      onClick={() => selectModule(mod.name)}
                      className="px-3 py-3 transition-all text-left"
                      style={{
                        fontSize: '12px',
                        fontFamily: '"JetBrains Mono", "Fira Code", monospace',
                        fontWeight: 500,
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        color: 'var(--text-primary)',
                        borderRadius: '8px',
                        cursor: 'pointer',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.borderColor = 'rgba(16, 185, 129, 0.4)'
                        e.currentTarget.style.background = 'rgba(16, 185, 129, 0.05)'
                        e.currentTarget.style.color = '#10b981'
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.borderColor = 'var(--border-color)'
                        e.currentTarget.style.background = 'var(--bg-secondary)'
                        e.currentTarget.style.color = 'var(--text-primary)'
                      }}
                    >
                      {mod.name}
                    </button>
                  ))}
                </div>
                {filteredModules.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16">
                    <span style={{ fontSize: '28px', opacity: 0.15, marginBottom: '12px' }}>&#x2B21;</span>
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                      {moduleSearch ? 'No modules match your search' : 'No modules with mod.py found'}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
