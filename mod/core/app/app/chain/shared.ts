"use client"

// Shared bits for the chain console: styling tokens, the chain-API client,
// and the wallet layer (a browser-local key or an injected browser wallet).

import { useState, useEffect } from 'react'
import { ethers } from 'ethers'
import { blake2AsHex } from '@polkadot/util-crypto'

export const TERM_FONT = "var(--font-digital), 'JetBrains Mono', 'Courier New', monospace"
export const ACCENT = 'var(--accent-primary, #10b981)'
export const READ = '#3b82f6'
export const WRITE = '#f59e0b'
export const DANGER = '#ef4444'

export interface NetworkInfo {
  key: string
  name: string
  chainId: number
  rpc: string
  explorer: string
  /** native currency symbol */
  currency: string
  /** the fleet's contracts are deployed here — DEPLOY/CONTRACTS/CONFIG apply */
  fleet?: boolean
  /** added by the user in this browser */
  custom?: boolean
  testnet?: boolean
}

/** The networks the console ships with. Fleet ones match config.json deployments. */
export const NETWORKS: Record<string, NetworkInfo> = {
  testnet: { key: 'testnet', name: 'Base Sepolia', chainId: 84532, rpc: 'https://sepolia.base.org', explorer: 'https://sepolia.basescan.org', currency: 'ETH', fleet: true, testnet: true },
  ganache: { key: 'ganache', name: 'Ganache', chainId: 1337, rpc: 'http://localhost:8545', explorer: '', currency: 'ETH', fleet: true, testnet: true },
  mainnet: { key: 'mainnet', name: 'Base', chainId: 8453, rpc: 'https://mainnet.base.org', explorer: 'https://basescan.org', currency: 'ETH', fleet: true },
  eth_sepolia: { key: 'eth_sepolia', name: 'Ethereum Sepolia', chainId: 11155111, rpc: 'https://ethereum-sepolia-rpc.publicnode.com', explorer: 'https://sepolia.etherscan.io', currency: 'ETH', testnet: true },
  arb_sepolia: { key: 'arb_sepolia', name: 'Arbitrum Sepolia', chainId: 421614, rpc: 'https://sepolia-rollup.arbitrum.io/rpc', explorer: 'https://sepolia.arbiscan.io', currency: 'ETH', testnet: true },
  op_sepolia: { key: 'op_sepolia', name: 'Optimism Sepolia', chainId: 11155420, rpc: 'https://sepolia.optimism.io', explorer: 'https://sepolia-optimism.etherscan.io', currency: 'ETH', testnet: true },
  amoy: { key: 'amoy', name: 'Polygon Amoy', chainId: 80002, rpc: 'https://rpc-amoy.polygon.technology', explorer: 'https://amoy.polygonscan.com', currency: 'POL', testnet: true },
  ethereum: { key: 'ethereum', name: 'Ethereum', chainId: 1, rpc: 'https://ethereum-rpc.publicnode.com', explorer: 'https://etherscan.io', currency: 'ETH' },
  arbitrum: { key: 'arbitrum', name: 'Arbitrum One', chainId: 42161, rpc: 'https://arb1.arbitrum.io/rpc', explorer: 'https://arbiscan.io', currency: 'ETH' },
  optimism: { key: 'optimism', name: 'Optimism', chainId: 10, rpc: 'https://mainnet.optimism.io', explorer: 'https://optimistic.etherscan.io', currency: 'ETH' },
  polygon: { key: 'polygon', name: 'Polygon', chainId: 137, rpc: 'https://polygon-rpc.com', explorer: 'https://polygonscan.com', currency: 'POL' },
}

export const NETWORK_KEYS = Object.keys(NETWORKS)

// ── custom networks ─────────────────────────────────────────────────────────
// Anything else the user points the console at, kept in this browser.

const LS_NETWORKS = 'chain_custom_networks'

