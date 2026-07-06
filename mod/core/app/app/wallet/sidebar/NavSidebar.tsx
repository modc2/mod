"use client"

import { useState, useEffect, useRef } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import Link from 'next/link'
import { Plus, Palette, Globe, Server, ChevronLeft, ChevronRight, Search, Wrench } from 'lucide-react'
import { getAllNavItems, getNavHref } from '@/config/navigation'
import { useModuleApps } from '@/hooks/useModuleApps'
import { ThemeSelectorCompact } from '@/themes/ThemeSelectorCompact'
import {
  Blocks, Landmark, FileCode2, ArrowLeftRight,
  BookOpen, MessageSquare, Waypoints, Shield, Cpu, Bot, Terminal
} from 'lucide-react'
import { createPortal } from 'react-dom'
import { clearModsCache, syncModsConfig } from '@/mod/explore/ModExplorePage'
import { NetworkSelector } from '@/network/NetworkSelector'
import { useLayoutContext } from '@/context/LayoutContext'
import { useSearchContext } from '@/context/SearchContext'
import { userContext } from '@/context'
import { text2color, colorWithOpacity, shorten } from '@/utils'
import modConfig from '@config'

const NAV_ICONS: Record<string, React.ReactNode> = {
  'MODS': <Blocks size={22} strokeWidth={1.5} />,
  'TREASURY': <Landmark size={22} strokeWidth={1.5} />,
  'CONTRACTS': <FileCode2 size={22} strokeWidth={1.5} />,
  'TRANSACTIONS': <ArrowLeftRight size={22} strokeWidth={1.5} />,
  'DOCS': <BookOpen size={22} strokeWidth={1.5} />,
  'CHAT': <MessageSquare size={22} strokeWidth={1.5} />,
  'AGENT': <Bot size={22} strokeWidth={1.5} />,
  'BRIDGE': <Waypoints size={22} strokeWidth={1.5} />,
  'WORKERS': <Shield size={22} strokeWidth={1.5} />,
  'JOBS': <Cpu size={22} strokeWidth={1.5} />,
}

function getModuleFromPath(pathname: string): string | null {
  const modMatch = pathname.match(/^\/mod\/([^/]+)/)
  if (modMatch) return modMatch[1]
  const knownRoutes = ['mod', 'mods', 'user', 'cid', 'chat', 'docs', 'quests', 'create', 'safe', 'contracts', 'treasury', 'jobs', 'traders', 'network', 'home', 'transactions', 'buidl', 'apps', 'chain', 'balancer', 'workers', 'agent']
  const twoSegMatch = pathname.match(/^\/([^/]+)\/([^/]+)$/)
  if (twoSegMatch && !knownRoutes.includes(twoSegMatch[1])) return twoSegMatch[1]
  const singleMatch = pathname.match(/^\/([^/]+)$/)
  if (singleMatch && !knownRoutes.includes(singleMatch[1])) return singleMatch[1]
  return null
}

export const SIDEBAR_WIDTH = 64
export const COLLAPSED_WIDTH = 16

const PIXEL_FONT = "var(--font-pixel), 'Press Start 2P', monospace"

