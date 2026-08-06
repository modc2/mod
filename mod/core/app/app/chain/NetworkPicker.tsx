"use client"

// Network picker — every chain the console knows, plus any RPC you add yourself.
// Fleet networks (the ones config.json has deployments for) are marked; the rest
// are yours to build and deploy on.

import { useState, useEffect, useRef } from 'react'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, NETWORKS, netInfo, allNetworks,
  saveCustomNetwork, removeCustomNetwork, type NetworkInfo,
} from './shared'
import { Btn, Input, panelStyle } from './ui'

const groupOf = (n: NetworkInfo) =>
  n.custom ? 'CUSTOM' : n.fleet ? 'FLEET' : n.testnet ? 'TESTNETS' : 'MAINNETS'

const GROUPS = ['FLEET', 'TESTNETS', 'MAINNETS', 'CUSTOM']

export function NetworkPicker({
  network, setNetwork,
}: {
  network: string
  setNetwork: (key: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [nets, setNets] = useState<Record<string, NetworkInfo>>(NETWORKS)
  const [form, setForm] = useState({ name: '', rpc: '', chainId: '', explorer: '', currency: 'ETH' })
  const box = useRef<HTMLDivElement>(null)

  // custom networks live in localStorage — only readable after mount
  useEffect(() => { setNets(allNetworks()) }, [])

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) { setOpen(false); setAdding(false) }
    }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

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
    setAdding(false); setOpen(false)
    toast.success(`${form.name} added`)
  }

  const drop = (key: string) => {
    removeCustomNetwork(key)
    const next = allNetworks()
    setNets(next)
    if (network === key) setNetwork('testnet')
  }

  return (
    <div ref={box} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          fontFamily: TERM_FONT, fontSize: '12px', letterSpacing: '0.06em',
          padding: '8px 12px', minHeight: '40px', border: `2px solid ${ACCENT}`, background: `${ACCENT}14`,
          color: ACCENT, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px',
          maxWidth: '100%',
        }}
      >
        <span style={{ opacity: 0.7 }}>NET</span>
        {active.name}
        <span style={{ opacity: 0.6, fontSize: '11px' }}>#{active.chainId}</span>
        <span style={{ opacity: 0.6 }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{
          ...panelStyle, position: 'absolute', zIndex: 40, top: 'calc(100% + 4px)', left: 0,
          // never wider than the phone it's dropping down on
          width: 'min(300px, calc(100vw - 40px))', maxHeight: '440px', overflowY: 'auto',
          boxShadow: `3px 3px 0px 0px ${ACCENT}55`,
        }}>
          {GROUPS.map(group => {
            const rows = Object.values(nets).filter(n => groupOf(n) === group)
            if (!rows.length) return null
            return (
              <div key={group}>
                <div style={{
                  fontFamily: TERM_FONT, fontSize: '10px', letterSpacing: '0.14em',
                  color: 'var(--text-tertiary)', padding: '8px 10px 4px',
                }}>
                  {group}
                </div>
                {rows.map(n => (
                  <div key={n.key} style={{ display: 'flex', alignItems: 'stretch' }}>
                    <button
                      onClick={() => { setNetwork(n.key); setOpen(false) }}
                      style={{
                        flex: 1, textAlign: 'left', fontFamily: TERM_FONT, fontSize: '12px',
                        padding: '10px', minHeight: '40px', border: 'none',
                        borderLeft: `2px solid ${n.key === network ? ACCENT : 'transparent'}`,
                        background: n.key === network ? `${ACCENT}14` : 'transparent',
                        color: n.key === network ? ACCENT : 'var(--text-secondary)', cursor: 'pointer',
                      }}
                    >
                      {n.name}
                      <span style={{ color: 'var(--text-tertiary)', marginLeft: '8px', fontSize: '10px' }}>
                        #{n.chainId} · {n.currency}
                      </span>
                    </button>
                    {n.custom && (
                      <button
                        onClick={() => drop(n.key)}
                        title="remove network"
                        style={{
                          fontFamily: TERM_FONT, fontSize: '11px', padding: '0 8px', border: 'none',
                          background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer',
                        }}
                      >
                        ✕
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )
          })}

          <div style={{ borderTop: '1px solid var(--border-color)', padding: '10px' }}>
            {!adding ? (
              <Btn size="sm" active={false} onClick={() => setAdding(true)}>+ ADD NETWORK</Btn>
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
        </div>
      )}
    </div>
  )
}
