"use client"

// CHAINS — the machine's chain select. Every network the console can talk to,
// what it's actually doing right now, and the controls to add, retune or drop
// one. Anything you change here is kept in this browser.

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, allNetworks, netInfo, isBuiltinNetwork, isOverridden,
  saveCustomNetwork, removeCustomNetwork, probeNetwork, short, useIsMobile,
  type NetworkInfo, type NetProbe,
} from './shared'
import { Btn, Input, Label, Empty, panelStyle } from './ui'
import { PIXEL, PX, NEON, Led, type LedState } from './arcade'

type Filter = 'ALL' | 'FLEET' | 'TESTNETS' | 'MAINNETS' | 'CUSTOM'
const FILTERS: Filter[] = ['ALL', 'FLEET', 'TESTNETS', 'MAINNETS', 'CUSTOM']

const matches = (n: NetworkInfo, f: Filter) =>
  f === 'ALL' ? true
    : f === 'FLEET' ? !!n.fleet
      : f === 'TESTNETS' ? !!n.testnet
        : f === 'CUSTOM' ? (!!n.custom || isOverridden(n.key))
          : !n.testnet

type Form = { key: string; name: string; rpc: string; chainId: string; explorer: string; currency: string }
const BLANK: Form = { key: '', name: '', rpc: '', chainId: '', explorer: '', currency: 'ETH' }

