"use client"

import { useState } from 'react'
import { TERM_FONT, ACCENT, netInfo } from './shared'
import { Btn, panelStyle } from './ui'
import { WalletBar, useChainWallet } from './WalletBar'
import { Balances } from './Balances'
import { Sidebar } from './Sidebar'
import { useProjects } from './projects'
import { BuildTab } from './BuildTab'
import { TestTab } from './TestTab'
import { DeployTab } from './DeployTab'
import { ContractsTab } from './ContractsTab'
import { InteractTab, type InteractTarget } from './InteractTab'
import { ConfigTab } from './ConfigTab'

export const dynamic = 'force-dynamic'

type Tab = 'build' | 'test' | 'deploy' | 'contracts' | 'interact' | 'config'

const TABS: { key: Tab; label: string; icon: string; fleetOnly?: boolean }[] = [
  { key: 'build', label: 'BUILD', icon: '✎' },
  { key: 'test', label: 'TEST', icon: '✓' },
  { key: 'interact', label: 'INTERACT', icon: '◉' },
  { key: 'contracts', label: 'CONTRACTS', icon: '▤', fleetOnly: true },
  { key: 'deploy', label: 'DEPLOY', icon: '▶', fleetOnly: true },
  { key: 'config', label: 'CONFIG', icon: '☰', fleetOnly: true },
]

export default function ChainPage() {
  const [activeTab, setActiveTab] = useState<Tab>('build')
  const [network, setNetwork] = useState('testnet')
  const [target, setTarget] = useState<InteractTarget | null>(null)
  const wallet = useChainWallet(network)
  const projects = useProjects(wallet.address)

  const net = netInfo(network)
  const fleet = !!net.fleet

  const handoff = (t: InteractTarget) => {
    setTarget(t)
    setActiveTab('interact')
  }

  const offFleet = (
    <div style={{ ...panelStyle, padding: '16px', fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
      This panel describes the fleet&apos;s own deployment, and {net.name} has none.
      Switch to a fleet network to use it — BUILD, TEST and INTERACT work here as normal.
    </div>
  )

  return (
    <div className="min-h-screen" style={{ fontFamily: TERM_FONT, backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)' }}>
      <div className="max-w-7xl mx-auto px-4 py-8">

        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-3 mb-2">
            <span style={{ color: ACCENT, fontSize: '20px' }}>$</span>
            <span style={{
              fontSize: '22px', letterSpacing: '0.08em', color: ACCENT,
              textShadow: `0 0 12px ${ACCENT}`,
            }}>
              chain
            </span>
            <span style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>
              --build --test --deploy --interact
            </span>
          </div>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>
            Write contracts and their tests, run the suite, deploy with your own wallet
          </p>
          <div className="mt-3" style={{ height: '2px', background: ACCENT, opacity: 0.3 }} />
        </div>

        {/* Wallet + network */}
        <WalletBar wallet={wallet} network={network} setNetwork={setNetwork} />

        {/* Balances */}
        <Balances wallet={wallet} network={network} />

        <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
          <Sidebar projects={projects} address={wallet.address} />

          <div style={{ flex: 1, minWidth: 0 }}>
            {/* Tabs */}
            <div className="flex gap-2 mb-8" style={{ borderBottom: '2px solid var(--border-color)', paddingBottom: '8px', flexWrap: 'wrap' }}>
              {TABS.map(tab => {
                const active = activeTab === tab.key
                const dim = tab.fleetOnly && !fleet
                return (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className="transition-all"
                    title={dim ? `${net.name} has no fleet deployment` : undefined}
                    style={{
                      fontFamily: TERM_FONT,
                      fontSize: '13px',
                      letterSpacing: '0.1em',
                      padding: '8px 16px',
                      border: active ? `2px solid ${ACCENT}` : '2px solid transparent',
                      background: active ? `${ACCENT}14` : 'transparent',
                      color: active ? ACCENT : 'var(--text-tertiary)',
                      boxShadow: active ? `2px 2px 0px 0px ${ACCENT}` : 'none',
                      cursor: 'pointer',
                      opacity: dim ? 0.45 : 1,
                      textShadow: active ? `0 0 8px ${ACCENT}` : 'none',
                    }}
                  >
                    <span style={{ marginRight: '6px' }}>{tab.icon}</span>
                    {tab.label}
                  </button>
                )
              })}
            </div>

            {/* Panels */}
            {activeTab === 'build' && (
              <BuildTab wallet={wallet} network={network} projects={projects} onInteract={handoff} />
            )}
            {activeTab === 'test' && <TestTab projects={projects} address={wallet.address} />}
            {activeTab === 'interact' && (
              <InteractTab wallet={wallet} network={network} target={target} setTarget={setTarget} />
            )}
            {activeTab === 'contracts' && (fleet ? <ContractsTab wallet={wallet} network={network} /> : offFleet)}
            {activeTab === 'deploy' && (fleet ? <DeployTab network={network} /> : offFleet)}
            {activeTab === 'config' && (fleet ? <ConfigTab network={network} /> : offFleet)}
          </div>
        </div>
      </div>
    </div>
  )
}