export function customNetworks(): Record<string, NetworkInfo> {
  try {
    const raw = JSON.parse(safeGet(LS_NETWORKS) || '{}')
    return Object.fromEntries(
      Object.entries(raw as Record<string, NetworkInfo>)
        .map(([k, n]) => [k, { ...n, key: k, custom: true }]),
    )
  } catch { return {} }
}

export function saveCustomNetwork(net: NetworkInfo) {
  const all = customNetworks()
  all[net.key] = { ...net, custom: true }
  safeSet(LS_NETWORKS, JSON.stringify(all))
}

export function removeCustomNetwork(key: string) {
  const all = customNetworks()
  delete all[key]
  safeSet(LS_NETWORKS, JSON.stringify(all))
}

/**
 * Every chain the console knows. A saved entry under a builtin key is an
 * *override* — a different RPC for Base Sepolia, say — so it inherits the
 * builtin's flags (fleet/testnet) rather than losing them.
 */
export const allNetworks = (): Record<string, NetworkInfo> => {
  const out: Record<string, NetworkInfo> = { ...NETWORKS }
  for (const [k, n] of Object.entries(customNetworks())) out[k] = { ...NETWORKS[k], ...n }
  return out
}

/** True when this key ships with the console — an override of it can be reverted. */
export const isBuiltinNetwork = (key: string) => key in NETWORKS

/** True when the saved entry differs from the builtin it shadows. */
export const isOverridden = (key: string) => isBuiltinNetwork(key) && key in customNetworks()

/** Network by key — builtin or custom. Never undefined, so callers can just read it. */
export function netInfo(key: string): NetworkInfo {
  return allNetworks()[key]
    || { key, name: key, chainId: 0, rpc: '', explorer: '', currency: 'ETH' }
}

/** Name a chain id from the networks we know — falls back to the bare number. */
export function chainName(chainId: number): string {
  const hit = Object.values(allNetworks()).find(n => n.chainId === chainId)
  return hit ? hit.name : `chain #${chainId}`
}

export const explorerUrl = (network: string, address: string) => {
  const base = netInfo(network).explorer
  return base ? `${base}/address/${address}` : ''
}

export const txUrl = (network: string, hash: string) => {
  const base = netInfo(network).explorer
  return base ? `${base}/tx/${hash}` : ''
}

export const short = (s: string, head = 6, tail = 4) =>
  s && s.length > head + tail + 2 ? `${s.slice(0, head)}…${s.slice(-tail)}` : (s || '')

// ── viewport ────────────────────────────────────────────────────────────────

export const MOBILE_MAX = 760

/**
 * True on a phone-width viewport. Starts false so the server and the first
 * client render agree; the real answer lands on mount, before paint.
 */
export function useIsMobile(max = MOBILE_MAX): boolean {
  const [mobile, setMobile] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${max}px)`)
    const sync = () => setMobile(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [max])
  return mobile
}

// ── chain API ───────────────────────────────────────────────────────────────
// /chain/api/* is proxied to the chain module's API by app/chain/api/[...path].
// NOT /api/chain — the fleet gateway on :3000 sends every /api/* request to the
// protocol API and Next never sees it, which 405'd every call on the public
// host while working fine against :3001.

export const CHAIN_API_BASE = '/chain/api'

export async function chainApi(path: string, init?: { method?: string; body?: any }) {
  const method = init?.method || (init?.body ? 'POST' : 'GET')
  let res: Response
  try {
    res = await fetch(`${CHAIN_API_BASE}${path}`, {
      method,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      body: init?.body ? JSON.stringify(init.body) : undefined,
      cache: 'no-store',
    })
  } catch (e: any) {
    // The browser never reached the app at all — offline, or the app is down.
    noteApi(false, `can't reach ${CHAIN_API_BASE}${path} — ${e?.message || e}`)
    throw e
  }
  const text = await res.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { data = { detail: text } }
  // A 4xx from the chain module is an *answer* (bad args, unknown contract).
  // These statuses mean the bridge itself is broken — nobody is home behind it,
  // so every panel would sit empty with nothing to explain why.
  const bridgeDown = [0, 404, 405, 500, 502, 503, 504].includes(res.status)
  noteApi(!bridgeDown, bridgeDown ? `${CHAIN_API_BASE}${path} → HTTP ${res.status}` : '')
  if (!res.ok) throw new Error(data?.detail || `chain api ${res.status}`)
  return data
}