export function ChainsTab({
  network, setNetwork, address,
}: {
  network: string
  setNetwork: (key: string) => void
  address: string
}) {
  const [nets, setNets] = useState<Record<string, NetworkInfo>>({})
  const [probes, setProbes] = useState<Record<string, NetProbe | 'scanning'>>({})
  const [balances, setBalances] = useState<Record<string, string>>({})
  const [filter, setFilter] = useState<Filter>('ALL')
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState<Form>(BLANK)
  const mobile = useIsMobile()

  // custom networks live in localStorage — only readable after mount
  const reload = useCallback(() => setNets(allNetworks()), [])
  useEffect(() => { reload() }, [reload])

  const scan = useCallback(async (keys: string[]) => {
    setProbes(prev => ({ ...prev, ...Object.fromEntries(keys.map(k => [k, 'scanning' as const])) }))
    await Promise.all(keys.map(async key => {
      const probe = await probeNetwork(key)
      setProbes(prev => ({ ...prev, [key]: probe }))
      if (!probe.up || !address) return
      try {
        const bal = await new ethers.JsonRpcProvider(netInfo(key).rpc, undefined, { staticNetwork: true })
          .getBalance(address)
        setBalances(prev => ({ ...prev, [key]: ethers.formatEther(bal) }))
      } catch { /* a chain that won't answer for a balance still counts as up */ }
    }))
  }, [address])

  // Scan on arrival: a list of chains with no state is just the config file.
  useEffect(() => {
    const keys = Object.keys(allNetworks())
    if (keys.length) scan(keys)
  }, [scan])

  const startAdd = () => { setForm(BLANK); setEditing('__new__') }

  const startEdit = (n: NetworkInfo) => {
    setForm({
      key: n.key, name: n.name, rpc: n.rpc, chainId: String(n.chainId),
      explorer: n.explorer, currency: n.currency,
    })
    setEditing(n.key)
  }

  const save = () => {
    const chainId = Number(form.chainId)
    if (!form.name.trim() || !form.rpc.trim() || !chainId) {
      toast.error('name, RPC url and chain id are all required')
      return
    }
    // Editing keeps the key so an override lands on top of its builtin; a new
    // chain gets one derived from its name — and never one already taken, or
    // adding a chain called "Base" would quietly rewrite the fleet's mainnet.
    let key = editing || ''
    if (!editing || editing === '__new__') {
      const base = form.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_')
      key = base
      for (let n = 2; key in nets; n++) key = `${base}_${n}`
    }
    saveCustomNetwork({
      key, name: form.name.trim(), chainId, rpc: form.rpc.trim(),
      explorer: form.explorer.trim(), currency: form.currency.trim() || 'ETH',
      custom: true,
    })
    setEditing(null)
    reload()
    scan([key])
    toast.success(`${form.name} saved`)
  }

  const drop = (n: NetworkInfo) => {
    removeCustomNetwork(n.key)
    reload()
    if (isBuiltinNetwork(n.key)) {
      scan([n.key])
      toast.success(`${n.name} reset to the built-in RPC`)
    } else {
      if (network === n.key) setNetwork('testnet')
      toast.success(`${n.name} removed`)
    }
  }

  const rows = Object.values(nets).filter(n => matches(n, filter))

  const editor = (
    <div style={{ ...panelStyle, padding: '14px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <Label style={{ color: ACCENT }}>
        {editing === '__new__' ? 'NEW CHAIN' : `EDIT ${form.name.toUpperCase()}`}
      </Label>
      <Input value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} placeholder="name" />
      <Input value={form.rpc} onChange={v => setForm(f => ({ ...f, rpc: v }))} placeholder="https://rpc…" />
      <div style={{ display: 'flex', gap: '8px' }}>
        <Input value={form.chainId} onChange={v => setForm(f => ({ ...f, chainId: v }))} placeholder="chain id" />
        <Input value={form.currency} onChange={v => setForm(f => ({ ...f, currency: v }))} placeholder="ETH" />
      </div>
      <Input value={form.explorer} onChange={v => setForm(f => ({ ...f, explorer: v }))}
        placeholder="explorer url (optional)" />
      <div style={{ display: 'flex', gap: '8px' }}>
        <Btn size="sm" onClick={save}>SAVE</Btn>
        <Btn size="sm" active={false} onClick={() => setEditing(null)}>CANCEL</Btn>
      </div>
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>

      <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
        {FILTERS.map(f => (
          <Btn key={f} size="sm" active={filter === f} onClick={() => setFilter(f)}>{f}</Btn>
        ))}
        <div style={{ marginLeft: mobile ? 0 : 'auto', display: 'flex', gap: '6px' }}>
          <Btn size="sm" active={false} onClick={() => scan(Object.keys(nets))}>RESCAN</Btn>
          <Btn size="sm" color={NEON.coin} onClick={startAdd}>+ ADD CHAIN</Btn>
        </div>
      </div>

      {editing === '__new__' && editor}

      {rows.length === 0 && <Empty>No chains match {filter}.</Empty>}

      <div style={{
        display: 'grid', gap: '10px',
        gridTemplateColumns: mobile ? '1fr' : 'repeat(auto-fill, minmax(320px, 1fr))',
      }}>
        {rows.map(n => {
          const probe = probes[n.key]
          const scanning = probe === 'scanning'
          const p = scanning ? undefined : probe as NetProbe | undefined
          // A chain id the RPC disagrees with is the one failure that silently
          // sends a deploy to the wrong place — it outranks "is it up".
          const mismatch = !!p?.up && p.chainId !== undefined && p.chainId !== n.chainId
          const state: LedState = scanning ? 'idle'
            : !p ? 'idle' : mismatch ? 'warn' : p.up ? 'live' : 'dead'
          const active = n.key === network
          const bal = balances[n.key]

          return (
            <div key={n.key} style={{
              ...panelStyle, padding: '12px 14px',
              borderColor: active ? ACCENT : mismatch ? NEON.coin : 'var(--border-color)',
              boxShadow: active ? `3px 3px 0 0 ${ACCENT}` : panelStyle.boxShadow,
              display: 'flex', flexDirection: 'column', gap: '10px',
            }}>
              {/* name + status lamp */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Led state={state} />
                <span style={{
                  fontFamily: PIXEL, fontSize: PX.md, letterSpacing: '0.06em',
                  color: active ? ACCENT : 'var(--text-primary)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {n.name}
                </span>
                <span style={{ marginLeft: 'auto', display: 'flex', gap: '5px' }}>
                  {n.fleet && (
                    <span style={{
                      fontFamily: PIXEL, fontSize: PX.xs, color: NEON.p2,
                      border: `2px solid ${NEON.p2}`, padding: '2px 4px',
                    }}>FLEET</span>
                  )}
                  {!n.testnet && (
                    <span style={{
                      fontFamily: PIXEL, fontSize: PX.xs, color: NEON.p1,
                      border: `2px solid ${NEON.p1}`, padding: '2px 4px',
                    }}>REAL$</span>
                  )}
                  {isOverridden(n.key) && (
                    <span style={{
                      fontFamily: PIXEL, fontSize: PX.xs, color: NEON.coin,
                      border: `2px solid ${NEON.coin}`, padding: '2px 4px',
                    }}>TUNED</span>
                  )}
                </span>
              </div>

              {/* the readout: what the RPC just told us */}
              <div style={{
                fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-secondary)',
                display: 'flex', gap: '14px', flexWrap: 'wrap',
              }}>
                <span>#{n.chainId}</span>
                <span>{n.currency}</span>
                <span
                  style={{ color: state === 'dead' ? NEON.dead : 'var(--text-secondary)' }}
                  title={!scanning && p && !p.up ? p.detail : undefined}
                >
                  {scanning ? 'scanning…'
                    : !p ? '—'
                      : p.up ? `block ${p.block?.toLocaleString()} · ${p.ms}ms`
                        : p.error}
                </span>
                {bal !== undefined && (
                  <span style={{ color: NEON.coin }}>
                    {Number(bal).toFixed(4)} {n.currency}
                  </span>
                )}
              </div>

              {mismatch && (
                <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: NEON.coin }}>
                  this RPC reports chain #{p!.chainId} — deploys here would land on the wrong chain
                </div>
              )}

              <div style={{
                fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }} title={n.rpc}>
                {n.rpc || 'no RPC url'}
              </div>

              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                <Btn size="sm" active={active} onClick={() => setNetwork(n.key)}>
                  {active ? '● SELECTED' : 'SELECT'}
                </Btn>
                <Btn size="sm" active={false} onClick={() => startEdit(n)}>EDIT RPC</Btn>
                <Btn size="sm" active={false} onClick={() => scan([n.key])}>PING</Btn>
                {(n.custom || isOverridden(n.key)) && (
                  <Btn size="sm" active={false} color={NEON.dead} onClick={() => drop(n)}>
                    {isBuiltinNetwork(n.key) ? 'RESET' : 'REMOVE'}
                  </Btn>
                )}
                {n.explorer && (
                  <a href={n.explorer} target="_blank" rel="noreferrer" style={{
                    fontFamily: PIXEL, fontSize: PX.xs, padding: '7px 10px', lineHeight: 1.6,
                    border: '2px solid var(--border-color)', color: 'var(--text-tertiary)',
                    textDecoration: 'none',
                  }}>
                    EXPLORER
                  </a>
                )}
              </div>

              {editing === n.key && editor}
            </div>
          )
        })}
      </div>

      <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
        Chains you add or retune are kept in this browser. Editing a built-in chain
        overrides its RPC — RESET puts the shipped one back.{' '}
        {address ? `Balances are for ${short(address, 6, 4)}.` : 'Sign in to see your balance on each chain.'}
      </div>
    </div>
  )
}
