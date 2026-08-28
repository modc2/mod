"use client"

import { useState } from 'react'
import { TERM_FONT, ACCENT, netInfo, useIsMobile } from './shared'
import { Btn, Sheet, panelStyle } from './ui'
import { WalletBar, useChainWallet } from './WalletBar'
import { Balances } from './Balances'
import { Sidebar } from './Sidebar'
import { Gallery } from './Gallery'
import { useProjects } from './projects'
import { BuildTab } from './BuildTab'
import { TestTab } from './TestTab'
import { DeployTab } from './DeployTab'
import { ContractsTab } from './ContractsTab'
import { InteractTab, type InteractTarget } from './InteractTab'
import { ConfigTab } from './ConfigTab'

export const dynamic = 'force-dynamic'

type Tab = 'build' | 'test' | 'deploy' | 'contracts' | 'interact' | 'config'

interface TabDef { key: Tab; label: string; icon: string; hint: string; fleetOnly?: boolean }

// Two groups, because they answer different questions: the first three are
// about the project you have open, the last three about the fleet's own
// deployment. Mixing them in one row is what made the dimmed tabs confusing.
const MINE: TabDef[] = [
  { key: 'build', label: 'BUILD', icon: '✎', hint: 'Write it, compile it, deploy it with your wallet.' },
  { key: 'test', label: 'TEST', icon: '✓', hint: 'Run the project’s tests on an in-process EVM — no wallet, no gas.' },
  { key: 'interact', label: 'INTERACT', icon: '◉', hint: 'Call any contract: yours, the fleet’s, or one loaded by address / ABI CID.' },
]

const FLEET: TabDef[] = [
  { key: 'contracts', label: 'CONTRACTS', icon: '▤', hint: 'The fleet’s deployed contracts on this network.', fleetOnly: true },
  { key: 'deploy', label: 'DEPLOY', icon: '▶', hint: 'Re-deploy the fleet’s own contracts (owner tooling).', fleetOnly: true },
  { key: 'config', label: 'CONFIG', icon: '☰', hint: 'What config.json records for this network.', fleetOnly: true },
]

const ALL_TABS = [...MINE, ...FLEET]

export default function ChainPage() {
  const [activeTab, setActiveTab] = useState<Tab>('build')
  const [network, setNetwork] = useState('testnet')
  const [target, setTarget] = useState<InteractTarget | null>(null)
  const [railOpen, setRailOpen] = useState(false)
  const wallet = useChainWallet(network)
  const projects = useProjects(wallet.address)
  const mobile = useIsMobile()

  const net = netInfo(network)
  const fleet = !!net.fleet
  const hint = ALL_TABS.find(t => t.key === activeTab)?.hint || ''

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

  const rail = (
    <>
      <Sidebar projects={projects} address={wallet.address} onNavigate={() => setRailOpen(false)} />
      <Gallery projects={projects} address={wallet.address} onNavigate={() => setRailOpen(false)} />
    </>
  )

  const tabButton = (tab: TabDef) => {
    const active = activeTab === tab.key
    const dim = tab.fleetOnly && !fleet
    return (
      <button
        key={tab.key}
        onClick={() => setActiveTab(tab.key)}
        className="transition-all"
        title={dim ? `${net.name} has no fleet deployment` : tab.hint}
        style={{
          fontFamily: TERM_FONT,
          fontSize: '13px',
          letterSpacing: '0.1em',
          padding: mobile ? '10px 12px' : '8px 16px',
          minHeight: '44px',
          flexShrink: 0,
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
  }

  // width:0 + min-width:100% keeps this page from widening the app shell: the
  // shell's columns size to their content, so without it one wide row (the tab
  // strip on a phone) drags the whole page past the viewport.
  return (
    <div className="min-h-screen" style={{
      fontFamily: TERM_FONT, backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)',
      width: 0, minWidth: '100%',
    }}>
      <div className="max-w-7xl mx-auto" style={{ padding: mobile ? '16px 12px 40px' : '32px 16px' }}>

        {/* Header */}
        <div className="mb-4">
          <div className="flex items-center gap-3 mb-1" style={{ flexWrap: 'wrap' }}>
            <span style={{ color: ACCENT, fontSize: '20px' }}>$</span>
            <span style={{
              fontSize: mobile ? '19px' : '22px', letterSpacing: '0.08em', color: ACCENT,
              textShadow: `0 0 12px ${ACCENT}`,
            }}>
              chain
            </span>
            {!mobile && (
              <span style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>
                --build --test --deploy --interact
              </span>
            )}
          </div>
          <p style={{ color: 'var(--text-tertiary)', fontSize: mobile ? '12px' : '14px' }}>
            Write contracts and their tests, run the suite, deploy with your own wallet
          </p>
          <div className="mt-3" style={{ height: '2px', background: ACCENT, opacity: 0.3 }} />
        </div>

        {/* Wallet + network */}
        <WalletBar wallet={wallet} network={network} setNetwork={setNetwork} />

        {/* Balances */}
        <Balances wallet={wallet} network={network} />

        {/* On a phone the rail is a drawer — one button, always showing what's open */}
        {mobile && (
          <button
            onClick={() => setRailOpen(true)}
            style={{
              ...panelStyle, width: '100%', marginBottom: '12px', padding: '12px 14px',
              display: 'flex', alignItems: 'center', gap: '10px', minHeight: '48px',
              fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-primary)', cursor: 'pointer',
            }}
          >
            <span style={{ color: ACCENT }}>☰</span>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {projects.project ? projects.project.name : 'PROJECTS'}
            </span>
            <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-tertiary)' }}>
              {projects.project
                ? (projects.saving ? 'saving…' : projects.dirty ? 'unsaved' : 'saved')
                : 'pick or start one'}
            </span>
          </button>
        )}

        <Sheet open={mobile && railOpen} onClose={() => setRailOpen(false)} title="PROJECTS">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>{rail}</div>
        </Sheet>

        <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
          {/* Left rail — your projects on top, the shared gallery under them */}
          {!mobile && (
            <div style={{
              width: '232px', flexShrink: 0, alignSelf: 'flex-start',
              position: 'sticky', top: '16px', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto',
              display: 'flex', flexDirection: 'column', gap: '12px',
            }}>
              {rail}
            </div>
          )}

          <div style={{ flex: 1, minWidth: 0 }}>
            {/* Tabs — your project first, the fleet's own deployment after it */}
            <div style={{
              display: 'flex', gap: mobile ? '4px' : '8px', alignItems: 'center',
              borderBottom: '2px solid var(--border-color)', paddingBottom: '8px',
              overflowX: mobile ? 'auto' : 'visible', flexWrap: mobile ? 'nowrap' : 'wrap',
            }}>
              {MINE.map(tabButton)}
              <span style={{
                flexShrink: 0, width: '1px', alignSelf: 'stretch', margin: '0 4px',
                background: 'var(--border-color)',
              }} />
              <span style={{
                flexShrink: 0, fontSize: '10px', letterSpacing: '0.14em',
                color: 'var(--text-tertiary)', opacity: 0.7,
              }}>
                FLEET
              </span>
              {FLEET.map(tabButton)}
            </div>

            <p style={{
              fontFamily: TERM_FONT, fontSize: '11.5px', color: 'var(--text-tertiary)',
              margin: '10px 0 20px', lineHeight: 1.5,
            }}>
              {hint}
            </p>

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
