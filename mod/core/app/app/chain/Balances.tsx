"use client"

// Balances — the native coin always, your ERC-20s on a toggle. Token balances
// are read straight from the network RPC, so they work on any chain the picker
// offers. The list is the fleet's tokens + anything you deployed here + any
// address you track yourself.

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, READ, chainApi, netInfo, readProvider, readToken, isErc20Abi,
  trackedTokens, trackToken, untrackToken, explorerUrl, type TokenBalance,
} from './shared'
import { Btn, Input, panelStyle } from './ui'
import type { ChainWallet } from './WalletBar'

const LS_OPEN = 'chain_balances_open'

const pretty = (v: string) => {
  const n = Number(v)
  if (!n) return '0'
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

export function Balances({ wallet, network }: { wallet: ChainWallet; network: string }) {
  const [open, setOpen] = useState(false)
  const [tokens, setTokens] = useState<TokenBalance[]>([])
  const [loading, setLoading] = useState(false)
  const [hideZero, setHideZero] = useState(true)
  const [adding, setAdding] = useState('')
  const net = netInfo(network)

  useEffect(() => { try { setOpen(localStorage.getItem(LS_OPEN) === '1') } catch {} }, [])

  const toggle = () => setOpen(o => {
    try { localStorage.setItem(LS_OPEN, o ? '0' : '1') } catch {}
    return !o
  })

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

  return (
    <div style={{ ...panelStyle, marginBottom: '16px' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: '14px', padding: '10px 16px', flexWrap: 'wrap',
      }}>
        <span style={{ fontFamily: TERM_FONT, fontSize: '11px', letterSpacing: '0.1em', color: 'var(--text-tertiary)' }}>
          BALANCE
        </span>

        <span style={{ fontFamily: TERM_FONT, fontSize: '15px', color: 'var(--text-primary)' }}>
          {wallet.balance === null ? '—' : pretty(wallet.balance)}
          <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginLeft: '5px' }}>
            {net.currency}
          </span>
        </span>

        <Btn size="sm" active={open} onClick={toggle}>
          {open ? '▾' : '▸'} TOKENS{tokens.length ? ` (${shown.length})` : ''}
        </Btn>

        <Btn size="sm" active={false} onClick={() => { wallet.refresh(); load() }} title="refresh balances">
          ↻
        </Btn>

        {!wallet.address && (
          <span style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>
            connect a wallet to see balances
          </span>
        )}
      </div>

      {open && (
        <div style={{ borderTop: '1px solid var(--border-color)', padding: '10px 16px' }}>
          {loading && tokens.length === 0 ? (
            <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>reading tokens…</div>
          ) : shown.length === 0 ? (
            <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>
              {tokens.length ? 'no non-zero token balances' : 'no tokens found on this network'}
            </div>
          ) : shown.map(t => (
            <div key={t.address} style={{
              display: 'flex', alignItems: 'center', gap: '10px', padding: '4px 0',
              fontFamily: TERM_FONT, fontSize: '12px',
            }}>
              <span style={{ color: ACCENT, minWidth: '70px' }}>{t.symbol}</span>
              <span style={{ color: 'var(--text-primary)', minWidth: '140px' }}>{pretty(t.balance)}</span>
              <span style={{ color: 'var(--text-tertiary)', fontSize: '10px' }}>{t.source}</span>
              {explorerUrl(network, t.address) && (
                <a href={explorerUrl(network, t.address)} target="_blank" rel="noreferrer"
                  style={{ color: READ, fontSize: '10px', textDecoration: 'none' }}>
                  {t.address.slice(0, 8)}…↗
                </a>
              )}
              {t.source === 'tracked' && (
                <button
                  onClick={() => { untrackToken(network, t.address); load() }}
                  title="stop tracking"
                  style={{
                    border: 'none', background: 'transparent', color: 'var(--text-tertiary)',
                    cursor: 'pointer', fontFamily: TERM_FONT, fontSize: '11px',
                  }}
                >
                  ✕
                </button>
              )}
            </div>
          ))}

          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '10px', flexWrap: 'wrap' }}>
            <div style={{ width: '340px' }}>
              <Input value={adding} onChange={setAdding} placeholder="track a token by address — 0x…" />
            </div>
            <Btn size="sm" onClick={add}>TRACK</Btn>
            <Btn size="sm" active={hideZero} onClick={() => setHideZero(z => !z)}>
              {hideZero ? 'HIDING ZEROES' : 'SHOWING ALL'}
            </Btn>
          </div>
        </div>
      )}
    </div>
  )
}
