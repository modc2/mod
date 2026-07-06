"use client";

import { useEffect, useState, useRef, useCallback } from 'react'
import { userContext } from '@/context'
import { colorWithOpacity } from '@/utils'

interface LogsPanelProps {
  modName: string
  moduleColor?: string
  /** Which log sources to show — defaults to all */
  filter?: 'app' | 'api' | 'all'
  /** Start collapsed or expanded */
  defaultOpen?: boolean
}

export default function LogsPanel({ modName, moduleColor = '#10b981', filter = 'all', defaultOpen = false }: LogsPanelProps) {
  const { client } = userContext()
  const [logs, setLogs] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(defaultOpen)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [selectedSource, setSelectedSource] = useState<string | null>(null)
  const scrollRef = useRef<HTMLPreElement>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchLogs = useCallback(async () => {
    if (!client || !modName) return
    setLoading(true)
    try {
      const res = await client.call('app_logs', { name: modName, lines: 200 })
      if (res && !res.error) {
        setLogs(res)
        // Auto-select first source matching filter
        const sources = Object.keys(res)
        if (sources.length > 0 && !selectedSource) {
          if (filter === 'app') {
            setSelectedSource(sources.find(s => s.includes('app')) || sources[0])
          } else if (filter === 'api') {
            setSelectedSource(sources.find(s => s.includes('api') || !s.includes('app')) || sources[0])
          } else {
            setSelectedSource(sources[0])
          }
        }
      } else {
        setLogs({})
      }
    } catch {
      setLogs({})
    } finally {
      setLoading(false)
    }
  }, [client, modName, filter, selectedSource])

  // Fetch on open
  useEffect(() => {
    if (open) fetchLogs()
  }, [open, fetchLogs])

  // Auto-refresh
  useEffect(() => {
    if (autoRefresh && open) {
      intervalRef.current = setInterval(fetchLogs, 4000)
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [autoRefresh, open, fetchLogs])

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs, selectedSource])

  const sources = Object.keys(logs)
  const filteredSources = filter === 'all' ? sources : sources.filter(s => {
    if (filter === 'app') return s.includes('app')
    if (filter === 'api') return s.includes('api') || !s.includes('app')
    return true
  })
  const currentLog = selectedSource && logs[selectedSource] ? logs[selectedSource] : ''

  return (
    <div
      style={{
        fontFamily: 'var(--font-digital), monospace',
        borderTop: '1px solid var(--border-color)',
      }}
    >
      {/* Toggle bar */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2 transition-all"
        style={{
          background: open ? colorWithOpacity(moduleColor, 0.04) : 'var(--bg-surface)',
          borderBottom: open ? '1px solid var(--border-color)' : 'none',
        }}
      >
        <div className="flex items-center gap-2">
          <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
            {open ? '▾' : '▸'}
          </span>
          <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>
            LOGS
          </span>
          {!open && sources.length > 0 && (
            <span className="text-[9px]" style={{ color: 'var(--text-tertiary)' }}>
              {sources.length} source{sources.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        {open && (
          <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
            <button
              onClick={(e) => { e.stopPropagation(); setAutoRefresh(!autoRefresh) }}
              className="text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 transition-all"
              style={{
                border: `1px solid ${autoRefresh ? colorWithOpacity(moduleColor, 0.5) : 'var(--border-color)'}`,
                color: autoRefresh ? moduleColor : 'var(--text-tertiary)',
                background: autoRefresh ? colorWithOpacity(moduleColor, 0.08) : 'transparent',
                borderRadius: '3px',
              }}
            >
              {autoRefresh ? 'LIVE' : 'AUTO'}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); fetchLogs() }}
              className="text-[9px] font-bold uppercase tracking-wider px-2 py-0.5"
              style={{
                border: '1px solid var(--border-color)',
                color: 'var(--text-tertiary)',
                borderRadius: '3px',
              }}
            >
              {loading ? '...' : 'REFRESH'}
            </button>
          </div>
        )}
      </button>

      {/* Logs content */}
      {open && (
        <div>
          {/* Source tabs */}
          {filteredSources.length > 1 && (
            <div className="flex items-center gap-0 px-2 pt-1" style={{ background: 'var(--bg-surface)' }}>
              {filteredSources.map(source => (
                <button
                  key={source}
                  onClick={() => setSelectedSource(source)}
                  className="px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider transition-all"
                  style={{
                    color: selectedSource === source ? moduleColor : 'var(--text-tertiary)',
                    borderBottom: selectedSource === source ? `2px solid ${moduleColor}` : '2px solid transparent',
                    background: selectedSource === source ? colorWithOpacity(moduleColor, 0.04) : 'transparent',
                  }}
                >
                  {source.replace('pm2_', '').replace('_out', ' (out)').replace('_error', ' (err)')}
                </button>
              ))}
            </div>
          )}

          {/* Log output */}
          {filteredSources.length === 0 ? (
            <div className="px-4 py-8 text-center">
              <span className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
                {loading ? 'LOADING LOGS...' : 'NO LOGS FOUND'}
              </span>
            </div>
          ) : (
            <pre
              ref={scrollRef}
              className="px-4 py-3 text-[11px] leading-relaxed overflow-auto"
              style={{
                background: 'var(--bg-primary)',
                color: 'var(--text-secondary)',
                maxHeight: '300px',
                scrollbarWidth: 'thin',
                scrollbarColor: 'var(--scrollbar-thumb) transparent',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}
            >
              {currentLog || 'No log content'}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
