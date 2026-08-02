"use client"

// DEPLOY — the protocol fleet, deployed by the chain module's own key.
// The picker is the dependency graph itself (DeployGraph): waves run in
// parallel internally and sequentially across, because that's what the
// constructor wiring allows. Pick a subset by clicking nodes, or ship the lot.

import { useState, useCallback, useEffect, useMemo } from 'react'
import { toast } from 'react-toastify'
import { TERM_FONT, ACCENT, WRITE, chainApi, short, explorerUrl } from './shared'
import { Label, Btn, Log } from './ui'
import { DeployGraph, FLEET, CONFIG_KEY, ancestorsOf } from './DeployGraph'

export function DeployTab({ network }: { network: string }) {
  const [deploying, setDeploying] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [log, setLog] = useState<string[]>([])
  const [result, setResult] = useState<Record<string, any> | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [live, setLive] = useState<Record<string, string>>({})

  // What's already on this network — the graph paints those nodes live, and
  // the missing-dep check below trusts them as satisfied inputs.
  useEffect(() => {
    let cancelled = false
    chainApi('/contracts', { body: { network } })
      .then(res => {
        if (cancelled) return
        const contracts = res.contracts || {}
        setLive(Object.fromEntries(FLEET
          .map(n => [n.id, contracts[CONFIG_KEY[n.id]]?.address || ''])
          .filter(([, addr]) => addr)))
      })
      .catch(() => { if (!cancelled) setLive({}) })
    return () => { cancelled = true }
  }, [network, result])

  useEffect(() => {
    if (!deploying) return
    const iv = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(iv)
  }, [deploying])

  const toggle = (mod: string) =>
    setSelected(prev => prev.includes(mod) ? prev.filter(m => m !== mod) : [...prev, mod])

  // A partial deploy only works if every constructor input already has an
  // address: either it's in this run, or it's live on the network.
  const missing = useMemo(() => {
    if (!selected.length) return []
    const need = new Set<string>()
    selected.forEach(id => ancestorsOf(id).forEach(dep => {
      if (!selected.includes(dep) && !live[dep]) need.add(dep)
    }))
    return [...need]
  }, [selected, live])

  const deploy = useCallback(async () => {
    setDeploying(true)
    setResult(null)
    setElapsed(0)
    setLog([`> deploying ${selected.length ? selected.join(', ') : 'all modules'} to ${network}…`,
            '> this runs on the chain module key and can take a few minutes'])
    try {
      const res = await chainApi('/deploy', {
        body: { network, mods: selected.length ? selected : null, confirm: network === 'mainnet' },
      })
      const deployed = res.result || {}
      setResult(deployed)
      setLog(prev => [...prev, `> ✓ complete — ${Object.keys(deployed).length} contract(s)`])
      toast.success('Deploy complete')
    } catch (e: any) {
      setLog(prev => [...prev, `> FAILED: ${e?.message || 'deploy failed'}`])
      toast.error(e?.message || 'Deploy failed')
    } finally {
      setDeploying(false)
    }
  }, [network, selected])

  return (
    <div>
      <Label>
        DEPENDENCY GRAPH — each edge is a constructor argument · click to pick, hover to trace
      </Label>
      <div style={{ marginBottom: '20px' }}>
        <DeployGraph selected={selected} onToggle={toggle} deployed={live} busy={deploying} />
      </div>

      {missing.length > 0 && (
        <div style={{
          fontFamily: TERM_FONT, fontSize: '11px', color: WRITE,
          display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px', flexWrap: 'wrap',
        }}>
          <span>needs an address it won&apos;t have: {missing.join(', ')}</span>
          <Btn size="sm" color={WRITE} onClick={() => setSelected(prev => [...new Set([...prev, ...missing])])}>
            ADD DEPS
          </Btn>
        </div>
      )}

      <div style={{ display: 'flex', gap: '12px', marginBottom: '24px', alignItems: 'center', flexWrap: 'wrap' }}>
        <Btn onClick={deploy} disabled={deploying}>
          {deploying ? `DEPLOYING… ${elapsed}s`
            : selected.length ? `DEPLOY [${selected.join(', ')}]` : 'DEPLOY ALL'}
        </Btn>
        {selected.length > 0 && (
          <Btn size="sm" active={false} onClick={() => setSelected([])}>CLEAR</Btn>
        )}
        {network === 'mainnet' && (
          <span style={{ fontFamily: TERM_FONT, fontSize: '11px', color: '#ef4444' }}>
            mainnet — this spends real gas
          </span>
        )}
      </div>

      <Log lines={log} live={deploying} />

      {result && Object.keys(result).length > 0 && (
        <div style={{ marginTop: '16px' }}>
          <Label style={{ color: ACCENT }}>DEPLOYED CONTRACTS</Label>
          {Object.entries(result).map(([name, addr]) => {
            const address = typeof addr === 'string' ? addr : (addr as any)?.address || ''
            return (
              <div key={name} style={{
                fontFamily: TERM_FONT, fontSize: '12px', padding: '4px 0',
                display: 'flex', gap: '12px', alignItems: 'center',
              }}>
                <span style={{ color: 'var(--text-primary)', minWidth: '120px' }}>{name}</span>
                <span style={{ color: 'var(--text-tertiary)' }}>
                  {address ? short(address, 10, 8) : JSON.stringify(addr)}
                </span>
                {address && explorerUrl(network, address) && (
                  <a href={explorerUrl(network, address)} target="_blank" rel="noreferrer"
                    style={{ color: 'var(--text-tertiary)', textDecoration: 'none' }}>↗</a>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
