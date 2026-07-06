"use client";

import { useState, useEffect, useRef, useCallback } from 'react'
import { userContext } from '@/context/UserContext'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircleIcon, ExclamationCircleIcon, MagnifyingGlassIcon, StarIcon, ArrowPathIcon } from '@heroicons/react/24/outline'
import { StarIcon as StarSolid } from '@heroicons/react/24/solid'
import { useRouter } from 'next/navigation'
import Link from 'next/link'

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

export default function CreateModule() {
  const router = useRouter()
  const { user, client } = userContext()
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [comment, setComment] = useState('')
  const [registerToKey, setRegisterToKey] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [focusedField, setFocusedField] = useState<string | null>(null)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  // GitHub search state
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<GitHubRepo[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [showSearch, setShowSearch] = useState(true)
  const searchTimeout = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    if (user?.key && !registerToKey) {
      setRegisterToKey(user.key)
    }
  }, [user?.key])

  useEffect(() => {
    if (isGitUrl(url) && !name) {
      const inferredName = inferModuleName(url)
      if (inferredName) setName(inferredName)
    }
  }, [url, name])

  // Debounced GitHub search
  useEffect(() => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current)
    if (!searchQuery.trim() || searchQuery.trim().length < 2) {
      setSearchResults([])
      setSearchError(null)
      return
    }
    searchTimeout.current = setTimeout(() => {
      searchGitHub(searchQuery.trim())
    }, 400)
    return () => {
      if (searchTimeout.current) clearTimeout(searchTimeout.current)
    }
  }, [searchQuery])

  const searchGitHub = async (query: string) => {
    setIsSearching(true)
    setSearchError(null)
    try {
      // Try using the gitsearch orbit module first
      if (client) {
        try {
          const results = await client.call('gitsearch/forward', {
            query,
            sort: 'stars',
            order: 'desc',
            per_page: 12,
          })
          if (Array.isArray(results) && results.length > 0 && !results[0]?.error) {
            setSearchResults(results)
            setIsSearching(false)
            return
          }
        } catch {
          // Module not available, fall through to direct API
        }
      }
      // Fallback: hit GitHub API directly
      const res = await fetch(
        `https://api.github.com/search/repositories?q=${encodeURIComponent(query)}&sort=stars&order=desc&per_page=12`,
        {
          headers: {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'mod-buidl',
          },
        }
      )
      if (!res.ok) throw new Error(`GitHub API ${res.status}`)
      const data = await res.json()
      const repos: GitHubRepo[] = (data.items || []).map((item: any) => ({
        name: item.name,
        full_name: item.full_name,
        description: item.description,
        url: item.html_url,
        stars: item.stargazers_count,
        forks: item.forks_count,
        language: item.language,
        updated_at: item.updated_at,
        topics: item.topics || [],
      }))
      setSearchResults(repos)
    } catch (err: any) {
      setSearchError(err?.message || 'Search failed')
      setSearchResults([])
    } finally {
      setIsSearching(false)
    }
  }

  const selectRepo = (repo: GitHubRepo) => {
    setUrl(repo.url + '.git')
    setName(repo.name)
    setShowSearch(false)
  }

  const isGitUrl = (input: string) => {
    if (input.includes('github.com') || input.includes('gitlab.com')) return true
    const shorthandPattern = /^[\w-]+\/[\w-]+(\.git)?$/
    return shorthandPattern.test(input.trim())
  }

  const isCid = (input: string) => {
    return input.startsWith('Qm') || input.startsWith('bafy') || input.startsWith('ipfs://')
  }

  const expandGitUrl = (input: string): string => {
    if (input.includes('github.com') || input.includes('gitlab.com') || input.startsWith('http')) return input
    const shorthandPattern = /^([\w-]+\/[\w-]+)(\.git)?$/
    const match = input.trim().match(shorthandPattern)
    if (match) return `https://github.com/${match[1]}.git`
    return input
  }

  const inferModuleName = (githubUrl: string): string => {
    try {
      let cleanUrl = githubUrl.trim()
      if (cleanUrl.startsWith('git@')) {
        const match = cleanUrl.match(/git@[^:]+:(.+)/)
        if (match) cleanUrl = match[1]
      }
      cleanUrl = cleanUrl.replace(/\.git$/, '')
      try {
        const urlObj = new URL(cleanUrl)
        const pathParts = urlObj.pathname.split('/').filter(p => p)
        if (pathParts.length >= 2) return pathParts[pathParts.length - 1]
      } catch {
        const pathParts = cleanUrl.split('/').filter(p => p)
        if (pathParts.length >= 2) return pathParts[pathParts.length - 1]
      }
      return ''
    } catch {
      return ''
    }
  }

  const isValidInput = () => url.trim() && (isGitUrl(url) || isCid(url))

  const getInputType = () => {
    if (!url.trim()) return ''
    if (isGitUrl(url)) return 'GitHub URL'
    if (isCid(url)) return 'IPFS CID'
    return 'Invalid'
  }

  const handleSubmit = async () => {
    if (!isValidInput() || !user?.key || !registerToKey.trim()) return
    setIsSubmitting(true)
    setError(null)
    setResult(null)
    try {
      if (!client?.token) throw new Error('Authentication required. Please connect your wallet.')
      const expandedUrl = expandGitUrl(url)
      const response = await client.call('reg', {
        mod: expandedUrl,
        key: registerToKey.trim(),
        public: false,
        token: client.token,
      }, true, {}, 120000)
      setResult(response)
      setUrl('')
      setName('')
      setComment('')
    } catch (err: any) {
      setError(err?.message || 'Failed to register module')
    } finally {
      setIsSubmitting(false)
    }
  }

  const formatStars = (n: number) => {
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
    return n.toString()
  }

  const timeAgo = (dateStr: string) => {
    const diff = Date.now() - new Date(dateStr).getTime()
    const days = Math.floor(diff / 86400000)
    if (days < 1) return 'today'
    if (days < 30) return `${days}d ago`
    if (days < 365) return `${Math.floor(days / 30)}mo ago`
    return `${Math.floor(days / 365)}y ago`
  }

  const langColors: Record<string, string> = {
    JavaScript: '#f1e05a', TypeScript: '#3178c6', Python: '#3572A5', Rust: '#dea584',
    Go: '#00ADD8', Java: '#b07219', C: '#555555', 'C++': '#f34b7d', Ruby: '#701516',
    Solidity: '#AA6746', Swift: '#F05138', Kotlin: '#A97BFF', Dart: '#00B4AB',
    PHP: '#4F5D95', Shell: '#89e051', Lua: '#000080', Zig: '#ec915c',
  }

  return (
    <div className="flex-1 flex flex-col gap-5 min-h-0 overflow-visible p-6 pb-8">
      {/* GitHub Search */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <button
            onClick={() => setShowSearch(!showSearch)}
            className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider transition-colors"
            style={{ color: showSearch ? '#a855f7' : '#737373', fontFamily: 'IBM Plex Mono, monospace' }}
          >
            <MagnifyingGlassIcon className="w-4 h-4" />
            Search GitHub
          </button>
          {url && (
            <button
              onClick={() => { setShowSearch(true); setUrl(''); setName('') }}
              className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors font-mono"
            >
              clear selection
            </button>
          )}
        </div>

        <AnimatePresence>
          {showSearch && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <div className="space-y-3">
                {/* Search input */}
                <div className="relative">
                  <MagnifyingGlassIcon className="w-5 h-5 absolute left-4 top-1/2 -translate-y-1/2 text-neutral-500 z-10" />
                  {isSearching && (
                    <ArrowPathIcon className="w-4 h-4 absolute right-4 top-1/2 -translate-y-1/2 text-purple-400 animate-spin z-10" />
                  )}
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search repos... e.g. 'machine learning pytorch' or 'solana nft'"
                    className="w-full border border-neutral-800 text-neutral-200 pl-12 pr-10 py-3 rounded-lg focus:outline-none text-sm bg-black/80 placeholder-neutral-600 focus:border-purple-500/60 transition-all"
                    style={{ fontFamily: 'IBM Plex Mono, monospace' }}
                  />
                </div>

                {/* Search results */}
                {searchError && (
                  <p className="text-red-400/80 text-xs font-mono px-1">{searchError}</p>
                )}

                {searchResults.length > 0 && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-[340px] overflow-y-auto pr-1 scrollbar-thin scrollbar-thumb-neutral-800 scrollbar-track-transparent">
                    {searchResults.map((repo) => (
                      <button
                        key={repo.full_name}
                        onClick={() => selectRepo(repo)}
                        className="text-left p-3 rounded-lg border border-neutral-800/80 bg-neutral-950/60 hover:border-purple-500/50 hover:bg-purple-500/5 transition-all group"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="text-sm font-bold text-neutral-200 group-hover:text-purple-300 transition-colors truncate" style={{ fontFamily: 'IBM Plex Mono, monospace' }}>
                            {repo.full_name}
                          </span>
                          <div className="flex items-center gap-1 shrink-0">
                            <StarSolid className="w-3.5 h-3.5 text-amber-500/80" />
                            <span className="text-xs text-amber-500/80 font-mono">{formatStars(repo.stars)}</span>
                          </div>
                        </div>
                        {repo.description && (
                          <p className="text-xs text-neutral-500 mt-1.5 line-clamp-2 leading-relaxed">
                            {repo.description}
                          </p>
                        )}
                        <div className="flex items-center gap-3 mt-2">
                          {repo.language && (
                            <div className="flex items-center gap-1.5">
                              <span
                                className="w-2.5 h-2.5 rounded-full"
                                style={{ backgroundColor: langColors[repo.language] || '#737373' }}
                              />
                              <span className="text-xs text-neutral-500">{repo.language}</span>
                            </div>
                          )}
                          <span className="text-xs text-neutral-600">{timeAgo(repo.updated_at)}</span>
                        </div>
                        {repo.topics.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {repo.topics.slice(0, 3).map((t) => (
                              <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400/70 border border-purple-500/20">
                                {t}
                              </span>
                            ))}
                            {repo.topics.length > 3 && (
                              <span className="text-[10px] text-neutral-600">+{repo.topics.length - 3}</span>
                            )}
                          </div>
                        )}
                      </button>
                    ))}
                  </div>
                )}

                {searchQuery.trim().length >= 2 && !isSearching && searchResults.length === 0 && !searchError && (
                  <p className="text-neutral-600 text-xs font-mono text-center py-4">No repositories found</p>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Divider */}
      <div className="border-t border-neutral-800/50" />

      {/* URL + Register form */}
      <div className="flex-shrink-0 space-y-3 relative">
        {/* First Row: URL Input */}
        <div className="relative group">
          <input
            type="text"
            value={url}
            onChange={(e) => { setUrl(e.target.value); if (!showSearch) setShowSearch(false) }}
            onFocus={() => setFocusedField('url')}
            onBlur={() => setFocusedField(null)}
            placeholder="user/repo  or  github.com/user/repo.git  or  Qm..."
            className="w-full border-2 text-cyan-400 px-6 py-4 rounded-xl focus:outline-none text-lg bg-black/90 backdrop-blur-sm placeholder-neutral-600 hover:border-purple-500/60 transition-all duration-300 font-mono relative z-10"
            style={{
              borderColor: focusedField === 'url'
                ? (isValidInput() ? '#22c55e' : '#a855f7')
                : url ? '#22c55e80' : '#52525b',
              fontFamily: 'IBM Plex Mono, monospace',
            }}
          />
          {url && (
            <div className="absolute right-4 top-1/2 -translate-y-1/2 z-20">
              <span className={`text-sm font-bold px-3 py-1.5 rounded-lg ${
                isValidInput()
                  ? 'bg-green-500/20 text-green-400 border border-green-500/50'
                  : 'bg-red-500/20 text-red-400 border border-red-500/50'
              }`}>
                {isValidInput() ? `${getInputType()}` : 'Invalid'}
              </span>
            </div>
          )}
        </div>

        {/* Second Row: Key Input + Register Button */}
        <div className="flex gap-3">


          <button
            onClick={handleSubmit}
            disabled={!isValidInput() || isSubmitting || !user || !registerToKey.trim()}
            className="px-10 py-4 rounded-xl font-bold text-lg uppercase transition-all disabled:opacity-30 disabled:cursor-not-allowed border-2 hover:shadow-[0_0_30px_rgba(168,85,247,0.5)] hover:scale-[1.02] active:scale-[0.98] relative overflow-hidden group whitespace-nowrap"
            style={{
              borderColor: '#a855f7',
              color: '#a855f7',
              background: 'linear-gradient(135deg, rgba(168, 85, 247, 0.2), rgba(147, 51, 234, 0.3))',
              fontFamily: 'IBM Plex Mono, monospace',
            }}
          >
            <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
            {isSubmitting ? (
              <span className="flex items-center gap-3 relative z-10">
                <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>Registering...</span>
              </span>
            ) : (
              <span className="relative z-10">Register</span>
            )}
          </button>
        </div>
      </div>

      {/* Output Section */}
      <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-neutral-700/50 scrollbar-track-transparent">
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: "spring", bounce: 0.4, duration: 0.8 }}
            className="rounded-2xl border-[3px] bg-gradient-to-br from-green-950/60 via-emerald-950/40 to-black overflow-hidden shadow-2xl relative"
            style={{
              borderColor: '#22c55e',
              fontFamily: 'IBM Plex Mono, monospace',
              boxShadow: '0 0 60px rgba(34, 197, 94, 0.4), 0 8px 32px rgba(0, 0, 0, 0.8)',
            }}
          >
            <div className="absolute inset-0 bg-gradient-to-br from-green-500/10 to-transparent rounded-2xl" />
            <div className="px-6 py-5 border-b-2 border-green-500/40 bg-gradient-to-r from-green-900/50 to-emerald-900/50 backdrop-blur-sm relative">
              <div className="flex items-center gap-3">
                <CheckCircleIcon className="w-7 h-7 text-green-400" />
                <span className="font-black text-base uppercase tracking-[0.2em] text-green-400">
                  Module Registered
                </span>
              </div>
            </div>

            {result.name && registerToKey && (
              <div className="px-6 pt-6 relative">
                <Link href={`/mod/${result.name}`} className="block relative">
                  <button className="w-full py-4 px-8 rounded-xl font-black text-lg tracking-[0.15em] uppercase transition-all border-2 hover:shadow-[0_0_40px_rgba(34,197,94,0.5)] hover:scale-[1.01] active:scale-[0.99] bg-gradient-to-r from-green-500/15 via-emerald-500/20 to-green-500/15 border-green-500 text-green-400 hover:bg-green-500/30 group relative overflow-hidden">
                    <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
                    <span className="relative z-10 flex items-center justify-center gap-3">
                      <span>View Module</span>
                      <span className="group-hover:translate-x-1 transition-transform">-&gt;</span>
                    </span>
                  </button>
                </Link>
              </div>
            )}

            <div className="p-6 relative">
              <div className="bg-black/60 backdrop-blur-sm rounded-xl p-4 border border-green-500/20">
                <pre className="text-sm overflow-x-auto text-green-300/90 leading-relaxed">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </div>
            </div>
          </motion.div>
        )}

        {error && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: "spring", bounce: 0.4, duration: 0.8 }}
            className="rounded-2xl border-2 bg-gradient-to-br from-red-950/40 to-black overflow-hidden relative"
            style={{ borderColor: '#ef4444', fontFamily: 'IBM Plex Mono, monospace' }}
          >
            <div className="px-6 py-4 border-b border-red-500/30 bg-red-900/30">
              <div className="flex items-center gap-3">
                <ExclamationCircleIcon className="w-6 h-6 text-red-400" />
                <span className="font-bold text-sm uppercase tracking-widest text-red-400">
                  Registration Failed
                </span>
              </div>
            </div>
            <div className="p-6">
              <p className="text-sm text-red-300/90 font-mono">{error}</p>
            </div>
          </motion.div>
        )}

        {!user && !result && !error && (
          <div className="text-center py-12">
            <p className="text-neutral-500 font-mono text-sm uppercase tracking-widest">
              Connect wallet to register modules
            </p>
          </div>
        )}
      </div>
    </div>
  )
}