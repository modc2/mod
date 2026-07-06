"use client";

// ── Client-side wallet layer ─────────────────────────────────────────────────
//
// Two ways to sign: an injected wallet (MetaMask & friends) or a persistent
// browser-local keypair (ethers Wallet, private key kept in localStorage).
// Every page shares one WalletProvider; getSigner(network) hands back an
// ethers v6 signer already pointed at the right chain — MetaMask is asked to
// switch/add the chain, the local wallet just uses the network's RPC.

import { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react'

export type WalletKind = 'metamask' | 'local'

export interface NetworkInfo {
  key: string
  name: string
  chainId: number
  rpc: string
  explorer: string
  currency: string
}

export const NETWORKS: Record<string, NetworkInfo> = {
  testnet: { key: 'testnet', name: 'Base Sepolia', chainId: 84532, rpc: 'https://sepolia.base.org', explorer: 'https://sepolia.basescan.org', currency: 'ETH' },
  mainnet: { key: 'mainnet', name: 'Base', chainId: 8453, rpc: 'https://mainnet.base.org', explorer: 'https://basescan.org', currency: 'ETH' },
  ganache: { key: 'ganache', name: 'Ganache', chainId: 1337, rpc: 'http://localhost:8545', explorer: '', currency: 'ETH' },
}

// Shared modc2 localStorage origin — never let quota errors crash the app.
const LS_KIND = 'chain.wallet.kind'
const LS_PK = 'chain.wallet.pk'
const safeSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch {} }
const safeGet = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const safeDel = (k: string) => { try { localStorage.removeItem(k) } catch {} }

declare global {
  interface Window { ethereum?: any }
}

export interface WalletState {
  kind: WalletKind | null
  address: string
  /** chainId the injected wallet is currently on (local wallet: null — it follows the app network) */
  injectedChainId: number | null
  hasInjected: boolean
  connectMetaMask: () => Promise<void>
  /** create (or reload) the browser-local wallet */
  connectLocal: () => Promise<void>
  importLocalKey: (pk: string) => Promise<void>
  exportLocalKey: () => string | null
  disconnect: () => void
  /** signer bound to the given network; switches MetaMask chains as needed */
  getSigner: (network: string) => Promise<any>
}

const WalletCtx = createContext<WalletState | null>(null)

export function getReadProvider(network: string) {
  const net = NETWORKS[network]
  // lazy import keeps ethers out of the first paint
  return import('ethers').then(({ JsonRpcProvider }) => new JsonRpcProvider(net.rpc, undefined, { staticNetwork: true }))
}

async function ensureInjectedChain(network: string) {
  const net = NETWORKS[network]
  const eth = window.ethereum
  const hex = '0x' + net.chainId.toString(16)
  const current = await eth.request({ method: 'eth_chainId' })
  if (current === hex) return
  try {
    await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hex }] })
  } catch (e: any) {
    // 4902 = unknown chain → offer to add it
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
    } else {
      throw e
    }
  }
}

