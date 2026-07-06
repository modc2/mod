"use client"

import { useState, useEffect, useCallback } from 'react'
import { ModuleType } from '@/types'
import { userContext } from '@/context'
import { text2color, colorWithOpacity, getModApiUrl, getModAppUrl } from '@/utils'
import { CopyButton } from '@/ui/CopyButton'

interface ModInfoProps {
  mod: ModuleType
  moduleColor?: string
}

const FONT = "var(--font-digital), monospace"

export default function ModInfo({ mod, moduleColor }: ModInfoProps) {
  const { client, user } = userContext()
  const color = moduleColor || text2color(mod.name || mod.key)
  const apiUrl = getModApiUrl(mod)
  const appUrl = getModAppUrl(mod)

  const [apiHealthy, setApiHealthy] = useState(false)
  const [appHealthy, setAppHealthy] = useState(false)
  const [showPreview, setShowPreview] = useState(true)
  const [configOpen, setConfigOpen] = useState(false)
  const [fnsOpen, setFnsOpen] = useState(false)
  const [isInIframe, setIsInIframe] = useState(false)
  const [serverAction, setServerAction] = useState<string | null>(null)
  const [serverMsg, setServerMsg] = useState<{ text: string; type: 'success' | 'error' } | null>(null)

  const isOwner = user?.key && mod.key && user.key.toLowerCase() === mod.key.toLowerCase()

  useEffect(() => {
    if (typeof window !== 'undefined') {
      setIsInIframe(window.self !== window.top)
    }
  }, [])

  const checkHealth = useCallback(async () => {
    if (apiUrl) {
      try { const r = await fetch(apiUrl + '/health', { signal: AbortSignal.timeout(3000) }); setApiHealthy(r.ok) }
      catch { setApiHealthy(false) }
    } else { setApiHealthy(false) }
    if (appUrl) {
      try { const r = await fetch(appUrl, { signal: AbortSignal.timeout(3000) }); setAppHealthy(r.ok) }
      catch { setAppHealthy(false) }
    } else { setAppHealthy(false) }
  }, [apiUrl, appUrl])

  useEffect(() => {
    checkHealth()
    const iv = setInterval(checkHealth, 5000)
    return () => clearInterval(iv)
  }, [checkHealth])

  const handleServe = useCallback(async () => {
    if (!client || !mod.name) return
    setServerAction('serve')
    setServerMsg(null)
    try {
      const result = await client.call('serve_app', { name: mod.name })
      if (result?.error) {
        setServerMsg({ text: result.error, type: 'error' })
      } else {
        setServerMsg({ text: `${mod.name} started`, type: 'success' })
        setTimeout(checkHealth, 2000)
      }
    } catch (e: any) {
      setServerMsg({ text: e?.message || 'Failed to start', type: 'error' })
    } finally {
      setServerAction(null)
    }
  }, [client, mod.name, checkHealth])

  const handleKill = useCallback(async () => {
    if (!client || !mod.name) return
    setServerAction('kill')
    setServerMsg(null)
    try {
      const result = await client.call('kill_app', { name: mod.name })
      if (result?.error) {
        setServerMsg({ text: result.error, type: 'error' })
      } else {
        setServerMsg({ text: `${mod.name} stopped`, type: 'success' })
        setTimeout(checkHealth, 1000)
      }
    } catch (e: any) {
      setServerMsg({ text: e?.message || 'Failed to stop', type: 'error' })
    } finally {
      setServerAction(null)
    }
  }, [client, mod.name, checkHealth])

  const isRunning = apiHealthy || appHealthy
  const fnNames = mod.schema && typeof mod.schema === 'object' ? Object.keys(mod.schema) : []

  const config: Record<string, any> = {}
  if (mod.name) config.name = mod.name
  if (mod.key) config.key = mod.key
  if (mod.id !== undefined) config.id = mod.id
  if (mod.desc) config.desc = mod.desc
  if (mod.url) config.url = mod.url
  if (mod.url_app) config.url_app = mod.url_app
  if (mod.network) config.network = mod.network
  if (mod.chain_id) config.chain_id = mod.chain_id
  if (mod.cid) config.cid = mod.cid
  if (mod.created) config.created = mod.created
  if (mod.updated) config.updated = mod.updated
  if (mod.public !== undefined) config.public = mod.public
  if (mod.allowed_users && mod.allowed_users.length > 0) config.allowed_users = mod.allowed_users
  if (mod.take !== undefined) config.take = mod.take
  if (mod.collateral !== undefined) config.collateral = mod.collateral
  if (mod.local !== undefined) config.local = mod.local
  if (mod.path) config.path = mod.path
  const configJson = JSON.stringify(config, null, 2)

  return (
    <div style={{ fontFamily: FONT }} className="max-w-5xl pb-8">

      {mod.desc && (
        <div className="text-[11px] mb-3" style={{ color: 'var(--text-tertiary)' }}>{mod.desc}</div>
      )}

      {/* ── Status row ── */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        {isRunning ? (
          <div className="flex items-center gap-1.5 px-2 py-1" style={{ border: '1px solid rgba(16,185,129,0.3)', borderRadius: '3px', background: 'rgba(16,185,129,0.06)' }}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#10b981', boxShadow: '0 0 4px #10b981' }} />
            <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: '#10b981' }}>RUNNING</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 px-2 py-1" style={{ border: '1px solid rgba(107,114,128,0.3)', borderRadius: '3px', background: 'rgba(107,114,128,0.06)' }}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#6b7280' }} />
            <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: '#6b7280' }}>OFFLINE</span>
          </div>
        )}
        {isOwner && (
          isRunning ? (
            <button
              onClick={handleKill}
              disabled={serverAction === 'kill'}
              className="px-2 py-1 text-[9px] font-bold uppercase tracking-wider transition-all"
              style={{ border: '1px solid #ef4444', color: '#ef4444', background: 'transparent', borderRadius: '3px', fontFamily: FONT, opacity: serverAction === 'kill' ? 0.5 : 1, cursor: serverAction === 'kill' ? 'wait' : 'pointer' }}
            >
              {serverAction === 'kill' ? '...' : 'STOP'}
            </button>
          ) : (
            <button
              onClick={handleServe}
              disabled={serverAction === 'serve'}
              className="px-2 py-1 text-[9px] font-bold uppercase tracking-wider transition-all"
              style={{ border: '1px solid #10b981', color: '#10b981', background: 'transparent', borderRadius: '3px', fontFamily: FONT, opacity: serverAction === 'serve' ? 0.5 : 1, cursor: serverAction === 'serve' ? 'wait' : 'pointer' }}
            >
              {serverAction === 'serve' ? '...' : 'START'}
            </button>
          )
        )}
        {serverMsg && (
          <span className="text-[9px] font-bold uppercase tracking-wider cursor-pointer" style={{ color: serverMsg.type === 'error' ? '#ef4444' : '#10b981' }} onClick={() => setServerMsg(null)}>
            {serverMsg.text}
          </span>
        )}
        {fnNames.length > 0 && (
          <span className="text-[9px] font-bold uppercase tracking-wider px-2 py-1" style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border-color)', borderRadius: '3px' }}>
            {fnNames.length} FUNCTIONS
          </span>
        )}
        {apiUrl && (
          <span className="text-[9px] font-mono truncate" style={{ color: 'var(--text-tertiary)', opacity: 0.6, maxWidth: '200px' }}>{apiUrl.replace(/^https?:\/\//, '')}</span>
        )}
        {appUrl && (
          <span className="text-[9px] font-mono truncate" style={{ color: 'var(--text-tertiary)', opacity: 0.6, maxWidth: '200px' }}>{appUrl.replace(/^https?:\/\//, '')}</span>
        )}
      </div>

      {/* ── App Preview (skip when inside iframe to prevent recursive nesting) ── */}
      {appUrl && appHealthy && !isInIframe && (
        <div className="mb-4" style={{ borderRadius: '5px', overflow: 'hidden', border: `1px solid ${colorWithOpacity(color, 0.2)}` }}>
          <div
            className="flex items-center justify-between px-3 py-1"
            style={{ background: colorWithOpacity(color, 0.05), borderBottom: `1px solid ${colorWithOpacity(color, 0.1)}` }}
          >
            <div className="flex items-center gap-2">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#ef4444', opacity: 0.6 }} />
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#f59e0b', opacity: 0.6 }} />
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#10b981', opacity: 0.6 }} />
              </div>
              <span className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-tertiary)' }}>PREVIEW</span>
            </div>
            <div className="flex items-center gap-2">
              <a href={appUrl} target="_blank" rel="noopener noreferrer" className="text-[9px] font-bold uppercase tracking-wider hover:opacity-100" style={{ color, opacity: 0.6, textDecoration: 'none' }}>OPEN</a>
              <button onClick={() => setShowPreview(!showPreview)} className="text-[9px] font-bold uppercase tracking-wider hover:opacity-100" style={{ color: 'var(--text-tertiary)', opacity: 0.6, background: 'none', border: 'none', cursor: 'pointer', fontFamily: FONT }}>{showPreview ? 'HIDE' : 'SHOW'}</button>
            </div>
          </div>
          {showPreview && (
            <iframe src={appUrl} title={`${mod.name} preview`} style={{ width: '100%', height: '320px', border: 'none', background: 'var(--bg-primary)', display: 'block' }} sandbox="allow-scripts allow-same-origin allow-forms allow-popups" />
          )}
        </div>
      )}

      {/* ── Functions (collapsible) ── */}
      {fnNames.length > 0 && (
        <div className="mb-4" style={{ borderRadius: '5px', overflow: 'hidden', border: '1px solid var(--border-color)' }}>
          <div
            className="flex items-center justify-between px-3 py-1.5 cursor-pointer"
            style={{ background: 'var(--bg-secondary)' }}
            onClick={() => setFnsOpen(!fnsOpen)}
          >
            <div className="flex items-center gap-1.5">
              <svg className="w-2.5 h-2.5 transition-transform duration-200" style={{ color: 'var(--text-tertiary)', transform: fnsOpen ? 'rotate(90deg)' : 'rotate(0deg)' }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
              <span className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-tertiary)' }}>FUNCTIONS</span>
              <span className="text-[9px]" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>{fnNames.length}</span>
            </div>
          </div>
          {fnsOpen && (
            <div className="px-3 py-2 flex flex-wrap gap-1" style={{ background: 'var(--bg-secondary)', borderTop: '1px solid var(--border-color)' }}>
              {fnNames.map(fn => {
                const fnSchema = (mod.schema as unknown as Record<string, any>)?.[fn]
                const inputCount = fnSchema?.input
                  ? Array.isArray(fnSchema.input) ? fnSchema.input.length
                    : Object.keys(fnSchema.input).filter((k: string) => k !== 'self' && k !== 'cls' && k !== 'kwargs').length
                  : 0
                return (
                  <div
                    key={fn}
                    className="px-1.5 py-px text-[9px] font-bold uppercase tracking-wider flex items-center gap-1"
                    style={{ border: '1px solid var(--border-color)', color: 'var(--text-primary)', background: 'var(--bg-primary)', borderRadius: '2px' }}
                  >
                    {fn}
                    {inputCount > 0 && <span className="text-[8px]" style={{ color: 'var(--text-tertiary)' }}>({inputCount})</span>}
                    {fnSchema?.auth && <span className="text-[7px]" style={{ color: '#f59e0b' }}>AUTH</span>}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Config (collapsible) ── */}
      <div style={{ borderRadius: '5px', overflow: 'hidden', border: '1px solid var(--border-color)' }}>
        <div
          className="flex items-center justify-between px-3 py-1.5 cursor-pointer"
          style={{ background: 'var(--bg-secondary)' }}
          onClick={() => setConfigOpen(!configOpen)}
        >
          <div className="flex items-center gap-1.5">
            <svg className="w-2.5 h-2.5 transition-transform duration-200" style={{ color: 'var(--text-tertiary)', transform: configOpen ? 'rotate(90deg)' : 'rotate(0deg)' }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            <span className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-tertiary)' }}>CONFIG</span>
          </div>
          <CopyButton text={configJson} />
        </div>
        {configOpen && (
          <pre className="px-3 py-2 text-[10px] overflow-auto" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-code, monospace)', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: '400px', borderTop: '1px solid var(--border-color)', background: 'var(--bg-secondary)', margin: 0 }}>
            {configJson}
          </pre>
        )}
      </div>
    </div>
  )
}
