"use client"

// Sign-in strip. MetaMask (or any injected wallet) is the front door and
// reconnects itself on every visit; the browser-local key is the back door for
// when you'd rather not click a popup. Whichever is active signs every deploy
// and every write call on this page. The network picker lives here too, because
// which chain you're on is part of who you're signed in as.

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, WalletKind, chainName, ensureChain, getSigner, hasInjected, netInfo,
  localAddress, localKeyIsAccount, localPrivateKey, readProvider,
  savedWalletKind, saveWalletKind, signedOut, setSignedOut, short, explorerUrl,
} from './shared'
import { Btn, panelStyle } from './ui'
import { NetworkPicker } from './NetworkPicker'

export interface ChainWallet {
  kind: WalletKind | null
  address: string
  balance: string | null
  injectedChainId: number | null
  /** an injected wallet exists — resolved after mount, never during SSR */
  injected: boolean
  connecting: WalletKind | null
  connect: (kind: WalletKind) => Promise<void>
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
  const [connecting, setConnecting] = useState<WalletKind | null>(null)
  const [tick, setTick] = useState(0)

  // ── silent reconnect ──
  // An injected wallet that already trusts this site signs us straight back in,
  // even on a first visit — no popup, no button. Only an explicit SIGN OUT
  // (saved kind 'local') keeps us off it.
  useEffect(() => {
    setInjected(hasInjected())
    const saved = savedWalletKind()
    if (saved === 'local') {
      try { setKind('local'); setAddress(localAddress()) } catch {}
      return
    }
    if (!hasInjected() || signedOut()) return
    const eth = (window as any).ethereum
    eth.request({ method: 'eth_accounts' })
      .then((accs: string[]) => {
        if (accs?.length) { setKind('browser'); setAddress(accs[0]); saveWalletKind('browser') }
      })
      .catch(() => {})
    eth.request({ method: 'eth_chainId' })
      .then((id: string) => setInjectedChainId(parseInt(id, 16))).catch(() => {})
  }, [])