// ── is the chain API actually answering? ────────────────────────────────────
//
// Every panel here loads through chainApi and most of them swallow their own
// errors — which is fine per panel and awful in aggregate: when the bridge is
// down you get a console full of empty boxes and no idea why. So calls report
// their fate here and the page draws one banner over the lot.

export interface ApiHealth { down: boolean; detail: string }

let health: ApiHealth = { down: false, detail: '' }
const healthSubs = new Set<(h: ApiHealth) => void>()

function noteApi(ok: boolean, detail: string) {
  if (ok === !health.down && (ok || detail === health.detail)) return
  health = { down: !ok, detail: ok ? '' : detail }
  healthSubs.forEach(fn => fn(health))
}

export function useApiHealth(): ApiHealth {
  const [h, setH] = useState<ApiHealth>(health)
  useEffect(() => {
    setH(health)
    healthSubs.add(setH)
    return () => { healthSubs.delete(setH) }
  }, [])
  return h
}

// ── wallet ──────────────────────────────────────────────────────────────────
//
// Two ways to sign, both first-class, and each one is a *roster* rather than
// a single key:
//   local   — private keys held in this browser. The app sign-in key (when
//             signed in), a builder key minted on first use, and anything you
//             generate or paste in after that.
//   browser — an injected wallet (MetaMask & friends). Every account it has
//             permitted for this site is selectable; we switch/add the chain.

export type WalletKind = 'local' | 'browser'

const LS_KIND = 'chain_wallet_kind'
const LS_LOCAL_PK = 'chain_local_pk'
const LS_LOCAL_KEYS = 'chain_local_keys'
const LS_LOCAL_SEL = 'chain_local_key_sel'
const LS_BROWSER_SEL = 'chain_browser_addr'
const LS_SIGNED_OUT = 'chain_wallet_signed_out'

const safeGet = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const safeSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch {} }
const safeDel = (k: string) => { try { localStorage.removeItem(k) } catch {} }

export const savedWalletKind = () => (safeGet(LS_KIND) as WalletKind | null) || null
export const saveWalletKind = (kind: WalletKind | null) =>
  kind ? safeSet(LS_KIND, kind) : safeDel(LS_KIND)

/** SIGN OUT is a decision, not a gap — remember it so we don't silently reconnect. */
export const signedOut = () => safeGet(LS_SIGNED_OUT) === '1'
export const setSignedOut = (off: boolean) =>
  off ? safeSet(LS_SIGNED_OUT, '1') : safeDel(LS_SIGNED_OUT)

export const hasInjected = () => typeof window !== 'undefined' && !!(window as any).ethereum

// ── local keys ──

export interface LocalKey {
  id: string
  label: string
  pk: string
  address: string
  /** account = derived from the app sign-in; builder = minted once for this
      browser; imported = pasted or generated by hand */
  source: 'account' | 'builder' | 'imported'
}

const addrOf = (pk: string) => { try { return new ethers.Wallet(pk).address } catch { return '' } }

/** The builder key — minted the first time anyone asks for it. */
function builderPk(): string {
  let pk = safeGet(LS_LOCAL_PK)
  if (!pk) {
    pk = ethers.Wallet.createRandom().privateKey
    safeSet(LS_LOCAL_PK, pk)
  }
  return pk
}

const extraKeys = (): { id: string; label: string; pk: string }[] => {
  try { return JSON.parse(safeGet(LS_LOCAL_KEYS) || '[]') } catch { return [] }
}

