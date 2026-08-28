"use client"

// CONFIG — the raw deployment record for the selected network, as the chain
// module stores it.

import { useState, useEffect, JSX } from 'react'
import { TERM_FONT, chainApi } from './shared'
import { Empty, panelStyle } from './ui'

export function ConfigTab({ network }: { network: string }) {
  const [config, setConfig] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['root', 'root.contracts']))

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    chainApi('/config', { body: { network } })
      .then(d => { if (!cancelled) setConfig(d.config ?? d) })
      .catch(() => { if (!cancelled) setConfig(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [network])

  const toggle = (key: string) => setExpanded(prev => {
    const next = new Set(prev)
    next.has(key) ? next.delete(key) : next.add(key)
    return next
  })

  if (loading) return <Empty>Loading config…</Empty>
  if (!config || Object.keys(config).length === 0) return <Empty>No config for {network}.</Empty>

  const renderValue = (val: any, path: string, depth: number): JSX.Element => {
    if (val === null || val === undefined) {
      return <span style={{ color: 'var(--text-tertiary)' }}>null</span>
    }
    if (typeof val !== 'object') {
      return (
        <span style={{ color: typeof val === 'string' ? '#10b981' : '#3b82f6' }}>
          {typeof val === 'string' ? `"${val}"` : String(val)}
        </span>
      )
    }
    const entries = Object.entries(val)
    const isExpanded = expanded.has(path)
    return (
      <div style={{ marginLeft: depth > 0 ? '16px' : '0' }}>
        <button
          onClick={() => toggle(path)}
          style={{
            fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-tertiary)',
            background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0',
          }}
        >
          {isExpanded ? '▼' : '▶'} {entries.length} {Array.isArray(val) ? 'items' : 'keys'}
        </button>
        {isExpanded && entries.map(([k, v]) => (
          <div key={k} style={{ padding: '2px 0' }}>
            <span style={{ color: 'var(--text-secondary)', fontFamily: TERM_FONT, fontSize: '12px' }}>{k}: </span>
            {renderValue(v, `${path}.${k}`, depth + 1)}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div style={{ ...panelStyle, padding: '16px', fontFamily: TERM_FONT, fontSize: '12px' }}>
      <div style={{ color: 'var(--text-tertiary)', marginBottom: '12px', letterSpacing: '0.1em', fontSize: '11px' }}>
        {network.toUpperCase()} DEPLOYMENT CONFIG
      </div>
      {renderValue(config, 'root', 0)}
    </div>
  )
}
