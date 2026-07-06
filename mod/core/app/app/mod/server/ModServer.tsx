"use client"

import { useState, useEffect, useCallback, useRef } from 'react'
import { userContext } from '@/context'
import { ModuleType } from '@/types'
import { text2color, shorten, getModApiUrl, getModAppUrl } from '@/utils'

interface ModServerProps {
  mod?: ModuleType
  moduleColor?: string
}

interface ServerEntry {
  name: string
  address: string
}

interface WorkerStatus {
  active_workers: number
  max_workers: number
  active_cids: string[]
}

interface TaskInfo {
  fn: string
  status: string
  cid: string
  time: string
  delta?: number
  key?: string
  params?: any
  result?: any
}

interface UserUsage {
  user: string
  requests: number
  errors: number
  total_duration: number
  cost: number
  first_seen: number
  last_seen: number
}

interface UserBill {
  user: string
  requests: number
  compute_seconds: number
  base_cost: number
  margin: number
  margin_amount: number
  total: number
  currency: string
}

interface MeterConfig {
  rate_per_second: number
  rate_per_request: number
  margin: number
  currency: string
}

interface ReplicaInfo {
  name: string
  address: string
  live: boolean
}

interface ReplicaGroup {
  mod: string
  strategy: string
  replicas: ReplicaInfo[]
}

type Mode = 'logs' | 'interact' | 'compute' | 'meter' | 'replicas'

interface ModuleServerStatus {
  api: { running: boolean; url: string | null; port: number | null; healthy: boolean }
  app: { running: boolean; url: string | null; port: number | null; healthy: boolean }
}

const FONT = "var(--font-digital), monospace"