export function NavSidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const moduleApps = useModuleApps()
  const navItems = getAllNavItems(moduleApps)
  const { isHeaderCollapsed, isAgentSidebarOpen, toggleAgentSidebar, isTerminalMode, isEditSidebarOpen, toggleEditSidebar } = useLayoutContext()
  const [hoveredItem, setHoveredItem] = useState<string | null>(null)
  const [tooltipInfo, setTooltipInfo] = useState<{ label: string; y: number; color: string } | null>(null)
  const [mounted, setMounted] = useState(false)
  const [themeOpen, setThemeOpen] = useState(false)
  const themeBtnRef = useRef<HTMLButtonElement>(null)
  const [themeBtnRect, setThemeBtnRect] = useState<DOMRect | null>(null)
  const [networkOpen, setNetworkOpen] = useState(false)
  const networkBtnRef = useRef<HTMLButtonElement>(null)
  const [networkBtnRect, setNetworkBtnRect] = useState<DOMRect | null>(null)
  const [apiOpen, setApiOpen] = useState(false)
  const apiBtnRef = useRef<HTMLButtonElement>(null)
  const [apiBtnRect, setApiBtnRect] = useState<DOMRect | null>(null)
  const [collapsed, setCollapsed] = useState(false)

  // Search state
  const { handleSearch } = useSearchContext()
  const { client, user } = userContext()
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchValue, setSearchValue] = useState('')
  const [suggestions, setSuggestions] = useState<any[]>([])
  const [selectedIdx, setSelectedIdx] = useState(-1)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const searchBtnRef = useRef<HTMLButtonElement>(null)
  const [searchBtnRect, setSearchBtnRect] = useState<DOMRect | null>(null)
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Build dropdown state
  const activeModule = getModuleFromPath(pathname || '')
  const [buildOpen, setBuildOpen] = useState(false)
  const [buildName, setBuildName] = useState('')
  const [buildBase, setBuildBase] = useState('base')
  const [buildSubmitting, setBuildSubmitting] = useState(false)
  const [buildResult, setBuildResult] = useState<any>(null)
  const [buildError, setBuildError] = useState<string | null>(null)
  const [templates, setTemplates] = useState<any[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(false)
  const [templateSearch, setTemplateSearch] = useState('')
  const [buildMode, setBuildMode] = useState<'build' | 'fork'>('build')
  const buildRef = useRef<HTMLDivElement>(null)
  const buildBtnRef = useRef<HTMLButtonElement>(null)
  const [buildBtnRect, setBuildBtnRect] = useState<DOMRect | null>(null)

  // Fetch templates when build dropdown opens
  useEffect(() => {
    if (buildOpen && client && templates.length === 0) {
      setTemplatesLoading(true)
      client.call('mods', { page: 0, page_size: 100 })
        .then((res: any) => setTemplates(Array.isArray(res) ? res : []))
        .catch(() => {})
        .finally(() => setTemplatesLoading(false))
    }
    if (buildOpen) {
      setBuildError(null)
      setBuildResult(null)
      setTemplateSearch('')
    }
  }, [buildOpen, client])

  // Close build dropdown on click outside
  useEffect(() => {
    if (!buildOpen) return
    const handler = (e: MouseEvent) => {
      if (buildRef.current && !buildRef.current.contains(e.target as Node)) setBuildOpen(false)
    }
    const escHandler = (e: KeyboardEvent) => { if (e.key === 'Escape') setBuildOpen(false) }
    document.addEventListener('mousedown', handler)
    document.addEventListener('keydown', escHandler)
    return () => { document.removeEventListener('mousedown', handler); document.removeEventListener('keydown', escHandler) }
  }, [buildOpen])

  const handleBuildSubmit = async () => {
    if (!client?.token || !user?.key || !buildName.trim()) return
    setBuildSubmitting(true); setBuildError(null); setBuildResult(null)
    try {
      const response = await client.call('new', { name: buildName.trim(), base: buildBase, key: user.key })
      if (response?.error) throw new Error(response.error)
      setBuildResult(response)
      await syncModsConfig(client?.token)
      clearModsCache()
      setTimeout(() => { setBuildOpen(false); router.push('/mods') }, 1000)
    } catch (err: any) { setBuildError(err?.message || 'Failed to build module') }
    finally { setBuildSubmitting(false) }
  }

  const handleForkSubmit = async (modName: string) => {
    if (!client?.token || !user?.key || !modName) return
    setBuildSubmitting(true); setBuildError(null); setBuildResult(null)
    try {
      const response = await client.call('fork', { mod: modName, key: user.key })
      if (response?.error) throw new Error(response.error)
      setBuildResult(response)
      await syncModsConfig(client?.token)
      clearModsCache()
      setTimeout(() => { setBuildOpen(false); router.push('/mods') }, 1000)
    } catch (err: any) { setBuildError(err?.message || 'Failed to fork module') }
    finally { setBuildSubmitting(false) }
  }

  useEffect(() => {
    setMounted(true)
    const saved = localStorage.getItem('sidebar-collapsed')
    if (saved === 'true') setCollapsed(true)
  }, [])

  // Global ⌘K to open search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        if (searchBtnRef.current) setSearchBtnRect(searchBtnRef.current.getBoundingClientRect())
        setSearchOpen(true)
        setTimeout(() => searchInputRef.current?.focus(), 50)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const onSearchInput = (value: string) => {
    setSearchValue(value)
    setSelectedIdx(-1)
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current)
    if (!value.trim() || !client) {
      setSuggestions([])
      return
    }
    searchDebounceRef.current = setTimeout(async () => {
      try {
        const res = await client.call('mods', { search: value.trim(), page_size: 8 })
        setSuggestions(Array.isArray(res) ? res : [])
      } catch { setSuggestions([]) }
    }, 200)
  }

  const pickSuggestion = (s: any) => {
    setSearchOpen(false)
    setSuggestions([])
    setSearchValue('')
    setSelectedIdx(-1)
    router.push(`/mod/${s.name}`)
  }

  const onSearchKeyDown = (e: React.KeyboardEvent) => {
    if (suggestions.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSelectedIdx(p => p < suggestions.length - 1 ? p + 1 : 0); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSelectedIdx(p => p > 0 ? p - 1 : suggestions.length - 1); return }
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (selectedIdx >= 0 && suggestions[selectedIdx]) { pickSuggestion(suggestions[selectedIdx]); return }
      const trimmed = searchValue.trim()
      if (!trimmed) return
      setSearchOpen(false)
      setSuggestions([])
      setSearchValue('')
      handleSearch(trimmed)
      if (pathname !== '/mods') router.push('/mods')
    }
    if (e.key === 'Escape') {
      setSearchOpen(false)
      setSuggestions([])
      setSearchValue('')
      setSelectedIdx(-1)
    }
  }

  const toggleCollapsed = () => {
    setCollapsed(prev => {
      const next = !prev
      localStorage.setItem('sidebar-collapsed', String(next))
      return next
    })
  }

  const getShortLabel = (label: string) => {
    const map: Record<string, string> = { 'TRANSACTIONS': 'TXS' }
    return map[label] || label
  }

  const getIcon = (label: string) => {
    if (label === 'AGENT' && isTerminalMode) return <Terminal size={22} strokeWidth={1.5} />
    return NAV_ICONS[label] || <span style={{ fontSize: '10px', fontFamily: PIXEL_FONT }}>{label.slice(0, 3)}</span>
  }

  const getShortestLabel = (label: string) => {
    if (label === 'AGENT' && isTerminalMode) return 'TERM'
    const map: Record<string, string> = { 'TRANSACTIONS': 'TXS', 'CONTRACTS': 'CNTR', 'TREASURY': 'TRSY' }
    return map[label] || label
  }

  const currentWidth = collapsed ? COLLAPSED_WIDTH : SIDEBAR_WIDTH

  useEffect(() => {
    document.documentElement.style.setProperty('--sidebar-width', `${currentWidth}px`)
  }, [currentWidth])

  const handleHover = (href: string, label: string, color: string, e: React.MouseEvent) => {
    setHoveredItem(href)
    const r = e.currentTarget.getBoundingClientRect()
    setTooltipInfo({ label: getShortLabel(label).toLowerCase(), y: r.top + r.height / 2, color })
  }

  const clearHover = () => {
    setHoveredItem(null)
    setTooltipInfo(null)
  }

  // Hide sidebar when header is collapsed (module page full-screen mode)
  if (isHeaderCollapsed) {
    return null
  }

  return (
    <>
      <div
        className="fixed left-0 z-[60] flex flex-col transition-all duration-200"
        style={{
          top: '60px',
          bottom: 0,
          width: `${currentWidth}px`,
          background: 'var(--bg-header)',
          borderRight: '1px solid var(--border-default)',
          fontFamily: PIXEL_FONT,
          overflow: 'hidden',
        }}
      >
        {/* Collapsed state: just a thin expand strip */}
        {collapsed && (
          <button
            onClick={toggleCollapsed}
            className="w-full h-full flex items-center justify-center transition-all duration-150"
            style={{ color: 'var(--text-secondary)' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--accent-primary)'; e.currentTarget.style.background = 'var(--hover-bg)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'transparent' }}
            title="Expand sidebar"
          >
            <ChevronRight size={14} />
          </button>
        )}

        {/* Expanded content */}
        {!collapsed && <>

        {/* Search button */}
        <button
          ref={searchBtnRef}
          onClick={() => {
            if (searchBtnRef.current) setSearchBtnRect(searchBtnRef.current.getBoundingClientRect())
            setSearchOpen(!searchOpen)
            setTimeout(() => searchInputRef.current?.focus(), 50)
          }}
          className="w-full flex flex-col items-center justify-center transition-all duration-200 shrink-0"
          style={{
            height: '48px',
            color: searchOpen ? 'var(--accent-primary)' : 'var(--text-secondary)',
            fontFamily: PIXEL_FONT,
            borderBottom: '1px solid var(--border-default)',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--accent-primary)'; e.currentTarget.style.background = 'var(--hover-bg)' }}
          onMouseLeave={(e) => { if (!searchOpen) e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'transparent' }}
          title="Search (⌘K)"
        >
          <Search size={20} strokeWidth={1.5} className="transition-all duration-200" style={{
            filter: searchOpen ? 'drop-shadow(0 0 4px var(--accent-primary))' : 'none',
          }} />
          <span style={{ fontSize: '7px', marginTop: '3px', opacity: searchOpen ? 1 : 0.45 }}>⌘K</span>
        </button>

        {/* Nav items */}
        <div className="flex-1 overflow-y-auto hide-scrollbar py-2 px-1.5 flex flex-col gap-1" style={{ scrollbarWidth: 'none' }}>
          {navItems.map(item => {
            const isAgentItem = item.label === 'AGENT'
            const isActive = isAgentItem ? isAgentSidebarOpen : pathname?.startsWith(item.href)
            const isHovered = hoveredItem === item.href
            const finalHref = getNavHref(item)
            const color = (isAgentItem && isTerminalMode) ? '#10b981' : (item.color || 'var(--accent-primary)')

            const content = (
              <>
                {/* Active indicator dot */}
                {isActive && (
                  <div
                    className="absolute left-0 top-1/2 -translate-y-1/2 rounded-r-full"
                    style={{
                      width: '3px',
                      height: '20px',
                      background: color,
                      boxShadow: `0 0 8px ${color}`,
                    }}
                  />
                )}

                <span
                  className="relative transition-all duration-200"
                  style={{
                    filter: isActive ? `drop-shadow(0 0 4px ${color})` : 'none',
                    transform: isHovered && !isActive ? 'scale(1.1)' : 'scale(1)',
                  }}
                >
                  {getIcon(item.label)}
                </span>
                <span
                  className="transition-all duration-200"
                  style={{
                    fontSize: '7px',
                    marginTop: '3px',
                    letterSpacing: '0.05em',
                    opacity: isActive ? 1 : isHovered ? 0.8 : 0.45,
                    color: isActive ? color : 'var(--text-secondary)',
                  }}
                >
                  {getShortestLabel(item.label)}
                </span>
              </>
            )

            const sharedStyle = {
              height: '52px',
              padding: '4px 0',
              fontFamily: PIXEL_FONT,
              color: isActive ? color : isHovered ? 'var(--text-primary)' : 'var(--text-secondary)',
              background: isActive
                ? `${color}15`
                : isHovered ? 'var(--hover-bg)' : 'transparent',
            }

            if (isAgentItem) {
              return (
                <button
                  key={item.href}
                  onClick={toggleAgentSidebar}
                  className="group relative flex flex-col items-center justify-center transition-all duration-200 rounded-lg"
                  style={sharedStyle}
                  onMouseEnter={(e) => handleHover(item.href, item.label, color, e)}
                  onMouseLeave={clearHover}
                >
                  {content}
                </button>
              )
            }

            return (
              <Link
                key={item.href}
                href={finalHref}
                className="group relative flex flex-col items-center justify-center transition-all duration-200 rounded-lg"
                style={sharedStyle}
                onMouseEnter={(e) => handleHover(item.href, item.label, color, e)}
                onMouseLeave={clearHover}
              >
                {content}
              </Link>
            )
          })}
        </div>

        {/* Bottom actions */}
        <div className="shrink-0 flex flex-col gap-1 px-1.5 py-2" style={{ borderTop: '1px solid var(--border-default)' }}>
          {/* Build / Edit button */}
          {user && (
            <button
              ref={buildBtnRef}
              onClick={() => {
                if (activeModule) {
                  toggleEditSidebar()
                } else {
                  if (buildBtnRef.current) setBuildBtnRect(buildBtnRef.current.getBoundingClientRect())
                  setBuildOpen(!buildOpen)
                }
              }}
              className="w-full flex flex-col items-center justify-center transition-all duration-200 rounded-lg"
              style={{
                height: '48px',
                color: (activeModule ? isEditSidebarOpen : buildOpen) ? 'var(--accent-primary)' : 'var(--text-secondary)',
                fontFamily: PIXEL_FONT,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = 'var(--accent-primary)'
                e.currentTarget.style.background = 'var(--hover-bg)'
                handleHover('__build', 'BUILD', 'var(--accent-primary)', e)
              }}
              onMouseLeave={(e) => {
                if (!(activeModule ? isEditSidebarOpen : buildOpen)) e.currentTarget.style.color = 'var(--text-secondary)'
                e.currentTarget.style.background = 'transparent'
                clearHover()
              }}
              title={activeModule ? `Edit ${activeModule}` : 'Build new module'}
            >
              <Wrench size={20} strokeWidth={1.5} className="transition-all duration-200" style={{
                filter: (activeModule ? isEditSidebarOpen : buildOpen) ? 'drop-shadow(0 0 4px var(--accent-primary))' : 'none',
              }} />
              <span style={{ fontSize: '7px', marginTop: '3px', opacity: (activeModule ? isEditSidebarOpen : buildOpen) ? 1 : 0.45 }}>
                {activeModule ? 'EDIT' : 'BUILD'}
              </span>
            </button>
          )}

          {/* Network selector icon */}
          <button
            ref={networkBtnRef}
            onClick={() => {
              if (networkBtnRef.current) {
                setNetworkBtnRect(networkBtnRef.current.getBoundingClientRect())
              }
              setNetworkOpen(!networkOpen)
            }}
            className="w-full flex flex-col items-center justify-center transition-all duration-200 rounded-lg"
            style={{
              height: '48px',
              color: networkOpen ? 'var(--text-primary)' : 'var(--text-secondary)',
              fontFamily: PIXEL_FONT,
            }}
            title="Network"
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--text-primary)'
              e.currentTarget.style.background = 'var(--hover-bg)'
              handleHover('__network', 'NETWORK', 'var(--accent-primary)', e)
            }}
            onMouseLeave={(e) => {
              if (!networkOpen) e.currentTarget.style.color = 'var(--text-secondary)'
              e.currentTarget.style.background = 'transparent'
              clearHover()
            }}
          >
            <Globe size={20} className="transition-all duration-200" style={{
              filter: hoveredItem === '__network' || networkOpen ? 'drop-shadow(0 0 4px var(--accent-primary))' : 'none',
            }} />
            <span style={{ fontSize: '7px', marginTop: '3px', opacity: 0.5 }}>NET</span>
          </button>

          {/* API host selector icon */}
          <button
            ref={apiBtnRef}
            onClick={() => {
              if (apiBtnRef.current) {
                setApiBtnRect(apiBtnRef.current.getBoundingClientRect())
              }
              setApiOpen(!apiOpen)
            }}
            className="w-full flex flex-col items-center justify-center transition-all duration-200 rounded-lg"
            style={{
              height: '48px',
              color: apiOpen ? 'var(--text-primary)' : 'var(--text-secondary)',
              fontFamily: PIXEL_FONT,
            }}
            title="API Host"
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--text-primary)'
              e.currentTarget.style.background = 'var(--hover-bg)'
              handleHover('__api', 'API', 'var(--accent-primary)', e)
            }}
            onMouseLeave={(e) => {
              if (!apiOpen) e.currentTarget.style.color = 'var(--text-secondary)'
              e.currentTarget.style.background = 'transparent'
              clearHover()
            }}
          >
            <Server size={20} className="transition-all duration-200" style={{
              filter: hoveredItem === '__api' || apiOpen ? 'drop-shadow(0 0 4px var(--accent-primary))' : 'none',
            }} />
            <span style={{ fontSize: '7px', marginTop: '3px', opacity: 0.5 }}>API</span>
          </button>

          {/* Theme selector icon */}
          <button
            ref={themeBtnRef}
            onClick={() => {
              if (themeBtnRef.current) {
                setThemeBtnRect(themeBtnRef.current.getBoundingClientRect())
              }
              setThemeOpen(!themeOpen)
            }}
            className="w-full flex flex-col items-center justify-center transition-all duration-200 rounded-lg"
            style={{
              height: '48px',
              color: themeOpen ? 'var(--text-primary)' : 'var(--text-secondary)',
              fontFamily: PIXEL_FONT,
            }}
            title="Theme"
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--text-primary)'
              e.currentTarget.style.background = 'var(--hover-bg)'
              handleHover('__theme', 'THEME', 'var(--accent-primary)', e)
            }}
            onMouseLeave={(e) => {
              if (!themeOpen) e.currentTarget.style.color = 'var(--text-secondary)'
              e.currentTarget.style.background = 'transparent'
              clearHover()
            }}
          >
            <Palette size={20} className="transition-all duration-200" style={{
              filter: hoveredItem === '__theme' || themeOpen ? 'drop-shadow(0 0 4px var(--accent-primary))' : 'none',
            }} />
            <span style={{ fontSize: '7px', marginTop: '3px', opacity: 0.5 }}>THEME</span>
          </button>
        </div>
        </>}
      </div>

      {/* Edge-mounted collapse/expand pill */}
      {!collapsed && (
        <button
          onClick={toggleCollapsed}
          className="fixed z-[61] flex items-center justify-center transition-all duration-200"
          style={{
            left: `${currentWidth - 6}px`,
            top: '50%',
            transform: 'translateY(-50%)',
            width: '12px',
            height: '40px',
            borderRadius: '0 6px 6px 0',
            background: 'var(--bg-header)',
            border: '1px solid var(--border-default)',
            borderLeft: 'none',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            opacity: 0.4,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.opacity = '1'
            e.currentTarget.style.color = 'var(--accent-primary)'
            e.currentTarget.style.width = '16px'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.opacity = '0.4'
            e.currentTarget.style.color = 'var(--text-tertiary)'
            e.currentTarget.style.width = '12px'
          }}
          title="Collapse sidebar"
        >
          <ChevronLeft size={10} />
        </button>
      )}

      {/* Search portal */}
      {mounted && searchOpen && createPortal(
        <>
          <div
            className="fixed inset-0 z-[9998]"
            onClick={() => { setSearchOpen(false); setSuggestions([]); setSearchValue('') }}
          />
          <div
            style={{
              position: 'fixed',
              left: `${currentWidth + 4}px`,
              top: '60px',
              zIndex: 9999,
              width: '380px',
            }}
          >
            <div
              style={{
                background: 'var(--bg-secondary)',
                backdropFilter: 'blur(20px)',
                WebkitBackdropFilter: 'blur(20px)',
                border: '1px solid var(--border-color)',
                borderRadius: '10px',
                boxShadow: '0 12px 40px rgba(0, 0, 0, 0.25)',
                fontFamily: 'var(--font-digital), monospace',
              }}
            >
              <div className="flex items-center px-3 gap-2" style={{ height: '40px', borderBottom: '1px solid var(--border-color)' }}>
                <Search size={14} style={{ color: 'var(--accent-primary)', flexShrink: 0 }} />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchValue}
                  onChange={(e) => onSearchInput(e.target.value)}
                  onKeyDown={onSearchKeyDown}
                  placeholder="modules, functions, cid..."
                  className="flex-1 bg-transparent focus:outline-none min-w-0"
                  style={{
                    fontSize: '13px',
                    fontFamily: 'var(--font-digital), monospace',
                    color: 'var(--text-primary)',
                    caretColor: 'var(--accent-primary)',
                  }}
                  autoFocus
                />
                <kbd
                  className="flex-shrink-0 px-1.5 py-0.5"
                  style={{
                    fontSize: '10px',
                    fontFamily: 'var(--font-digital), monospace',
                    backgroundColor: 'var(--hover-bg)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '3px',
                    color: 'var(--text-tertiary)',
                    lineHeight: 1.2,
                  }}
                >
                  ESC
                </kbd>
              </div>
              {suggestions.length > 0 && (
                <div className="overflow-y-auto" style={{ maxHeight: '320px' }}>
                  {suggestions.map((mod, idx) => {
                    const isSelected = idx === selectedIdx
                    const mColor = text2color(mod.name || '')
                    const fnCount = mod.schema ? Object.keys(mod.schema).length : 0
                    return (
                      <button
                        key={`${mod.name}-${mod.key}-${idx}`}
                        onClick={() => pickSuggestion(mod)}
                        onMouseEnter={() => setSelectedIdx(idx)}
                        className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition-all"
                        style={{
                          background: isSelected ? colorWithOpacity(mColor, 0.1) : 'transparent',
                          borderBottom: '1px solid var(--border-color)',
                        }}
                      >
                        <div
                          className="w-2 h-2 rounded-full flex-shrink-0"
                          style={{ background: mColor, boxShadow: isSelected ? `0 0 6px ${mColor}` : 'none' }}
                        />
                        <span
                          className="text-xs font-bold uppercase tracking-wide flex-shrink-0"
                          style={{ color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)' }}
                        >
                          {mod.name}
                        </span>
                        {mod.key && (
                          <span className="text-[10px] truncate" style={{ color: 'var(--text-tertiary)', opacity: 0.6 }}>
                            {shorten(mod.key, 4, 4)}
                          </span>
                        )}
                        <span className="flex-1" />
                        {fnCount > 0 && (
                          <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                            {fnCount} fn
                          </span>
                        )}
                        {mod.url && (
                          <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: '#10b981', boxShadow: '0 0 4px #10b981' }} />
                        )}
                      </button>
                    )
                  })}
                </div>
              )}
              {searchValue && suggestions.length === 0 && (
                <div className="px-3 py-4 text-center text-[11px] uppercase" style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-digital), monospace' }}>
                  Press Enter to search
                </div>
              )}
            </div>
          </div>
        </>,
        document.body
      )}

      {/* Network selector portal */}
      {mounted && networkOpen && networkBtnRect && createPortal(
        <>
          <div
            className="fixed inset-0 z-[9998]"
            onClick={() => setNetworkOpen(false)}
          />
          <div
            style={{
              position: 'fixed',
              left: `${currentWidth}px`,
              bottom: `${window.innerHeight - networkBtnRect.bottom}px`,
              zIndex: 9999,
            }}
          >
            <NetworkSelector onClose={() => setNetworkOpen(false)} sidebar />
          </div>
        </>,
        document.body
      )}

      {/* API host selector portal */}
      {mounted && apiOpen && apiBtnRect && createPortal(
        <>
          <div
            className="fixed inset-0 z-[9998]"
            onClick={() => setApiOpen(false)}
          />
          <div
            style={{
              position: 'fixed',
              left: `${currentWidth}px`,
              bottom: `${window.innerHeight - apiBtnRect.bottom}px`,
              zIndex: 9999,
              width: '320px',
            }}
          >
            <ApiHostSelector onClose={() => setApiOpen(false)} />
          </div>
        </>,
        document.body
      )}

      {/* Theme dropdown portal */}
      {mounted && themeOpen && themeBtnRect && createPortal(
        <>
          {/* Backdrop to close */}
          <div
            className="fixed inset-0 z-[9998]"
            onClick={() => setThemeOpen(false)}
          />
          <div
            style={{
              position: 'fixed',
              left: `${currentWidth}px`,
              bottom: `${window.innerHeight - themeBtnRect.bottom}px`,
              zIndex: 9999,
              width: '240px',
            }}
          >
            <ThemeSelectorCompact expandUpwards={false} onClose={() => setThemeOpen(false)} alwaysOpen />
          </div>
        </>,
        document.body
      )}

      {/* Build dropdown portal */}
      {mounted && buildOpen && !activeModule && createPortal(
        <>
          <div
            className="fixed inset-0 z-[9998]"
            onClick={() => setBuildOpen(false)}
          />
          <div
            ref={buildRef}
            style={{
              position: 'fixed',
              left: `${currentWidth + 4}px`,
              bottom: buildBtnRect ? `${window.innerHeight - buildBtnRect.bottom}px` : '120px',
              zIndex: 9999,
              width: '400px',
            }}
          >
            <div
              style={{
                background: 'var(--bg-secondary)',
                backdropFilter: 'blur(20px)',
                WebkitBackdropFilter: 'blur(20px)',
                border: '1px solid var(--border-color)',
                borderRadius: '10px',
                boxShadow: '0 12px 40px rgba(0, 0, 0, 0.25)',
                fontFamily: 'var(--font-digital), monospace',
              }}
            >
              <div className="p-4 space-y-3">
                {/* Mode toggle */}
                <div className="flex items-center gap-1 p-0.5 rounded-md" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
                  {(['build', 'fork'] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => { setBuildMode(mode); setBuildError(null); setBuildResult(null) }}
                      className="flex-1 py-1.5 text-xs font-bold uppercase tracking-wider transition-all rounded-md"
                      style={{
                        background: buildMode === mode ? 'var(--bg-secondary)' : 'transparent',
                        color: buildMode === mode ? 'var(--text-primary)' : 'var(--text-tertiary)',
                        border: buildMode === mode ? '1px solid var(--border-color)' : '1px solid transparent',
                      }}
                    >
                      {mode === 'build' ? 'NEW MOD' : 'FORK MOD'}
                    </button>
                  ))}
                </div>

                {/* Search modules */}
                <div className="relative">
                  <Search
                    size={14}
                    className="absolute left-2.5 top-1/2 -translate-y-1/2"
                    style={{ color: 'var(--text-tertiary)' }}
                  />
                  <input
                    type="text"
                    value={templateSearch}
                    onChange={(e) => setTemplateSearch(e.target.value)}
                    placeholder={buildMode === 'build' ? 'SEARCH TEMPLATE...' : 'SEARCH MODULE TO FORK...'}
                    className="w-full pl-8 pr-3 py-2 text-xs font-bold focus:outline-none uppercase"
                    style={{
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '6px',
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-digital), monospace',
                    }}
                    autoFocus
                  />
                </div>

                {/* Module list */}
                <div
                  className="overflow-y-auto scrollbar-none"
                  style={{
                    maxHeight: '200px',
                    border: '1px solid var(--border-color)',
                    borderRadius: '6px',
                  }}
                >
                  {templatesLoading ? (
                    <div className="px-3 py-6 text-xs text-center" style={{ color: 'var(--text-tertiary)' }}>
                      LOADING MODULES...
                    </div>
                  ) : (() => {
                    const filtered = templates.filter((t: any) =>
                      !templateSearch || t.name?.toLowerCase().includes(templateSearch.toLowerCase())
                    )
                    if (filtered.length === 0) return (
                      <div className="px-3 py-6 text-xs text-center" style={{ color: 'var(--text-tertiary)' }}>
                        {templateSearch ? 'NO MATCHES' : 'NO MODULES FOUND'}
                      </div>
                    )
                    return filtered.map((t: any) => {
                      const isSelected = buildBase === t.name
                      const tColor = text2color(t.name)
                      const fnCount = t.schema ? Object.keys(t.schema).length : 0
                      return (
                        <button
                          key={`${t.name}-${t.key}`}
                          onClick={() => setBuildBase(t.name)}
                          className="w-full flex items-center gap-2.5 px-3 py-2 text-left transition-all"
                          style={{
                            background: isSelected ? colorWithOpacity(tColor, 0.08) : 'transparent',
                            borderBottom: '1px solid var(--border-color)',
                          }}
                          onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = 'var(--hover-bg)' }}
                          onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}
                        >
                          <div
                            className="w-2 h-2 rounded-full flex-shrink-0"
                            style={{ background: isSelected ? tColor : 'var(--text-tertiary)', boxShadow: isSelected ? `0 0 6px ${tColor}` : 'none' }}
                          />
                          <span
                            className="text-xs font-bold uppercase tracking-wide truncate"
                            style={{ color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)' }}
                          >
                            {t.name}
                          </span>
                          {fnCount > 0 && (
                            <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                              {fnCount} fn
                            </span>
                          )}
                          {t.url && (
                            <span className="flex items-center gap-1 flex-shrink-0" style={{ fontSize: '10px', color: 'var(--accent-success, #34d399)' }}>
                              <span className="w-1 h-1 rounded-full" style={{ background: 'var(--accent-success, #34d399)' }} />
                              LIVE
                            </span>
                          )}
                          <span className="flex-1" />
                          {isSelected && (
                            <span className="text-xs" style={{ color: tColor }}>&#10003;</span>
                          )}
                        </button>
                      )
                    })
                  })()}
                </div>

                {/* Name input (build mode only) */}
                {buildMode === 'build' && (
                  <input
                    type="text"
                    value={buildName}
                    onChange={(e) => setBuildName(e.target.value)}
                    placeholder="YOUR MOD NAME"
                    className="w-full px-3 py-2.5 text-sm font-bold focus:outline-none uppercase"
                    style={{
                      background: 'var(--bg-input)',
                      border: buildName.trim() ? '1px solid var(--accent-primary)' : '1px solid var(--border-color)',
                      borderRadius: '6px',
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-digital), monospace',
                    }}
                    onKeyDown={(e) => { if (e.key === 'Enter' && buildName.trim()) handleBuildSubmit() }}
                  />
                )}

                {/* Action button */}
                {buildMode === 'build' ? (
                  <button
                    onClick={handleBuildSubmit}
                    disabled={buildSubmitting || !buildName.trim()}
                    className="w-full py-2.5 text-sm font-bold uppercase tracking-wider transition-all disabled:opacity-30"
                    style={{
                      background: 'var(--hover-bg)',
                      border: '1px solid var(--accent-primary)',
                      borderRadius: '6px',
                      color: 'var(--accent-primary)',
                      fontFamily: 'var(--font-digital), monospace',
                    }}
                  >
                    {buildSubmitting ? 'BUILDING...' : `BUILD FROM ${buildBase.toUpperCase()}`}
                  </button>
                ) : (
                  <button
                    onClick={() => handleForkSubmit(buildBase)}
                    disabled={buildSubmitting || !buildBase}
                    className="w-full py-2.5 text-sm font-bold uppercase tracking-wider transition-all disabled:opacity-30"
                    style={{
                      background: colorWithOpacity(text2color(buildBase), 0.12),
                      border: `1px solid ${colorWithOpacity(text2color(buildBase), 0.4)}`,
                      borderRadius: '6px',
                      color: text2color(buildBase),
                      fontFamily: 'var(--font-digital), monospace',
                    }}
                  >
                    {buildSubmitting ? 'FORKING...' : `FORK ${buildBase.toUpperCase()}`}
                  </button>
                )}

                {buildResult && (
                  <div className="text-xs font-bold text-green-500 uppercase px-1">
                    {buildMode === 'fork' ? 'FORKED' : 'BUILT'} &mdash; {buildResult.name || buildName}
                  </div>
                )}
                {buildError && (
                  <div className="text-xs font-bold text-red-500 uppercase px-1 py-1">
                    {buildError}
                  </div>
                )}
              </div>
            </div>
          </div>
        </>,
        document.body
      )}

      {/* Tooltip portal - rendered outside sidebar to avoid overflow clipping */}
      {mounted && tooltipInfo && createPortal(
        <div
          className="pointer-events-none"
          style={{
            position: 'fixed',
            left: `${SIDEBAR_WIDTH + 4}px`,
            top: `${tooltipInfo.y}px`,
            transform: 'translateY(-50%)',
            zIndex: 9999,
          }}
        >
          <div
            className="flex items-center px-3 py-1"
            style={{
              background: 'var(--bg-header)',
              border: `1px solid ${tooltipInfo.color}40`,
              borderRadius: '6px',
              whiteSpace: 'nowrap',
              boxShadow: `0 4px 16px rgba(0,0,0,0.3), 0 0 8px ${tooltipInfo.color}15`,
            }}
          >
            <span
              style={{
                fontSize: '11px',
                fontFamily: PIXEL_FONT,
                color: tooltipInfo.color,
                letterSpacing: '0.06em',
                textShadow: `0 0 6px ${tooltipInfo.color}80`,
              }}
            >
              {tooltipInfo.label}
            </span>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || modConfig.url.api || 'http://localhost:8000'

function ApiHostSelector({ onClose }: { onClose: () => void }) {
  const [hosts, setHosts] = useState<{ url: string; status: 'active' | 'inactive' | 'checking' }[]>([])
  const [currentHost, setCurrentHost] = useState('')
  const [newUrl, setNewUrl] = useState('')

  useEffect(() => {
    const current = localStorage.getItem('custom_node_url') || DEFAULT_API_URL
    setCurrentHost(current)
    const saved = localStorage.getItem('custom_hosts')
    const urls: string[] = saved ? JSON.parse(saved) : [DEFAULT_API_URL]
    if (!urls.includes(DEFAULT_API_URL)) urls.unshift(DEFAULT_API_URL)
    setHosts(urls.map(url => ({ url, status: 'checking' })))
    urls.forEach((url, i) => {
      fetch(`${url}/info`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(r => r.ok ? 'active' as const : 'inactive' as const)
        .catch(() => 'inactive' as const)
        .then(status => setHosts(prev => prev.map((h, j) => j === i ? { ...h, status } : h)))
    })
  }, [])

  const selectHost = (url: string) => {
    localStorage.setItem('custom_node_url', url)
    window.location.reload()
  }

  const addHost = () => {
    if (!newUrl.trim()) return
    const url = newUrl.trim()
    const updated = [...hosts, { url, status: 'checking' as const }]
    setHosts(updated)
    localStorage.setItem('custom_hosts', JSON.stringify(updated.map(h => h.url)))
    setNewUrl('')
    fetch(`${url}/info`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(r => r.ok ? 'active' as const : 'inactive' as const)
      .catch(() => 'inactive' as const)
      .then(status => setHosts(prev => prev.map(h => h.url === url ? { ...h, status } : h)))
  }

  const removeHost = (url: string) => {
    const updated = hosts.filter(h => h.url !== url)
    setHosts(updated)
    localStorage.setItem('custom_hosts', JSON.stringify(updated.map(h => h.url)))
    if (currentHost === url) selectHost(DEFAULT_API_URL)
  }

  return (
    <div
      className="flex flex-col gap-1 p-3"
      style={{
        background: 'var(--bg-header)',
        border: '1px solid var(--border-default)',
        borderRadius: '8px',
        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
        fontFamily: PIXEL_FONT,
        maxHeight: '360px',
        overflowY: 'auto',
      }}
    >
      <div className="text-[9px] font-bold uppercase tracking-wider mb-1 px-1" style={{ color: 'var(--text-tertiary)' }}>
        API HOST
      </div>
      {hosts.map(host => {
        const isCurrent = host.url === currentHost
        return (
          <div
            key={host.url}
            className="flex items-center gap-2 px-2 py-2 rounded transition-all cursor-pointer"
            style={{
              background: isCurrent ? 'var(--accent-primary)15' : 'transparent',
              border: isCurrent ? '1px solid var(--accent-primary)' : '1px solid transparent',
            }}
            onClick={() => !isCurrent && selectHost(host.url)}
          >
            <div
              className="w-2 h-2 rounded-full shrink-0"
              style={{
                background: host.status === 'active' ? '#10b981' : host.status === 'inactive' ? '#ef4444' : '#f59e0b',
                boxShadow: host.status === 'active' ? '0 0 6px #10b981' : 'none',
              }}
            />
            <span
              className="flex-1 text-[9px] truncate"
              style={{ color: isCurrent ? 'var(--accent-primary)' : 'var(--text-primary)' }}
            >
              {host.url.replace(/^https?:\/\//, '')}
            </span>
            {isCurrent && (
              <span className="text-[7px] uppercase tracking-wider shrink-0" style={{ color: 'var(--accent-primary)' }}>active</span>
            )}
            {!isCurrent && host.url !== DEFAULT_API_URL && (
              <button
                onClick={(e) => { e.stopPropagation(); removeHost(host.url) }}
                className="text-[9px] shrink-0 px-1 rounded transition-all"
                style={{ color: '#ef4444' }}
              >
                ×
              </button>
            )}
          </div>
        )
      })}
      <div className="flex gap-1 mt-1">
        <input
          type="text"
          value={newUrl}
          onChange={e => setNewUrl(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && addHost()}
          placeholder="http://host:port"
          className="flex-1 px-2 py-1 text-[9px] rounded"
          style={{
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-default)',
            color: 'var(--text-primary)',
            outline: 'none',
            fontFamily: PIXEL_FONT,
          }}
        />
        <button
          onClick={addHost}
          className="px-2 py-1 text-[9px] rounded font-bold"
          style={{
            background: 'var(--accent-primary)20',
            color: 'var(--accent-primary)',
            border: '1px solid var(--accent-primary)',
          }}
        >
          +
        </button>
      </div>
    </div>
  )
}
