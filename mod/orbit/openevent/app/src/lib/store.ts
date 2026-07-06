// Tiny quota-safe localStorage wrapper. modc2.com shares localStorage across
// modules and it can be full → never let a write crash the app.
const mem: Record<string, string> = {}

export function lsGet(k: string): string | null {
  try { const v = localStorage.getItem(k); if (v != null) return v } catch {}
  return k in mem ? mem[k] : null
}
export function lsSet(k: string, v: string) {
  mem[k] = v
  try { localStorage.setItem(k, v) }
  catch { try { localStorage.removeItem(k); localStorage.setItem(k, v) } catch {} }
}

const K_KEYS = 'openevent.editkeys'   // event_id -> edit_key (this browser only)
const K_HANDLE = 'openevent.handle'
const K_CITY = 'openevent.city'

export function loadKeys(): Record<string, string> {
  try { return JSON.parse(lsGet(K_KEYS) || '{}') } catch { return {} }
}
export function saveKey(id: string, key: string) {
  const k = loadKeys(); k[id] = key; lsSet(K_KEYS, JSON.stringify(k))
}
export function loadHandle(): string { return lsGet(K_HANDLE) || '' }
export function saveHandle(h: string) { lsSet(K_HANDLE, h) }
export function loadCity(): string | null { return lsGet(K_CITY) }
export function saveCity(c: string) { lsSet(K_CITY, c) }
