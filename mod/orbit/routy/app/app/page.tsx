'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'

const API = process.env.NEXT_PUBLIC_ROUTY_API || 'http://localhost:3001'

interface Website {
  name: string
  target_url: string
  description: string | null
  storage_type: string | null
  cid: string | null
  created_at: number
}

interface Stats {
  cpu_usage_percent: number
  apps: number
  apis: number
  total: number
  max_websites: number
}

type ViewMode = 'grid' | 'list'
type Tab = 'all' | 'apps' | 'apis'

export default function Home() {
  const [apps, setApps] = useState<Website[]>([])
  const [apis, setApis] = useState<Website[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState<Tab>('all')
  const [view, setView] = useState<ViewMode>('grid')
  const [lastSync, setLastSync] = useState<Date | null>(null)
  const [connected, setConnected] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [ws, st] = await Promise.all([
        fetch(`${API}/_api/websites`).then(r => r.json()),
        fetch(`${API}/_api/stats`).then(r => r.json()),
      ])
      setApps(ws.apps || [])
      setApis(ws.apis || [])
      setStats(st)
      setError(null)
      setConnected(true)
    } catch {
      setError('Cannot reach routy gateway on :3001')
      setConnected(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 4000)
    return () => clearInterval(id)
  }, [refresh])

  const sync = async () => {
    setSyncing(true)
    try {
      await fetch(`${API}/_api/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      await new Promise(r => setTimeout(r, 400))
      await refresh()
      setLastSync(new Date())
    } catch {
      setError('Sync failed')
    }
    setSyncing(false)
  }

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    const filterFn = (w: Website) =>
      w.name.toLowerCase().includes(q) ||
      (w.description?.toLowerCase().includes(q) ?? false) ||
      w.target_url.toLowerCase().includes(q)

    const taggedApps = apps.filter(filterFn).map(w => ({ ...w, _type: 'app' as const }))
    const taggedApis = apis.filter(filterFn).map(w => ({ ...w, _type: 'api' as const }))

    if (tab === 'apps') return taggedApps
    if (tab === 'apis') return taggedApis
    return [...taggedApps, ...taggedApis]
  }, [apps, apis, search, tab])

  const cpuColor = stats
    ? stats.cpu_usage_percent > 80
      ? 'var(--red)'
      : stats.cpu_usage_percent > 50
        ? 'var(--yellow)'
        : 'var(--green)'
    : 'var(--text-muted)'

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '40px 24px 80px' }}>
      {/* Header */}
      <header style={{ marginBottom: 36 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 'var(--radius)',
              background: 'linear-gradient(135deg, var(--accent), var(--purple))',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 18, fontWeight: 700, color: '#fff',
            }}>
              R
            </div>
            <div>
              <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.03em', lineHeight: 1.2 }}>
                routy
              </h1>
              <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 1 }}>local gateway router</p>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* Connection indicator */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '6px 12px', borderRadius: 20,
              background: connected ? 'var(--green-dim)' : 'var(--red-dim)',
              border: `1px solid ${connected ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}`,
            }}>
              <div style={{
                width: 7, height: 7, borderRadius: '50%',
                background: connected ? 'var(--green)' : 'var(--red)',
                boxShadow: connected ? '0 0 6px var(--green)' : '0 0 6px var(--red)',
              }} className={connected ? 'pulse' : ''} />
              <span style={{ fontSize: 12, fontWeight: 500, color: connected ? 'var(--green)' : 'var(--red)' }}>
                {connected ? 'online' : 'offline'}
              </span>
            </div>

            <button
              onClick={sync}
              disabled={syncing || !connected}
              style={{
                background: 'var(--bg-card)', border: '1px solid var(--border)',
                color: syncing ? 'var(--text-muted)' : 'var(--accent)',
                padding: '7px 16px', borderRadius: 'var(--radius-sm)',
                cursor: syncing || !connected ? 'default' : 'pointer',
                fontSize: 13, fontWeight: 500,
                opacity: syncing ? 0.6 : 1,
                transition: 'all 0.15s ease',
              }}
            >
              {syncing ? (
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Spinner /> syncing
                </span>
              ) : 'sync'}
            </button>
          </div>
        </div>
      </header>

      {/* Error Banner */}
      {error && (
        <div className="animate-in" style={{
          background: 'var(--red-dim)', border: '1px solid rgba(239,68,68,0.2)',
          borderRadius: 'var(--radius)', padding: '12px 16px', marginBottom: 20,
          color: 'var(--red)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5"/><path d="M8 4.5v4M8 10.5v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
          {error}
        </div>
      )}

      {/* Stats Row */}
      {stats && (
        <div className="animate-in" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 28 }}>
          <StatCard label="apps" value={stats.apps} color="var(--green)" dimColor="var(--green-dim)" />
          <StatCard label="apis" value={stats.apis} color="var(--accent)" dimColor="rgba(59,130,246,0.1)" />
          <StatCard label="total" value={stats.total} color="var(--purple)" dimColor="var(--purple-dim)" />
          <StatCard label="cpu" value={`${stats.cpu_usage_percent.toFixed(0)}%`} color={cpuColor}
            dimColor={stats.cpu_usage_percent > 80 ? 'var(--red-dim)' : stats.cpu_usage_percent > 50 ? 'var(--yellow-dim)' : 'var(--green-dim)'}
            bar={stats.cpu_usage_percent} />
        </div>
      )}

      {/* Toolbar: search + tabs + view toggle */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20,
        flexWrap: 'wrap',
      }}>
        {/* Search */}
        <div style={{
          flex: 1, minWidth: 200, position: 'relative',
        }}>
          <svg style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }}
            width="15" height="15" viewBox="0 0 16 16" fill="none">
            <circle cx="7" cy="7" r="5.5" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search services..."
            style={{
              width: '100%', padding: '9px 12px 9px 36px',
              background: 'var(--bg-raised)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)', color: 'var(--text)',
              fontSize: 13, outline: 'none',
            }}
          />
        </div>

        {/* Tabs */}
        <div style={{
          display: 'flex', background: 'var(--bg-raised)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)', overflow: 'hidden',
        }}>
          {(['all', 'apps', 'apis'] as Tab[]).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                padding: '8px 16px', fontSize: 13, fontWeight: 500, border: 'none',
                cursor: 'pointer', transition: 'all 0.15s',
                background: tab === t ? 'var(--bg-card)' : 'transparent',
                color: tab === t ? 'var(--text)' : 'var(--text-muted)',
                borderRight: t !== 'apis' ? '1px solid var(--border)' : 'none',
              }}
            >
              {t}{t !== 'all' && stats ? ` (${t === 'apps' ? stats.apps : stats.apis})` : ''}
            </button>
          ))}
        </div>

        {/* View toggle */}
        <div style={{
          display: 'flex', background: 'var(--bg-raised)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)', overflow: 'hidden',
        }}>
          <button onClick={() => setView('grid')} style={{
            padding: '8px 10px', border: 'none', cursor: 'pointer',
            background: view === 'grid' ? 'var(--bg-card)' : 'transparent',
            color: view === 'grid' ? 'var(--text)' : 'var(--text-muted)',
            borderRight: '1px solid var(--border)', display: 'flex',
          }}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <rect x="1" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
              <rect x="9" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
              <rect x="1" y="9" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
              <rect x="9" y="9" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.3"/>
            </svg>
          </button>
          <button onClick={() => setView('list')} style={{
            padding: '8px 10px', border: 'none', cursor: 'pointer',
            background: view === 'list' ? 'var(--bg-card)' : 'transparent',
            color: view === 'list' ? 'var(--text)' : 'var(--text-muted)',
            display: 'flex',
          }}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M1 3h14M1 8h14M1 13h14" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
            </svg>
          </button>
        </div>
      </div>

      {/* Service Grid / List */}
      {filtered.length === 0 ? (
        <div className="animate-in" style={{
          textAlign: 'center', padding: '60px 20px',
          color: 'var(--text-muted)', fontSize: 14,
          background: 'var(--bg-raised)', borderRadius: 'var(--radius)',
          border: '1px solid var(--border)',
        }}>
          {search ? `No services match "${search}"` : 'No services registered'}
        </div>
      ) : view === 'grid' ? (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 10,
        }}>
          {filtered.map((w, i) => (
            <ServiceCard key={`${w._type}-${w.name}`} website={w} type={w._type} index={i} />
          ))}
        </div>
      ) : (
        <div style={{
          background: 'var(--bg-raised)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['name', 'type', 'target', 'storage', 'route'].map(h => (
                  <th key={h} style={{
                    textAlign: 'left', padding: '10px 16px', color: 'var(--text-muted)',
                    fontWeight: 500, fontSize: 11, textTransform: 'uppercase',
                    letterSpacing: '0.06em', background: 'var(--bg-card)',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((w, i) => {
                const linkPrefix = w._type === 'api' ? '/api/' : '/'
                return (
                  <tr key={`${w._type}-${w.name}`} style={{
                    borderBottom: '1px solid var(--border-subtle)',
                    transition: 'background 0.1s',
                  }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={{ padding: '10px 16px', fontWeight: 500, fontSize: 14 }}>{w.name}</td>
                    <td style={{ padding: '10px 16px' }}>
                      <TypeBadge type={w._type} />
                    </td>
                    <td style={{ padding: '10px 16px' }}>
                      <code style={{ fontSize: 12, color: 'var(--text-muted)' }}>{w.target_url}</code>
                    </td>
                    <td style={{ padding: '10px 16px' }}>
                      {w.storage_type ? (
                        <span style={{
                          fontSize: 11, padding: '2px 7px', borderRadius: 4,
                          background: 'var(--purple-dim)', color: 'var(--purple)',
                          fontWeight: 500,
                        }}>{w.storage_type}</span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>-</span>
                      )}
                    </td>
                    <td style={{ padding: '10px 16px' }}>
                      <a href={`${linkPrefix}${w.name}/`} target="_blank" rel="noreferrer"
                        style={{ fontFamily: 'monospace', fontSize: 12 }}>
                        {linkPrefix}{w.name}/
                      </a>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Footer */}
      <footer style={{
        marginTop: 40, paddingTop: 20, borderTop: '1px solid var(--border-subtle)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        color: 'var(--text-muted)', fontSize: 12,
      }}>
        <span>routy gateway &middot; port {stats?.max_websites ? `${stats.total}/${stats.max_websites} slots` : ':3001'}</span>
        {lastSync && <span>last sync {lastSync.toLocaleTimeString()}</span>}
      </footer>
    </div>
  )
}

/* ── Components ── */

function StatCard({ label, value, color, dimColor, bar }: {
  label: string; value: number | string; color: string; dimColor: string; bar?: number
}) {
  return (
    <div style={{
      background: 'var(--bg-raised)', border: '1px solid var(--border)',
      borderRadius: 'var(--radius)', padding: '16px 18px',
      position: 'relative', overflow: 'hidden',
    }}>
      {bar !== undefined && (
        <div style={{
          position: 'absolute', bottom: 0, left: 0, right: 0, height: 3,
          background: 'var(--border-subtle)',
        }}>
          <div style={{
            height: '100%', width: `${Math.min(bar, 100)}%`,
            background: color, transition: 'width 0.5s ease',
            borderRadius: '0 2px 2px 0',
          }} />
        </div>
      )}
      <div style={{
        position: 'absolute', top: 12, right: 14, width: 32, height: 32,
        borderRadius: '50%', background: dimColor, display: 'flex',
        alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color, letterSpacing: '-0.02em', lineHeight: 1 }}>
        {value}
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 5, fontWeight: 500 }}>
        {label}
      </div>
    </div>
  )
}

function ServiceCard({ website: w, type, index }: {
  website: Website & { _type: 'app' | 'api' }; type: 'app' | 'api'; index: number
}) {
  const linkPrefix = type === 'api' ? '/api/' : '/'
  const route = `${linkPrefix}${w.name}/`

  return (
    <div className="animate-in" style={{
      background: 'var(--bg-raised)', border: '1px solid var(--border)',
      borderRadius: 'var(--radius)', padding: '18px',
      transition: 'border-color 0.15s, background 0.15s',
      cursor: 'default', animationDelay: `${index * 30}ms`,
    }}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = 'var(--text-muted)'
        e.currentTarget.style.background = 'var(--bg-hover)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = 'var(--border)'
        e.currentTarget.style.background = 'var(--bg-raised)'
      }}
    >
      {/* Top row: name + type badge */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{w.name}</span>
        <TypeBadge type={type} />
      </div>

      {/* Description */}
      {w.description && (
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.4 }}>
          {w.description}
        </p>
      )}

      {/* Target URL */}
      <div style={{ marginBottom: 12 }}>
        <code style={{
          fontSize: 11, color: 'var(--text-muted)',
          background: 'var(--bg-card)', padding: '3px 7px',
          borderRadius: 4, display: 'inline-block',
        }}>
          {w.target_url}
        </code>
      </div>

      {/* Meta row: storage + CID */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        {w.storage_type && (
          <span style={{
            fontSize: 10, padding: '2px 7px', borderRadius: 4,
            background: 'var(--purple-dim)', color: 'var(--purple)',
            fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>{w.storage_type}</span>
        )}
        {w.cid && (
          <span style={{
            fontSize: 10, color: 'var(--text-muted)', fontFamily: 'monospace',
          }} title={w.cid}>
            {w.cid.length > 16 ? `${w.cid.slice(0, 8)}...${w.cid.slice(-6)}` : w.cid}
          </span>
        )}
      </div>

      {/* Route link */}
      <a href={route} target="_blank" rel="noreferrer"
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          padding: '8px 0', borderRadius: 'var(--radius-sm)',
          background: type === 'app' ? 'var(--green-dim)' : 'rgba(59,130,246,0.08)',
          border: `1px solid ${type === 'app' ? 'rgba(34,197,94,0.15)' : 'rgba(59,130,246,0.15)'}`,
          color: type === 'app' ? 'var(--green)' : 'var(--accent)',
          fontSize: 12, fontWeight: 500, fontFamily: 'monospace',
          textDecoration: 'none', transition: 'all 0.15s',
        }}
        onMouseEnter={e => { e.currentTarget.style.opacity = '0.8' }}
        onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
      >
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
          <path d="M6 3h7v7M13 3L6 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
        {route}
      </a>
    </div>
  )
}

function TypeBadge({ type }: { type: 'app' | 'api' }) {
  const isApp = type === 'app'
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
      letterSpacing: '0.06em', padding: '3px 8px', borderRadius: 4,
      background: isApp ? 'var(--green-dim)' : 'rgba(59,130,246,0.1)',
      color: isApp ? 'var(--green)' : 'var(--accent)',
      border: `1px solid ${isApp ? 'rgba(34,197,94,0.15)' : 'rgba(59,130,246,0.15)'}`,
    }}>
      {type}
    </span>
  )
}

function Spinner() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" style={{ animation: 'spin 0.8s linear infinite' }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
      <circle cx="8" cy="8" r="6" stroke="var(--text-muted)" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" strokeLinecap="round"/>
    </svg>
  )
}
