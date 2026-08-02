// Shared bits for the chain console: styling tokens, the chain-API client,
// and the wallet layer (a browser-local key or an injected browser wallet).

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

export const allNetworks = (): Record<string, NetworkInfo> => ({ ...NETWORKS, ...customNetworks() })

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

// ── chain API ───────────────────────────────────────────────────────────────
// /api/chain/* is proxied to the chain module's API by app/api/chain/[...path].

export async function chainApi(path: string, init?: { method?: string; body?: any }) {
  const method = init?.method || (init?.body ? 'POST' : 'GET')
  const res = await fetch(`/api/chain${path}`, {
    method,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.body ? JSON.stringify(init.body) : undefined,
    cache: 'no-store',
  })
  const text = await res.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { data = { detail: text } }
  if (!res.ok) throw new Error(data?.detail || `chain api ${res.status}`)
  return data
}

// ── wallet ──────────────────────────────────────────────────────────────────
//
// Two ways to sign, both first-class:
//   local   — a private key held in this browser. Reuses the key derived from
//             the app sign-in password when you're signed in, otherwise mints
//             a builder key so you can deploy to ganache/testnet right away.
//   browser — an injected wallet (MetaMask & friends); we switch/add the chain.

export type WalletKind = 'local' | 'browser'

const LS_KIND = 'chain_wallet_kind'
const LS_LOCAL_PK = 'chain_local_pk'
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

/** True when the local key comes from the app sign-in (not a minted builder key). */
export const localKeyIsAccount = () => !!safeGet('wallet_password')

/**
 * The local signing key: the app account's key when signed in, else a builder
 * key minted once and kept in this browser.
 */
export function localPrivateKey(): string {
  const password = safeGet('wallet_password')
  if (password) return blake2AsHex(password, 256)
  let pk = safeGet(LS_LOCAL_PK)
  if (!pk) {
    pk = ethers.Wallet.createRandom().privateKey
    safeSet(LS_LOCAL_PK, pk)
  }
  return pk
}

export const localAddress = () => new ethers.Wallet(localPrivateKey()).address

export function readProvider(network: string) {
  return new ethers.JsonRpcProvider(netInfo(network).rpc, undefined, { staticNetwork: true })
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
export async function getSigner(kind: WalletKind, network: string): Promise<ethers.Signer> {
  if (kind === 'browser') {
    if (!hasInjected()) throw new Error('No browser wallet found — install MetaMask')
    await (window as any).ethereum.request({ method: 'eth_requestAccounts' })
    await ensureChain(network)
    return new ethers.BrowserProvider((window as any).ethereum).getSigner()
  }
  return new ethers.Wallet(localPrivateKey(), readProvider(network))
}

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
