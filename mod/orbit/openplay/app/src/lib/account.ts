import { Wallet, scryptSync, toUtf8Bytes } from 'ethers'
import { api } from './api'

// ── Identity ──────────────────────────────────────────────────────
// You sign in with a NAME + PASSWORD. The browser deterministically derives
// an Ethereum keypair from them (scrypt) — the password never leaves this
// device. The name is then *claimed* on the server by proving control of the
// key (a signature), so one name maps to exactly one key and can't be
// impersonated. The same name+password regenerates the same key anywhere, so
// it's a real sign-in, not just local storage. You can rotate the key any time
// (new password, or a brand-new key) while keeping your name — authorised by a
// signature from your current key.
export type Account = {
  name: string          // claimed display name (unique, case-insensitive)
  address: string       // public address others can pay you at
  privateKey: string    // lives only in this browser
  derived: boolean      // true: reproducible from name+password; false: a one-off key (back it up!)
  rotations?: number
}

// ── quota-safe storage (modc2.com shares ONE localStorage across modules,
//    so a write can throw if the origin is full → never let it crash us) ──
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

const K_ACCT = 'openplay.acct'
const K_KEYS = 'openplay.adminkeys'
const K_SUDO = 'openplay.sudo'
const K_CITY = 'openplay.city'
const K_GUEST = 'openplay.guest'

// ── name normalisation (must match the server's _name_key / _valid_name) ──
export function normName(name: string): string { return (name || '').trim().replace(/\s+/g, ' ') }
function nameKey(name: string): string { return normName(name).toLowerCase() }
export function nameError(name: string): string | null {
  const d = normName(name)
  if (d.length < 2 || d.length > 32) return 'Name must be 2–32 characters.'
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f]/.test(d)) return 'Name has invalid characters.'
  return null
}

// ── deterministic key derivation (scrypt: name is the salt) ──────────
export function deriveWallet(name: string, password: string): Wallet {
  const salt = toUtf8Bytes('openplay/acct/v1/' + nameKey(name))
  const dk = scryptSync(toUtf8Bytes(password), salt, 16384, 8, 1, 32) // 0x-prefixed 32-byte hex
  return new Wallet(dk)
}

// ── canonical signed messages — MUST byte-match the server (mod.py) ──
function claimMessage(name: string, address: string, ts: number) {
  return `OpenPlay — claim identity\nName: ${name}\nKey: ${address}\nTime: ${ts}`
}
function rotateMessage(name: string, newAddress: string, ts: number) {
  return `OpenPlay — rotate key\nName: ${name}\nNew key: ${newAddress}\nTime: ${ts}`
}
function nowTs() { return Math.floor(Date.now() / 1000) }

// ── persistence ──────────────────────────────────────────────────
export function loadAccount(): Account | null {
  const raw = safeGet(K_ACCT); if (!raw) return null
  try { const a = JSON.parse(raw); return a && a.privateKey ? a : null } catch { return null }
}
export function saveAccount(a: Account): boolean { return safeSet(K_ACCT, JSON.stringify(a)) }
export function clearAccount() { safeDel(K_ACCT) }

// ── sign in: claim a new name, or unlock the one that's yours ────────
// Throws a friendly Error if the name is someone else's / the password is wrong.
export async function signIn(name: string, password: string): Promise<{ account: Account; created: boolean }> {
  const disp = normName(name)
  const nameErr = nameError(disp); if (nameErr) throw new Error(nameErr)
  if (!password) throw new Error('Enter a password.')
  const w = deriveWallet(disp, password)

  // If the name exists but resolves to a different key, it's either taken by
  // someone else or the password is wrong — same signal, friendly message.
  const info = await api(`account/${encodeURIComponent(disp)}`)
  if (info.exists && (info.address || '').toLowerCase() !== w.address.toLowerCase()) {
    throw new Error(`“${disp}” is taken, or that password doesn’t match it.`)
  }

  const ts = nowTs()
  const signature = await w.signMessage(claimMessage(disp, w.address, ts))
  const res = await api('account/register', { method: 'POST', body: { name: disp, address: w.address, signature, ts } })
  const account: Account = { name: res.name || disp, address: w.address, privateKey: w.privateKey, derived: true, rotations: 0 }
  saveAccount(account)
  return { account, created: !!res.claimed }
}