/** Every local key this browser can sign with, in display order. */
export function localKeys(): LocalKey[] {
  const out: LocalKey[] = []
  const password = safeGet('wallet_password')
  if (password) {
    const pk = blake2AsHex(password, 256)
    out.push({ id: 'account', label: 'APP ACCOUNT', pk, address: addrOf(pk), source: 'account' })
  }
  const b = builderPk()
  out.push({ id: 'builder', label: 'BUILDER', pk: b, address: addrOf(b), source: 'builder' })
  for (const k of extraKeys()) {
    const address = addrOf(k.pk)
    if (address) out.push({ id: k.id, label: k.label, pk: k.pk, address, source: 'imported' })
  }
  return out
}

/** Generate (no pk) or import a key. Returns the roster entry. */
export function addLocalKey(label: string, pk?: string): LocalKey {
  const key = pk ? (pk.startsWith('0x') ? pk : `0x${pk}`) : ethers.Wallet.createRandom().privateKey
  const address = addrOf(key)
  if (!address) throw new Error('not a private key')
  const all = extraKeys()
  const dupe = all.find(k => addrOf(k.pk).toLowerCase() === address.toLowerCase())
  if (dupe) return { ...dupe, address, source: 'imported' }
  const id = `k_${Date.now().toString(36)}`
  const name = label.trim() || `KEY ${all.length + 1}`
  safeSet(LS_LOCAL_KEYS, JSON.stringify([...all, { id, label: name, pk: key }]))
  return { id, label: name, pk: key, address, source: 'imported' }
}

export function renameLocalKey(id: string, label: string) {
  safeSet(LS_LOCAL_KEYS, JSON.stringify(extraKeys().map(k => (k.id === id ? { ...k, label } : k))))
}

/** Only imported keys can be dropped — the account and builder keys are structural. */
export function removeLocalKey(id: string) {
  safeSet(LS_LOCAL_KEYS, JSON.stringify(extraKeys().filter(k => k.id !== id)))
  if (safeGet(LS_LOCAL_SEL) === id) safeDel(LS_LOCAL_SEL)
}

/** The local key that signs: the selected one, else the account key, else the builder. */
export function selectedLocalKey(): LocalKey {
  const keys = localKeys()
  const sel = safeGet(LS_LOCAL_SEL)
  return keys.find(k => k.id === sel) || keys[0]
}

export const selectLocalKey = (id: string) => safeSet(LS_LOCAL_SEL, id)

/** True when the signing local key comes from the app sign-in (not a minted key). */
export const localKeyIsAccount = () => selectedLocalKey().source === 'account'

export const localPrivateKey = () => selectedLocalKey().pk

export const localAddress = () => selectedLocalKey().address

// ── browser accounts ──

/** Which of the wallet's permitted accounts we sign with (it may expose several). */
export const savedBrowserAddress = () => safeGet(LS_BROWSER_SEL) || ''
export const saveBrowserAddress = (addr: string) =>
  addr ? safeSet(LS_BROWSER_SEL, addr) : safeDel(LS_BROWSER_SEL)

/** Every account the injected wallet has already permitted for this site. */
export async function browserAccounts(): Promise<string[]> {
  if (!hasInjected()) return []
  try {
    const accs: string[] = await (window as any).ethereum.request({ method: 'eth_accounts' })
    return (accs || []).map(a => ethers.getAddress(a))
  } catch { return [] }
}

/**
 * Ask the wallet to expose more accounts. MetaMask opens its account picker
 * for `wallet_requestPermissions`; wallets that don't know it fall back to a
 * plain connect prompt.
 */
export async function requestBrowserAccounts(): Promise<string[]> {
  if (!hasInjected()) throw new Error('No browser wallet found — install MetaMask')
  const eth = (window as any).ethereum
  try {
    await eth.request({ method: 'wallet_requestPermissions', params: [{ eth_accounts: {} }] })
  } catch (e: any) {
    if (e?.code === 4001) throw e
    await eth.request({ method: 'eth_requestAccounts' })
  }
  return browserAccounts()
}

export function readProvider(network: string) {
  return new ethers.JsonRpcProvider(netInfo(network).rpc, undefined, { staticNetwork: true })
}

// ── chain health ────────────────────────────────────────────────────────────

