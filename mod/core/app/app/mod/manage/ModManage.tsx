"use client"

import { useState, useEffect, useCallback } from 'react'
import { userContext } from '@/context'
import { ModuleType } from '@/types'
import { text2color, shorten } from '@/utils'
import Link from 'next/link'
import { clearModsCache } from '@/mod/explore/ModExplorePage'

interface AppStatus {
  port: number
  owner: string
  path: string
  running: boolean
  url: string
}

interface LogsData {
  [key: string]: string
}

interface WorkerStatus {
  active_workers: number
  max_workers: number
  active_cids: string[]
}

interface ModManageProps {
  mod?: ModuleType
  moduleColor?: string
}

const FONT = "var(--font-digital), monospace"

export default function ModManage({ mod, moduleColor }: ModManageProps) {
  const { client, user } = userContext()
  const [apps, setApps] = useState<Record<string, AppStatus>>({})
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null)

  // Module management state
  const [myMods, setMyMods] = useState<ModuleType[]>([])
  const [modsLoading, setModsLoading] = useState(true)
  const [modActionLoading, setModActionLoading] = useState<string | null>(null)
  const [forkName, setForkName] = useState('')
  const [showForkInput, setShowForkInput] = useState(false)
  const [showCreateInput, setShowCreateInput] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDescription, setCreateDescription] = useState('')

  // Worker state
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null)
  const [workerActionLoading, setWorkerActionLoading] = useState<string | null>(null)

  const fetchMyMods = useCallback(async () => {
    if (!client || !user?.key) return
    try {
      const result = await client.call('mods', { key: user.key })
      if (Array.isArray(result)) {
        setMyMods(result)
      }
    } catch {
      // ignore
    } finally {
      setModsLoading(false)
    }
  }, [client, user?.key])

  const fetchApps = useCallback(async () => {
    if (!client) return
    try {
      const result = await client.call('app_status')
      if (result && typeof result === 'object' && !result.error) {
        setApps(result)
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [client])

  const fetchWorkers = useCallback(async () => {
    if (!client) return
    try {
      const result = await client.call('workers')
      if (result && typeof result === 'object' && !result.error) {
        setWorkerStatus(result as WorkerStatus)
      }
    } catch {
      // ignore
    }
  }, [client])

  useEffect(() => {
    fetchApps()
    fetchWorkers()
    fetchMyMods()
    const interval = setInterval(() => { fetchApps(); fetchWorkers() }, 5000)
    return () => clearInterval(interval)
  }, [fetchApps, fetchWorkers, fetchMyMods])

  // --- Module operations ---

  const handleFork = async (sourceMod: string) => {
    if (!client || !user?.key) return
    const targetName = forkName.trim() || sourceMod
    setModActionLoading(`fork:${sourceMod}`)
    setMessage(null)
    try {
      const result = await client.call('fork', {
        mod: sourceMod,
        key: user.key,
        comment: `forked from ${sourceMod}`,
      })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `Forked ${sourceMod} successfully`, type: 'success' })
        setShowForkInput(false)
        setForkName('')
        clearModsCache()
        await fetchMyMods()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to fork module', type: 'error' })
    } finally {
      setModActionLoading(null)
    }
  }

  const handleDeleteMod = async (modName: string) => {
    if (!client || !user?.key) return
    if (!confirm(`Delete module "${modName}"? This cannot be undone.`)) return
    setModActionLoading(`delete:${modName}`)
    setMessage(null)
    try {
      const result = await client.call('rm_mod', {
        mod: modName,
        key: user.key,
      })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${modName} deleted`, type: 'success' })
        clearModsCache()
        await fetchMyMods()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to delete module', type: 'error' })
    } finally {
      setModActionLoading(null)
    }
  }

  const handleCreateMod = async () => {
    if (!client || !user?.key || !createName.trim()) return
    setModActionLoading('__create')
    setMessage(null)
    try {
      const result = await client.call('reg', {
        mod: createName.trim(),
        name: createName.trim(),
        key: user.key,
        local: true,
        description: createDescription.trim() || undefined,
        public: false,
        token: client.token,
      }, true, {}, 120000)
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${createName.trim()} created`, type: 'success' })
        setShowCreateInput(false)
        setCreateName('')
        setCreateDescription('')
        clearModsCache()
        await fetchMyMods()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to create module', type: 'error' })
    } finally {
      setModActionLoading(null)
    }
  }

  // --- App operations ---

  const handleKillWorker = async (cid: string) => {
    if (!client) return
    setWorkerActionLoading(cid)
    try {
      const result = await client.call('kill_worker', { cid })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `Worker ${cid.slice(0, 12)}... killed`, type: 'success' })
        await fetchWorkers()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to kill worker', type: 'error' })
    } finally {
      setWorkerActionLoading(null)
    }
  }

  const handleKillAllWorkers = async () => {
    if (!client) return
    setWorkerActionLoading('__all')
    try {
      const result = await client.call('kill_all_workers')
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: 'All workers killed', type: 'success' })
        await fetchWorkers()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to kill workers', type: 'error' })
    } finally {
      setWorkerActionLoading(null)
    }
  }

  const isOwner = (app: AppStatus) => {
    return user?.key && app.owner && user.key.toLowerCase() === app.owner.toLowerCase()
  }

  const handleStart = async (name: string) => {
    if (!client) return
    setActionLoading(name)
    setMessage(null)
    try {
      const result = await client.call('serve_app', { name })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${name} started`, type: 'success' })
        await fetchApps()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to start', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  const handleStop = async (name: string) => {
    if (!client) return
    setActionLoading(name)
    setMessage(null)
    try {
      const result = await client.call('kill_app', { name })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${name} stopped`, type: 'success' })
        await fetchApps()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to stop', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  const handleRemove = async (name: string) => {
    if (!client) return
    setActionLoading(name)
    setMessage(null)
    try {
      const result = await client.call('remove_app', { name })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${name} removed`, type: 'success' })
        await fetchApps()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to remove', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  const handleAdd = async () => {
    if (!client) return
    const name = prompt('Module app name:')
    if (!name) return
    setActionLoading('__add')
    setMessage(null)
    try {
      const result = await client.call('new_app', { name })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${name} created and serving on port ${result.port}`, type: 'success' })
        await fetchApps()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to create', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  const entries = Object.entries(apps)
  const myApps = entries.filter(([, app]) => isOwner(app))
  const otherApps = entries.filter(([, app]) => !isOwner(app))

  if (loading && modsLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <span className="animate-pulse text-lg font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)', fontFamily: FONT }}>
          LOADING...
        </span>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto py-4" style={{ fontFamily: FONT }}>

      {/* Status message */}
      {message && (
        <div
          className="mb-4 px-4 py-2 border-2"
          style={{
            borderColor: message.type === 'error' ? '#ef4444' : '#10b981',
            color: message.type === 'error' ? '#ef4444' : '#10b981',
            background: message.type === 'error' ? 'rgba(239,68,68,0.1)' : 'rgba(16,185,129,0.1)',
          }}
        >
          {message.text}
        </div>
      )}

      {/* ====== MODULE OPERATIONS (when viewing a specific module) ====== */}
      {mod && (
        <div className="mb-8">
          <h3 className="text-sm font-bold uppercase tracking-wider mb-3" style={{ color: 'var(--text-secondary)' }}>
            Module: <span style={{ color: moduleColor || 'var(--text-primary)' }}>{mod.name}</span>
          </h3>

          <div className="border-2 p-4 space-y-3" style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}>
            {/* Module info row */}
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                Owner: {shorten(mod.key)}
              </span>
              {mod.cid && (
                <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                  CID: {mod.cid.slice(0, 16)}...
                </span>
              )}
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-2 flex-wrap">
              {/* Fork */}
              <ActionButton
                label={showForkInput ? 'CANCEL' : 'FORK'}
                color="#a78bfa"
                loading={modActionLoading?.startsWith('fork:') || false}
                onClick={() => { setShowForkInput(!showForkInput); setForkName('') }}
              />

              {/* Delete (only if user owns) */}
              {user?.key && mod.key === user.key && (
                <ActionButton
                  label="DELETE"
                  color="#ef4444"
                  loading={modActionLoading === `delete:${mod.name}`}
                  onClick={() => handleDeleteMod(mod.name)}
                />
              )}
            </div>

            {/* Fork input */}
            {showForkInput && (
              <div className="flex items-center gap-2 pt-1">
                <span className="text-xs font-bold uppercase tracking-wider shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                  Fork as:
                </span>
                <input
                  type="text"
                  value={forkName}
                  onChange={(e) => setForkName(e.target.value)}
                  placeholder={mod.name}
                  className="flex-1 px-3 py-1.5 text-xs font-bold focus:outline-none uppercase border-2"
                  style={{
                    background: 'var(--bg-primary)',
                    borderColor: 'var(--border-strong)',
                    color: 'var(--text-primary)',
                    fontFamily: FONT,
                  }}
                />
                <ActionButton
                  label="CONFIRM FORK"
                  color="#a78bfa"
                  loading={modActionLoading?.startsWith('fork:') || false}
                  onClick={() => handleFork(mod.name)}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {/* ====== MY MODULES ====== */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
            My Modules
            <span className="ml-2 text-xs" style={{ color: 'var(--text-tertiary)' }}>
              {myMods.length}
            </span>
          </h3>
          <div className="flex items-center gap-2">
            <ActionButton
              label={showCreateInput ? 'CANCEL' : '+ CREATE'}
              color="var(--accent-primary, #00aaff)"
              loading={false}
              onClick={() => { setShowCreateInput(!showCreateInput); setCreateName(''); setCreateDescription('') }}
            />
            <Link href="/create">
              <ActionButton
                label="+ IMPORT"
                color="#a78bfa"
                loading={false}
                onClick={() => {}}
              />
            </Link>
          </div>
        </div>

        {/* Inline create form */}
        {showCreateInput && (
          <div className="border-2 p-4 mb-3 space-y-3" style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                Name:
              </span>
              <input
                type="text"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="MY-MODULE"
                className="flex-1 px-3 py-1.5 text-xs font-bold focus:outline-none uppercase border-2"
                style={{
                  background: 'var(--bg-primary)',
                  borderColor: 'var(--border-strong)',
                  color: 'var(--text-primary)',
                  fontFamily: FONT,
                }}
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                Desc:
              </span>
              <input
                type="text"
                value={createDescription}
                onChange={(e) => setCreateDescription(e.target.value)}
                placeholder="OPTIONAL DESCRIPTION"
                className="flex-1 px-3 py-1.5 text-xs font-bold focus:outline-none uppercase border-2"
                style={{
                  background: 'var(--bg-primary)',
                  borderColor: 'var(--border-strong)',
                  color: 'var(--text-primary)',
                  fontFamily: FONT,
                }}
              />
            </div>
            <div className="flex justify-end">
              <ActionButton
                label="CREATE MODULE"
                color="#10b981"
                loading={modActionLoading === '__create'}
                onClick={handleCreateMod}
              />
            </div>
          </div>
        )}

        {modsLoading && (
          <span className="text-xs animate-pulse font-bold uppercase" style={{ color: 'var(--text-tertiary)' }}>Loading modules...</span>
        )}

        {!modsLoading && myMods.length === 0 && (
          <div
            className="border-2 px-4 py-6 text-center"
            style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}
          >
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
              No modules yet — create or import one
            </span>
          </div>
        )}

        {myMods.length > 0 && (
          <div className="space-y-1">
            {myMods.map((m) => (
              <ModRow
                key={`${m.name}-${m.key}`}
                mod={m}
                actionLoading={modActionLoading}
                onFork={(name) => handleFork(name)}
                onDelete={(name) => handleDeleteMod(name)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ====== MODULE APPS ====== */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
            Module Apps
          </h2>
          <ActionButton
            label={actionLoading === '__add' ? 'CREATING...' : '+ ADD APP'}
            color="var(--accent-primary, #00aaff)"
            loading={actionLoading === '__add'}
            onClick={handleAdd}
          />
        </div>

        {/* My apps */}
        {myApps.length > 0 && (
          <div className="mb-4">
            <div className="space-y-2">
              {myApps.map(([name, app]) => (
                <AppRow
                  key={name}
                  name={name}
                  app={app}
                  owned
                  actionLoading={actionLoading}
                  onStart={handleStart}
                  onStop={handleStop}
                  onRemove={handleRemove}
                  client={client}
                />
              ))}
            </div>
          </div>
        )}

        {/* Other apps */}
        {otherApps.length > 0 && (
          <div className="mb-4">
            <h3 className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--text-tertiary)' }}>
              Other Apps
            </h3>
            <div className="space-y-2">
              {otherApps.map(([name, app]) => (
                <AppRow
                  key={name}
                  name={name}
                  app={app}
                  owned={false}
                  actionLoading={actionLoading}
                  onStart={handleStart}
                  onStop={handleStop}
                  onRemove={handleRemove}
                  client={client}
                />
              ))}
            </div>
          </div>
        )}

        {entries.length === 0 && (
          <div className="border-2 px-4 py-6 text-center" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}>
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
              No module apps installed
            </span>
          </div>
        )}
      </div>

      {/* ====== SANDBOX WORKERS ====== */}
      {workerStatus && (
        <div className="mb-8">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
              Sandbox Workers
              <span className="ml-2" style={{ color: workerStatus.active_workers > 0 ? '#10b981' : 'var(--text-tertiary)' }}>
                {workerStatus.active_workers}/{workerStatus.max_workers}
              </span>
            </h3>
            {workerStatus.active_workers > 0 && (
              <ActionButton
                label="KILL ALL"
                color="#ef4444"
                loading={workerActionLoading === '__all'}
                onClick={() => {
                  if (confirm('Kill all active workers?')) handleKillAllWorkers()
                }}
              />
            )}
          </div>

          {workerStatus.active_workers === 0 && (
            <div
              className="border-2 px-4 py-6 text-center"
              style={{
                borderColor: 'var(--border-color)',
                background: 'var(--bg-secondary)',
              }}
            >
              <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                No active workers
              </span>
            </div>
          )}

          {workerStatus.active_cids?.length > 0 && (
            <div className="space-y-1">
              {workerStatus.active_cids.map((cid) => (
                <div
                  key={cid}
                  className="border-2 flex items-center gap-3 px-4 py-2.5"
                  style={{
                    borderColor: 'var(--border-strong)',
                    background: 'var(--bg-secondary)',
                  }}
                >
                  <div
                    className="w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ background: '#f59e0b', boxShadow: '0 0 8px #f59e0b' }}
                  />
                  <code className="flex-1 text-xs font-mono truncate" style={{ color: 'var(--text-primary)' }}>
                    {cid}
                  </code>
                  <span className="text-xs font-bold uppercase tracking-wider shrink-0" style={{ color: '#f59e0b' }}>
                    RUNNING
                  </span>
                  <ActionButton
                    label="KILL"
                    color="#ef4444"
                    loading={workerActionLoading === cid}
                    onClick={() => handleKillWorker(cid)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// --- Module row for "My Modules" list ---

function ModRow({
  mod, actionLoading, onFork, onDelete,
}: {
  mod: ModuleType
  actionLoading: string | null
  onFork: (name: string) => void
  onDelete: (name: string) => void
}) {
  const color = text2color(mod.name)
  const updated = mod.updated || mod.created
  const timeStr = updated ? new Date(updated * 1000).toLocaleDateString() : ''

  return (
    <div
      className="border-2 flex items-center gap-3 px-4 py-2.5"
      style={{
        borderColor: 'var(--border-strong)',
        background: 'var(--bg-secondary)',
        fontFamily: FONT,
      }}
    >
      {/* Color dot */}
      <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />

      {/* Name (link to module) */}
      <Link href={`/mod/${mod.name}`} className="flex-1 min-w-0 truncate">
        <span className="font-bold uppercase tracking-wider hover:underline" style={{ color }}>
          {mod.name}
        </span>
      </Link>

      {/* Timestamp */}
      {timeStr && (
        <span className="text-xs shrink-0" style={{ color: 'var(--text-tertiary)' }}>
          {timeStr}
        </span>
      )}

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">
        <ActionButton
          label="FORK"
          color="#a78bfa"
          loading={actionLoading === `fork:${mod.name}`}
          onClick={() => onFork(mod.name)}
        />
        <ActionButton
          label="DELETE"
          color="#ef4444"
          loading={actionLoading === `delete:${mod.name}`}
          onClick={() => onDelete(mod.name)}
        />
      </div>
    </div>
  )
}

// --- App row (existing) ---

function AppRow({
  name, app, owned, actionLoading, onStart, onStop, onRemove, client,
}: {
  name: string
  app: AppStatus
  owned: boolean
  actionLoading: string | null
  onStart: (name: string) => void
  onStop: (name: string) => void
  onRemove: (name: string) => void
  client: any
}) {
  const color = text2color(name)
  const isLoading = actionLoading === name
  const [showLogs, setShowLogs] = useState(false)
  const [logs, setLogs] = useState<LogsData | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const [logsError, setLogsError] = useState<string | null>(null)

  const fetchLogs = useCallback(async () => {
    if (!client) return
    setLogsLoading(true)
    setLogsError(null)
    try {
      const result = await client.call('app_logs', { name, lines: 200 })
      if (result?.error) {
        setLogsError(result.error)
        setLogs(null)
      } else {
        setLogs(result)
      }
    } catch (e: any) {
      setLogsError(e?.message || 'Failed to fetch logs')
    } finally {
      setLogsLoading(false)
    }
  }, [client, name])

  useEffect(() => {
    if (!showLogs) return
    fetchLogs()
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [showLogs, fetchLogs])

  return (
    <div
      className="border-2 transition-all"
      style={{
        borderColor: 'var(--border-strong)',
        background: 'var(--bg-secondary)',
        fontFamily: FONT,
      }}
    >
      <div className="flex items-center gap-4 px-4 py-3">
        <div
          className="w-3 h-3 rounded-full shrink-0"
          style={{
            background: app.running ? '#10b981' : '#6b7280',
            boxShadow: app.running ? '0 0 8px #10b981' : 'none',
          }}
        />
        <div className="flex-1 min-w-0">
          <span className="font-bold uppercase tracking-wider" style={{ color }}>
            {name}
          </span>
          {app.port > 0 && (
            <span className="ml-2 text-xs" style={{ color: 'var(--text-tertiary)' }}>
              :{app.port}
            </span>
          )}
          {app.owner && (
            <span className="ml-2 text-xs" style={{ color: 'var(--text-tertiary)' }}>
              {shorten(app.owner)}
            </span>
          )}
        </div>
        <span
          className="text-xs font-bold uppercase tracking-wider"
          style={{ color: app.running ? '#10b981' : '#6b7280' }}
        >
          {app.running ? 'RUNNING' : 'STOPPED'}
        </span>
        <ActionButton
          label={showLogs ? 'HIDE LOGS' : 'LOGS'}
          color="var(--accent-primary, #00aaff)"
          loading={false}
          onClick={() => setShowLogs(!showLogs)}
        />
        {owned && (
          <div className="flex items-center gap-2">
            {app.running ? (
              <ActionButton
                label="STOP"
                color="#ef4444"
                loading={isLoading}
                onClick={() => onStop(name)}
              />
            ) : (
              <ActionButton
                label="START"
                color="#10b981"
                loading={isLoading}
                onClick={() => onStart(name)}
              />
            )}
            <ActionButton
              label="REMOVE"
              color="#6b7280"
              loading={isLoading}
              onClick={() => {
                if (confirm(`Remove ${name}? This will stop the server and unregister it.`)) {
                  onRemove(name)
                }
              }}
            />
          </div>
        )}
      </div>

      {showLogs && (
        <div
          className="border-t-2 px-4 py-3"
          style={{ borderColor: 'var(--border-strong)' }}
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
              Logs
            </span>
            <ActionButton
              label="REFRESH"
              color="var(--text-secondary, #888)"
              loading={logsLoading}
              onClick={fetchLogs}
            />
          </div>
          {logsLoading && !logs && (
            <span className="text-xs animate-pulse" style={{ color: 'var(--text-tertiary)' }}>Loading logs...</span>
          )}
          {logsError && (
            <span className="text-xs" style={{ color: '#6b7280' }}>{logsError}</span>
          )}
          {logs && Object.entries(logs).map(([logKey, logContent]) => (
            <div key={logKey} className="mb-3">
              <div className="text-xs font-bold uppercase tracking-wider mb-1" style={{ color: logKey.includes('error') ? '#ef4444' : 'var(--accent-primary, #00aaff)' }}>
                {logKey.replace(/_/g, ' ')}
              </div>
              <pre
                className="text-xs overflow-auto max-h-64 p-2 border"
                style={{
                  color: 'var(--text-primary)',
                  background: 'var(--bg-primary)',
                  borderColor: 'var(--border-color)',
                  fontFamily: 'var(--font-code, monospace)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {logContent}
              </pre>
            </div>
          ))}
          {logs && Object.keys(logs).length === 0 && (
            <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>No logs available</span>
          )}
        </div>
      )}
    </div>
  )
}

function ActionButton({
  label, color, loading, onClick,
}: {
  label: string
  color: string
  loading: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="px-3 py-1 text-xs font-bold uppercase tracking-wider transition-all"
      style={{
        border: `1px solid ${color}`,
        color,
        background: 'transparent',
        fontFamily: FONT,
        opacity: loading ? 0.5 : 1,
        cursor: loading ? 'wait' : 'pointer',
      }}
      onMouseEnter={e => { if (!loading) { e.currentTarget.style.background = color; e.currentTarget.style.color = 'var(--bg-primary)' } }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = color }}
    >
      {loading ? '...' : label}
    </button>
  )
}
