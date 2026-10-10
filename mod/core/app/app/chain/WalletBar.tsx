"use client"

// The sign-in layer. Two rosters, one active signer:
//   browser — every account the injected wallet (MetaMask & friends) has
//             permitted for this site. It reconnects itself on every visit;
//             only an explicit SIGN OUT keeps us off it.
//   local   — keys held in this browser: the app account, a builder key, and
//             anything generated or imported since.
// Whichever is active signs every deploy and every write on this page. The
// picker that drives it lives in AccountPicker.tsx.

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import {
  WalletKind, ensureChain, getSigner, hasInjected, readProvider,
  localKeys as loadLocalKeys, selectedLocalKey, selectLocalKey, addLocalKey, removeLocalKey,
  browserAccounts, requestBrowserAccounts, savedBrowserAddress, saveBrowserAddress,
  savedWalletKind, saveWalletKind, signedOut, setSignedOut, type LocalKey,
} from './shared'

export interface ChainWallet {
  kind: WalletKind | null
  address: string
  balance: string | null
  injectedChainId: number | null
  /** an injected wallet exists — resolved after mount, never during SSR */
  injected: boolean
  /** accounts the injected wallet has permitted for this site */
  accounts: string[]
  /** keys held in this browser */
  localKeys: LocalKey[]
  connecting: WalletKind | null
  /** sign in with a browser account (address) or a local key (id) */
  connect: (kind: WalletKind, pick?: string) => Promise<void>
  /** ask the browser wallet to expose more accounts */
  connectMore: () => Promise<void>
  newKey: (label: string, pk?: string) => LocalKey
  dropKey: (id: string) => void
  disconnect: () => void
  signer: () => Promise<ethers.Signer>
  switchChain: () => Promise<void>
  refresh: () => void
}

