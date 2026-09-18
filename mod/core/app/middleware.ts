import { NextRequest, NextResponse } from 'next/server'

/**
 * Federated routing middleware.
 *
 * Single gateway on port 3000:
 *   /{mod}/*       → module app server  (basePath /{mod})
 *   /api/{mod}/*   → module API server  (prefix stripped)
 *
 * Modules register via namespace. Main app routes are reserved.
 */

interface AppEntry {
  url: string
  api_url?: string
  owner: string
}

let appNamespaceCache: Record<string, AppEntry> = {}
let lastFetch = 0
const CACHE_TTL = 5000

const API_URL = process.env.API_URL_INTERNAL || 'http://localhost:8000'

// Scale-to-zero: these modules are routed through the activator (:9000) instead
// of straight to their port, so a slept module cold-starts on demand. Must match
// the activator's ACTIVATOR_MANAGED set. Modules NOT listed here keep routing
// directly to their registered url/api_url (unchanged behavior).
const ACTIVATOR_URL = 'http://localhost:9000'
const ACTIVATOR_MODS = new Set([
  'openplay', 'modcity', 'openevent', 'neartensor', 'whitepaper',
])

async function getAppNamespace(): Promise<Record<string, AppEntry>> {
  const now = Date.now()
  if (now - lastFetch < CACHE_TTL && Object.keys(appNamespaceCache).length > 0) {
    return appNamespaceCache
  }
  try {
    const res = await fetch(`${API_URL}/app_namespace`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    if (res.ok) {
      const data = await res.json()
      const raw = data.result || data || {}
      const normalized: Record<string, AppEntry> = {}
      for (const [key, val] of Object.entries(raw)) {
        if (typeof val === 'string') {
          normalized[key] = { url: val, owner: '' }
        } else if (val && typeof val === 'object') {
          normalized[key] = val as AppEntry
        }
      }
      appNamespaceCache = normalized
      lastFetch = now
    }
  } catch {
    // API not available — use cached value
  }
  return appNamespaceCache
}

// Main app's own /api/* routes — never proxy these to modules
const RESERVED_API_ROUTES = new Set([
  'proxy', 'logs', 'mods', 'mod-code', 'terminal', 'contract-source',
  // legacy bridge to the chain module API (app/api/chain/…). The console
  // itself calls /chain/api/* instead — the :3000 gateway hands every /api/*
  // request to the protocol API, so nothing mounted here survives the hop.
  'chain',
])

// Routes that belong to the main app — never proxy these
const MAIN_APP_ROUTES = new Set([
  '_next', 'favicon.ico', 'api',
  // App pages (from app/*/page.tsx)
  'agent', 'apps', 'balancer', 'buidl', 'chain', 'chat', 'contracts',
  'coplay', 'create', 'docs', 'home', 'jobs', 'mod', 'mods', 'network',
  'quests', 'safe', 'traders', 'transactions', 'treasury', 'user',
  'wallet', 'workers',
  // Other top-level app routes
  'key', 'cid', 'host',
  // App internals
  'client', 'components', 'config', 'context', 'header', 'images',
  'public', 'styles', 'themes', 'types', 'ui', 'utils',
])

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  // ── /api/* → route to module API or main Flask backend ──
  if (pathname.startsWith('/api/')) {
    const rest = pathname.slice(5)
    const slash = rest.indexOf('/')
    const modName = slash === -1 ? rest : rest.slice(0, slash)
    const apiPath = slash === -1 ? '/' : rest.slice(slash)

    // Next.js own API routes — let Next.js handle them
    if (RESERVED_API_ROUTES.has(modName)) return NextResponse.next()

    // Scale-to-zero modules → through the activator (wakes the api on demand).
    if (ACTIVATOR_MODS.has(modName)) {
      return NextResponse.rewrite(new URL(`${ACTIVATOR_URL}${pathname}${request.nextUrl.search}`))
    }

    // Check if first segment is a registered module
    const namespace = await getAppNamespace()
    const entry = namespace[modName]
    if (entry?.api_url) {
      const target = new URL(apiPath + request.nextUrl.search, entry.api_url)
      return NextResponse.rewrite(target)
    }

    // Everything else → main Flask API backend
    const target = new URL('/' + rest + request.nextUrl.search, API_URL)
    return NextResponse.rewrite(target)
  }

  // ── /{mod}/* → proxy to module app server ──
  const segments = pathname.split('/').filter(Boolean)
  if (segments.length === 0) return NextResponse.next()

  const firstSegment = segments[0]
  if (MAIN_APP_ROUTES.has(firstSegment) || firstSegment.startsWith('_')) {
    return NextResponse.next()
  }

  // Scale-to-zero modules → through the activator (wakes the app on demand).
  if (ACTIVATOR_MODS.has(firstSegment)) {
    return NextResponse.rewrite(new URL(`${ACTIVATOR_URL}${pathname}${request.nextUrl.search}`))
  }

  const namespace = await getAppNamespace()
  const entry = namespace[firstSegment]
  if (entry?.url) {
    // Forward full /{mod}/... path — module basePath is /{mod}
    const target = new URL(pathname + request.nextUrl.search, entry.url)
    const response = NextResponse.rewrite(target)
    response.headers.set('x-mod-owner', entry.owner || '')
    return response
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico).*)',
  ],
}
