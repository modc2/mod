"use client";

import React, { useState, useEffect, useRef } from 'react'
import { Terminal, RefreshCw, Download } from 'lucide-react'

interface LogsViewerProps {
  modName: string
  token?: string
  moduleColor?: string
}

export function LogsViewer({ modName, token, moduleColor = '#0bf58c' }: LogsViewerProps) {
  const [logs, setLogs] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<string>('api')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [lines, setLines] = useState(100)
  const logsEndRef = useRef<HTMLDivElement>(null)

  const fetchLogs = async () => {
    if (!modName) return

    setLoading(true)
    setError(null)

    try {
      const response = await fetch('/api/logs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: modName,
          lines,
          token: token || ''
        }),
      })

      const data = await response.json()

      if (data.error) {
        setError(data.error)
        setLogs({})
      } else {
        setLogs(data)
        // Set first available log as active tab if current tab has no logs
        if (!data[activeTab] && Object.keys(data).length > 0) {
          setActiveTab(Object.keys(data)[0])
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch logs')
      setLogs({})
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchLogs()
  }, [modName, lines])

  useEffect(() => {
    if (!autoRefresh) return

    const interval = setInterval(() => {
      fetchLogs()
    }, 3000)

    return () => clearInterval(interval)
  }, [autoRefresh, modName, lines])

  const scrollToBottom = () => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    if (autoRefresh) {
      scrollToBottom()
    }
  }, [logs, autoRefresh])

  const downloadLogs = () => {
    const logContent = logs[activeTab] || ''
    const blob = new Blob([logContent], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${modName}-${activeTab}-${new Date().toISOString()}.log`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const availableTabs = Object.keys(logs).length > 0 ? Object.keys(logs) : ['api', 'app']

  return (
    <div className="flex flex-col h-full" style={{ fontFamily: 'var(--font-digital), monospace' }}>
      {/* Header */}
      <div className="flex items-center justify-between p-3" style={{
        borderBottom: '1px solid var(--border-color)',
        backgroundColor: 'var(--background-secondary)'
      }}>
        <div className="flex items-center gap-2">
          <Terminal size={16} style={{ color: moduleColor }} />
          <span style={{ color: moduleColor, fontSize: '14px', fontWeight: 600 }}>
            LOGS: {modName}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Lines selector */}
          <select
            value={lines}
            onChange={(e) => setLines(Number(e.target.value))}
            className="px-2 py-1 text-xs rounded"
            style={{
              backgroundColor: 'var(--background-tertiary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-color)',
            }}
          >
            <option value={50}>50 lines</option>
            <option value={100}>100 lines</option>
            <option value={200}>200 lines</option>
            <option value={500}>500 lines</option>
            <option value={1000}>1000 lines</option>
          </select>

          {/* Auto-refresh toggle */}
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className="px-2 py-1 text-xs rounded flex items-center gap-1"
            style={{
              backgroundColor: autoRefresh ? moduleColor : 'var(--background-tertiary)',
              color: autoRefresh ? '#000' : 'var(--text-secondary)',
              border: `1px solid ${autoRefresh ? moduleColor : 'var(--border-color)'}`,
            }}
          >
            <RefreshCw size={12} className={autoRefresh ? 'animate-spin' : ''} />
            Auto
          </button>

          {/* Manual refresh */}
          <button
            onClick={fetchLogs}
            disabled={loading}
            className="px-2 py-1 text-xs rounded"
            style={{
              backgroundColor: 'var(--background-tertiary)',
              color: moduleColor,
              border: `1px solid ${moduleColor}`,
              opacity: loading ? 0.5 : 1,
            }}
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>

          {/* Download */}
          <button
            onClick={downloadLogs}
            disabled={!logs[activeTab]}
            className="px-2 py-1 text-xs rounded"
            style={{
              backgroundColor: 'var(--background-tertiary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-color)',
              opacity: !logs[activeTab] ? 0.5 : 1,
            }}
          >
            <Download size={12} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-3 pt-2" style={{ borderBottom: '1px solid var(--border-color)' }}>
        {availableTabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className="px-3 py-1 text-xs rounded-t"
            style={{
              backgroundColor: activeTab === tab ? 'var(--background-secondary)' : 'transparent',
              color: activeTab === tab ? moduleColor : 'var(--text-secondary)',
              borderTop: `2px solid ${activeTab === tab ? moduleColor : 'transparent'}`,
              borderLeft: activeTab === tab ? '1px solid var(--border-color)' : 'none',
              borderRight: activeTab === tab ? '1px solid var(--border-color)' : 'none',
              fontWeight: activeTab === tab ? 600 : 400,
            }}
          >
            {tab.toUpperCase()}
            {logs[tab] && typeof logs[tab] === 'string' && (
              <span className="ml-1" style={{ opacity: 0.6 }}>
                ({logs[tab].split('\n').length})
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Log content */}
      <div className="flex-1 overflow-auto p-3" style={{
        backgroundColor: '#000',
        fontSize: '11px',
        lineHeight: '1.4',
      }}>
        {error ? (
          <div style={{ color: '#ef4444', padding: '20px', textAlign: 'center' }}>
            {error}
          </div>
        ) : loading && Object.keys(logs).length === 0 ? (
          <div style={{ color: moduleColor, padding: '20px', textAlign: 'center' }}>
            Loading logs...
          </div>
        ) : !logs[activeTab] ? (
          <div style={{ color: 'var(--text-secondary)', padding: '20px', textAlign: 'center' }}>
            No {activeTab} logs available
          </div>
        ) : (
          <pre style={{
            margin: 0,
            color: '#0f0',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}>
            {logs[activeTab]}
            <div ref={logsEndRef} />
          </pre>
        )}
      </div>
    </div>
  )
}