export function useChainWallet(network: string): ChainWallet {
  const [kind, setKind] = useState<WalletKind | null>(null)
  const [address, setAddress] = useState('')
  const [balance, setBalance] = useState<string | null>(null)
  const [injectedChainId, setInjectedChainId] = useState<number | null>(null)
  const [injected, setInjected] = useState(false)
  const [accounts, setAccounts] = useState<string[]>([])
  const [localKeys, setLocalKeys] = useState<LocalKey[]>([])
  const [connecting, setConnecting] = useState<WalletKind | null>(null)
  const [tick, setTick] = useState(0)

  // Which permitted account signs: the one we picked last time if the wallet
  // still exposes it, else whatever the wallet has selected.
  const pickBrowser = (accs: string[]) => {
    const saved = savedBrowserAddress().toLowerCase()
    return accs.find(a => a.toLowerCase() === saved) || accs[0] || ''
  }

  // ── silent reconnect ──
  useEffect(() => {
    setInjected(hasInjected())
    setLocalKeys(loadLocalKeys())
    const saved = savedWalletKind()
    if (saved === 'local') {
      try { setKind('local'); setAddress(selectedLocalKey().address) } catch {}
    }
    if (!hasInjected()) return
    const eth = (window as any).ethereum
    browserAccounts().then(accs => {
      setAccounts(accs)
      if (saved === 'local' || signedOut() || !accs.length) return
      const addr = pickBrowser(accs)
      setKind('browser'); setAddress(addr); saveWalletKind('browser'); saveBrowserAddress(addr)
    })
    eth.request({ method: 'eth_chainId' })
      .then((id: string) => setInjectedChainId(parseInt(id, 16))).catch(() => {})
  }, [])

  // ── injected wallet events ──
  useEffect(() => {
    const eth = typeof window !== 'undefined' ? (window as any).ethereum : null
    if (!eth?.on) return
    const onAccounts = (raw: string[]) => {
      const accs = (raw || []).map(a => { try { return ethers.getAddress(a) } catch { return a } })
      setAccounts(accs)
      if (savedWalletKind() !== 'browser') return
      if (!accs.length) { setKind(null); setAddress(''); saveWalletKind(null); return }
      const addr = pickBrowser(accs)
      setAddress(addr); saveBrowserAddress(addr)
    }
    const onChain = (id: string) => setInjectedChainId(parseInt(id, 16))
    eth.on('accountsChanged', onAccounts)
    eth.on('chainChanged', onChain)
    return () => {
      eth.removeListener?.('accountsChanged', onAccounts)
      eth.removeListener?.('chainChanged', onChain)
    }
  }, [])

  // ── balance on the selected network ──
  useEffect(() => {
    let cancelled = false
    if (!address) { setBalance(null); return }
    readProvider(network).getBalance(address)
      .then(b => { if (!cancelled) setBalance(ethers.formatEther(b)) })
      .catch(() => { if (!cancelled) setBalance(null) })
    return () => { cancelled = true }
  }, [address, network, tick])

  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 20000)
    return () => clearInterval(iv)
  }, [])

  const connect = useCallback(async (want: WalletKind, pick?: string) => {
    setConnecting(want)
    setSignedOut(false)
    try {
      if (want === 'local') {
        if (pick) selectLocalKey(pick)
        const key = selectedLocalKey()
        setLocalKeys(loadLocalKeys())
        setKind('local'); setAddress(key.address); saveWalletKind('local')
      } else {
        if (!hasInjected()) throw new Error('No browser wallet found — install MetaMask')
        let accs = await browserAccounts()
        if (!accs.length) {
          await (window as any).ethereum.request({ method: 'eth_requestAccounts' })
          accs = await browserAccounts()
        }
        if (!accs.length) throw new Error('The wallet exposed no accounts')
        setAccounts(accs)
        const addr = pick && accs.some(a => a.toLowerCase() === pick.toLowerCase())
          ? accs.find(a => a.toLowerCase() === pick.toLowerCase())!
          : pickBrowser(accs)
        saveBrowserAddress(addr)
        setKind('browser'); setAddress(addr); saveWalletKind('browser')
        const id = await (window as any).ethereum.request({ method: 'eth_chainId' })
        setInjectedChainId(parseInt(id, 16))
      }
    } finally {
      setConnecting(null)
    }
  }, [])

  const connectMore = useCallback(async () => {
    setConnecting('browser')
    setSignedOut(false)
    try {
      const accs = await requestBrowserAccounts()
      setAccounts(accs)
      if (!accs.length) return
      // the wallet puts its newly selected account first — follow it
      const addr = accs[0]
      saveBrowserAddress(addr)
      setKind('browser'); setAddress(addr); saveWalletKind('browser')
      const id = await (window as any).ethereum.request({ method: 'eth_chainId' })
      setInjectedChainId(parseInt(id, 16))
    } finally {
      setConnecting(null)
    }
  }, [])

  const newKey = useCallback((label: string, pk?: string) => {
    const key = addLocalKey(label, pk)
    setLocalKeys(loadLocalKeys())
    return key
  }, [])

  const dropKey = useCallback((id: string) => {
    removeLocalKey(id)
    const keys = loadLocalKeys()
    setLocalKeys(keys)
    // dropping the key that was signing falls back to the next one
    if (kind === 'local') setAddress(selectedLocalKey().address)
  }, [kind])

  const disconnect = useCallback(() => {
    setKind(null); setAddress(''); saveWalletKind(null); setSignedOut(true)
  }, [])

  const signer = useCallback(async () => {
    if (!kind) throw new Error('Sign in with a wallet first')
    return getSigner(kind, network, kind === 'browser' ? address : undefined)
  }, [kind, network, address])

  const switchChain = useCallback(async () => {
    await ensureChain(network)
    const id = await (window as any).ethereum.request({ method: 'eth_chainId' })
    setInjectedChainId(parseInt(id, 16))
  }, [network])

  return {
    kind, address, balance, injectedChainId, injected, accounts, localKeys, connecting,
    connect, connectMore, newKey, dropKey, disconnect, signer, switchChain,
    refresh: () => setTick(t => t + 1),
  }
}
