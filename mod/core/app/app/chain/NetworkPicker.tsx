"use client"

// NETWORK — every chain the console knows, plus any RPC you add yourself.
// Fleet networks (the ones config.json has deployments for) are marked; the
// rest are yours to build and deploy on. The lamp is the chain's pulse.

import { useState, useEffect, useCallback } from 'react'
import { toast } from 'react-toastify'
import {
  ACCENT, NETWORKS, netInfo, allNetworks, isBuiltinNetwork, isOverridden,
  saveCustomNetwork, removeCustomNetwork, type NetworkInfo,
} from './shared'
import { Btn, Input, Pill, Dropdown, DropHead, DropRow, DropRule, Quiet } from './ui'
import { PIXEL, NEON, Led, type LedState } from './arcade'

// An override of a builtin keeps that builtin's group — it's still Base
// Sepolia, it just dials a different RPC.
const groupOf = (n: NetworkInfo) =>
  n.custom && !isBuiltinNetwork(n.key) ? 'CUSTOM'
    : n.fleet ? 'FLEET' : n.testnet ? 'TESTNETS' : 'MAINNETS'

const GROUPS = ['FLEET', 'TESTNETS', 'MAINNETS', 'CUSTOM']

export function NetworkPicker({
  network, setNetwork, led = 'idle', block, onManage,
}: {
  network: string
  setNetwork: (key: string) => void
  led?: LedState
  block?: number | null
  /** jump to the CHAINS tab for the full table */
  onManage?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [nets, setNets] = useState<Record<string, NetworkInfo>>(NETWORKS)
  const [form, setForm] = useState({ name: '', rpc: '', chainId: '', explorer: '', currency: 'ETH' })
  const close = useCallback(() => { setOpen(false); setAdding(false) }, [])

  // custom networks live in localStorage — only readable after mount
  useEffect(() => { setNets(allNetworks()) }, [])
  useEffect(() => { if (open) setNets(allNetworks()) }, [open])

  const active = netInfo(network)

  const addNetwork = () => {
    const chainId = Number(form.chainId)
    if (!form.name.trim() || !form.rpc.trim() || !chainId) {
      toast.error('name, RPC URL and chain id are all required')
      return
    }
    const key = form.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_')
    saveCustomNetwork({
      key, name: form.name.trim(), chainId, rpc: form.rpc.trim(),
      explorer: form.explorer.trim(), currency: form.currency.trim() || 'ETH', custom: true,
    })
    setNets(allNetworks())
    setNetwork(key)
    setForm({ name: '', rpc: '', chainId: '', explorer: '', currency: 'ETH' })
    close()
    toast.success(`${form.name} added`)
  }

  const drop = (key: string) => {
    // Dropping an override of a builtin puts the shipped RPC back — the chain
    // itself is still there, so don't move the console off it.
    const wasOverride = isBuiltinNetwork(key)
    removeCustomNetwork(key)
    setNets(allNetworks())
    if (!wasOverride && network === key) setNetwork('testnet')
  }

  const trigger = (
    <Pill
      label="NET"
      color={ACCENT}
      open={open}
      onClick={() => (open ? close() : setOpen(true))}
      led={<Led state={led} />}
      title={led === 'dead' ? `${active.rpc} is not answering`
        : led === 'warn' ? 'the RPC reports a different chain id than recorded'
          : active.rpc}
    >
      <span>{active.name}</span>
      <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
        #{active.chainId}{block ? ` · ${block.toLocaleString()}` : ''}
      </span>
      {isOverridden(active.key) && (
        <span style={{ fontFamily: PIXEL, fontSize: '7px', color: NEON.coin }}>TUNED</span>
      )}
    </Pill>
  )

  return (
    <Dropdown open={open} onClose={close} trigger={trigger} width={340}>
      {GROUPS.map(group => {
        const rows = Object.values(nets).filter(n => groupOf(n) === group)
        if (!rows.length) return null
        return (
          <div key={group}>
            <DropHead>{group}</DropHead>
            {rows.map(n => (
              <DropRow
                key={n.key}
                active={n.key === network}
                onClick={() => { setNetwork(n.key); close() }}
                title={n.rpc}
                right={n.custom && (
                  <Quiet onClick={() => drop(n.key)}
                    title={isBuiltinNetwork(n.key) ? 'reset to the built-in RPC' : 'remove network'}>
                    ✕
                  </Quiet>
                )}
              >
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {n.name}
                  {isOverridden(n.key) && (
                    <span style={{ fontFamily: PIXEL, fontSize: '7px', color: NEON.coin, marginLeft: '8px' }}>TUNED</span>
                  )}
                </span>
                <span style={{ fontSize: '13px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
                  #{n.chainId} · {n.currency}
                </span>
              </DropRow>
            ))}
          </div>
        )
      })}

      <DropRule />
      <div style={{ padding: '4px 12px 12px' }}>
        {!adding ? (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            <Btn size="sm" active={false} onClick={() => setAdding(true)}>+ ADD NETWORK</Btn>
            {onManage && (
              <Btn size="sm" active={false} onClick={() => { onManage(); close() }}>ALL CHAINS →</Btn>
            )}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <Input value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} placeholder="name" />
            <Input value={form.rpc} onChange={v => setForm(f => ({ ...f, rpc: v }))} placeholder="https://rpc…" />
            <Input value={form.chainId} onChange={v => setForm(f => ({ ...f, chainId: v }))} placeholder="chain id" />
            <Input value={form.currency} onChange={v => setForm(f => ({ ...f, currency: v }))} placeholder="ETH" />
            <Input value={form.explorer} onChange={v => setForm(f => ({ ...f, explorer: v }))} placeholder="explorer url (optional)" />
            <div style={{ display: 'flex', gap: '6px' }}>
              <Btn size="sm" onClick={addNetwork}>SAVE</Btn>
              <Btn size="sm" active={false} onClick={() => setAdding(false)}>CANCEL</Btn>
            </div>
          </div>
        )}
      </div>
    </Dropdown>
  )
}
