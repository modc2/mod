"use client"

import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowPathIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline'
import { StarIcon as StarSolid } from '@heroicons/react/24/solid'
import { userContext } from '@/context'
import { text2color, shorten } from '@/utils'
import { useRouter } from 'next/navigation'

interface AppStatus {
  port: number
  owner: string
  path: string
  running: boolean
  url: string
}

interface ModEntry {
  name: string
  key: string
  url?: string | { api?: string; app?: string }
  fns?: string[]
}

interface GitHubRepo {
  name: string
  full_name: string
  description: string | null
  url: string
  stars: number
  forks: number
  language: string | null
  updated_at: string
  topics: string[]
}

const FONT = "var(--font-digital), monospace"

const langColors: Record<string, string> = {
  JavaScript: '#f1e05a', TypeScript: '#3178c6', Python: '#3572A5', Rust: '#dea584',
  Go: '#00ADD8', Java: '#b07219', C: '#555555', 'C++': '#f34b7d', Ruby: '#701516',
  Solidity: '#AA6746', Swift: '#F05138', Kotlin: '#A97BFF', Dart: '#00B4AB',
  PHP: '#4F5D95', Shell: '#89e051', Lua: '#000080', Zig: '#ec915c',
}

export function ModsTab({ show }: { show: boolean }) {
  const { client, user } = userContext()
  const router = useRouter()
  const [apps, setApps] = useState<Record<string, AppStatus>>({})
  const [ownedMods, setOwnedMods] = useState<ModEntry[]>([])
  const [apiServers, setApiServers] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null)
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [filter, setFilter] = useState<'mine' | 'all'>('mine')

  // Create form state
  const [createUrl, setCreateUrl] = useState('')
  const [createName, setCreateName] = useState('')
  const [isRegistering, setIsRegistering] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<GitHubRepo[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const searchTimeout = useRef<NodeJS.Timeout | null>(null)

  const fetchApps = useCallback(async () => {
    if (!client) return
    try {
      const [statusResult, namespaceResult] = await Promise.all([
        client.call('app_status'),
        client.call('namespace'),
      ])
      if (statusResult && typeof statusResult === 'object' && !statusResult.error) {
        setApps(statusResult)
      }
      if (namespaceResult && typeof namespaceResult === 'object' && !namespaceResult.error) {
        setApiServers(namespaceResult)
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [client])

  const fetchOwnedMods = useCallback(async () => {
    if (!client || !user?.key) return
    try {
      const result = await client.call('mods', { key: user.key })
      if (result && Array.isArray(result)) {
        setOwnedMods(result)
      }
    } catch {
      // ignore
    }
  }, [client, user?.key])

  useEffect(() => {
    if (!show) return
    fetchApps()
    fetchOwnedMods()
    const interval = setInterval(fetchApps, 5000)
    return () => clearInterval(interval)
  }, [fetchApps, fetchOwnedMods, show])

  // GitHub search debounce
  useEffect(() => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current)
    if (!searchQuery.trim() || searchQuery.trim().length < 2) {
      setSearchResults([])
      return
    }
    searchTimeout.current = setTimeout(() => {
      searchGitHub(searchQuery.trim())
    }, 400)
    return () => {
      if (searchTimeout.current) clearTimeout(searchTimeout.current)
    }
  }, [searchQuery])

  // Auto-infer name from URL
  useEffect(() => {
    if (isGitUrl(createUrl) && !createName) {
      const inferred = inferModuleName(createUrl)
      if (inferred) setCreateName(inferred)
    }
  }, [createUrl, createName])

  const searchGitHub = async (query: string) => {
    setIsSearching(true)
    try {
      if (client) {
        try {
          const results = await client.call('gitsearch/forward', {
            query, sort: 'stars', order: 'desc', per_page: 6,
          })
          if (Array.isArray(results) && results.length > 0 && !results[0]?.error) {
            setSearchResults(results)
            setIsSearching(false)
            return
          }
        } catch { /* fall through */ }
      }
      const res = await fetch(
        `https://api.github.com/search/repositories?q=${encodeURIComponent(query)}&sort=stars&order=desc&per_page=6`,
        { headers: { 'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'mod-buidl' } }
      )
      if (!res.ok) throw new Error(`GitHub API ${res.status}`)
      const data = await res.json()
      setSearchResults((data.items || []).map((item: any) => ({
        name: item.name, full_name: item.full_name, description: item.description,
        url: item.html_url, stars: item.stargazers_count, forks: item.forks_count,
        language: item.language, updated_at: item.updated_at, topics: item.topics || [],
      })))
    } catch {
      setSearchResults([])
    } finally {
      setIsSearching(false)
    }
  }

  const isGitUrl = (input: string) => {
    if (input.includes('github.com') || input.includes('gitlab.com')) return true
    return /^[\w-]+\/[\w-]+(\.git)?$/.test(input.trim())
  }

  const isCid = (input: string) => {
    return input.startsWith('Qm') || input.startsWith('bafy') || input.startsWith('ipfs://')
  }

  const expandGitUrl = (input: string): string => {
    if (input.includes('github.com') || input.includes('gitlab.com') || input.startsWith('http')) return input
    const match = input.trim().match(/^([\w-]+\/[\w-]+)(\.git)?$/)
    if (match) return `https://github.com/${match[1]}.git`
    return input
  }

  const inferModuleName = (githubUrl: string): string => {
    try {
      let cleanUrl = githubUrl.trim().replace(/\.git$/, '')
      if (cleanUrl.startsWith('git@')) {
        const match = cleanUrl.match(/git@[^:]+:(.+)/)
        if (match) cleanUrl = match[1]
      }
      try {
        const urlObj = new URL(cleanUrl)
        const parts = urlObj.pathname.split('/').filter(p => p)
        if (parts.length >= 2) return parts[parts.length - 1]
      } catch {
        const parts = cleanUrl.split('/').filter(p => p)
        if (parts.length >= 2) return parts[parts.length - 1]
      }
      return ''
    } catch { return '' }
  }

  const isValidCreate = () => createUrl.trim() && (isGitUrl(createUrl) || isCid(createUrl))

  const selectRepo = (repo: GitHubRepo) => {
    setCreateUrl(repo.url + '.git')
    setCreateName(repo.name)
    setSearchQuery('')
    setSearchResults([])
  }

  const handleRegister = async () => {
    if (!isValidCreate() || !user?.key || !client?.token) return
    setIsRegistering(true)
    setMessage(null)
    try {
      const expandedUrl = expandGitUrl(createUrl)
      const result = await client.call('reg', {
        mod: expandedUrl, key: user.key, public: false, token: client.token,
      }, true, {}, 120000)
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${result?.name || createName} registered`, type: 'success' })
        setCreateUrl('')
        setCreateName('')
        setShowCreateForm(false)
        await Promise.all([fetchApps(), fetchOwnedMods()])
      }
    } catch (e: any) {
      setMessage({ text: e?.message || 'Failed to register', type: 'error' })
    } finally {
      setIsRegistering(false)
    }
  }

  const formatStars = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n.toString()

  const isOwner = (app: AppStatus) => {
    return user?.key && app.owner && user.key.toLowerCase() === app.owner.toLowerCase()
  }

  const handleAction = async (action: string, name: string, endpoint: string) => {
    if (!client) return
    setActionLoading(name)
    setMessage(null)
    try {
      const result = await client.call(endpoint, { name })
      if (result?.error) {
        setMessage({ text: result.error, type: 'error' })
      } else {
        setMessage({ text: `${name} ${action}`, type: 'success' })
        await fetchApps()
      }
    } catch (e: any) {
      setMessage({ text: e?.message || `Failed to ${action}`, type: 'error' })
    } finally {
      setActionLoading(null)
    }
  }

  // Build unified list
  const appEntries = Object.entries(apps)
  const myApps = appEntries.filter(([, app]) => isOwner(app))
  const otherApps = appEntries.filter(([, app]) => !isOwner(app))
  const appNames = new Set(appEntries.map(([name]) => name))
  const ownedNotInstalled = ownedMods.filter(m => !appNames.has(m.name))
  const apiServerEntries = Object.entries(apiServers).filter(([name]) => name !== 'api')
  const getApiServer = (name: string) => apiServers[name] || null

  const filtered = filter === 'mine'
    ? { apps: myApps, others: [], mods: ownedNotInstalled }
    : { apps: appEntries, others: [], mods: [] }

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.35, ease: [0.25, 0.1, 0.25, 1] }}
          className="mt-3 pt-3 overflow-hidden"
          style={{ borderTop: '1px solid var(--border-color)' }}
        >
          <div className="space-y-1.5">
            {/* Header */}
            <div className="flex items-center justify-between px-1 mb-3">
              <span className="text-sm uppercase tracking-widest font-bold" style={{
                fontFamily: FONT,
                color: 'var(--text-primary)',
                textShadow: 'var(--effect-text-shadow, 0) 0px 10px var(--text-primary)',
                letterSpacing: '0.2em'
              }}>
                MODULES
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setFilter(f => f === 'mine' ? 'all' : 'mine')}
                  className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider transition-all border"
                  style={{
                    fontFamily: FONT,
                    borderColor: filter === 'mine' ? 'rgba(16,185,129,0.4)' : 'var(--border-color)',
                    color: filter === 'mine' ? '#10b981' : 'var(--text-tertiary)',
                    background: filter === 'mine' ? 'rgba(16,185,129,0.08)' : 'transparent',
                  }}
                >
                  {filter === 'mine' ? 'MINE' : 'ALL'}
                </button>
                <button
                  onClick={() => { setShowCreateForm(!showCreateForm); if (showCreateForm) { setSearchQuery(''); setSearchResults([]); setCreateUrl(''); setCreateName('') } }}
                  className="p-1 transition-all"
                  title="Register module"
                >
                  <svg className={`w-3.5 h-3.5 ${showCreateForm ? 'text-teal-400' : ''}`} style={!showCreateForm ? { color: 'var(--text-tertiary)' } : {}} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                </button>
                <button
                  onClick={() => { fetchApps(); fetchOwnedMods() }}
                  disabled={loading}
                  className="p-1 transition-all disabled:opacity-50"
                  title="Refresh"
                >
                  <ArrowPathIcon className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} style={{ color: 'var(--text-tertiary)' }} />
                </button>
              </div>
            </div>

            {/* Create / Register Form */}
            <AnimatePresence>
              {showCreateForm && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <div className="space-y-2 mb-3 p-2 border" style={{ borderColor: 'rgba(168,85,247,0.3)', background: 'rgba(168,85,247,0.04)' }}>
                    {/* Search GitHub */}
                    <div className="relative">
                      <MagnifyingGlassIcon className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-tertiary)' }} />
                      {isSearching && (
                        <ArrowPathIcon className="w-3 h-3 absolute right-2 top-1/2 -translate-y-1/2 animate-spin" style={{ color: '#a855f7' }} />
                      )}
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search GitHub..."
                        className="w-full pl-7 pr-7 py-1.5 text-xs focus:outline-none transition-colors"
                        style={{
                          fontFamily: FONT, backgroundColor: 'var(--bg-input)',
                          border: '1px solid var(--border-color)', color: 'var(--text-secondary)',
                          fontSize: '11px',
                        }}
                      />
                    </div>

                    {/* Search Results */}
                    <AnimatePresence>
                      {searchResults.length > 0 && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          className="overflow-hidden"
                        >
                          <div className="space-y-1 max-h-[200px] overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
                            {searchResults.map((repo) => (
                              <button
                                key={repo.full_name}
                                onClick={() => selectRepo(repo)}
                                className="w-full text-left px-2 py-1.5 border transition-all hover:border-purple-500/40"
                                style={{
                                  borderColor: 'var(--border-color)', background: 'var(--bg-input)', fontFamily: FONT,
                                }}
                              >
                                <div className="flex items-center justify-between gap-1">
                                  <span className="text-[10px] font-bold truncate" style={{ color: '#a855f7' }}>
                                    {repo.full_name}
                                  </span>
                                  <div className="flex items-center gap-0.5 shrink-0">
                                    <StarSolid className="w-2.5 h-2.5" style={{ color: '#f59e0b' }} />
                                    <span className="text-[9px]" style={{ color: 'var(--text-tertiary)' }}>{formatStars(repo.stars)}</span>
                                  </div>
                                </div>
                                {repo.description && (
                                  <p className="text-[9px] mt-0.5 line-clamp-1" style={{ color: 'var(--text-tertiary)' }}>
                                    {repo.description}
                                  </p>
                                )}
                                <div className="flex items-center gap-2 mt-0.5">
                                  {repo.language && (
                                    <div className="flex items-center gap-1">
                                      <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: langColors[repo.language] || '#737373' }} />
                                      <span className="text-[9px]" style={{ color: 'var(--text-tertiary)' }}>{repo.language}</span>
                                    </div>
                                  )}
                                </div>
                              </button>
                            ))}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>

                    {/* URL input */}
                    <input
                      type="text"
                      value={createUrl}
                      onChange={(e) => { setCreateUrl(e.target.value); setCreateName('') }}
                      placeholder="user/repo or github.com/user/repo.git or Qm..."
                      className="w-full px-2 py-1.5 text-xs focus:outline-none transition-colors"
                      style={{
                        fontFamily: FONT, backgroundColor: 'var(--bg-input)',
                        border: `1px solid ${isValidCreate() ? 'rgba(16,185,129,0.5)' : 'var(--border-color)'}`,
                        color: isValidCreate() ? '#10b981' : 'var(--text-secondary)',
                        fontSize: '11px',
                      }}
                    />

                    {/* Selected name display + register button */}
                    {createUrl && (
                      <div className="flex gap-1.5">
                        {createName && (
                          <div className="flex-1 flex items-center px-2 py-1 text-[10px] font-bold uppercase tracking-wider" style={{ color: text2color(createName), fontFamily: FONT }}>
                            {createName}
                          </div>
                        )}
                        <button
                          onClick={handleRegister}
                          disabled={!isValidCreate() || isRegistering || !user?.key}
                          className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider transition-all border disabled:opacity-30"
                          style={{
                            borderColor: '#a855f7', color: '#a855f7',
                            background: 'rgba(168,85,247,0.1)', fontFamily: FONT,
                          }}
                        >
                          {isRegistering ? <ArrowPathIcon className="w-3 h-3 animate-spin" /> : 'REGISTER'}
                        </button>
                      </div>
                    )}

                    {!user && (
                      <div className="text-[10px] px-1" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>
                        Connect wallet to register
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Status Message */}
            <AnimatePresence>
              {message && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden"
                >
                  <div
                    className="text-[10px] px-2 py-1.5 font-mono mb-2"
                    style={{
                      border: `1px solid ${message.type === 'error' ? 'rgba(239,68,68,0.3)' : 'rgba(16,185,129,0.3)'}`,
                      color: message.type === 'error' ? '#ef4444' : '#10b981',
                      background: message.type === 'error' ? 'rgba(239,68,68,0.1)' : 'rgba(16,185,129,0.1)',
                      fontFamily: FONT,
                    }}
                  >
                    {message.text}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {loading ? (
              <div className="flex items-center justify-center py-6 gap-2">
                <ArrowPathIcon className="w-4 h-4 animate-spin" style={{ color: 'var(--text-tertiary)' }} />
                <span className="text-xs" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>Loading...</span>
              </div>
            ) : filtered.apps.length === 0 && filtered.mods.length === 0 && apiServerEntries.length === 0 ? (
              <div className="text-center py-6 text-xs" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>
                {filter === 'mine' ? 'No modules owned' : 'No module apps found'}
              </div>
            ) : (
              <div className="space-y-3">
                {/* Installed Apps */}
                {filtered.apps.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-[10px] uppercase tracking-widest px-1" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>
                      {filter === 'mine' ? 'YOUR APPS' : 'ALL APPS'}
                    </span>
                    {filtered.apps.map(([name, app]) => (
                      <AppRow
                        key={name}
                        name={name}
                        app={app}
                        owned={!!isOwner(app)}
                        apiServer={getApiServer(name)}
                        isLoading={actionLoading === name}
                        onStart={() => handleAction('started', name, 'serve_app')}
                        onStop={() => handleAction('stopped', name, 'kill_app')}
                        onRemove={() => handleAction('removed', name, 'remove_app')}
                        onNavigate={() => router.push(`/mod/${name}`)}
                      />
                    ))}
                  </div>
                )}

                {/* Owned Mods (not installed as apps) */}
                {filter === 'mine' && filtered.mods.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-[10px] uppercase tracking-widest px-1" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>
                      YOUR MODULES
                    </span>
                    {filtered.mods.map((mod) => (
                      <ModRow
                        key={mod.name}
                        mod={mod}
                        apiServer={getApiServer(mod.name)}
                        onNavigate={() => router.push(`/mod/${mod.name}`)}
                      />
                    ))}
                  </div>
                )}

                {/* Active API Servers */}
                {apiServerEntries.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-[10px] uppercase tracking-widest px-1" style={{ color: 'var(--text-tertiary)', fontFamily: FONT }}>
                      API SERVERS
                    </span>
                    {apiServerEntries.map(([name, addr]) => {
                      if (appNames.has(name)) return null
                      return (
                        <div
                          key={name}
                          className="flex items-center gap-3 px-3 py-2 border transition-all cursor-pointer hover:border-emerald-500/30"
                          style={{ borderColor: 'var(--border-color)', background: 'var(--bg-input)', fontFamily: FONT }}
                          onClick={() => router.push(`/mod/${name}`)}
                        >
                          <div className="w-2 h-2 rounded-full shrink-0" style={{ background: '#10b981', boxShadow: '0 0 6px #10b981' }} />
                          <span className="font-bold uppercase tracking-wider text-xs flex-1" style={{ color: text2color(name) }}>{name}</span>
                          <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>{addr}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function AppRow({
  name, app, owned, apiServer, isLoading, onStart, onStop, onRemove, onNavigate,
}: {
  name: string
  app: AppStatus
  owned: boolean
  apiServer: string | null
  isLoading: boolean
  onStart: () => void
  onStop: () => void
  onRemove: () => void
  onNavigate: () => void
}) {
  const color = text2color(name)

  return (
    <div
      className="flex items-center gap-3 px-3 py-2.5 border-2 transition-all cursor-pointer hover:border-emerald-500/20"
      style={{
        borderColor: 'var(--border-color)',
        background: 'var(--bg-input)',
        fontFamily: FONT,
      }}
      onClick={onNavigate}
    >
      <div
        className="w-2 h-2 rounded-full shrink-0"
        style={{
          background: app.running ? '#10b981' : '#6b7280',
          boxShadow: app.running ? '0 0 6px #10b981' : 'none',
        }}
      />
      <div className="flex-1 min-w-0">
        <span className="font-bold uppercase tracking-wider text-xs" style={{ color }}>
          {name}
        </span>
        {app.port > 0 && (
          <span className="ml-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
            :{app.port}
          </span>
        )}
        {apiServer && (
          <span className="ml-1 text-[10px]" style={{ color: 'rgba(59,130,246,0.7)' }}>
            api
          </span>
        )}
      </div>
      {!owned && app.owner && (
        <span className="text-[9px] shrink-0" style={{ color: 'var(--text-tertiary)' }}>
          {shorten(app.owner)}
        </span>
      )}
      <span
        className="text-[10px] font-bold uppercase tracking-wider shrink-0"
        style={{ color: app.running ? '#10b981' : '#6b7280' }}
      >
        {app.running ? 'ON' : 'OFF'}
      </span>
      {owned && (
        <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
          {app.running ? (
            <button
              onClick={onStop}
              disabled={isLoading}
              className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-all border"
              style={{
                borderColor: '#ef4444', color: '#ef4444', background: 'transparent',
                fontFamily: FONT, opacity: isLoading ? 0.5 : 1,
              }}
            >
              {isLoading ? '...' : 'STOP'}
            </button>
          ) : (
            <button
              onClick={onStart}
              disabled={isLoading}
              className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-all border"
              style={{
                borderColor: '#10b981', color: '#10b981', background: 'transparent',
                fontFamily: FONT, opacity: isLoading ? 0.5 : 1,
              }}
            >
              {isLoading ? '...' : 'START'}
            </button>
          )}
          <button
            onClick={() => {
              if (confirm(`Remove ${name}?`)) onRemove()
            }}
            disabled={isLoading}
            className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-all border"
            style={{
              borderColor: '#6b7280', color: '#6b7280', background: 'transparent',
              fontFamily: FONT, opacity: isLoading ? 0.5 : 1,
            }}
          >
            RM
          </button>
        </div>
      )}
    </div>
  )
}

function ModRow({
  mod, apiServer, onNavigate,
}: {
  mod: ModEntry
  apiServer: string | null
  onNavigate: () => void
}) {
  const color = text2color(mod.name)
  const isServed = !!apiServer

  return (
    <div
      className="flex items-center gap-3 px-3 py-2 border transition-all cursor-pointer hover:border-emerald-500/20"
      style={{
        borderColor: 'var(--border-color)',
        background: 'var(--bg-input)',
        fontFamily: FONT,
      }}
      onClick={onNavigate}
    >
      <div
        className="w-2 h-2 rounded-full shrink-0"
        style={{
          background: isServed ? '#3b82f6' : '#6b7280',
          boxShadow: isServed ? '0 0 6px #3b82f6' : 'none',
          opacity: isServed ? 1 : 0.4,
        }}
      />
      <span className="font-bold uppercase tracking-wider text-xs flex-1" style={{ color }}>
        {mod.name}
      </span>
      {isServed && (
        <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
          {apiServer}
        </span>
      )}
      {mod.fns && (
        <span className="text-[9px]" style={{ color: 'var(--text-tertiary)' }}>
          {mod.fns.length} fns
        </span>
      )}
    </div>
  )
}