export function WalletProvider({ children }: { children: React.ReactNode }) {
  const [kind, setKind] = useState<WalletKind | null>(null)
  const [address, setAddress] = useState('')
  const [injectedChainId, setInjectedChainId] = useState<number | null>(null)
  const [hasInjected, setHasInjected] = useState(false)

  // ── silent reconnect on mount ──
  useEffect(() => {
    setHasInjected(typeof window !== 'undefined' && !!window.ethereum)
    const saved = safeGet(LS_KIND) as WalletKind | null
    if (saved === 'local') {
      const pk = safeGet(LS_PK)
      if (pk) {
        import('ethers').then(({ Wallet }) => {
          try {
            const w = new Wallet(pk)
            setKind('local'); setAddress(w.address)
          } catch { safeDel(LS_PK); safeDel(LS_KIND) }
        })
      }
    } else if (saved === 'metamask' && window.ethereum) {
      window.ethereum.request({ method: 'eth_accounts' }).then((accs: string[]) => {
        if (accs?.length) { setKind('metamask'); setAddress(accs[0]) }
      }).catch(() => {})
      window.ethereum.request({ method: 'eth_chainId' })
        .then((id: string) => setInjectedChainId(parseInt(id, 16))).catch(() => {})
    }
  }, [])

  // ── injected wallet event streams ──
  useEffect(() => {
    const eth = typeof window !== 'undefined' ? window.ethereum : null
    if (!eth?.on) return
    const onAccounts = (accs: string[]) => {
      if (kind !== 'metamask') return
      if (accs?.length) setAddress(accs[0])
      else { setKind(null); setAddress(''); safeDel(LS_KIND) }
    }
    const onChain = (id: string) => setInjectedChainId(parseInt(id, 16))
    eth.on('accountsChanged', onAccounts)
    eth.on('chainChanged', onChain)
    return () => {
      eth.removeListener?.('accountsChanged', onAccounts)
      eth.removeListener?.('chainChanged', onChain)
    }
  }, [kind])

  const connectMetaMask = useCallback(async () => {
    if (!window.ethereum) throw new Error('No injected wallet found — install MetaMask')
    const accs: string[] = await window.ethereum.request({ method: 'eth_requestAccounts' })
    if (!accs?.length) throw new Error('No account authorized')
    setKind('metamask'); setAddress(accs[0])
    safeSet(LS_KIND, 'metamask')
    const id: string = await window.ethereum.request({ method: 'eth_chainId' })
    setInjectedChainId(parseInt(id, 16))
  }, [])

  const connectLocal = useCallback(async () => {
    const { Wallet } = await import('ethers')
    let pk = safeGet(LS_PK)
    if (!pk) {
      pk = Wallet.createRandom().privateKey
      safeSet(LS_PK, pk)
    }
    const w = new Wallet(pk)
    setKind('local'); setAddress(w.address); setInjectedChainId(null)
    safeSet(LS_KIND, 'local')
  }, [])

  const importLocalKey = useCallback(async (pk: string) => {
    const { Wallet } = await import('ethers')
    const clean = pk.trim().startsWith('0x') ? pk.trim() : '0x' + pk.trim()
    const w = new Wallet(clean) // throws on invalid key
    safeSet(LS_PK, clean)
    setKind('local'); setAddress(w.address); setInjectedChainId(null)
    safeSet(LS_KIND, 'local')
  }, [])

  const exportLocalKey = useCallback(() => safeGet(LS_PK), [])

  const disconnect = useCallback(() => {
    // keep the local private key so "Browser wallet" reconnects to the same identity
    setKind(null); setAddress(''); setInjectedChainId(null)
    safeDel(LS_KIND)
  }, [])

  const getSigner = useCallback(async (network: string) => {
    const net = NETWORKS[network]
    if (!net) throw new Error(`Unknown network: ${network}`)
    if (kind === 'metamask') {
      const { BrowserProvider } = await import('ethers')
      await ensureInjectedChain(network)
      const provider = new BrowserProvider(window.ethereum)
      return provider.getSigner()
    }
    if (kind === 'local') {
      const pk = safeGet(LS_PK)
      if (!pk) throw new Error('Local wallet key missing')
      const { Wallet, JsonRpcProvider } = await import('ethers')
      return new Wallet(pk, new JsonRpcProvider(net.rpc, undefined, { staticNetwork: true }))
    }
    throw new Error('Connect a wallet first')
  }, [kind])

  const value = useMemo<WalletState>(() => ({
    kind, address, injectedChainId, hasInjected,
    connectMetaMask, connectLocal, importLocalKey, exportLocalKey, disconnect, getSigner,
  }), [kind, address, injectedChainId, hasInjected, connectMetaMask, connectLocal, importLocalKey, exportLocalKey, disconnect, getSigner])

  return <WalletCtx.Provider value={value}>{children}</WalletCtx.Provider>
}

export function useWallet(): WalletState {
  const ctx = useContext(WalletCtx)
  if (!ctx) throw new Error('useWallet must be used inside WalletProvider')
  return ctx
}