export interface NetProbe {
  /** the RPC answered */
  up: boolean
  block?: number
  /** what the RPC says its chain id is — not what we recorded */
  chainId?: number
  /** round trip in ms */
  ms: number
  error?: string
}

/**
 * Ask an RPC two questions: what block are you on, and who are you. The second
 * one matters — a copy-pasted RPC that points at the wrong chain deploys your
 * contract somewhere you never meant, and nothing else in the console notices.
 *
 * `staticNetwork` would answer the chain id from our own config, so this sends
 * the raw calls instead.
 */
export async function probeNetwork(network: string, timeoutMs = 6000): Promise<NetProbe> {
  const net = netInfo(network)
  const started = Date.now()
  if (!net.rpc) return { up: false, ms: 0, error: 'no RPC url' }
  const provider = new ethers.JsonRpcProvider(net.rpc, undefined, { staticNetwork: true })
  const timeout = new Promise<never>((_, reject) =>
    setTimeout(() => reject(new Error('timed out')), timeoutMs))
  try {
    const [block, chainId] = await Promise.race([
      Promise.all([
        provider.send('eth_blockNumber', []),
        provider.send('eth_chainId', []),
      ]),
      timeout,
    ])
    return {
      up: true, ms: Date.now() - started,
      block: parseInt(block, 16),
      chainId: parseInt(chainId, 16),
    }
  } catch (e: any) {
    return { up: false, ms: Date.now() - started, error: e?.shortMessage || e?.message || 'unreachable' }
  } finally {
    provider.destroy()
  }
}

/** Ask an injected wallet to move to `network`, adding the chain if unknown. */
export async function ensureChain(network: string) {
  const net = netInfo(network)
  const eth = (window as any).ethereum
  const hex = '0x' + net.chainId.toString(16)
  if ((await eth.request({ method: 'eth_chainId' })) === hex) return
  try {
    await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hex }] })
  } catch (e: any) {
    // 4902 = chain unknown to the wallet → offer to add it
    if (e?.code === 4902 || /unrecognized|not been added/i.test(e?.message || '')) {
      await eth.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: hex,
          chainName: net.name,
          rpcUrls: [net.rpc],
          nativeCurrency: { name: net.currency, symbol: net.currency, decimals: 18 },
          blockExplorerUrls: net.explorer ? [net.explorer] : undefined,
        }],
      })
    } else throw e
  }
}

/** Signer for the selected wallet + network. */
export async function getSigner(kind: WalletKind, network: string, address?: string): Promise<ethers.Signer> {
  if (kind === 'browser') {
    if (!hasInjected()) throw new Error('No browser wallet found — install MetaMask')
    await (window as any).ethereum.request({ method: 'eth_requestAccounts' })
    await ensureChain(network)
    // a permitted account other than the wallet's "selected" one still signs —
    // the wallet just prompts for that account instead
    return new ethers.BrowserProvider((window as any).ethereum).getSigner(address || undefined)
  }
  return new ethers.Wallet(localPrivateKey(), readProvider(network))
}

// ── protocol-auth token ─────────────────────────────────────────────────────
//
// The AGENT tab runs through the orbit/agent module, which authenticates with
// a wallet-signed, time-bounded token rather than a session: base64url of
// {data, time, key, signature}, the signature an EIP-191 personal_sign over
// exactly JSON.stringify({data, time}). A local key signs silently; a browser
// wallet prompts once. Cached per address for half of its 24h life.

const LS_AGENT_TOKEN = 'chain_agent_token'
const TOKEN_REUSE_S = 12 * 3600

