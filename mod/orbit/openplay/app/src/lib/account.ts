import { Wallet } from 'ethers'

const K_ACCT = 'openplay.acct'
const K_SUDO = 'openplay.sudo'
const K_KEYS = 'openplay.adminkeys'
const K_CITY = 'openplay.city'

// Identity = an email label backed by a locally-generated Ethereum key.
// The key lives only in this browser; it's the user's wallet (receive/spend)
// and can be revealed/exported to load into MetaMask etc.
export type Account = {
  email: string
  handle: string
  address: string
  privateKey: string
  mnemonic?: string
}

// ── quota-safe storage (localStorage on modc2.com is shared across modules
//    and can be full → never let a write crash the app) ──────────────
const mem: Record<string, string> = {}
export function safeGet(k: string): string | null {
  try { const v = localStorage.getItem(k); if (v != null) return v } catch {}
  return k in mem ? mem[k] : null
}
export function safeSet(k: string, v: string): boolean {
  mem[k] = v
  try { localStorage.setItem(k, v); return true }
  catch {
    try { localStorage.removeItem(k); localStorage.setItem(k, v); return true } catch { return false }
  }
}
export function safeDel(k: string) {
  delete mem[k]
  try { localStorage.removeItem(k) } catch {}
}

// ── account ──────────────────────────────────────────────────────
export function loadAccount(): Account | null {
  const raw = safeGet(K_ACCT); if (!raw) return null
  try { return JSON.parse(raw) } catch { return null }
}
export function saveAccount(a: Account): boolean { return safeSet(K_ACCT, JSON.stringify(a)) }
export function clearAccount() { safeDel(K_ACCT) }

export function createAccount(email: string, handle?: string): Account {
  const w = Wallet.createRandom()
  const a: Account = {
    email: email.trim(),
    handle: (handle || email.split('@')[0] || 'player').trim(),
    address: w.address, privateKey: w.privateKey, mnemonic: w.mnemonic?.phrase,
  }
  saveAccount(a); return a
}

// load an existing key (private key 0x… or 12/24-word mnemonic) "to spend"
export function importAccount(email: string, secret: string, handle?: string): Account {
  secret = secret.trim()
  const w: any = secret.includes(' ') ? Wallet.fromPhrase(secret) : new Wallet(secret)
  const a: Account = {
    email: email.trim(),
    handle: (handle || email.split('@')[0] || 'player').trim(),
    address: w.address, privateKey: w.privateKey, mnemonic: w.mnemonic?.phrase,
  }
  saveAccount(a); return a
}

// ── per-game organizer keys ──────────────────────────────────────
export function loadKeys(): Record<string, string> {
  try { return JSON.parse(safeGet(K_KEYS) || '{}') } catch { return {} }
}
export function saveKey(id: string, key: string) {
  const k = loadKeys(); k[id] = key; safeSet(K_KEYS, JSON.stringify(k))
}

// ── module-admin (owner) secret ──────────────────────────────────
export function loadSudo(): string { return safeGet(K_SUDO) || '' }
export function saveSudo(s: string) { safeSet(K_SUDO, s) }
export function clearSudo() { safeDel(K_SUDO) }

// ── selected city ────────────────────────────────────────────────
export function loadCity(): string | null { return safeGet(K_CITY) }
export function saveCity(c: string) { safeSet(K_CITY, c) }

export function shortAddr(a?: string) { return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '' }
