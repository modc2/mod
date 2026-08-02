"use client"

// CONTRACTS — everything live on this network: the deployed fleet plus the
// contracts you built here.

import { useState, useEffect } from 'react'
import { TERM_FONT, chainApi, short, explorerUrl } from './shared'
import { Label, Btn, Empty, panelStyle } from './ui'
import type { ChainWallet } from './WalletBar'

interface Row { name: string; kind: string; address: string }

export function ContractsTab({ wallet, network }: { wallet: ChainWallet; network: string }) {
  const [fleet, setFleet] = useState<Row[]>([])
  const [mine, setMine] = useState<Row[]>([])
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const q = wallet.address ? `?address=${wallet.address}&` : '?'
    Promise.all([
      chainApi('/contracts', { body: { network } }).catch(() => ({ contracts: {} })),
      chainApi(`/build/deployments${q}network=${network}`).catch(() => ({ deployments: [] })),
    ]).then(([c, b]) => {
      if (cancelled) return
      setFleet(Object.entries(c.contracts || {}).map(([name, info]: [string, any]) => ({
        name, kind: info?.contract || '', address: info?.address || '',
      })).filter(r => r.address))
      setMine((b.deployments || []).map((d: any) => ({
        name: d.name, kind: 'built here', address: d.address,
      })))
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [network, wallet.address])

  const copy = (address: string, name: string) => {
    navigator.clipboard.writeText(address)
    setCopied(name)
    setTimeout(() => setCopied(null), 2000)
  }

  const List = ({ rows }: { rows: Row[] }) => (
    <>
      {rows.map(r => (
        <div key={`${r.name}-${r.address}`} style={{
          ...panelStyle, padding: '12px 16px', marginBottom: '8px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap',
        }}>
          <div>
            <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-primary)', fontWeight: 'bold' }}>
              {r.name}
            </div>
            <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
              {r.kind}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-secondary)' }}>
              {short(r.address, 6, 4)}
            </span>
            <Btn size="sm" active={copied === r.name} onClick={() => copy(r.address, r.name)}>
              {copied === r.name ? 'copied' : 'copy'}
            </Btn>
            {explorerUrl(network, r.address) && (
              <a href={explorerUrl(network, r.address)} target="_blank" rel="noreferrer"
                style={{
                  fontFamily: TERM_FONT, fontSize: '11px', padding: '4px 8px',
                  border: '1px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
                }}>↗</a>
            )}
          </div>
        </div>
      ))}
    </>
  )

  if (loading) return <Empty>Loading contracts…</Empty>

  return (
    <div>
      {mine.length > 0 && (
        <div style={{ marginBottom: '24px' }}>
          <Label>MY BUILDS</Label>
          <List rows={mine} />
        </div>
      )}
      <Label>FLEET — {network}</Label>
      {fleet.length === 0 ? <Empty>No contracts deployed on {network}.</Empty> : <List rows={fleet} />}
    </div>
  )
}