export default function ModServer({ mod, moduleColor }: ModServerProps) {
  const { client, user } = userContext()
  const [mode, setMode] = useState<Mode>('logs')
  const [servers, setServers] = useState<ServerEntry[]>([])
  const [selectedServer, setSelectedServer] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Logs state
  const [logs, setLogs] = useState<Record<string, string> | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const logsRef = useRef<HTMLPreElement>(null)

  // Interact state
  const [serverInfo, setServerInfo] = useState<any>(null)
  const [selectedFn, setSelectedFn] = useState<string | null>(null)
  const [fnParams, setFnParams] = useState('')
  const [fnResult, setFnResult] = useState<any>(null)
  const [fnError, setFnError] = useState<string | null>(null)
  const [fnLoading, setFnLoading] = useState(false)

  // Compute state
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null)
  const [tasks, setTasks] = useState<TaskInfo[]>([])
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null)

  // Meter state
  const [meterUsage, setMeterUsage] = useState<UserUsage[]>([])
  const [meterBills, setMeterBills] = useState<UserBill[]>([])
  const [meterConfig, setMeterConfig] = useState<MeterConfig | null>(null)
  const [meterRecent, setMeterRecent] = useState<any[]>([])
  const [editMargin, setEditMargin] = useState('')
  const [editRatePerSec, setEditRatePerSec] = useState('')
  const [editRatePerReq, setEditRatePerReq] = useState('')

  // Paywall state
  const [paywallStatus, setPaywallStatus] = useState<any>(null)
  const [paywallReceiver, setPaywallReceiver] = useState('')
  const [paywallPrice, setPaywallPrice] = useState('0.01')
  const [paywallCurrency, setPaywallCurrency] = useState('USDC')
  const [paywallNetwork, setPaywallNetwork] = useState('base')

  // Replicas state
  const [replicaGroups, setReplicaGroups] = useState<string[]>([])
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)
  const [groupInfo, setGroupInfo] = useState<ReplicaGroup | null>(null)
  const [deployMod, setDeployMod] = useState('')
  const [deployCount, setDeployCount] = useState('3')
  const [deployLoading, setDeployLoading] = useState(false)

  // Module server management state
  const [modServerStatus, setModServerStatus] = useState<ModuleServerStatus>({
    api: { running: false, url: null, port: null, healthy: false },
    app: { running: false, url: null, port: null, healthy: false },
  })
  const [modServerLoading, setModServerLoading] = useState<string | null>(null)
  const [modServerLogs, setModServerLogs] = useState<{ api: string | null; app: string | null }>({ api: null, app: null })
  const [showModLogs, setShowModLogs] = useState<'api' | 'app' | null>(null)
  const [serveApiPort, setServeApiPort] = useState('')
  const [serveAppPort, setServeAppPort] = useState('')

  // Check module server health
  const checkModuleHealth = useCallback(async () => {
    if (!mod) return
    const apiUrl = getModApiUrl(mod)
    const appUrl = getModAppUrl(mod)

    const apiPort = apiUrl ? parseInt(new URL(apiUrl).port || '0') : null
    const appPort = appUrl ? parseInt(new URL(appUrl).port || '0') : null

    let apiHealthy = false
    let appHealthy = false

    if (apiUrl) {
      try {
        const resp = await fetch(apiUrl + '/health', { signal: AbortSignal.timeout(3000) })
        apiHealthy = resp.ok
      } catch { }
    }
    if (appUrl) {
      try {
        const resp = await fetch(appUrl, { signal: AbortSignal.timeout(3000) })
        appHealthy = resp.ok
      } catch { }
    }

    setModServerStatus({
      api: { running: apiHealthy, url: apiUrl || null, port: apiPort, healthy: apiHealthy },
      app: { running: appHealthy, url: appUrl || null, port: appPort, healthy: appHealthy },
    })
  }, [mod])

  // Fetch module server logs
  const fetchModServerLogs = useCallback(async (type: 'api' | 'app') => {
    if (!client || !mod) return
    try {
      const result = await client.call('app_logs', { name: mod.name, lines: 200 })
      if (result && !result.error) {
        if (type === 'api') {
          setModServerLogs(prev => ({ ...prev, api: typeof result === 'string' ? result : (result.stdout || result.api || JSON.stringify(result, null, 2)) }))
        } else {
          setModServerLogs(prev => ({ ...prev, app: typeof result === 'string' ? result : (result.app || result.stdout || JSON.stringify(result, null, 2)) }))
        }
      }
    } catch { }
  }, [client, mod])

  // Serve module (API + App)
  const handleModServe = useCallback(async (type: 'both' | 'api' | 'app') => {
    if (!client || !mod) return
    setModServerLoading(`serve:${type}`)
    setMessage(null)
    try {
      const params: any = { name: mod.name }
      if (serveApiPort) params.api_port = parseInt(serveApiPort)
      if (serveAppPort) params.port = parseInt(serveAppPort)
      const result = await client.call('serve_app', params)
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${mod.name} started`, type: 'success' })
        // Optimistically show "STARTING" while servers boot
        const apiUrl = getModApiUrl(mod)
        const appUrl = getModAppUrl(mod)
        if (apiUrl || appUrl) {
          setModServerStatus(prev => ({
            api: apiUrl ? { ...prev.api, running: true, url: apiUrl, healthy: false } : prev.api,
            app: appUrl ? { ...prev.app, running: true, url: appUrl, healthy: false } : prev.app,
          }))
        }
        setTimeout(checkModuleHealth, 2000)
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to start', type: 'error' })
    } finally {
      setModServerLoading(null)
    }
  }, [client, mod, serveApiPort, serveAppPort, checkModuleHealth])

  // Kill module server
  const handleModKill = useCallback(async (type: 'both' | 'api' | 'app') => {
    if (!client || !mod) return
    setModServerLoading(`kill:${type}`)
    setMessage(null)
    try {
      const result = await client.call('kill_app', { name: mod.name })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${mod.name} stopped`, type: 'success' })
        setTimeout(checkModuleHealth, 1000)
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to stop', type: 'error' })
    } finally {
      setModServerLoading(null)
    }
  }, [client, mod, checkModuleHealth])

  // Poll module health
  useEffect(() => {
    if (!mod) return
    checkModuleHealth()
    const interval = setInterval(checkModuleHealth, 5000)
    return () => clearInterval(interval)
  }, [checkModuleHealth])

  // Poll logs when viewing
  useEffect(() => {
    if (!showModLogs) return
    fetchModServerLogs(showModLogs)
    const interval = setInterval(() => fetchModServerLogs(showModLogs), 4000)
    return () => clearInterval(interval)
  }, [showModLogs, fetchModServerLogs])

  // Fetch server list
  const fetchServers = useCallback(async () => {
    if (!client) return
    try {
      const result = await client.call('namespace')
      if (result && typeof result === 'object' && !result.error) {
        const entries: ServerEntry[] = Object.entries(result).map(([name, address]) => ({
          name,
          address: address as string,
        }))
        setServers(entries)
        if (!selectedServer && entries.length > 0) {
          setSelectedServer(entries[0].name)
        }
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [client, selectedServer])

  // Fetch logs for selected server
  const fetchLogs = useCallback(async () => {
    if (!client || !selectedServer) return
    setLogsLoading(true)
    try {
      const result = await client.call('logs', { name: selectedServer, lines: 300 })
      if (result && !result.error) {
        if (typeof result === 'string') {
          setLogs({ stdout: result })
        } else {
          setLogs(result)
        }
      }
    } catch {
      setLogs({ error: 'Failed to fetch logs' })
    } finally {
      setLogsLoading(false)
    }
  }, [client, selectedServer])

  // Fetch server info for interact mode
  const fetchServerInfo = useCallback(async () => {
    if (!client || !selectedServer) return
    try {
      const server = servers.find(s => s.name === selectedServer)
      if (!server) return
      const result = await client.call(selectedServer + '/info', {})
      if (result && !result.error) {
        setServerInfo(result)
        if (result.schema) {
          const fns = Object.keys(result.schema)
          if (fns.length > 0 && !selectedFn) {
            setSelectedFn(fns[0])
          }
        }
      }
    } catch {
      setServerInfo(null)
    }
  }, [client, selectedServer, servers, selectedFn])

  // Fetch worker status
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

  // Fetch tasks
  const fetchTasks = useCallback(async () => {
    if (!client) return
    try {
      const result = await client.call('txs', { df: 0, n: 20, page: 0 })
      if (Array.isArray(result)) {
        setTasks(result)
      }
    } catch {
      // ignore
    }
  }, [client])

  // Fetch meter data
  const fetchMeter = useCallback(async () => {
    if (!client) return
    try {
      const [usage, bills, config, recent] = await Promise.all([
        client.call('meter/usage_all', {}),
        client.call('meter/bill_all', {}),
        client.call('meter/config', {}),
        client.call('meter/recent', { n: 50 }),
      ])
      if (Array.isArray(usage)) setMeterUsage(usage)
      if (Array.isArray(bills)) setMeterBills(bills)
      if (config && !config.error) {
        setMeterConfig(config)
        if (!editMargin) setEditMargin(String(config.margin ?? 0))
        if (!editRatePerSec) setEditRatePerSec(String(config.rate_per_second ?? 0.001))
        if (!editRatePerReq) setEditRatePerReq(String(config.rate_per_request ?? 0.0001))
      }
      if (Array.isArray(recent)) setMeterRecent(recent)
    } catch {
      // ignore
    }
  }, [client])

  // Fetch paywall status
  const fetchPaywall = useCallback(async () => {
    if (!client) return
    try {
      const status = await client.call('paywall/status', {})
      if (status && !status.error) {
        setPaywallStatus(status)
        if (status.receiver && !paywallReceiver) setPaywallReceiver(status.receiver)
        if (status.price) setPaywallPrice(status.price)
        if (status.currency) setPaywallCurrency(status.currency)
        if (status.network) setPaywallNetwork(status.network)
      }
    } catch {
      // ignore - paywall module may not be available
    }
  }, [client])

  // Fetch replica groups
  const fetchReplicas = useCallback(async () => {
    if (!client) return
    try {
      const groups = await client.call('balancer/groups', {})
      if (Array.isArray(groups)) {
        setReplicaGroups(groups)
        if (!selectedGroup && groups.length > 0) setSelectedGroup(groups[0])
      }
    } catch {
      // ignore
    }
  }, [client, selectedGroup])

  // Fetch single replica group info
  const fetchGroupInfo = useCallback(async () => {
    if (!client || !selectedGroup) return
    try {
      const info = await client.call('balancer/replicas', { mod: selectedGroup })
      if (info && !info.error) setGroupInfo(info)
    } catch {
      // ignore
    }
  }, [client, selectedGroup])

  // Initial fetch + polling
  useEffect(() => {
    fetchServers()
    const interval = setInterval(fetchServers, 5000)
    return () => clearInterval(interval)
  }, [fetchServers])

  // Mode-specific polling
  useEffect(() => {
    if (mode === 'logs' && selectedServer) {
      fetchLogs()
      const interval = setInterval(fetchLogs, 3000)
      return () => clearInterval(interval)
    }
    if (mode === 'interact' && selectedServer) {
      fetchServerInfo()
    }
    if (mode === 'compute') {
      fetchWorkers()
      fetchTasks()
      const interval = setInterval(() => { fetchWorkers(); fetchTasks() }, 3000)
      return () => clearInterval(interval)
    }
    if (mode === 'meter') {
      fetchMeter()
      fetchPaywall()
      const interval = setInterval(() => { fetchMeter(); fetchPaywall() }, 5000)
      return () => clearInterval(interval)
    }
    if (mode === 'replicas') {
      fetchReplicas()
      if (selectedGroup) fetchGroupInfo()
      const interval = setInterval(() => { fetchReplicas(); if (selectedGroup) fetchGroupInfo() }, 5000)
      return () => clearInterval(interval)
    }
  }, [mode, selectedServer, selectedGroup, fetchLogs, fetchServerInfo, fetchWorkers, fetchTasks, fetchMeter, fetchReplicas, fetchGroupInfo])

  // Auto-scroll logs
  useEffect(() => {
    if (logsRef.current) {
      logsRef.current.scrollTop = logsRef.current.scrollHeight
    }
  }, [logs])

  // Reset state when switching servers
  useEffect(() => {
    setLogs(null)
    setServerInfo(null)
    setSelectedFn(null)
    setFnResult(null)
    setFnError(null)
    setFnParams('')
  }, [selectedServer])

  // Execute function on server
  const handleExecute = async () => {
    if (!client || !selectedServer || !selectedFn) return
    setFnLoading(true)
    setFnResult(null)
    setFnError(null)
    try {
      let params = {}
      if (fnParams.trim()) {
        params = JSON.parse(fnParams)
      }
      const fnPath = `${selectedServer}/${selectedFn}`
      const result = await client.call(fnPath, params)
      if (result?.error) {
        setFnError(result.error)
      } else {
        setFnResult(result)
      }
    } catch (e: any) {
      setFnError(e?.message || 'Execution failed')
    } finally {
      setFnLoading(false)
    }
  }

  // Worker actions
  const handleKillWorker = async (cid: string) => {
    if (!client) return
    setActionLoading(cid)
    try {
      await client.call('kill_worker', { cid })
      setMessage({ text: `Worker ${cid.slice(0, 12)}... killed`, type: 'success' })
      await fetchWorkers()
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to kill worker', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  const handleKillAll = async () => {
    if (!client) return
    if (!confirm('Kill all active workers?')) return
    setActionLoading('__all')
    try {
      await client.call('kill_all_workers')
      setMessage({ text: 'All workers killed', type: 'success' })
      await fetchWorkers()
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  const handleKillTask = async (cid: string) => {
    if (!client) return
    setActionLoading(cid)
    try {
      await client.call('kill_task', { cid })
      setMessage({ text: 'Task killed', type: 'success' })
      await fetchWorkers()
      await fetchTasks()
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  // Meter actions
  const handleSetMargin = async () => {
    if (!client) return
    const val = parseFloat(editMargin)
    if (isNaN(val)) return
    try {
      await client.call('meter/set_margin', { margin: val })
      setMessage({ text: `Margin set to ${(val * 100).toFixed(0)}%`, type: 'success' })
      fetchMeter()
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed', type: 'error' })
    }
  }

  const handleSetRates = async () => {
    if (!client) return
    try {
      await client.call('meter/set_rate', {
        rate_per_second: parseFloat(editRatePerSec) || undefined,
        rate_per_request: parseFloat(editRatePerReq) || undefined,
      })
      setMessage({ text: 'Rates updated', type: 'success' })
      fetchMeter()
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed', type: 'error' })
    }
  }

  // Paywall actions
  const handleTogglePaywall = async () => {
    if (!client) return
    try {
      if (paywallStatus?.enabled) {
        await client.call('paywall/disable', {})
        setMessage({ text: 'Paywall disabled', type: 'success' })
      } else {
        await client.call('paywall/enable', {
          receiver: paywallReceiver,
          price: paywallPrice,
          currency: paywallCurrency,
          network: paywallNetwork,
        })
        setMessage({ text: 'Paywall enabled', type: 'success' })
      }
      fetchPaywall()
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed', type: 'error' })
    }
  }

  // Replica actions
  const handleDeploy = async () => {
    if (!client || !deployMod.trim()) return
    setDeployLoading(true)
    try {
      const result = await client.call('balancer/deploy', {
        mod: deployMod.trim(),
        n: parseInt(deployCount) || 3,
      })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `Deployed ${result?.count || 0} replicas of ${deployMod}`, type: 'success' })
        setDeployMod('')
        fetchReplicas()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Deploy failed', type: 'error' })
    } finally {
      setDeployLoading(false)
    }
  }

  const handleTeardown = async (group: string) => {
    if (!client) return
    if (!confirm(`Teardown all replicas of ${group}?`)) return
    setActionLoading(`teardown:${group}`)
    try {
      await client.call('balancer/teardown', { mod: group })
      setMessage({ text: `${group} replicas torn down`, type: 'success' })
      fetchReplicas()
      setSelectedGroup(null)
      setGroupInfo(null)
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed', type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  const handleSetStrategy = async (group: string, strategy: string) => {
    if (!client) return
    try {
      await client.call('balancer/set_strategy', { mod: group, strategy })
      setMessage({ text: `Strategy set to ${strategy}`, type: 'success' })
      fetchGroupInfo()
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed', type: 'error' })
    }
  }

  const runningTasks = tasks.filter(t => t.status === 'running' || t.status === 'pending')
  const recentTasks = tasks.filter(t => t.status === 'success' || t.status === 'error').slice(0, 10)
  const serverFns = serverInfo?.schema ? Object.keys(serverInfo.schema) : []

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <span className="animate-pulse text-lg font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)', fontFamily: FONT }}>
          LOADING SERVERS...
        </span>
      </div>
    )
  }

  return (
    <div style={{ fontFamily: FONT }} className="flex flex-col" >

      {/* Module Server Management */}
      {mod && (
        <div className="mb-4 border-2 p-4" style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}>
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
              {mod.name} Servers
            </span>
            <div className="flex items-center gap-2">
              {(modServerStatus.api.running || modServerStatus.app.running) ? (
                <>
                  <Btn label="RESTART" color="#f59e0b" loading={modServerLoading === 'serve:both'} onClick={async () => { await handleModKill('both'); setTimeout(() => handleModServe('both'), 1500) }} />
                  <Btn label="STOP ALL" color="#ef4444" loading={modServerLoading === 'kill:both'} onClick={() => handleModKill('both')} />
                </>
              ) : (
                <div className="flex items-center gap-2">
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>API</span>
                    <input
                      type="number"
                      placeholder="auto"
                      value={serveApiPort}
                      onChange={e => setServeApiPort(e.target.value)}
                      className="px-2 py-0.5 text-[10px] w-20 border focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-color)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>APP</span>
                    <input
                      type="number"
                      placeholder="auto"
                      value={serveAppPort}
                      onChange={e => setServeAppPort(e.target.value)}
                      className="px-2 py-0.5 text-[10px] w-20 border focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-color)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <Btn label="SERVE" color="#10b981" loading={modServerLoading === 'serve:both'} onClick={() => handleModServe('both')} />
                </div>
              )}
            </div>
          </div>

          {/* API + App status cards */}
          <div className="grid grid-cols-2 gap-3">
            {/* API Server */}
            <div
              className="border-2 p-3"
              style={{ borderColor: modServerStatus.api.running ? '#10b981' : 'var(--border-color)', background: 'var(--bg-primary)' }}
            >
              <div className="flex items-center gap-2 mb-2">
                <div
                  className="w-2.5 h-2.5 rounded-full shrink-0"
                  style={{
                    background: modServerStatus.api.healthy ? '#10b981' : modServerStatus.api.running ? '#f59e0b' : '#6b7280',
                    boxShadow: modServerStatus.api.healthy ? '0 0 8px #10b981' : modServerStatus.api.running ? '0 0 6px #f59e0b' : 'none',
                  }}
                />
                <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>API Server</span>
                <span className="text-[10px] font-bold uppercase ml-auto" style={{ color: modServerStatus.api.healthy ? '#10b981' : modServerStatus.api.running ? '#f59e0b' : '#6b7280' }}>
                  {modServerStatus.api.healthy ? 'HEALTHY' : modServerStatus.api.running ? 'STARTING' : 'OFFLINE'}
                </span>
              </div>
              {modServerStatus.api.url && (
                <div className="text-[10px] mb-2" style={{ color: 'var(--text-tertiary)' }}>
                  {modServerStatus.api.url}
                </div>
              )}
              <div className="flex items-center gap-1">
                {modServerStatus.api.running ? (
                  <Btn label="STOP" color="#ef4444" loading={modServerLoading === 'kill:api'} onClick={() => handleModKill('api')} />
                ) : (
                  <Btn label="START" color="#10b981" loading={modServerLoading === 'serve:api'} onClick={() => handleModServe('api')} />
                )}
                <Btn
                  label={showModLogs === 'api' ? 'HIDE LOGS' : 'LOGS'}
                  color="var(--text-secondary)"
                  loading={false}
                  onClick={() => setShowModLogs(showModLogs === 'api' ? null : 'api')}
                />
                {modServerStatus.api.url && (
                  <Btn label="OPEN" color="var(--text-tertiary)" loading={false} onClick={() => window.open(modServerStatus.api.url!, '_blank')} />
                )}
              </div>
            </div>

            {/* App Server */}
            <div
              className="border-2 p-3"
              style={{ borderColor: modServerStatus.app.running ? '#10b981' : 'var(--border-color)', background: 'var(--bg-primary)' }}
            >
              <div className="flex items-center gap-2 mb-2">
                <div
                  className="w-2.5 h-2.5 rounded-full shrink-0"
                  style={{
                    background: modServerStatus.app.healthy ? '#10b981' : modServerStatus.app.running ? '#f59e0b' : '#6b7280',
                    boxShadow: modServerStatus.app.healthy ? '0 0 8px #10b981' : modServerStatus.app.running ? '0 0 6px #f59e0b' : 'none',
                  }}
                />
                <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>App Server</span>
                <span className="text-[10px] font-bold uppercase ml-auto" style={{ color: modServerStatus.app.healthy ? '#10b981' : modServerStatus.app.running ? '#f59e0b' : '#6b7280' }}>
                  {modServerStatus.app.healthy ? 'HEALTHY' : modServerStatus.app.running ? 'STARTING' : 'OFFLINE'}
                </span>
              </div>
              {modServerStatus.app.url && (
                <div className="text-[10px] mb-2" style={{ color: 'var(--text-tertiary)' }}>
                  {modServerStatus.app.url}
                </div>
              )}
              <div className="flex items-center gap-1">
                {modServerStatus.app.running ? (
                  <Btn label="STOP" color="#ef4444" loading={modServerLoading === 'kill:app'} onClick={() => handleModKill('app')} />
                ) : (
                  <Btn label="START" color="#10b981" loading={modServerLoading === 'serve:app'} onClick={() => handleModServe('app')} />
                )}
                <Btn
                  label={showModLogs === 'app' ? 'HIDE LOGS' : 'LOGS'}
                  color="var(--text-secondary)"
                  loading={false}
                  onClick={() => setShowModLogs(showModLogs === 'app' ? null : 'app')}
                />
                {modServerStatus.app.url && (
                  <Btn label="OPEN" color="var(--text-tertiary)" loading={false} onClick={() => window.open(modServerStatus.app.url!, '_blank')} />
                )}
              </div>
            </div>
          </div>

          {/* Inline logs viewer */}
          {showModLogs && (
            <div className="mt-3 border-2" style={{ borderColor: 'var(--border-strong)', background: '#0a0a0a' }}>
              <div className="flex items-center justify-between px-3 py-1.5 border-b" style={{ borderColor: 'var(--border-color)' }}>
                <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: showModLogs === 'api' ? '#3b82f6' : '#a78bfa' }}>
                  {showModLogs} logs
                </span>
                <div className="flex items-center gap-1">
                  <Btn label="REFRESH" color="var(--text-tertiary)" loading={false} onClick={() => fetchModServerLogs(showModLogs)} />
                  <Btn label="CLOSE" color="var(--text-tertiary)" loading={false} onClick={() => setShowModLogs(null)} />
                </div>
              </div>
              <pre
                className="px-3 py-2 text-xs overflow-auto"
                style={{
                  color: '#d4d4d4',
                  fontFamily: 'var(--font-code, monospace)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  lineHeight: '1.5',
                  maxHeight: '300px',
                }}
              >
                {modServerLogs[showModLogs] || '(no logs)'}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Message */}
      {message && (
        <div
          className="mb-3 px-3 py-1 text-xs font-bold uppercase cursor-pointer"
          style={{ color: message.type === 'error' ? '#ef4444' : '#10b981' }}
          onClick={() => setMessage(null)}
        >
          {message.text}
        </div>
      )}

      {/* Mode switcher */}
      <div className="flex items-center gap-1 mb-3">
        {(['logs', 'interact', 'compute'] as Mode[]).map(m => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className="px-4 py-1.5 text-xs font-bold uppercase tracking-wider transition-all"
            style={{
              border: `1px solid ${mode === m ? (moduleColor || 'var(--accent-primary)') : 'var(--border-color)'}`,
              color: mode === m ? 'var(--bg-primary)' : 'var(--text-tertiary)',
              background: mode === m ? (moduleColor || 'var(--accent-primary)') : 'transparent',
            }}
          >
            {m}
          </button>
        ))}
      </div>

      <div className="flex gap-3" style={{ minHeight: 'calc(100vh - 300px)' }}>

        {/* Left sidebar: Server list */}
        {mode !== 'compute' && (
          <div
            className="shrink-0 border-2 overflow-y-auto"
            style={{
              width: '200px',
              borderColor: 'var(--border-strong)',
              background: 'var(--bg-secondary)',
            }}
          >
            <div className="px-3 py-2 border-b-2" style={{ borderColor: 'var(--border-strong)' }}>
              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                Servers ({servers.length})
              </span>
            </div>
            {servers.length === 0 && (
              <div className="px-3 py-4 text-center">
                <span className="text-[10px] font-bold uppercase" style={{ color: 'var(--text-tertiary)' }}>
                  No servers running
                </span>
              </div>
            )}
            {servers.map(s => {
              const isSelected = selectedServer === s.name
              const color = text2color(s.name)
              return (
                <button
                  key={s.name}
                  onClick={() => setSelectedServer(s.name)}
                  className="w-full text-left px-3 py-2 flex items-center gap-2 transition-all"
                  style={{
                    background: isSelected ? 'var(--bg-primary)' : 'transparent',
                    borderLeft: isSelected ? `3px solid ${color}` : '3px solid transparent',
                  }}
                >
                  <div
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{ background: '#10b981', boxShadow: '0 0 6px #10b981' }}
                  />
                  <span
                    className="text-xs font-bold uppercase tracking-wider truncate"
                    style={{ color: isSelected ? color : 'var(--text-secondary)' }}
                  >
                    {s.name}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {/* Right panel */}
        <div className="flex-1 min-w-0">

          {/* ===== LOGS MODE ===== */}
          {mode === 'logs' && (
            <div className="h-full flex flex-col">
              {!selectedServer ? (
                <EmptyState text="Select a server to view logs" />
              ) : (
                <>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
                      {selectedServer}
                    </span>
                    <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                      {servers.find(s => s.name === selectedServer)?.address}
                    </span>
                    <div className="ml-auto flex items-center gap-1">
                      {logsLoading && (
                        <span className="text-[10px] animate-pulse" style={{ color: 'var(--text-tertiary)' }}>
                          refreshing...
                        </span>
                      )}
                      <Btn label="REFRESH" color="var(--text-secondary)" onClick={fetchLogs} loading={false} />
                      <Btn label="CLEAR" color="var(--text-tertiary)" onClick={() => setLogs(null)} loading={false} />
                    </div>
                  </div>
                  <div
                    className="flex-1 border-2 overflow-auto"
                    style={{
                      borderColor: 'var(--border-strong)',
                      background: '#0a0a0a',
                      maxHeight: 'calc(100vh - 200px)',
                    }}
                  >
                    {!logs && !logsLoading && (
                      <div className="p-4">
                        <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>No logs available</span>
                      </div>
                    )}
                    {logs && Object.entries(logs).map(([key, content]) => (
                      <div key={key}>
                        <div
                          className="px-3 py-1 text-[10px] font-bold uppercase tracking-wider sticky top-0"
                          style={{
                            color: key.includes('error') || key.includes('err') ? '#ef4444' : '#10b981',
                            background: '#0a0a0a',
                            borderBottom: '1px solid var(--border-color)',
                          }}
                        >
                          {key.replace(/_/g, ' ')}
                        </div>
                        <pre
                          ref={logsRef}
                          className="px-3 py-2 text-xs overflow-auto"
                          style={{
                            color: '#d4d4d4',
                            fontFamily: 'var(--font-code, monospace)',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-all',
                            lineHeight: '1.5',
                          }}
                        >
                          {content || '(empty)'}
                        </pre>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {/* ===== INTERACT MODE ===== */}
          {mode === 'interact' && (
            <div className="h-full flex flex-col">
              {!selectedServer ? (
                <EmptyState text="Select a server to interact" />
              ) : (
                <>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
                      {selectedServer}
                    </span>
                    <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                      {serverFns.length} functions
                    </span>
                  </div>

                  <div className="flex gap-3 flex-1" style={{ maxHeight: 'calc(100vh - 200px)' }}>
                    {/* Function list */}
                    <div
                      className="shrink-0 border-2 overflow-y-auto"
                      style={{
                        width: '180px',
                        borderColor: 'var(--border-strong)',
                        background: 'var(--bg-secondary)',
                      }}
                    >
                      <div className="px-3 py-2 border-b-2" style={{ borderColor: 'var(--border-strong)' }}>
                        <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                          Functions
                        </span>
                      </div>
                      {serverFns.length === 0 && (
                        <div className="px-3 py-4 text-center">
                          <span className="text-[10px] animate-pulse" style={{ color: 'var(--text-tertiary)' }}>
                            {serverInfo ? 'No functions' : 'Loading...'}
                          </span>
                        </div>
                      )}
                      {serverFns.map(fn => {
                        const isActive = selectedFn === fn
                        return (
                          <button
                            key={fn}
                            onClick={() => { setSelectedFn(fn); setFnResult(null); setFnError(null) }}
                            className="w-full text-left px-3 py-1.5 text-xs font-bold uppercase tracking-wider transition-all truncate"
                            style={{
                              color: isActive ? (moduleColor || 'var(--accent-primary)') : 'var(--text-secondary)',
                              background: isActive ? 'var(--bg-primary)' : 'transparent',
                              borderLeft: isActive ? `3px solid ${moduleColor || 'var(--accent-primary)'}` : '3px solid transparent',
                            }}
                          >
                            {fn}
                          </button>
                        )
                      })}
                    </div>

                    {/* Execution panel */}
                    <div className="flex-1 flex flex-col gap-3 min-w-0">
                      {selectedFn && (
                        <>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-bold" style={{ color: moduleColor || 'var(--accent-primary)' }}>
                              {selectedServer}/{selectedFn}
                            </span>
                          </div>

                          {/* Params input */}
                          <div>
                            <div className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: 'var(--text-tertiary)' }}>
                              Params (JSON)
                            </div>
                            <textarea
                              value={fnParams}
                              onChange={e => setFnParams(e.target.value)}
                              placeholder='{"key": "value"}'
                              rows={3}
                              className="w-full px-3 py-2 text-xs font-mono border-2 focus:outline-none resize-none"
                              style={{
                                background: 'var(--bg-primary)',
                                borderColor: 'var(--border-strong)',
                                color: 'var(--text-primary)',
                                fontFamily: 'var(--font-code, monospace)',
                              }}
                            />
                          </div>

                          <Btn
                            label={fnLoading ? 'EXECUTING...' : 'EXECUTE'}
                            color={moduleColor || 'var(--accent-primary)'}
                            onClick={handleExecute}
                            loading={fnLoading}
                          />

                          {/* Error */}
                          {fnError && (
                            <div
                              className="border-2 px-3 py-2"
                              style={{ borderColor: '#ef4444', background: 'rgba(239,68,68,0.05)' }}
                            >
                              <div className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: '#ef4444' }}>Error</div>
                              <pre className="text-xs overflow-auto" style={{ color: '#ef4444', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                {fnError}
                              </pre>
                            </div>
                          )}

                          {/* Result */}
                          {fnResult !== null && (
                            <div
                              className="border-2 px-3 py-2 flex-1 overflow-auto"
                              style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}
                            >
                              <div className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: '#10b981' }}>Result</div>
                              <pre className="text-xs overflow-auto" style={{ color: 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontFamily: 'var(--font-code, monospace)' }}>
                                {typeof fnResult === 'string' ? fnResult : JSON.stringify(fnResult, null, 2)}
                              </pre>
                            </div>
                          )}
                        </>
                      )}
                      {!selectedFn && <EmptyState text="Select a function" />}
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ===== COMPUTE MODE ===== */}
          {mode === 'compute' && (
            <div className="max-w-4xl">
              {/* Stats */}
              <div className="grid grid-cols-3 gap-3 mb-6">
                <StatCard
                  label="Active Workers"
                  value={workerStatus?.active_workers ?? 0}
                  max={workerStatus?.max_workers}
                  color={workerStatus?.active_workers ? '#f59e0b' : 'var(--text-tertiary)'}
                />
                <StatCard
                  label="Running Tasks"
                  value={runningTasks.length}
                  color={runningTasks.length > 0 ? '#3b82f6' : 'var(--text-tertiary)'}
                />
                <StatCard
                  label="Max Workers"
                  value={workerStatus?.max_workers ?? 0}
                  color="var(--text-secondary)"
                />
              </div>

              {/* Active Workers */}
              <div className="mb-6">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
                    Active Workers
                  </span>
                  {(workerStatus?.active_workers ?? 0) > 0 && (
                    <Btn label="KILL ALL" color="#ef4444" loading={actionLoading === '__all'} onClick={handleKillAll} />
                  )}
                </div>

                {(!workerStatus?.active_cids || workerStatus.active_cids.length === 0) && (
                  <div
                    className="py-6 text-center border-2"
                    style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}
                  >
                    <span className="text-[10px] font-bold uppercase" style={{ color: 'var(--text-tertiary)' }}>No active workers</span>
                  </div>
                )}

                <div className="space-y-1">
                  {workerStatus?.active_cids?.map(cid => (
                    <div
                      key={cid}
                      className="flex items-center gap-3 px-4 py-2.5 border-2"
                      style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}
                    >
                      <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: '#f59e0b', boxShadow: '0 0 8px #f59e0b' }} />
                      <code className="flex-1 text-xs truncate" style={{ color: 'var(--text-primary)' }}>{cid}</code>
                      <span className="text-xs font-bold uppercase tracking-wider shrink-0" style={{ color: '#f59e0b' }}>RUNNING</span>
                      <Btn label="KILL" color="#ef4444" loading={actionLoading === cid} onClick={() => handleKillWorker(cid)} />
                    </div>
                  ))}
                </div>
              </div>

              {/* Running Tasks */}
              {runningTasks.length > 0 && (
                <div className="mb-6">
                  <span className="text-xs font-bold uppercase tracking-wider mb-2 block" style={{ color: 'var(--text-secondary)' }}>
                    Running Tasks
                  </span>
                  <div className="space-y-1">
                    {runningTasks.map((task, i) => (
                      <div
                        key={task.cid || i}
                        className="flex items-center gap-3 px-4 py-2.5 border-2"
                        style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}
                      >
                        <div className="w-2.5 h-2.5 rounded-full shrink-0 animate-pulse" style={{ background: '#3b82f6' }} />
                        <span className="text-xs font-bold shrink-0" style={{ color: '#3b82f6' }}>{task.status.toUpperCase()}</span>
                        <span className="text-xs flex-1 truncate" style={{ color: 'var(--text-primary)' }}>{task.fn}</span>
                        {task.key && (
                          <span className="text-[10px] shrink-0" style={{ color: 'var(--text-tertiary)' }}>{shorten(task.key, 4, 4)}</span>
                        )}
                        {task.cid && (
                          <Btn label="KILL" color="#ef4444" loading={actionLoading === task.cid} onClick={() => handleKillTask(task.cid)} />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recent Tasks */}
              {recentTasks.length > 0 && (
                <div>
                  <span className="text-xs font-bold uppercase tracking-wider mb-2 block" style={{ color: 'var(--text-secondary)' }}>
                    Recent Tasks
                  </span>
                  <div className="space-y-1">
                    {recentTasks.map((task, i) => {
                      const isSuccess = task.status === 'success'
                      return (
                        <div
                          key={task.cid || i}
                          className="flex items-center gap-3 px-4 py-2 border"
                          style={{ borderColor: 'var(--border-color)', background: 'var(--bg-surface, var(--bg-secondary))' }}
                        >
                          <div className="w-2 h-2 rounded-full shrink-0" style={{ background: isSuccess ? '#10b981' : '#ef4444' }} />
                          <span className="text-[10px] font-bold uppercase shrink-0" style={{ color: isSuccess ? '#10b981' : '#ef4444' }}>
                            {task.status}
                          </span>
                          <span className="text-xs flex-1 truncate" style={{ color: 'var(--text-primary)' }}>{task.fn}</span>
                          {task.delta !== undefined && (
                            <span className="text-[10px] shrink-0" style={{ color: 'var(--text-tertiary)' }}>{task.delta.toFixed(1)}s</span>
                          )}
                          {task.time && (
                            <span className="text-[10px] shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                              {typeof task.time === 'string' ? task.time : new Date(Number(task.time) * 1000).toLocaleTimeString()}
                            </span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ===== METER MODE ===== */}
          {mode === 'meter' && (
            <div className="max-w-5xl">
              {/* Config */}
              <div className="mb-6 border-2 p-4" style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}>
                <span className="text-xs font-bold uppercase tracking-wider block mb-3" style={{ color: 'var(--text-secondary)' }}>
                  Billing Config
                </span>
                <div className="flex items-end gap-3 flex-wrap">
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Rate/sec ({meterConfig?.currency || 'USD'})</div>
                    <input
                      value={editRatePerSec}
                      onChange={e => setEditRatePerSec(e.target.value)}
                      className="px-2 py-1 text-xs border-2 w-28 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Rate/req</div>
                    <input
                      value={editRatePerReq}
                      onChange={e => setEditRatePerReq(e.target.value)}
                      className="px-2 py-1 text-xs border-2 w-28 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <Btn label="SET RATES" color="var(--accent-primary)" loading={false} onClick={handleSetRates} />
                  <div className="ml-4">
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Margin %</div>
                    <input
                      value={editMargin}
                      onChange={e => setEditMargin(e.target.value)}
                      className="px-2 py-1 text-xs border-2 w-20 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                      placeholder="0.2"
                    />
                  </div>
                  <Btn label="SET MARGIN" color="#f59e0b" loading={false} onClick={handleSetMargin} />
                </div>
                {meterConfig && (
                  <div className="mt-2 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                    Current: {meterConfig.rate_per_second}/{meterConfig.currency}/sec, {meterConfig.rate_per_request}/{meterConfig.currency}/req, {(meterConfig.margin * 100).toFixed(0)}% margin
                  </div>
                )}
              </div>

              {/* x402 Paywall */}
              <div className="mb-6 border-2 p-4" style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}>
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
                    x402 Paywall
                  </span>
                  <Btn
                    label={paywallStatus?.enabled ? 'DISABLE' : 'ENABLE'}
                    color={paywallStatus?.enabled ? '#ef4444' : '#10b981'}
                    loading={false}
                    onClick={handleTogglePaywall}
                  />
                </div>
                <div className="flex items-end gap-3 flex-wrap">
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Receiver</div>
                    <input
                      value={paywallReceiver}
                      onChange={e => setPaywallReceiver(e.target.value)}
                      placeholder="0x..."
                      className="px-2 py-1 text-xs border-2 w-64 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Price</div>
                    <input
                      value={paywallPrice}
                      onChange={e => setPaywallPrice(e.target.value)}
                      className="px-2 py-1 text-xs border-2 w-20 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Currency</div>
                    <input
                      value={paywallCurrency}
                      onChange={e => setPaywallCurrency(e.target.value)}
                      className="px-2 py-1 text-xs border-2 w-20 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Network</div>
                    <input
                      value={paywallNetwork}
                      onChange={e => setPaywallNetwork(e.target.value)}
                      className="px-2 py-1 text-xs border-2 w-24 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                </div>
                {paywallStatus && (
                  <div className="mt-2 flex items-center gap-2">
                    <div
                      className="w-2 h-2 rounded-full"
                      style={{ background: paywallStatus.enabled ? '#10b981' : '#6b7280', boxShadow: paywallStatus.enabled ? '0 0 6px #10b981' : 'none' }}
                    />
                    <span className="text-[10px] font-bold uppercase" style={{ color: paywallStatus.enabled ? '#10b981' : 'var(--text-tertiary)' }}>
                      {paywallStatus.enabled ? 'ACTIVE' : 'DISABLED'}
                    </span>
                    {paywallStatus.enabled && (
                      <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        {paywallStatus.price} {paywallStatus.currency} on {paywallStatus.network}
                      </span>
                    )}
                    {paywallStatus.protected_fns?.length > 0 && (
                      <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        ({paywallStatus.protected_fns.length} protected fns)
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* User Bills */}
              <div className="mb-6">
                <span className="text-xs font-bold uppercase tracking-wider mb-2 block" style={{ color: 'var(--text-secondary)' }}>
                  User Bills ({meterBills.length})
                </span>
                {meterBills.length === 0 && (
                  <div className="py-6 text-center border-2" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}>
                    <span className="text-[10px] font-bold uppercase" style={{ color: 'var(--text-tertiary)' }}>No usage data yet</span>
                  </div>
                )}
                <div className="space-y-1">
                  {meterBills.map((bill, i) => (
                    <div
                      key={bill.user || i}
                      className="border-2 px-4 py-3"
                      style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}
                    >
                      <div className="flex items-center gap-4 flex-wrap">
                        <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>
                          {shorten(bill.user, 8, 6)}
                        </span>
                        <div className="flex items-center gap-4 ml-auto">
                          <div className="text-right">
                            <div className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>Requests</div>
                            <div className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{bill.requests}</div>
                          </div>
                          <div className="text-right">
                            <div className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>Compute</div>
                            <div className="text-sm font-bold" style={{ color: '#3b82f6' }}>{bill.compute_seconds.toFixed(1)}s</div>
                          </div>
                          <div className="text-right">
                            <div className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>Base Cost</div>
                            <div className="text-sm font-bold" style={{ color: 'var(--text-secondary)' }}>{bill.base_cost.toFixed(6)}</div>
                          </div>
                          <div className="text-right">
                            <div className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>Margin</div>
                            <div className="text-sm font-bold" style={{ color: '#f59e0b' }}>{bill.margin_amount.toFixed(6)}</div>
                          </div>
                          <div className="text-right">
                            <div className="text-[10px] uppercase" style={{ color: 'var(--text-tertiary)' }}>Total</div>
                            <div className="text-sm font-bold" style={{ color: '#10b981' }}>
                              {bill.total.toFixed(6)} {bill.currency}
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Recent Requests */}
              {meterRecent.length > 0 && (
                <div>
                  <span className="text-xs font-bold uppercase tracking-wider mb-2 block" style={{ color: 'var(--text-secondary)' }}>
                    Recent Requests
                  </span>
                  <div className="space-y-0.5">
                    {meterRecent.slice().reverse().slice(0, 30).map((entry, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-3 px-3 py-1.5 border"
                        style={{ borderColor: 'var(--border-color)', background: 'var(--bg-surface, var(--bg-secondary))' }}
                      >
                        <div
                          className="w-1.5 h-1.5 rounded-full shrink-0"
                          style={{ background: entry.status === 'success' ? '#10b981' : '#ef4444' }}
                        />
                        <span className="text-[10px] w-16 shrink-0 truncate" style={{ color: 'var(--text-tertiary)' }}>
                          {shorten(entry.user || '', 4, 4)}
                        </span>
                        <span className="text-[10px] flex-1 truncate" style={{ color: 'var(--text-primary)' }}>{entry.fn}</span>
                        <span className="text-[10px] shrink-0" style={{ color: '#3b82f6' }}>{entry.duration?.toFixed(2)}s</span>
                        <span className="text-[10px] shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                          {entry.time ? new Date(entry.time * 1000).toLocaleTimeString() : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ===== REPLICAS MODE ===== */}
          {mode === 'replicas' && (
            <div className="max-w-4xl">
              {/* Deploy */}
              <div className="mb-6 border-2 p-4" style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)' }}>
                <span className="text-xs font-bold uppercase tracking-wider block mb-3" style={{ color: 'var(--text-secondary)' }}>
                  Deploy Replicas
                </span>
                <div className="flex items-end gap-3">
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Module</div>
                    <input
                      value={deployMod}
                      onChange={e => setDeployMod(e.target.value)}
                      placeholder="module-name"
                      className="px-2 py-1 text-xs border-2 w-48 focus:outline-none uppercase"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <div>
                    <div className="text-[10px] font-bold uppercase mb-1" style={{ color: 'var(--text-tertiary)' }}>Count</div>
                    <input
                      value={deployCount}
                      onChange={e => setDeployCount(e.target.value)}
                      className="px-2 py-1 text-xs border-2 w-16 focus:outline-none"
                      style={{ background: 'var(--bg-primary)', borderColor: 'var(--border-strong)', color: 'var(--text-primary)', fontFamily: FONT }}
                    />
                  </div>
                  <Btn label={deployLoading ? 'DEPLOYING...' : 'DEPLOY'} color="#10b981" loading={deployLoading} onClick={handleDeploy} />
                </div>
              </div>

              {/* Groups */}
              <div className="mb-6">
                <span className="text-xs font-bold uppercase tracking-wider mb-2 block" style={{ color: 'var(--text-secondary)' }}>
                  Replica Groups ({replicaGroups.length})
                </span>
                {replicaGroups.length === 0 && (
                  <div className="py-6 text-center border-2" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}>
                    <span className="text-[10px] font-bold uppercase" style={{ color: 'var(--text-tertiary)' }}>No replica groups deployed</span>
                  </div>
                )}
                <div className="space-y-1">
                  {replicaGroups.map(group => {
                    const isSelected = selectedGroup === group
                    const color = text2color(group)
                    return (
                      <div key={group}>
                        <div
                          className="border-2 px-4 py-3 flex items-center gap-3 cursor-pointer"
                          style={{
                            borderColor: isSelected ? color : 'var(--border-strong)',
                            background: isSelected ? 'var(--bg-primary)' : 'var(--bg-secondary)',
                          }}
                          onClick={() => { setSelectedGroup(group); fetchGroupInfo() }}
                        >
                          <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
                          <span className="text-xs font-bold uppercase tracking-wider flex-1" style={{ color }}>{group}</span>
                          <Btn label="TEARDOWN" color="#ef4444" loading={actionLoading === `teardown:${group}`} onClick={() => handleTeardown(group)} />
                        </div>

                        {/* Group detail */}
                        {isSelected && groupInfo && (
                          <div className="border-2 border-t-0 px-4 py-3" style={{ borderColor: color, background: 'var(--bg-secondary)' }}>
                            <div className="flex items-center gap-2 mb-3">
                              <span className="text-[10px] font-bold uppercase" style={{ color: 'var(--text-tertiary)' }}>Strategy:</span>
                              {['round_robin', 'aggregate', 'failover', 'random'].map(s => (
                                <button
                                  key={s}
                                  onClick={() => handleSetStrategy(group, s)}
                                  className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider"
                                  style={{
                                    border: `1px solid ${groupInfo.strategy === s ? color : 'var(--border-color)'}`,
                                    color: groupInfo.strategy === s ? color : 'var(--text-tertiary)',
                                    background: groupInfo.strategy === s ? `${color}15` : 'transparent',
                                  }}
                                >
                                  {s.replace('_', ' ')}
                                </button>
                              ))}
                            </div>
                            <div className="space-y-1">
                              {groupInfo.replicas?.map(replica => (
                                <div
                                  key={replica.name}
                                  className="flex items-center gap-3 px-3 py-2 border"
                                  style={{ borderColor: 'var(--border-color)', background: 'var(--bg-primary)' }}
                                >
                                  <div
                                    className="w-2 h-2 rounded-full shrink-0"
                                    style={{
                                      background: replica.live ? '#10b981' : '#ef4444',
                                      boxShadow: replica.live ? '0 0 6px #10b981' : 'none',
                                    }}
                                  />
                                  <span className="text-xs font-bold uppercase flex-1" style={{ color: replica.live ? 'var(--text-primary)' : 'var(--text-tertiary)' }}>
                                    {replica.name}
                                  </span>
                                  <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>{replica.address}</span>
                                  <span
                                    className="text-[10px] font-bold uppercase"
                                    style={{ color: replica.live ? '#10b981' : '#ef4444' }}
                                  >
                                    {replica.live ? 'LIVE' : 'DOWN'}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Btn({ label, color, loading, onClick }: { label: string; color: string; loading: boolean; onClick: () => void }) {
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

function StatCard({ label, value, max, color }: { label: string; value: number; max?: number; color: string }) {
  return (
    <div className="px-4 py-3 border-2" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}>
      <div className="text-[10px] font-bold uppercase tracking-wider mb-1" style={{ color: 'var(--text-tertiary)' }}>{label}</div>
      <div className="text-2xl font-bold" style={{ color }}>
        {value}
        {max !== undefined && <span className="text-sm" style={{ color: 'var(--text-tertiary)' }}>/{max}</span>}
      </div>
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex items-center justify-center py-16 border-2" style={{ borderColor: 'var(--border-color)', background: 'var(--bg-secondary)' }}>
      <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>{text}</span>
    </div>
  )
}