function b64url(obj: any): string {
  const bytes = new TextEncoder().encode(JSON.stringify(obj))
  let bin = ''
  bytes.forEach(b => { bin += String.fromCharCode(b) })
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export async function agentToken(kind: WalletKind, address: string): Promise<string> {
  const addr = address.toLowerCase()
  try {
    const cached = JSON.parse(safeGet(LS_AGENT_TOKEN) || 'null')
    if (cached?.address === addr && cached.token && Date.now() / 1000 - cached.minted < TOKEN_REUSE_S) {
      return cached.token
    }
  } catch {}
  const data = { scope: 'chain' }
  const time = (Date.now() / 1000).toString()
  const message = JSON.stringify({ data, time })
  let signature: string
  if (kind === 'browser') {
    if (!hasInjected()) throw new Error('No browser wallet found — install MetaMask')
    signature = await (window as any).ethereum.request({ method: 'personal_sign', params: [message, addr] })
  } else {
    signature = await new ethers.Wallet(localPrivateKey()).signMessage(message)
  }
  const token = b64url({ data, time, key: addr, signature })
  safeSet(LS_AGENT_TOKEN, JSON.stringify({ address: addr, token, minted: Date.now() / 1000 }))
  return token
}

export const forgetAgentToken = () => { try { localStorage.removeItem(LS_AGENT_TOKEN) } catch {} }

// ── ERC-20 tokens ───────────────────────────────────────────────────────────
//
// Balances are read straight from the RPC so they work on any network, fleet
// or not. The tracked list per network lives in this browser; the fleet's own
// tokens and anything you deployed here get folded in automatically.

export const ERC20_ABI = [
  'function name() view returns (string)',
  'function symbol() view returns (string)',
  'function decimals() view returns (uint8)',
  'function balanceOf(address) view returns (uint256)',
]

export interface TokenBalance {
  address: string
  symbol: string
  decimals: number
  balance: string
  source: 'fleet' | 'built' | 'tracked'
}

const LS_TOKENS = 'chain_tokens'

const tokenStore = (): Record<string, string[]> => {
  try { return JSON.parse(safeGet(LS_TOKENS) || '{}') } catch { return {} }
}

export const trackedTokens = (network: string): string[] => tokenStore()[network] || []

export function trackToken(network: string, address: string) {
  const store = tokenStore()
  const list = store[network] || []
  const addr = ethers.getAddress(address)
  if (!list.some(a => a.toLowerCase() === addr.toLowerCase())) store[network] = [...list, addr]
  safeSet(LS_TOKENS, JSON.stringify(store))
}

export function untrackToken(network: string, address: string) {
  const store = tokenStore()
  store[network] = (store[network] || []).filter(a => a.toLowerCase() !== address.toLowerCase())
  safeSet(LS_TOKENS, JSON.stringify(store))
}

/** True when an ABI looks like an ERC-20 — used to spot tokens you deployed. */
export const isErc20Abi = (abi: any[]) => {
  const names = new Set((abi || []).filter(f => f?.type === 'function').map(f => f.name))
  return names.has('balanceOf') && names.has('symbol') && names.has('transfer')
}

/** symbol / decimals / balance for one token, or null if it isn't one. */
export async function readToken(
  provider: ethers.Provider, address: string, holder: string,
): Promise<TokenBalance | null> {
  try {
    const c = new ethers.Contract(address, ERC20_ABI, provider)
    const [symbol, decimals, raw] = await Promise.all([
      c.symbol(), c.decimals(), c.balanceOf(holder),
    ])
    return {
      address: ethers.getAddress(address),
      symbol,
      decimals: Number(decimals),
      balance: ethers.formatUnits(raw, decimals),
      source: 'tracked',
    }
  } catch {
    return null
  }
}

// ── ABI argument coercion ───────────────────────────────────────────────────

/** Turn a form string into the value ethers expects for `type`. */
export function coerceArg(type: string, raw: string): any {
  const v = (raw ?? '').trim()
  if (type.endsWith(']') || type.startsWith('tuple')) {
    if (!v) return []
    try { return JSON.parse(v) } catch { throw new Error(`${type} expects JSON, got: ${v}`) }
  }
  if (type === 'bool') return v.toLowerCase() === 'true' || v === '1'
  if (/^u?int/.test(type)) {
    if (!v) throw new Error(`${type} is required`)
    return BigInt(v)
  }
  if (type === 'address') {
    if (!ethers.isAddress(v)) throw new Error(`invalid address: ${v}`)
    return ethers.getAddress(v)
  }
  return v
}

export function coerceArgs(inputs: { name: string; type: string }[], values: Record<string, string>) {
  return inputs.map((i, idx) => coerceArg(i.type, values[i.name || `arg${idx}`] ?? ''))
}

/** Render a call result: scalars bare (uint256 → 5), structures as JSON. */
export const jsonify = (v: any): string => {
  if (typeof v === 'bigint' || typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (typeof v === 'string') return v
  return JSON.stringify(v, (_k, val) => (typeof val === 'bigint' ? val.toString() : val), 2)
}

export const placeholderFor = (type: string) =>
  type === 'address' ? '0x…'
    : type === 'bool' ? 'true'
      : type.endsWith(']') ? '["a","b"]'
        : /^u?int/.test(type) ? '0'
          : type

// ── contract cards ──────────────────────────────────────────────────────────
//
// The PLAY tab's deck, per network, kept in this browser: contracts you loaded
// by address, which cards are pinned or hidden, and any name you gave a card.
// The fleet's contracts and your builds come from the API — the book only
// records what you did to them.

export interface SavedContract {
  name: string
  address: string
  abi: any[]
  abiCid?: string
  added: number
}

export interface CardBook {
  saved: SavedContract[]
  /** lower-cased addresses */
  pinned: string[]
  hidden: string[]
  /** lower-cased address → the name you gave it */
  names: Record<string, string>
}

const LS_CARDS = 'chain_contract_cards'
const lc = (a: string) => a.toLowerCase()
const emptyBook = (): CardBook => ({ saved: [], pinned: [], hidden: [], names: {} })

const cardStore = (): Record<string, CardBook> => {
  try { return JSON.parse(safeGet(LS_CARDS) || '{}') } catch { return {} }
}

export const cardBook = (network: string): CardBook => ({ ...emptyBook(), ...(cardStore()[network] || {}) })

const writeBook = (network: string, book: CardBook) => {
  const store = cardStore()
  store[network] = book
  safeSet(LS_CARDS, JSON.stringify(store))
}

/** Keep a contract you loaded by hand. Same address again replaces the card. */
export function saveContractCard(network: string, c: Omit<SavedContract, 'added'>) {
  const book = cardBook(network)
  const address = ethers.getAddress(c.address)
  book.saved = [
    ...book.saved.filter(s => lc(s.address) !== lc(address)),
    { ...c, address, added: Date.now() },
  ]
  writeBook(network, book)
}

/** Drop a saved card and everything the book remembers about that address. */
export function forgetContractCard(network: string, address: string) {
  const book = cardBook(network)
  book.saved = book.saved.filter(s => lc(s.address) !== lc(address))
  book.pinned = book.pinned.filter(a => a !== lc(address))
  book.hidden = book.hidden.filter(a => a !== lc(address))
  delete book.names[lc(address)]
  writeBook(network, book)
}

/** Name any card — fleet, build or saved. An empty name puts the original back. */
export function nameContractCard(network: string, address: string, name: string) {
  const book = cardBook(network)
  const trimmed = name.trim()
  if (trimmed) book.names[lc(address)] = trimmed
  else delete book.names[lc(address)]
  book.saved = book.saved.map(s => lc(s.address) === lc(address) && trimmed ? { ...s, name: trimmed } : s)
  writeBook(network, book)
}

/** Pin a card to the top of the deck, or hide it — both toggle. */
export function toggleContractCard(network: string, flag: 'pinned' | 'hidden', address: string) {
  const book = cardBook(network)
  const a = lc(address)
  book[flag] = book[flag].includes(a) ? book[flag].filter(x => x !== a) : [...book[flag], a]
  // a hidden card can't be pinned — whichever you did last wins
  const other = flag === 'pinned' ? 'hidden' : 'pinned'
  if (book[flag].includes(a)) book[other] = book[other].filter(x => x !== a)
  writeBook(network, book)
}