// ── sign in with a raw private key (e.g. a rotated one-off key) ──────
export async function signInWithKey(name: string, privateKey: string): Promise<Account> {
  const disp = normName(name)
  const nameErr = nameError(disp); if (nameErr) throw new Error(nameErr)
  let w: Wallet
  try { w = new Wallet(privateKey.trim()) } catch { throw new Error('That doesn’t look like a valid private key.') }
  const info = await api(`account/${encodeURIComponent(disp)}`)
  if (info.exists && (info.address || '').toLowerCase() !== w.address.toLowerCase()) {
    throw new Error(`That key doesn’t own “${disp}”.`)
  }
  const ts = nowTs()
  const signature = await w.signMessage(claimMessage(disp, w.address, ts))
  const res = await api('account/register', { method: 'POST', body: { name: disp, address: w.address, signature, ts } })
  const account: Account = { name: res.name || disp, address: w.address, privateKey: w.privateKey, derived: false, rotations: info.rotations || 0 }
  saveAccount(account)
  return account
}

// ── rotate the key (keep the name); signed by the CURRENT key ────────
async function rotateTo(acct: Account, next: Wallet, derived: boolean): Promise<Account> {
  const cur = new Wallet(acct.privateKey)
  const ts = nowTs()
  const signature = await cur.signMessage(rotateMessage(acct.name, next.address, ts))
  const res = await api('account/rotate', { method: 'POST', body: { name: acct.name, new_address: next.address, signature, ts } })
  const updated: Account = { name: acct.name, address: next.address, privateKey: next.privateKey, derived, rotations: res.rotations ?? (acct.rotations || 0) + 1 }
  saveAccount(updated)
  return updated
}

// change password → re-derive a new key from the new password (stays recoverable)
export function changePassword(acct: Account, newPassword: string): Promise<Account> {
  if (!newPassword) return Promise.reject(new Error('Enter a new password.'))
  return rotateTo(acct, deriveWallet(acct.name, newPassword), true)
}

// rotate to a brand-new random key (not tied to a password — back it up!)
export function rotateFresh(acct: Account): Promise<Account> {
  return rotateTo(acct, Wallet.createRandom(), false)
}

// ── guest identity — play (and host) without an account ──────────
// Anyone can create a game: you pick a name, it's kept on this device, and
// the organizer key the server hands back is what actually proves you run
// the game. A guest name is NOT claimed on the server, so we refuse any
// name a key already owns — otherwise a guest could wear someone's handle.
// Signing in later upgrades the same flow to a name that's provably yours.
export function loadGuest(): string { return safeGet(K_GUEST) || '' }
export function saveGuest(name: string): string {
  const disp = normName(name)
  safeSet(K_GUEST, disp)
  return disp
}
export function clearGuest() { safeDel(K_GUEST) }

// Validate a guest name and make sure it isn't a claimed account. Returns
// the normalised name; throws a friendly Error otherwise.
export async function useGuestName(name: string): Promise<string> {
  const disp = normName(name)
  const err = nameError(disp); if (err) throw new Error(err)
  const info = await api(`account/${encodeURIComponent(disp)}`).catch(() => ({ exists: false }))
  if (info.exists) throw new Error(`“${disp}” belongs to a signed-in player — pick another, or sign in with it.`)
  return saveGuest(disp)
}

const GUEST_WORDS = ['striker', 'keeper', 'sweeper', 'winger', 'baller', 'hooper',
                     'spiker', 'netminder', 'captain', 'sub', 'rookie', 'ringer']
export function randomGuestName(): string {
  const w = GUEST_WORDS[Math.floor(Math.random() * GUEST_WORDS.length)]
  return `${w}_${Math.floor(Math.random() * 900 + 100)}`
}

// ── per-game organizer keys ──────────────────────────────────────
export function loadKeys(): Record<string, string> {
  try { return JSON.parse(safeGet(K_KEYS) || '{}') } catch { return {} }
}
export function saveKey(id: string, key: string): Record<string, string> {
  const k = loadKeys(); k[id] = key; safeSet(K_KEYS, JSON.stringify(k)); return k
}

// ── module-admin (owner) secret ──────────────────────────────────
export function loadSudo(): string { return safeGet(K_SUDO) || '' }
export function saveSudo(s: string) { safeSet(K_SUDO, s) }
export function clearSudo() { safeDel(K_SUDO) }

// ── selected city ────────────────────────────────────────────────
export function loadCity(): string | null { return safeGet(K_CITY) }
export function saveCity(c: string) { safeSet(K_CITY, c) }

export function shortAddr(a?: string) { return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '' }
