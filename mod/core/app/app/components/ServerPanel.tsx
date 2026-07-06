"use client"

import { useState, useEffect, useCallback } from 'react'
import { userContext } from '@/context'

interface ServerPanelProps {
  moduleName: string
  apiUrl: string
  appUrl?: string
  color?: string
}

interface ServerStatus {
  running: boolean
  healthy: boolean
  url: string | null
}

const FONT = "var(--font-digital), monospace"

export default function ServerPanel({ moduleName, apiUrl, appUrl, color = '#10b981' }: ServerPanelProps) {
  const { client } = userContext()
  const [api, setApi] = useState<ServerStatus>({ running: false, healthy: false, url: apiUrl })
  const [app, setApp] = useState<ServerStatus>({ running: false, healthy: false, url: appUrl || null })
  const [loading, setLoading] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const checkHealth = useCallback(async () => {
    const check = async (url: string | null): Promise<boolean> => {
      if (!url) return false
      try {
        const controller = new AbortController()
        const timeout = setTimeout(() => controller.abort(), 3000)
        await fetch(`${url}?_h=${Date.now()}`, { method: 'GET', mode: 'no-cors', cache: 'no-store', signal: controller.signal })
        clearTimeout(timeout)
        return true
      } catch {
        return false
      }
    }

    const [apiAlive, appAlive] = await Promise.all([check(apiUrl), check(appUrl || null)])
    setApi(prev => ({ ...prev, running: apiAlive, healthy: apiAlive }))
    setApp(prev => ({ ...prev, running: appAlive, healthy: appAlive }))
  }, [apiUrl, appUrl])

  useEffect(() => {
    checkHealth()
    const iv = setInterval(checkHealth, 5000)
    return () => clearInterval(iv)
  }, [checkHealth])

  const handleServe = async () => {
    if (!client) return
    setLoading('serve')
    setMsg(null)
    try {
      const result = await client.call('serve_app', { name: moduleName })
      if (result?.error) {
        setMsg(result.error)
      } else {
        setMsg(`${moduleName} started`)
        setTimeout(checkHealth, 2000)
      }
    } catch (e: any) {
      setMsg(e?.message || 'Failed to start')
    } finally {
      setLoading(null)
    }
  }

  const handleKill = async () => {
    if (!client) return
    setLoading('kill')
    setMsg(null)
    try {
      const result = await client.call('kill_app', { name: moduleName })
      if (result?.error) {
        setMsg(result.error)
      } else {
        setMsg(`${moduleName} stopped`)
        setTimeout(checkHealth, 1000)
      }
    } catch (e: any) {
      setMsg(e?.message || 'Failed to stop')
    } finally {
      setLoading(null)
    }
  }

  const handleRestart = async () => {
    if (!client) return
    setLoading('restart')
    setMsg(null)
    try {
      await client.call('kill_app', { name: moduleName })
      await new Promise(r => setTimeout(r, 1500))
      await client.call('serve_app', { name: moduleName })
      setMsg(`${moduleName} restarting...`)
      setTimeout(checkHealth, 2000)
    } catch (e: any) {
      setMsg(e?.message || 'Restart failed')
    } finally {
      setLoading(null)
    }
  }

  const isRunning = api.healthy || app.healthy

  return (
    <div className="mb-4 border-2 p-4" style={{ borderColor: 'var(--border-strong)', background: 'var(--bg-secondary)', fontFamily: FONT }}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
          {moduleName} Servers
        </span>
        <div className="flex items-center gap-2">
          {isRunning ? (
            <>
              <Btn label="RESTART" color="#f59e0b" loading={loading === 'restart'} onClick={handleRestart} />
              <Btn label="STOP ALL" color="#ef4444" loading={loading === 'kill'} onClick={handleKill} />
            </>
          ) : (
            <Btn label="SERVE" color="#10b981" loading={loading === 'serve'} onClick={handleServe} />
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <ServerCard label="API" status={api} color={color} />
        {appUrl && <ServerCard label="APP" status={app} color={color} />}
      </div>

      {msg && (
        <div
          className="mt-2 text-xs font-bold uppercase cursor-pointer"
          style={{ color: msg.includes('fail') || msg.includes('error') ? '#ef4444' : '#10b981' }}
          onClick={() => setMsg(null)}
        >
          {msg}
        </div>
      )}
    </div>
  )
}

function ServerCard({ label, status, color }: { label: string; status: ServerStatus; color: string }) {
  const dotColor = status.healthy ? '#10b981' : '#6b7280'
  const statusLabel = status.healthy ? 'ONLINE' : 'OFFLINE'
  const statusColor = status.healthy ? '#10b981' : '#6b7280'

  return (
    <div
      className="border-2 p-3"
      style={{ borderColor: status.healthy ? '#10b981' : 'var(--border-color)', background: 'var(--bg-primary)' }}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)', fontFamily: FONT }}>
          {label}
        </span>
        <span
          className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider"
          style={{
            color: statusColor,
            border: `1px solid ${statusColor}`,
            borderRadius: '4px',
          }}
        >
          {statusLabel}
        </span>
      </div>
      {status.url && (
        <div className="flex items-center gap-2">
          <div
            className="w-2.5 h-2.5 rounded-full shrink-0"
            style={{
              background: dotColor,
              boxShadow: status.healthy ? `0 0 8px ${dotColor}` : 'none',
            }}
          />
          <span className="text-[11px]" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>
            {status.url}
          </span>
        </div>
      )}
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
