"use client";

import { useEffect, useState, useRef, useCallback } from 'react'
import { motion } from 'framer-motion'
import { GlobeAltIcon } from '@heroicons/react/24/outline'
import { ModuleType } from '@/types'
import { getModAppUrl, getModApiUrl } from '@/utils'
import { userContext } from '@/context'
import LogsPanel from '@/mod/LogsPanel'

interface ModAppProps {
  mod: ModuleType
  moduleColor?: string
}

type AppStatus = 'checking' | 'live' | 'down' | 'no-url'

export default function ModApp({ mod, moduleColor = 'white' }: ModAppProps) {
  const { client } = userContext()
  const [status, setStatus] = useState<AppStatus>('checking')
  const [restarting, setRestarting] = useState(false)
  const [restartMsg, setRestartMsg] = useState<string | null>(null)
  const [iframeKey, setIframeKey] = useState(0)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const healthInterval = useRef<ReturnType<typeof setInterval> | null>(null)

  const appUrl = getModAppUrl(mod)
  const apiUrl = getModApiUrl(mod)

  const checkHealth = useCallback(async (): Promise<boolean> => {
    // Try the app URL first, then the API URL
    const urlsToTry = [appUrl, apiUrl].filter(Boolean)
    for (const url of urlsToTry) {
      try {
        const controller = new AbortController()
        const timeout = setTimeout(() => controller.abort(), 4000)
        // Use GET (not HEAD) — some servers don't handle HEAD properly.
        // Cache-bust to avoid stale failure responses.
        const sep = url!.includes('?') ? '&' : '?'
        await fetch(`${url}${sep}_h=${Date.now()}`, {
          method: 'GET',
          mode: 'no-cors',
          cache: 'no-store',
          signal: controller.signal,
        })
        clearTimeout(timeout)
        // no-cors returns opaque response (status 0) — that's fine, it means the server responded
        return true
      } catch {
        continue
      }
    }
    return false
  }, [appUrl, apiUrl])

  // Initial health check + periodic re-check when down
  useEffect(() => {
    if (!appUrl) {
      setStatus('no-url')
      return
    }

    let mounted = true
    const doCheck = async () => {
      const alive = await checkHealth()
      if (!mounted) return
      setStatus(alive ? 'live' : 'down')
    }

    setStatus('checking')
    doCheck()

    // Poll every 4s while the tab is visible
    healthInterval.current = setInterval(async () => {
      const alive = await checkHealth()
      if (!mounted) return
      setStatus(prev => {
        if (alive && (prev === 'down' || prev === 'checking')) return 'live'
        if (!alive && prev === 'live') return 'down'
        return prev
      })
    }, 4000)

    return () => {
      mounted = false
      if (healthInterval.current) clearInterval(healthInterval.current)
    }
  }, [appUrl, checkHealth])

  // Broadcast live status changes to TopBar
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('mod:health', { detail: { isLive: status === 'live' } }))
  }, [status])

  const handleRestart = async () => {
    if (!client || !mod.name) return
    setRestarting(true)
    setRestartMsg('stopping...')
    try {
      await client.call('kill_app', { name: mod.name })
      setRestartMsg('starting...')
      await new Promise(r => setTimeout(r, 1500))
      await client.call('serve_app', { name: mod.name })
      setRestartMsg('waiting for app...')
      // Poll until it comes back — 30 attempts at 1s intervals
      let attempts = 0
      while (attempts < 30) {
        await new Promise(r => setTimeout(r, 1000))
        const alive = await checkHealth()
        if (alive) {
          setStatus('live')
          setRestartMsg(null)
          setIframeKey(k => k + 1)
          setRestarting(false)
          return
        }
        attempts++
      }
      setRestartMsg('app may still be starting — check back shortly')
      setStatus('down')
    } catch (e: any) {
      setRestartMsg(e?.message || 'restart failed')
    } finally {
      setRestarting(false)
      setTimeout(() => setRestartMsg(null), 5000)
    }
  }

  const handleStart = async () => {
    if (!client || !mod.name) return
    setRestarting(true)
    setRestartMsg('starting...')
    try {
      await client.call('serve_app', { name: mod.name })
      setRestartMsg('waiting for app...')
      let attempts = 0
      while (attempts < 30) {
        await new Promise(r => setTimeout(r, 1000))
        const alive = await checkHealth()
        if (alive) {
          setStatus('live')
          setRestartMsg(null)
          setIframeKey(k => k + 1)
          setRestarting(false)
          return
        }
        attempts++
      }
      setRestartMsg('app may still be starting')
      setStatus('down')
    } catch (e: any) {
      setRestartMsg(e?.message || 'start failed')
    } finally {
      setRestarting(false)
      setTimeout(() => setRestartMsg(null), 5000)
    }
  }

  // No URL configured
  if (status === 'no-url') {
    return (
      <div className='flex min-h-[600px] items-center justify-center' style={{ fontFamily: 'var(--font-digital), monospace' }}>
        <div className='text-center space-y-4'>
          <div className='text-4xl opacity-30'>[ ]</div>
          <div className='text-sm font-bold uppercase tracking-wider' style={{ color: 'var(--text-tertiary)' }}>
            NO APP URL CONFIGURED
          </div>
          <div className='text-xs' style={{ color: 'var(--text-tertiary)', opacity: 0.6 }}>
            This module has no application endpoint
          </div>
        </div>
      </div>
    )
  }

  // Checking health
  if (status === 'checking') {
    return (
      <div className='flex min-h-[600px] items-center justify-center' style={{ fontFamily: 'var(--font-digital), monospace' }}>
        <div className='text-center space-y-4'>
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
            className='mx-auto h-8 w-8 rounded-full border-2 border-t-transparent'
            style={{ borderColor: moduleColor }}
          />
          <div className='text-sm font-bold uppercase tracking-wider' style={{ color: 'var(--text-secondary)' }}>
            CHECKING APP STATUS...
          </div>
        </div>
      </div>
    )
  }

  // App is down — show restart controls + logs
  if (status === 'down') {
    return (
      <div style={{ fontFamily: 'var(--font-digital), monospace' }}>
        <div className='flex min-h-[350px] items-center justify-center'>
          <div className='text-center space-y-5'>
            <div className='flex items-center justify-center gap-2'>
              <span
                className='w-3 h-3 rounded-full'
                style={{ background: '#ef4444', boxShadow: '0 0 8px rgba(239,68,68,0.5)' }}
              />
              <span className='text-sm font-bold uppercase tracking-wider' style={{ color: '#ef4444' }}>
                APP NOT RESPONDING
              </span>
            </div>
            <div className='text-xs' style={{ color: 'var(--text-tertiary)' }}>
              {appUrl}
            </div>
            <div className='flex items-center justify-center gap-3'>
              <button
                onClick={handleStart}
                disabled={restarting}
                className='px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all'
                style={{
                  fontFamily: 'var(--font-digital), monospace',
                  border: '1px solid rgba(16,185,129,0.5)',
                  color: '#10b981',
                  background: 'rgba(16,185,129,0.08)',
                  borderRadius: '4px',
                  opacity: restarting ? 0.5 : 1,
                }}
              >
                {restarting ? '...' : 'START'}
              </button>
              <button
                onClick={handleRestart}
                disabled={restarting}
                className='px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all'
                style={{
                  fontFamily: 'var(--font-digital), monospace',
                  border: '1px solid rgba(251,191,36,0.5)',
                  color: '#fbbf24',
                  background: 'rgba(251,191,36,0.08)',
                  borderRadius: '4px',
                  opacity: restarting ? 0.5 : 1,
                }}
              >
                {restarting ? '...' : 'RESTART'}
              </button>
              <button
                onClick={() => {
                  setStatus('checking')
                  checkHealth().then(alive => setStatus(alive ? 'live' : 'down'))
                }}
                className='px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all'
                style={{
                  fontFamily: 'var(--font-digital), monospace',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-tertiary)',
                  background: 'transparent',
                  borderRadius: '4px',
                }}
              >
                RETRY
              </button>
            </div>
            {restartMsg && (
              <div className='text-xs font-bold uppercase' style={{ color: 'var(--text-tertiary)' }}>
                {restartMsg}
              </div>
            )}
          </div>
        </div>
        <LogsPanel modName={mod.name || ''} moduleColor={moduleColor} filter="app" defaultOpen={true} />
      </div>
    )
  }

  // App is live — show iframe + logs
  return (
    <div className='relative w-full flex flex-col' style={{ height: 'calc(100vh - 64px)' }}>
      <div className='relative flex-1 min-h-0'>
        <iframe
          key={iframeKey}
          ref={iframeRef}
          src={appUrl}
          className='w-full h-full border-0'
          title={`${mod.name} Application`}
          allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture'
          allowFullScreen
        />

        {/* External link indicator */}
        <motion.a
          href={appUrl}
          target='_blank'
          rel='noopener noreferrer'
          className='absolute top-4 right-4 flex items-center gap-2 rounded-full bg-black/80 px-4 py-2 text-sm backdrop-blur-sm transition-all hover:scale-105'
          style={{
            borderColor: `${moduleColor}4D`,
            color: moduleColor,
            border: '1px solid'
          }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <GlobeAltIcon className='h-4 w-4' />
          Open in new tab
        </motion.a>
      </div>
      <LogsPanel modName={mod.name || ''} moduleColor={moduleColor} filter="app" />
    </div>
  )
}
