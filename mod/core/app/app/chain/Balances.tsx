"use client"

// BALANCE — the native coin on the pill, your ERC-20s in the dropdown. Token
// balances are read straight from the network RPC, so they work on any chain
// the picker offers. The list is the fleet's tokens + anything you deployed
// here + any address you track yourself.

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, READ, chainApi, netInfo, readProvider, readToken, isErc20Abi,
  trackedTokens, trackToken, untrackToken, explorerUrl, type TokenBalance,
} from './shared'
import { Btn, Input, Pill, Hint, Dropdown, DropHead, DropRow, DropRule, Quiet } from './ui'
import { NEON } from './arcade'
import type { ChainWallet } from './WalletBar'

// VT323's lone "0" is a thin oval that reads as "()" — a zero balance says so
// with decimals, like a real readout would.
const pretty = (v: string) => {
  const n = Number(v)
  if (!n) return '0.000'
  if (n < 0.0001) return n.toExponential(2)
  return n.toLocaleString(undefined, { maximumFractionDigits: n < 1 ? 6 : 4 })
}

/** Every token address worth checking on this network, in display order. */
async function tokenAddresses(network: string, owner: string) {
  const rows: { address: string; source: TokenBalance['source'] }[] = []

  if (netInfo(network).fleet) {
    try {
      const d = await chainApi('/contracts', { body: { network } })
      for (const c of Object.values<any>(d.contracts || {})) {
        if (c?.contract === 'Token' && c.address) rows.push({ address: c.address, source: 'fleet' })
      }
    } catch { /* fleet tokens are a bonus, never a blocker */ }
  }

  if (owner) {
    try {
      const d = await chainApi(`/build/deployments?address=${owner}&network=${network}`)
      for (const b of d.deployments || []) {
        if (isErc20Abi(b.abi)) rows.push({ address: b.address, source: 'built' })
      }
    } catch { /* same */ }
  }

  for (const address of trackedTokens(network)) rows.push({ address, source: 'tracked' })

  const seen = new Set<string>()
  return rows.filter(r => {
    const key = r.address.toLowerCase()
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function Balances({ wallet, network }: {
  wallet: ChainWallet
  network: string
}) {
  const [open, setOpen] = useState(false)
  const [tokens, setTokens] = useState<TokenBalance[]>([])
  const [loading, setLoading] = useState(false)
  const [hideZero, setHideZero] = useState(true)
  const [adding, setAdding] = useState('')
  const net = netInfo(network)
  const close = useCallback(() => setOpen(false), [])

  const load = useCallback(async () => {
    if (!wallet.address || !open) return
    setLoading(true)
    try {
      const rows = await tokenAddresses(network, wallet.address)
      const provider = readProvider(network)
      const read = await Promise.all(
        rows.map(r => readToken(provider, r.address, wallet.address)
          .then(t => (t ? { ...t, source: r.source } : null))),
      )
      setTokens(read.filter(Boolean) as TokenBalance[])
    } catch {
      setTokens([])
    } finally {
      setLoading(false)
    }
  }, [wallet.address, network, open])

  useEffect(() => { load() }, [load, wallet.balance])

  const add = async () => {
    const addr = adding.trim()
    if (!ethers.isAddress(addr)) { toast.error('not an address'); return }
    const found = await readToken(readProvider(network), addr, wallet.address)
    if (!found) { toast.error('no ERC-20 at that address on this network'); return }
    trackToken(network, addr)
    setAdding('')
    toast.success(`${found.symbol} tracked`)
    load()
  }

  const shown = hideZero ? tokens.filter(t => Number(t.balance) > 0) : tokens

  // Signed out there is nothing to show — the PLAYER pill already asks.
  if (!wallet.address) return null

  const trigger = (
    <Pill
      label="BALANCE"
      color={NEON.coin}
      open={open}
      onClick={() => (open ? close() : setOpen(true))}
      tip={`${net.currency} on ${net.name} — tokens inside`}
    >
      <span style={{ color: NEON.coin }}>{wallet.balance === null ? '—' : pretty(wallet.balance)}</span>
      <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{net.currency}</span>
      {shown.length > 0 && (
        <Hint><span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>+{shown.length}</span></Hint>
      )}
    </Pill>
  )

  return (
    <Dropdown open={open} onClose={close} trigger={trigger} color={NEON.coin} width={380} grow={2}>
      <DropHead color={NEON.coin} right={
        <Quiet color={NEON.coin} onClick={() => { wallet.refresh(); load() }} title="refresh balances">↻</Quiet>
      }>
        ON {net.name.toUpperCase()}
      </DropHead>
      <DropRow active color={NEON.coin}>
        <span style={{ color: NEON.coin, minWidth: '70px' }}>{net.currency}</span>
        <span>{wallet.balance === null ? '—' : pretty(wallet.balance)}</span>
        <span style={{ marginLeft: 'auto', fontSize: '13px', color: 'var(--text-tertiary)' }}>native</span>
      </DropRow>

      {loading && tokens.length === 0 ? (
        <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)', padding: '8px 12px' }}>reading tokens…</div>
      ) : shown.length === 0 ? (
        <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)', padding: '8px 12px' }}>
          {tokens.length ? 'no non-zero token balances' : 'no tokens found on this network'}
        </div>
      ) : shown.map(t => (
        <DropRow
          key={t.address}
          title={t.address}
          right={t.source === 'tracked' && (
            <Quiet onClick={() => { untrackToken(network, t.address); load() }} title="stop tracking">✕</Quiet>
          )}
        >
          <span style={{ color: ACCENT, minWidth: '70px' }}>{t.symbol}</span>
          <span>{pretty(t.balance)}</span>
          <span style={{ marginLeft: 'auto', fontSize: '13px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
            {t.source}
            {explorerUrl(network, t.address) && (
              <a href={explorerUrl(network, t.address)} target="_blank" rel="noreferrer"
                onClick={e => e.stopPropagation()}
                style={{ color: READ, textDecoration: 'none', marginLeft: '8px' }}>
                {t.address.slice(0, 8)}…↗
              </a>
            )}
          </span>
        </DropRow>
      ))}

      <DropRule />
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '4px 12px 12px' }}>
        <Input value={adding} onChange={setAdding} placeholder="track a token by address — 0x…" onEnter={add} />
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          <Btn size="sm" onClick={add}>TRACK</Btn>
          <Btn size="sm" active={hideZero} onClick={() => setHideZero(z => !z)}>
            {hideZero ? 'HIDING ZEROES' : 'SHOWING ALL'}
          </Btn>
        </div>
      </div>
    </Dropdown>
  )
}
