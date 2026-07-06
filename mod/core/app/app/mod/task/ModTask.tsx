"use client";

import { useState, useEffect, useCallback, useRef } from 'react'
import { userContext } from '@/context'
import { ModuleType, UserType } from '@/types'
import { CopyButton } from '@/ui/CopyButton'
import { Zap, Search, Clock, ChevronDown, ChevronUp, RefreshCw, X, Play, Copy, Clipboard, Users } from 'lucide-react'

const ADDRESS_PARAM_NAMES = ['key', 'address', 'owner', 'user', 'to', 'from', 'sender', 'receiver', 'recipient', 'wallet', 'account']

function isAddressParam(paramName: string): boolean {
  const lower = paramName.toLowerCase()
  return ADDRESS_PARAM_NAMES.some(name => lower === name || lower.endsWith('_' + name) || lower.endsWith('address') || lower.endsWith('key'))
}

function AddressParamInput({
  paramKey,
  value,
  placeholder,
  onChange,
  users,
  usersLoading,
}: {
  paramKey: string
  value: string
  placeholder: string
  onChange: (value: string) => void
  users: UserType[]
  usersLoading: boolean
}) {
  const [showDropdown, setShowDropdown] = useState(false)
  const [filter, setFilter] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const filtered = users.filter(u => {
    if (!u.key) return false
    if (!filter) return true
    return u.key.toLowerCase().includes(filter.toLowerCase())
  })

  const truncateAddr = (addr: string) => {
    if (addr.length <= 12) return addr
    return `${addr.slice(0, 6)}··${addr.slice(-4)}`
  }

  return (
    <div ref={containerRef} className="relative">
      <div className="flex gap-1">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full px-3 py-2.5 text-[13px] font-mono focus:outline-none transition-all"
          style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
        />
        <button
          type="button"
          onClick={() => setShowDropdown(!showDropdown)}
          className="shrink-0 px-2.5 py-2.5 transition-all flex items-center justify-center"
          style={{
            backgroundColor: showDropdown ? 'var(--text-primary)' : 'var(--bg-input)',
            border: '1px solid var(--border-color)',
            color: showDropdown ? 'var(--bg-primary)' : 'var(--text-secondary)',
          }}
          title="Select from users"
        >
          <Users className="w-3.5 h-3.5" />
        </button>
      </div>
      {showDropdown && (
        <div
          className="absolute z-50 left-0 right-0 mt-1 max-h-[240px] overflow-hidden flex flex-col"
          style={{
            backgroundColor: 'var(--bg-surface)',
            border: '1px solid var(--border-strong)',
            boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
          }}
        >
          <div className="relative shrink-0" style={{ borderBottom: '1px solid var(--border-color)' }}>
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3" style={{ color: 'var(--text-tertiary)' }} />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter users..."
              autoFocus
              className="w-full pl-8 pr-3 py-2 text-[11px] font-mono focus:outline-none"
              style={{ backgroundColor: 'var(--bg-input)', color: 'var(--text-primary)' }}
            />
          </div>
          <div className="overflow-y-auto flex-1 mod-scroll">
            {usersLoading && (
              <div className="px-3 py-4 text-center">
                <span className="animate-pulse text-[11px] font-mono" style={{ color: 'var(--text-tertiary)' }}>Loading users...</span>
              </div>
            )}
            {!usersLoading && filtered.length === 0 && (
              <div className="px-3 py-4 text-center">
                <span className="text-[11px] font-mono" style={{ color: 'var(--text-tertiary)' }}>No users found</span>
              </div>
            )}
            {!usersLoading && filtered.map((u) => (
              <button
                key={u.key}
                type="button"
                onClick={() => {
                  onChange(u.key || '')
                  setShowDropdown(false)
                  setFilter('')
                }}
                className="w-full text-left px-3 py-2 flex items-center gap-2 transition-all hover:brightness-125"
                style={{
                  borderBottom: '1px solid var(--border-color)',
                  backgroundColor: value === u.key ? 'var(--bg-input)' : 'transparent',
                }}
              >
                <span className="text-[11px] font-mono truncate" style={{ color: 'var(--text-primary)' }}>
                  {truncateAddr(u.key || '')}
                </span>
                <span className="text-[10px] font-mono ml-auto shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                  {u.key || ''}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

interface ModTaskProps {
  mod: ModuleType
  moduleColor?: string
}

interface Task {
  fn: string
  params: any
  status: string
  time: string
  key: string
  signature?: string
  result?: any
  cid?: string
  hash?: string
  delta?: number
  cost?: number
  module?: string
  owner?: string
}

type ViewSection = 'tasks' | 'api'

export default function ModTask({ mod, moduleColor = '#ffffff' }: ModTaskProps) {
  const { client } = userContext()

  // Task history state
  const [tasks, setTasks] = useState<Task[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [expandedTask, setExpandedTask] = useState<number | null>(null)

  // API execution state
  const [selectedFunction, setSelectedFunction] = useState<string>('')
  const [params, setParams] = useState<Record<string, any>>({})
  const [result, setResult] = useState<any>(null)
  const [execLoading, setExecLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fnSearch, setFnSearch] = useState('')

  // Cancel state
  const [cancellingCid, setCancellingCid] = useState<string | null>(null)
  const [execCid, setExecCid] = useState<string | null>(null)

  // Available users for address param dropdowns
  const [availableUsers, setAvailableUsers] = useState<UserType[]>([])
  const [usersLoading, setUsersLoading] = useState(false)

  // View section toggle
  const [activeSection, setActiveSection] = useState<ViewSection>('api')

  const schema = mod.schema || {}
  const functions = Object.keys(schema)
  const filteredFunctions = fnSearch
    ? functions.filter(fn => fn.toLowerCase().includes(fnSearch.toLowerCase()))
    : functions

  // Fetch previous tasks for this mod
  const fetchTasks = useCallback(async () => {
    if (!client) return
    setTasksLoading(true)
    try {
      const result = await client.call('txs', { df: 0, n: 50, page: 0 })
      const txs = Array.isArray(result) ? result : []
      // Filter to only tasks for this mod
      const modTasks = txs.filter((tx: Task) => {
        if (!tx.fn) return false
        const fnMod = tx.fn.split('/')[0].toLowerCase()
        return fnMod === mod.name.toLowerCase()
      })
      setTasks(modTasks)
    } catch (err) {
      console.error('Failed to fetch tasks:', err)
    } finally {
      setTasksLoading(false)
    }
  }, [client, mod.name])

  useEffect(() => {
    fetchTasks()
  }, [fetchTasks])

  // Fetch available users for address param dropdowns
  useEffect(() => {
    const fetchUsers = async () => {
      if (!client) return
      setUsersLoading(true)
      try {
        const raw = await client.call('users', {})
        const list = Array.isArray(raw) ? raw : []
        setAvailableUsers(list as UserType[])
      } catch (err) {
        console.error('Failed to fetch users:', err)
      } finally {
        setUsersLoading(false)
      }
    }
    fetchUsers()
  }, [client])

  useEffect(() => {
    if (functions.length > 0 && !selectedFunction) {
      setSelectedFunction(functions[0])
    }
  }, [functions, selectedFunction])

  // API execution handlers
  const handleParamChange = (key: string, value: string) => {
    setParams(prev => ({ ...prev, [key]: value }))
  }

  const handleSelectFunction = (fn: string) => {
    setSelectedFunction(fn === selectedFunction ? '' : fn)
    setParams({})
    setResult(null)
    setError(null)
  }

  const handleExecute = async (fnOverride?: string, paramsOverride?: Record<string, any>) => {
    if (!client) return
    const fn = fnOverride || selectedFunction
    if (!fn) return
    setExecLoading(true)
    setError(null)
    setResult(null)
    setExecCid(null)
    try {
      const fnpath = `${mod.name}/${fn}`
      const execParams = paramsOverride || { ...params }
      // Submit with wait=false to get CID for cancellation
      const task = await client.call(fnpath, execParams, false)
      const cid = task?.cid
      if (cid) {
        setExecCid(cid)
        // Poll for result
        const poll = async (): Promise<any> => {
          const txs = await client.call('txs', { df: 0, n: 1, page: 0, cid })
          const found = Array.isArray(txs) ? txs.find((t: any) => t.cid === cid) : null
          if (!found) return null
          if (found.status === 'success' || found.status === 'complete' || found.status === 'finished') return found.result
          if (found.status === 'error' || found.status === 'failed') throw new Error(found.result || 'Task failed')
          if (found.status === 'cancelled') throw new Error('Task cancelled')
          // Still running, wait and poll again
          await new Promise(r => setTimeout(r, 1000))
          return poll()
        }
        const res = await poll()
        setResult(res)
      } else {
        // Fallback: wait=false returned something unexpected, treat as direct result
        setResult(task)
      }
      fetchTasks()
    } catch (err: any) {
      if (err?.name === 'AbortError') return
      setError(err?.message || 'Execution failed')
      fetchTasks()
    } finally {
      setExecLoading(false)
      setExecCid(null)
    }
  }

  const handleCancelExec = async () => {
    if (execCid && client) {
      try {
        await client.call('kill_task', { cid: execCid })
      } catch (err) {
        console.error('Failed to cancel task:', err)
      }
    }
    setExecLoading(false)
    setExecCid(null)
    setError('Cancelled by user')
    fetchTasks()
  }

  const handleReplayTask = (task: Task) => {
    const fnName = task.fn?.split('/').slice(1).join('/') || task.fn
    if (!fnName) return
    // Load the function and params into the API panel
    setSelectedFunction(fnName)
    setParams(task.params && typeof task.params === 'object' ? { ...task.params } : {})
    setResult(null)
    setError(null)
    setActiveSection('api')
  }

  const handleReplayAndRun = (task: Task) => {
    const fnName = task.fn?.split('/').slice(1).join('/') || task.fn
    if (!fnName) return
    setSelectedFunction(fnName)
    const taskParams = task.params && typeof task.params === 'object' ? { ...task.params } : {}
    setParams(taskParams)
    setResult(null)
    setError(null)
    setActiveSection('api')
    // Execute immediately
    handleExecute(fnName, taskParams)
  }

  const handleCopyTask = (task: Task) => {
    const fnName = task.fn || ''
    const payload = { fn: fnName, params: task.params || {} }
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
  }

  const handleCopyInputs = (task: Task) => {
    const inputs = task.params && typeof task.params === 'object' ? task.params : {}
    navigator.clipboard.writeText(JSON.stringify(inputs, null, 2))
  }

  const handleCancelTask = async (cid: string) => {
    if (!client || !cid) return
    setCancellingCid(cid)
    try {
      await client.call('kill_task', { cid })
      fetchTasks()
    } catch (err: any) {
      console.error('Failed to cancel task:', err)
    } finally {
      setCancellingCid(null)
    }
  }

  const getFnParams = (fnName: string) => {
    const fnSchema = (schema as Record<string, any>)[fnName]
    if (!fnSchema?.input) return []
    if (Array.isArray(fnSchema.input)) {
      return fnSchema.input.map((param: any) => [param.name, { type: param.type, value: param.value }])
    }
    return Object.entries(fnSchema.input)
      .filter(([key]) => key !== 'self' && key !== 'cls' && key !== 'kwargs')
  }

  const getFnOutput = (fnName: string) => {
    const fnSchema = (schema as Record<string, any>)[fnName]
    return fnSchema?.output || null
  }

  const selectedFnParams = selectedFunction ? getFnParams(selectedFunction) : []
  const selectedFnOutput = selectedFunction ? getFnOutput(selectedFunction) : null

  const formatTime = (time: string) => {
    const ts = parseInt(time)
    if (!ts) return time
    const d = new Date(ts * 1000)
    return d.toLocaleString()
  }

  const getStatusColor = (status: string) => {
    if (status === 'success' || status === 'finished' || status === 'complete') return '#22c55e'
    if (status === 'error' || status === 'failed') return '#ef4444'
    if (status === 'cancelled') return '#f97316'
    if (status === 'pending' || status === 'running') return '#eab308'
    return 'var(--text-tertiary)'
  }

  const getStatusLabel = (status: string) => {
    if (status === 'success' || status === 'finished' || status === 'complete') return '✓'
    if (status === 'error' || status === 'failed') return '✗'
    if (status === 'cancelled') return '⊘'
    if (status === 'pending' || status === 'running') return '◉'
    return '?'
  }

  const sections: { id: ViewSection; label: string; show: boolean }[] = [
    { id: 'api', label: 'API', show: true },
    { id: 'tasks', label: `TASKS (${tasks.length})`, show: true },
  ]

  return (
    <div className="font-mono" style={{ fontFamily: 'JetBrains Mono, monospace' }}>
      {/* Section Toggle */}
      <div className="flex items-center gap-2 mb-4">
        {sections.filter(s => s.show).map(section => {
          const isActive = activeSection === section.id
          return (
            <button
              key={section.id}
              onClick={() => setActiveSection(section.id)}
              className="px-4 py-2 text-xs font-bold uppercase tracking-wider transition-all"
              style={{
                color: isActive ? 'var(--bg-primary)' : 'var(--text-secondary)',
                backgroundColor: isActive ? 'var(--text-primary)' : 'transparent',
                border: isActive ? '1px solid var(--text-primary)' : '1px solid var(--border-color)',
              }}
            >
              {section.label}
            </button>
          )
        })}
      </div>

      {/* === API SECTION === */}
      {activeSection === 'api' && (
        <div className="flex gap-4">
          {/* Left: function list */}
          <div className="w-[340px] shrink-0 flex flex-col">
            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-tertiary)' }} />
              <input
                type="text"
                value={fnSearch}
                onChange={(e) => setFnSearch(e.target.value)}
                placeholder="Search functions..."
                className="w-full pl-9 pr-16 py-2.5 text-sm font-mono focus:outline-none transition-all"
                style={{
                  backgroundColor: 'var(--bg-input)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-primary)',
                }}
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
                {filteredFunctions.length}/{functions.length}
              </span>
            </div>

            <div className="max-h-[600px] overflow-y-auto space-y-1 mod-scroll">
              {filteredFunctions.map(fn => {
                const isActive = selectedFunction === fn
                const fnParams = getFnParams(fn)
                const fnOutput = getFnOutput(fn)
                return (
                  <button
                    key={fn}
                    onClick={() => handleSelectFunction(fn)}
                    className="w-full text-left px-3 py-2.5 transition-all duration-150"
                    style={{
                      border: isActive ? '1px solid var(--border-strong)' : '1px solid transparent',
                      backgroundColor: isActive ? 'var(--bg-input)' : 'transparent',
                    }}
                  >
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-bold font-mono uppercase"
                        style={{ backgroundColor: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>fn</span>
                      <span className="text-sm font-semibold font-mono truncate"
                        style={{ color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{fn}</span>
                    </div>
                    <div className="ml-7 space-y-0.5">
                      {fnParams.length > 0 && (
                        <div className="flex flex-wrap gap-x-2 gap-y-0.5">
                          {fnParams.map(([key, value]: [string, any]) => (
                            <span key={key} className="text-xs font-mono">
                              <span style={{ color: 'var(--text-secondary)' }}>{key}</span>
                              <span style={{ color: 'var(--text-tertiary)' }}> : {value.type || 'any'}</span>
                            </span>
                          ))}
                        </div>
                      )}
                      {fnOutput && (
                        <div className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
                          → {typeof fnOutput === 'object' ? fnOutput.type || 'any' : fnOutput}
                        </div>
                      )}
                    </div>
                  </button>
                )
              })}
              {filteredFunctions.length === 0 && (
                <div className="text-center text-sm font-mono py-12" style={{ color: 'var(--text-tertiary)' }}>
                  {functions.length === 0 ? 'No functions' : 'No matches'}
                </div>
              )}
            </div>
          </div>

          {/* Right: interaction + output */}
          <div className="flex-1 min-w-0">
            {selectedFunction ? (
              <div className="p-5 space-y-4" style={{ border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-surface)' }}>
                {/* Function header */}
                <div className="flex items-center gap-2.5">
                  <span className="px-2 py-0.5 text-xs font-bold uppercase font-mono"
                    style={{ backgroundColor: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>FN</span>
                  <span className="text-lg font-bold font-mono" style={{ color: 'var(--text-primary)' }}>{selectedFunction}</span>
                </div>

                {/* Signature */}
                <div className="px-4 py-2.5" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
                  <code className="text-sm font-mono leading-relaxed">
                    <span style={{ color: 'var(--text-tertiary)' }}>fn </span>
                    <span style={{ color: 'var(--text-primary)' }}>{selectedFunction}</span>
                    <span style={{ color: 'var(--text-tertiary)' }}>(</span>
                    {selectedFnParams.map(([key, value]: [string, any], i: number) => (
                      <span key={key}>
                        {i > 0 && <span style={{ color: 'var(--text-tertiary)' }}>, </span>}
                        <span style={{ color: 'var(--text-secondary)' }}>{key}</span>
                        <span style={{ color: 'var(--text-tertiary)' }}> : {value.type || 'any'}</span>
                      </span>
                    ))}
                    <span style={{ color: 'var(--text-tertiary)' }}>)</span>
                    {selectedFnOutput && (
                      <span>
                        <span style={{ color: 'var(--text-tertiary)' }}> → </span>
                        <span style={{ color: 'var(--text-tertiary)' }}>{typeof selectedFnOutput === 'object' ? selectedFnOutput.type || 'any' : selectedFnOutput}</span>
                      </span>
                    )}
                  </code>
                </div>

                {/* Args */}
                {selectedFnParams.length > 0 && (
                  <div className="space-y-3">
                    {selectedFnParams.map(([key, value]: [string, any]) => (
                      <div key={key}>
                        <label className="text-sm font-mono mb-1.5 block font-medium" style={{ color: 'var(--text-secondary)' }}>
                          {key} <span style={{ color: 'var(--text-tertiary)' }}>:: {value.type || 'any'}</span>
                        </label>
                        {value.type === 'bool' ? (
                          <select
                            value={params[key] || ''}
                            onChange={(e) => handleParamChange(key, e.target.value)}
                            className="w-full px-3 py-2.5 text-sm font-mono focus:outline-none transition-all appearance-none cursor-pointer"
                            style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                          >
                            <option value="">Select...</option>
                            <option value="true">true</option>
                            <option value="false">false</option>
                          </select>
                        ) : isAddressParam(key) ? (
                          <AddressParamInput
                            paramKey={key}
                            value={params[key] ?? ''}
                            placeholder={value.value !== '_empty' ? String(value.value) : 'Select or enter address'}
                            onChange={(val) => handleParamChange(key, val)}
                            users={availableUsers}
                            usersLoading={usersLoading}
                          />
                        ) : (
                          <input
                            type="text"
                            value={params[key] ?? ''}
                            onChange={(e) => handleParamChange(key, e.target.value)}
                            placeholder={value.value !== '_empty' ? String(value.value) : value.type || '_empty'}
                            className="w-full px-3 py-2.5 text-sm font-mono focus:outline-none transition-all"
                            style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                          />
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Execute / Cancel button */}
                {execLoading ? (
                  <button
                    onClick={handleCancelExec}
                    className="w-full py-3 text-sm font-bold font-mono uppercase tracking-wider transition-all duration-150 flex items-center justify-center gap-2"
                    style={{
                      backgroundColor: 'transparent',
                      border: '1px solid #ef4444',
                      color: '#ef4444',
                    }}
                  >
                    <X className="w-3.5 h-3.5" />
                    <span>Cancel</span>
                  </button>
                ) : (
                  <button
                    onClick={() => handleExecute()}
                    disabled={!selectedFunction}
                    className="w-full py-3 text-sm font-bold font-mono uppercase tracking-wider transition-all duration-150 disabled:opacity-30 flex items-center justify-center gap-2"
                    style={{
                      backgroundColor: 'var(--bg-input)',
                      border: '1px solid var(--border-strong)',
                      color: 'var(--text-primary)',
                    }}
                  >
                    <Zap className="w-3.5 h-3.5" />
                    <span>Execute {selectedFunction}</span>
                  </button>
                )}

                {/* Error */}
                {error && (
                  <div style={{ border: '1px solid var(--border-color)' }}>
                    <div className="flex items-center justify-between px-4 py-2.5" style={{ borderBottom: '1px solid var(--border-color)' }}>
                      <span className="text-red-400 text-xs font-bold uppercase tracking-wider font-mono">error</span>
                      <CopyButton text={error} />
                    </div>
                    <pre className="text-red-400/80 text-sm font-mono overflow-x-auto whitespace-pre-wrap break-words max-h-96 overflow-y-auto p-4">
                      {error}
                    </pre>
                  </div>
                )}

                {/* Result / Output */}
                {result !== null && (
                  <div style={{ border: '1px solid var(--border-color)' }}>
                    <div className="flex items-center justify-between px-4 py-2.5" style={{
                      borderBottom: '1px solid var(--border-color)',
                      backgroundColor: 'var(--bg-input)',
                    }}>
                      <span className="text-xs font-bold uppercase tracking-wider font-mono" style={{ color: 'var(--text-secondary)' }}>Output</span>
                      <CopyButton text={JSON.stringify(result, null, 2)} />
                    </div>
                    {(() => {
                      const isBase64Image = typeof result === 'string' && result.startsWith('data:image/')
                      const isImageUrl = typeof result === 'string' && /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(result)
                      const hasImageField = typeof result === 'object' && result !== null &&
                        (result.image || result.url || result.data) &&
                        (typeof result.image === 'string' && (result.image.startsWith('data:image/') || /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(result.image)) ||
                         typeof result.url === 'string' && (result.url.startsWith('data:image/') || /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(result.url)) ||
                         typeof result.data === 'string' && (result.data.startsWith('data:image/') || /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(result.data)))
                      const imageSource = isBase64Image || isImageUrl ? result :
                                        hasImageField ? (result.image || result.url || result.data) : null

                      if (imageSource) {
                        return (
                          <div className="p-4 space-y-3">
                            <div className="overflow-hidden" style={{ backgroundColor: 'var(--bg-input)' }}>
                              <img src={imageSource as string} alt="Result" className="w-full h-auto" style={{ maxHeight: 'none' }} />
                            </div>
                          </div>
                        )
                      }
                      return (
                        <pre className="text-sm font-mono whitespace-pre-wrap break-all leading-relaxed p-4" style={{ color: 'var(--text-primary)' }}>
                          <code>{JSON.stringify(result, null, 2)}</code>
                        </pre>
                      )
                    })()}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-80"
                style={{ border: '1px dashed var(--border-color)', backgroundColor: 'var(--bg-input)' }}>
                <Zap className="w-5 h-5 mb-3" style={{ color: 'var(--text-tertiary)' }} />
                <span className="text-[12px] font-mono" style={{ color: 'var(--text-tertiary)' }}>Select a function to execute</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* === TASKS SECTION === */}
      {activeSection === 'tasks' && (
        <div>
          {/* Tasks header */}
          <div className="flex items-center justify-between mb-3">
            <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
              Previous tasks for {mod.name}
            </span>
            <button
              onClick={fetchTasks}
              disabled={tasksLoading}
              className="p-1.5 transition-all"
              style={{ color: 'var(--text-tertiary)' }}
              title="Refresh"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${tasksLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>

          {tasksLoading && tasks.length === 0 && (
            <div className="flex items-center justify-center py-12">
              <span className="animate-pulse text-lg font-bold" style={{ color: 'var(--text-tertiary)' }}>_</span>
            </div>
          )}

          {!tasksLoading && tasks.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12"
              style={{ border: '1px dashed var(--border-color)', backgroundColor: 'var(--bg-input)' }}>
              <Clock className="w-5 h-5 mb-3" style={{ color: 'var(--text-tertiary)' }} />
              <span className="text-[12px] font-mono" style={{ color: 'var(--text-tertiary)' }}>No tasks yet for {mod.name}</span>
              <span className="text-[10px] font-mono mt-1" style={{ color: 'var(--text-tertiary)' }}>Execute a function to see tasks here</span>
            </div>
          )}

          <div className="space-y-1">
            {tasks.map((task, idx) => {
              const isExpanded = expandedTask === idx
              const fnName = task.fn?.split('/').slice(1).join('/') || task.fn
              const hasResult = task.result !== undefined && task.result !== null
              const isRunning = task.status === 'pending' || task.status === 'running'
              const isCancelling = cancellingCid === task.cid
              const resultStr = hasResult ? JSON.stringify(task.result, null, 2) : ''
              return (
                <div key={task.cid || task.hash || idx}
                  style={{ border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-surface)' }}>
                  {/* Task row */}
                  <div
                    className="w-full text-left px-4 py-3 flex items-center gap-3 transition-all cursor-pointer"
                    onClick={() => setExpandedTask(isExpanded ? null : idx)}
                    style={{ backgroundColor: isExpanded ? 'var(--bg-input)' : 'transparent' }}
                  >
                    {/* Status */}
                    <span className="text-[12px] font-bold shrink-0" style={{ color: getStatusColor(task.status) }}>
                      {getStatusLabel(task.status)}
                    </span>

                    {/* Function name */}
                    <span className="text-[12px] font-mono font-semibold shrink-0" style={{ color: 'var(--text-primary)' }}>
                      {fnName}
                    </span>

                    {/* Result preview */}
                    {hasResult && (
                      <span className="text-[11px] font-mono truncate flex-1 min-w-0" style={{ color: 'var(--text-secondary)' }}>
                        {typeof task.result === 'string' ? task.result : JSON.stringify(task.result)}
                      </span>
                    )}
                    {!hasResult && <span className="flex-1" />}

                    {/* Cancel button for running/pending tasks */}
                    {isRunning && task.cid && (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleCancelTask(task.cid!) }}
                        disabled={isCancelling}
                        className="shrink-0 flex items-center gap-1 px-2 py-1 text-[10px] font-bold font-mono uppercase tracking-wider transition-all"
                        style={{
                          border: '1px solid #ef4444',
                          color: isCancelling ? 'var(--text-tertiary)' : '#ef4444',
                          backgroundColor: 'transparent',
                        }}
                        title="Cancel task"
                      >
                        <X className="w-3 h-3" />
                        {isCancelling ? 'Cancelling...' : 'Cancel'}
                      </button>
                    )}

                    {/* Time */}
                    <span className="text-[10px] font-mono shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                      {formatTime(task.time)}
                    </span>

                    {/* Delta */}
                    {task.delta !== undefined && (
                      <span className="text-[10px] font-mono shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                        {task.delta.toFixed(1)}s
                      </span>
                    )}

                    {/* Expand chevron */}
                    {isExpanded
                      ? <ChevronUp className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                      : <ChevronDown className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                    }
                  </div>

                  {/* Expanded details */}
                  {isExpanded && (
                    <div className="px-4 pb-4 space-y-3" style={{ borderTop: '1px solid var(--border-color)' }}>
                      {/* Params */}
                      {task.params && Object.keys(task.params).length > 0 && (
                        <div className="pt-3">
                          <span className="text-[10px] font-bold uppercase tracking-wider block mb-1.5" style={{ color: 'var(--text-tertiary)' }}>Params</span>
                          <pre className="text-[11px] font-mono p-3 overflow-x-auto" style={{ backgroundColor: 'var(--bg-input)', color: 'var(--text-secondary)' }}>
                            <code>{JSON.stringify(task.params, null, 2)}</code>
                          </pre>
                        </div>
                      )}

                      {/* Result */}
                      {hasResult && (
                        <div>
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Result</span>
                            <CopyButton text={resultStr} />
                          </div>
                          <pre className="text-[11px] font-mono p-3 overflow-x-auto max-h-64 overflow-y-auto" style={{ backgroundColor: 'var(--bg-input)', color: 'var(--text-primary)' }}>
                            <code>{resultStr}</code>
                          </pre>
                        </div>
                      )}

                      {/* Actions: Replay, Re-run, Copy */}
                      <div className="flex items-center gap-2 pt-1">
                        <button
                          onClick={(e) => { e.stopPropagation(); handleReplayAndRun(task) }}
                          className="flex items-center gap-1 px-2.5 py-1.5 text-[10px] font-bold font-mono uppercase tracking-wider transition-all"
                          style={{
                            border: '1px solid var(--border-strong)',
                            color: 'var(--text-primary)',
                            backgroundColor: 'var(--bg-input)',
                          }}
                          title="Re-run this task with same params"
                        >
                          <Play className="w-3 h-3" />
                          Re-run
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleReplayTask(task) }}
                          className="flex items-center gap-1 px-2.5 py-1.5 text-[10px] font-bold font-mono uppercase tracking-wider transition-all"
                          style={{
                            border: '1px solid var(--border-color)',
                            color: 'var(--text-secondary)',
                            backgroundColor: 'transparent',
                          }}
                          title="Load this task into API panel to edit params"
                        >
                          <RefreshCw className="w-3 h-3" />
                          Replay
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleCopyTask(task) }}
                          className="flex items-center gap-1 px-2.5 py-1.5 text-[10px] font-bold font-mono uppercase tracking-wider transition-all"
                          style={{
                            border: '1px solid var(--border-color)',
                            color: 'var(--text-secondary)',
                            backgroundColor: 'transparent',
                          }}
                          title="Copy task fn + params to clipboard"
                        >
                          <Copy className="w-3 h-3" />
                          Copy
                        </button>
                        {task.params && Object.keys(task.params).length > 0 && (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleCopyInputs(task) }}
                            className="flex items-center gap-1 px-2.5 py-1.5 text-[10px] font-bold font-mono uppercase tracking-wider transition-all"
                            style={{
                              border: '1px solid var(--border-color)',
                              color: 'var(--text-secondary)',
                              backgroundColor: 'transparent',
                            }}
                            title="Copy input params to clipboard"
                          >
                            <Clipboard className="w-3 h-3" />
                            Copy Inputs
                          </button>
                        )}
                      </div>

                      {/* CID */}
                      {task.cid && (
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>cid:</span>
                          <code className="text-[10px] font-mono" style={{ color: 'var(--text-secondary)' }}>{task.cid}</code>
                          <CopyButton text={task.cid} />
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <style jsx>{`
        .mod-scroll {
          scrollbar-width: thin;
          scrollbar-color: var(--scrollbar-thumb) transparent;
        }
        .mod-scroll::-webkit-scrollbar {
          width: 6px;
        }
        .mod-scroll::-webkit-scrollbar-thumb {
          background: var(--scrollbar-thumb);
          border-radius: 3px;
        }
        .mod-scroll::-webkit-scrollbar-track {
          background: transparent;
        }
      `}</style>
    </div>
  )
}
