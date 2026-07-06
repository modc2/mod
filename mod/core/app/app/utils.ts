import { ModuleType } from '@/types'

/** Check if the browser is accessing remotely (not localhost). */
function isRemote(): boolean {
  if (typeof window === 'undefined') return false
  const h = window.location.hostname
  return h !== 'localhost' && h !== '127.0.0.1'
}

/** Check if a URL points to localhost. */
function isLocalUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1'
  } catch {
    return false
  }
}

/** Rewrite localhost URLs for remote access.
 *  Uses path-based routing: /mod/{name} for API, /{mod} for app.
 *  Falls back to /proxy/api for the main API. */
function resolveUrl(url: string | undefined, modName?: string, kind?: 'app' | 'api'): string | undefined {
  if (!url) return url
  if (typeof window === 'undefined') return url
  if (isLocalUrl(url) && !isRemote()) {
    // Local access: module apps use basePath=/{modName}, so append it
    if (modName && kind === 'app') {
      return `${url.replace(/\/+$/, '')}/${modName}`
    }
    return url
  }
  if (!isLocalUrl(url)) return url
  // Remote access: rewrite localhost URLs to path-based routing
  if (modName && kind === 'api') {
    return `${window.location.origin}/mod/${modName}`
  }
  if (modName && kind === 'app') {
    return `${window.location.origin}/${modName}`
  }
  // Fallback: main API
  return `${window.location.origin}/proxy/api`
}

/** Extract the app URL from mod.url_app or mod.url.app */
export function getModAppUrl(mod: ModuleType): string | undefined {
  const raw = mod.url_app || (mod.url && typeof mod.url === 'object' ? mod.url.app : undefined)
  return resolveUrl(raw, mod.name, 'app')
}

/** Extract the api URL from mod.url (string) or mod.url.api */
export function getModApiUrl(mod: ModuleType): string | undefined {
  const raw = typeof mod.url === 'string' ? mod.url : (mod.url && typeof mod.url === 'object' ? mod.url.api : undefined)
  return resolveUrl(raw, mod.name, 'api')
}

export const time2str = (timestamp: number): string => {
  const date = new Date(timestamp)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hours}:${minutes}`
}

export const time2utc = (timestamp: number): string => {
  const date = new Date(timestamp)
  const year = date.getUTCFullYear()
  const month = String(date.getUTCMonth() + 1).padStart(2, '0')
  const day = String(date.getUTCDate()).padStart(2, '0')
  const hours = String(date.getUTCHours()).padStart(2, '0')
  const minutes = String(date.getUTCMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hours}:${minutes}`
}

export const timeAgo = (timestamp: number): string => {
  const now = Date.now()
  const diff = Math.floor((now - timestamp) / 1000) // seconds
  
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

// Helper function to convert HSL to RGB
const hslToRgb = (h: number, s: number, l: number): [number, number, number] => {
  s /= 100
  l /= 100
  const k = (n: number) => (n + h / 30) % 12
  const a = s * Math.min(l, 1 - l)
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  return [Math.round(255 * f(0)), Math.round(255 * f(8)), Math.round(255 * f(4))]
}

// Convert RGB to hex
const rgbToHex = (r: number, g: number, b: number): string => {
  return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('')
}

export const text2color = (text: string): string => {
  let hash = 0
  // if text is undefined or empty, return gray
  if (!text) return '#808080'

  for (let i = 0; i < text.length; i++) {
    hash = text.charCodeAt(i) + ((hash << 5) - hash)
  }
  const h = hash % 360
  // Convert HSL to hex for better cross-browser compatibility (especially Safari)
  const [r, g, b] = hslToRgb(h, 85, 55)
  return rgbToHex(r, g, b)
}

// Helper function to add opacity to hex colors (Safari-safe)
export const colorWithOpacity = (hexColor: string, opacity: number): string => {
  // Remove # if present
  const hex = hexColor.replace('#', '')

  // Parse RGB
  const r = parseInt(hex.substring(0, 2), 16)
  const g = parseInt(hex.substring(2, 4), 16)
  const b = parseInt(hex.substring(4, 6), 16)

  // Return rgba format which is consistently supported across browsers
  return `rgba(${r}, ${g}, ${b}, ${opacity})`
}

export const shorten = (str: string, start = 8, end = 8): string => {
  if (!str || str.length <= start + end) return str
  return `${str.slice(0, start)}...${str.slice(-end)}`
}

export const copyToClipboard = async (text: string): Promise<boolean> => {
  try {
    if (typeof window !== 'undefined' && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    } else if (typeof document !== 'undefined') {
      const textArea = document.createElement('textarea')
      textArea.value = text
      textArea.style.position = 'fixed'
      textArea.style.left = '-999999px'
      textArea.style.top = '-999999px'
      document.body.appendChild(textArea)
      textArea.focus()
      textArea.select()
      const success = document.execCommand('copy')
      textArea.remove()
      return success
    }
    return false
  } catch (err) {
    console.error('Failed to copy:', err)
    return false
  }
}