  // ── injected wallet events ──
  useEffect(() => {
    const eth = typeof window !== 'undefined' ? (window as any).ethereum : null
    if (!eth?.on) return
    const onAccounts = (accs: string[]) => {
      if (savedWalletKind() !== 'browser') return
      if (accs?.length) setAddress(accs[0])
      else { setKind(null); setAddress(''); saveWalletKind(null) }
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

  const connect = useCallback(async (want: WalletKind) => {
    setConnecting(want)
    setSignedOut(false)
    try {
      if (want === 'local') {
        const addr = localAddress()
        setKind('local'); setAddress(addr); saveWalletKind('local')
      } else {
        if (!hasInjected()) throw new Error('No browser wallet found — install MetaMask')
        const signer = await getSigner('browser', network)
        const addr = await signer.getAddress()
        setKind('browser'); setAddress(addr); saveWalletKind('browser')
        const id = await (window as any).ethereum.request({ method: 'eth_chainId' })
        setInjectedChainId(parseInt(id, 16))
      }
    } finally {
      setConnecting(null)
    }
  }, [network])

  const disconnect = useCallback(() => {
    setKind(null); setAddress(''); saveWalletKind(null); setSignedOut(true)
  }, [])

  const signer = useCallback(async () => {
    if (!kind) throw new Error('Sign in with a wallet first')
    return getSigner(kind, network)
  }, [kind, network])

  const switchChain = useCallback(async () => {
    await ensureChain(network)
    const id = await (window as any).ethereum.request({ method: 'eth_chainId' })
    setInjectedChainId(parseInt(id, 16))
  }, [network])

  return {
    kind, address, balance, injectedChainId, injected, connecting,
    connect, disconnect, signer, switchChain, refresh: () => setTick(t => t + 1),
  }
}

export function WalletBar({
  wallet, network, setNetwork,
}: {
  wallet: ChainWallet
  network: string
  setNetwork: (key: string) => void
}) {
  const [showKey, setShowKey] = useState(false)
  const net = netInfo(network)
  const wrongChain = wallet.kind === 'browser'
    && wallet.injectedChainId !== null
    && wallet.injectedChainId !== net.chainId

  const copy = (text: string, what: string) => {
    navigator.clipboard.writeText(text)
    toast.success(`${what} copied`)
  }

  const connectBrowser = () =>
    wallet.connect('browser').catch(e => toast.error(e?.message || 'Connect failed'))

  return (
    <div style={{
      ...panelStyle, padding: '10px 16px', marginBottom: '16px',
      display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap',
    }}>
      <NetworkPicker network={network} setNetwork={setNetwork} />

      {!wallet.kind ? (
        <>
          <Btn onClick={connectBrowser} size="sm" color="#f59e0b" disabled={!!wallet.connecting}>
            {wallet.connecting === 'browser' ? 'CONNECTING…' : '▤ CONNECT METAMASK'}
          </Btn>
          <Btn onClick={() => wallet.connect('local')} size="sm" active={false} disabled={!!wallet.connecting}>
            {wallet.connecting === 'local' ? 'SIGNING IN…' : 'USE LOCAL KEY'}
          </Btn>
          <span style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>
            {wallet.injected
              ? 'sign in to compile → test → deploy'
              : 'no injected wallet detected — the local key works on its own'}
          </span>
        </>
      ) : (
        <>
          <span style={{
            fontFamily: TERM_FONT, fontSize: '11px', padding: '3px 8px',
            border: `1px solid ${wallet.kind === 'local' ? ACCENT : '#f59e0b'}`,
            color: wallet.kind === 'local' ? ACCENT : '#f59e0b',
            letterSpacing: '0.1em',
          }}>
            {wallet.kind === 'local' ? (localKeyIsAccount() ? 'LOCAL / ACCOUNT' : 'LOCAL / BUILDER') : 'METAMASK'}
          </span>

          <button
            onClick={() => copy(wallet.address, 'Address')}
            title={wallet.address}
            style={{
              fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-primary)',
              background: 'none', border: 'none', cursor: 'pointer', padding: 0,
            }}
          >
            {short(wallet.address, 8, 6)}
          </button>

          {explorerUrl(network, wallet.address) && (
            <a
              href={explorerUrl(network, wallet.address)}
              target="_blank"
              rel="noreferrer"
              style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)', textDecoration: 'none' }}
            >
              {'↗'}
            </a>
          )}

          {/* the picker shows the target chain, so say which one the wallet is
              actually on — otherwise "Base Sepolia #84532" next to a red SWITCH
              button reads as a contradiction. */}
          {wrongChain && (
            <Btn size="sm" color="#ef4444"
              onClick={() => wallet.switchChain().catch(e => toast.error(e?.message || 'switch failed'))}>
              ON {chainName(wallet.injectedChainId!).toUpperCase()} → SWITCH TO {net.name.toUpperCase()}
            </Btn>
          )}

          {wallet.kind === 'local' && !localKeyIsAccount() && (
            <Btn size="sm" active={false} onClick={() => {
              if (showKey) { copy(localPrivateKey(), 'Private key'); setShowKey(false) }
              else setShowKey(true)
            }}>
              {showKey ? 'CONFIRM: COPY KEY' : 'EXPORT KEY'}
            </Btn>
          )}

          <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px' }}>
            {wallet.kind === 'local' && wallet.injected && (
              <Btn size="sm" active={false} color="#f59e0b" onClick={connectBrowser}>
                USE METAMASK
              </Btn>
            )}
            {wallet.kind === 'browser' && (
              <Btn size="sm" active={false} onClick={() => wallet.connect('local')}>
                USE LOCAL
              </Btn>
            )}
            <Btn size="sm" active={false} onClick={wallet.disconnect}>SIGN OUT</Btn>
          </div>
        </>
      )}
    </div>
  )
}
