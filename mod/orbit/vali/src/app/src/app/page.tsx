"use client";

import { useState, useEffect, useCallback } from 'react'
import { toast } from 'react-toastify'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50100'

// ── Types ───────────────────────────────────────────────────────

interface ValiStatus {
  name: string
  network: string
  subnet: number
  epochs: number
  tempo: number
  batch_size: number
  timeout: number
  modules: number
}

interface ModuleResult {
  name: string
  score: number
  duration: number
  url: string
  key: string
  time: number
  age: number
}

// ── API Helper ──────────────────────────────────────────────────

async function api(fn: string, method: string = 'GET', body?: any) {
  const opts: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body) opts.body = JSON.stringify(body)

  const res = await fetch(`${API_URL}/${fn}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  const data = await res.json()
  return data.result !== undefined ? data.result : data
}

// ── Main Component ──────────────────────────────────────────────

export default function ValiDashboard() {
  const [status, setStatus] = useState<ValiStatus | null>(null)
  const [results, setResults] = useState<ModuleResult[]>([])
  const [loading, setLoading] = useState(false)
  const [epochRunning, setEpochRunning] = useState(false)
  const [sortBy, setSortBy] = useState('score')
  const [sortAsc, setSortAsc] = useState(false)
  const [network, setNetwork] = useState('')
  const [tempo, setTempo] = useState('')
  const [search, setSearch] = useState('')
  const [connected, setConnected] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)

  // ── Fetch status ──────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    try {
      const s = await api('status')
      setStatus(s)
      setConnected(true)
      if (!network) setNetwork(s.network || '')
      if (!tempo) setTempo(String(s.tempo || 60))
    } catch {
      setConnected(false)
    }
  }, [network, tempo])

  // ── Fetch results ─────────────────────────────────────────────

  const fetchResults = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api(`results?by=${sortBy}&ascending=${sortAsc}`)
      setResults(Array.isArray(r) ? r : [])
    } catch {
      // Results may not exist yet
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [sortBy, sortAsc])

  // ── Run epoch ─────────────────────────────────────────────────

  const runEpoch = async () => {
    setEpochRunning(true)
    toast.info('Starting epoch...')
    try {
      const body: any = {}
      if (search) body.search = search
      const r = await api('epoch', 'POST', body)
      toast.success(`Epoch complete — ${Array.isArray(r) ? r.length : 0} results`)
      fetchResults()
      fetchStatus()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setEpochRunning(false)
    }
  }

  // ── Set network ───────────────────────────────────────────────

  const updateNetwork = async () => {
    try {
      const body: any = {}
      if (network) body.network = network
      if (tempo) body.tempo = parseInt(tempo)
      if (search) body.search = search
      await api('network', 'POST', body)
      toast.success('Network updated')
      fetchStatus()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  // ── Refresh results ───────────────────────────────────────────

  const clearResults = async () => {
    try {
      await api('refresh', 'POST')
      toast.success('Results cleared')
      setResults([])
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  // ── Sort handler ──────────────────────────────────────────────

  const handleSort = (field: string) => {
    if (sortBy === field) {
      setSortAsc(!sortAsc)
    } else {
      setSortBy(field)
      setSortAsc(false)
    }
  }

  // ── Effects ───────────────────────────────────────────────────

  useEffect(() => {
    fetchStatus()
    fetchResults()
  }, [])

  useEffect(() => {
    fetchResults()
  }, [sortBy, sortAsc])

  useEffect(() => {
    if (!autoRefresh) return
    const interval = setInterval(() => {
      fetchStatus()
      fetchResults()
    }, 10000)
    return () => clearInterval(interval)
  }, [autoRefresh, fetchStatus, fetchResults])

  // ── Render ────────────────────────────────────────────────────

  const sortIcon = (field: string) => {
    if (sortBy !== field) return ''
    return sortAsc ? ' ^' : ' v'
  }

  const formatAge = (seconds: number) => {
    if (!seconds || isNaN(seconds)) return '-'
    if (seconds < 60) return `${Math.round(seconds)}s`
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`
    return `${Math.round(seconds / 3600)}h`
  }

  return (
    <div className="min-h-screen bg-black text-white p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">VALI</h1>
          <p className="text-zinc-500 text-sm mt-1">Validator Dashboard</p>
        </div>
        <div className="flex items-center gap-3">
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-xs text-zinc-400">{connected ? 'Connected' : 'Disconnected'}</span>
        </div>
      </div>

      {/* Status Cards */}
      {status && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-zinc-900 rounded-lg p-4 border border-zinc-800">
            <div className="text-xs text-zinc-500 uppercase tracking-wider">Network</div>
            <div className="text-lg font-mono mt-1">{status.network}</div>
          </div>
          <div className="bg-zinc-900 rounded-lg p-4 border border-zinc-800">
            <div className="text-xs text-zinc-500 uppercase tracking-wider">Modules</div>
            <div className="text-lg font-mono mt-1">{status.modules}</div>
          </div>
          <div className="bg-zinc-900 rounded-lg p-4 border border-zinc-800">
            <div className="text-xs text-zinc-500 uppercase tracking-wider">Epochs</div>
            <div className="text-lg font-mono mt-1">{status.epochs}</div>
          </div>
          <div className="bg-zinc-900 rounded-lg p-4 border border-zinc-800">
            <div className="text-xs text-zinc-500 uppercase tracking-wider">Tempo</div>
            <div className="text-lg font-mono mt-1">{status.tempo}s</div>
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="bg-zinc-900 rounded-lg p-4 border border-zinc-800 mb-8">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Network</label>
            <input
              type="text"
              value={network}
              onChange={(e) => setNetwork(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm w-32 focus:outline-none focus:border-zinc-500"
              placeholder="local"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Tempo (s)</label>
            <input
              type="number"
              value={tempo}
              onChange={(e) => setTempo(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm w-20 focus:outline-none focus:border-zinc-500"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-500 block mb-1">Search</label>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm w-40 focus:outline-none focus:border-zinc-500"
              placeholder="filter modules"
            />
          </div>
          <button
            onClick={updateNetwork}
            className="bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded px-4 py-1.5 text-sm transition-colors"
          >
            Set Network
          </button>
          <button
            onClick={runEpoch}
            disabled={epochRunning}
            className="bg-white text-black hover:bg-zinc-200 rounded px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-50"
          >
            {epochRunning ? 'Running...' : 'Run Epoch'}
          </button>
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`border rounded px-4 py-1.5 text-sm transition-colors ${
              autoRefresh
                ? 'bg-green-900 border-green-700 text-green-300'
                : 'bg-zinc-800 border-zinc-700 hover:bg-zinc-700'
            }`}
          >
            Auto-Refresh {autoRefresh ? 'ON' : 'OFF'}
          </button>
          <button
            onClick={clearResults}
            className="bg-zinc-800 hover:bg-red-900 border border-zinc-700 hover:border-red-700 rounded px-4 py-1.5 text-sm transition-colors"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Scoreboard */}
      <div className="bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-300">Scoreboard</h2>
          <span className="text-xs text-zinc-500">{results.length} modules</span>
        </div>

        {loading && results.length === 0 ? (
          <div className="p-8 text-center text-zinc-500">Loading...</div>
        ) : results.length === 0 ? (
          <div className="p-8 text-center text-zinc-500">
            No results yet. Run an epoch to score modules.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-zinc-500 uppercase tracking-wider border-b border-zinc-800">
                  <th className="px-4 py-2">#</th>
                  <th className="px-4 py-2 cursor-pointer hover:text-white" onClick={() => handleSort('name')}>
                    Name{sortIcon('name')}
                  </th>
                  <th className="px-4 py-2 cursor-pointer hover:text-white" onClick={() => handleSort('score')}>
                    Score{sortIcon('score')}
                  </th>
                  <th className="px-4 py-2 cursor-pointer hover:text-white" onClick={() => handleSort('duration')}>
                    Duration{sortIcon('duration')}
                  </th>
                  <th className="px-4 py-2">URL</th>
                  <th className="px-4 py-2">Key</th>
                  <th className="px-4 py-2 cursor-pointer hover:text-white" onClick={() => handleSort('age')}>
                    Age{sortIcon('age')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition-colors">
                    <td className="px-4 py-2 text-zinc-500 font-mono text-xs">{i + 1}</td>
                    <td className="px-4 py-2 font-medium">{r.name || '-'}</td>
                    <td className="px-4 py-2 font-mono">
                      <span className={r.score > 0 ? 'text-green-400' : 'text-red-400'}>
                        {typeof r.score === 'number' ? r.score.toFixed(2) : r.score}
                      </span>
                    </td>
                    <td className="px-4 py-2 font-mono text-zinc-400">
                      {typeof r.duration === 'number' ? `${r.duration.toFixed(1)}s` : '-'}
                    </td>
                    <td className="px-4 py-2 text-zinc-400 text-xs font-mono truncate max-w-[200px]">
                      {r.url || '-'}
                    </td>
                    <td className="px-4 py-2 text-zinc-500 text-xs font-mono truncate max-w-[120px]">
                      {r.key ? `${r.key.slice(0, 8)}...${r.key.slice(-4)}` : '-'}
                    </td>
                    <td className="px-4 py-2 text-zinc-400 font-mono text-xs">
                      {formatAge(r.age)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="mt-6 text-center text-xs text-zinc-600">
        vali v1.0 &middot; API: {API_URL}
      </div>
    </div>
  )
}
