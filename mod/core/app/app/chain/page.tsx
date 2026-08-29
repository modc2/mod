"use client"

import { useState, useEffect } from 'react'
import { TERM_FONT, ACCENT, netInfo, probeNetwork, chainApi, short, useIsMobile, useApiHealth } from './shared'
import { Sheet, Banner, panelStyle } from './ui'
import { ArcadeStyles, Marquee, PIXEL, PX, NEON, type LedState } from './arcade'
import { useChainWallet } from './WalletBar'
import { AccountPicker } from './AccountPicker'
import { NetworkPicker } from './NetworkPicker'
import { Balances } from './Balances'
import { Sidebar } from './Sidebar'
import { Gallery } from './Gallery'
import { useProjects } from './projects'
import { BuildTab } from './BuildTab'
import { TestTab } from './TestTab'
import { DeployTab } from './DeployTab'
import { ContractsTab } from './ContractsTab'
import { ChainsTab } from './ChainsTab'
import { InteractTab, type InteractTarget } from './InteractTab'
import { ConfigTab } from './ConfigTab'

export const dynamic = 'force-dynamic'

type Tab = 'build' | 'test' | 'deploy' | 'contracts' | 'chains' | 'interact' | 'config'

interface TabDef { key: Tab; label: string; hint: string; fleetOnly?: boolean }

// Two tiers, because they answer different questions: the big three are what
// you're doing right now; the rest is what you own and what the fleet runs.
// The second tier wraps under the first when the window is narrow — one
// clean line under another, never a row scrolled off the edge.
const BUILDING: TabDef[] = [
  { key: 'build', label: 'BUILD', hint: 'Write it, compile it, deploy it with your wallet.' },
  { key: 'test', label: 'TEST', hint: 'Run the project’s tests on an in-process EVM — no wallet, no gas.' },
  { key: 'interact', label: 'PLAY', hint: 'Call any contract: yours, the fleet’s, or one loaded by address / ABI CID.' },
]

const MANAGING: TabDef[] = [
  { key: 'contracts', label: 'CONTRACTS', hint: 'Every contract you’ve deployed or watched — rename, verify, forget, or take one to PLAY.' },
  { key: 'chains', label: 'CHAINS', hint: 'Every chain the console can reach: live block, latency, and the RPC behind it. Add your own.' },
]

const FLEET: TabDef[] = [
  { key: 'deploy', label: 'DEPLOY', hint: 'Re-deploy the fleet’s own contracts (owner tooling).', fleetOnly: true },
  { key: 'config', label: 'CONFIG', hint: 'What config.json records for this network.', fleetOnly: true },
]

const ALL_TABS = [...BUILDING, ...MANAGING, ...FLEET]

export default function ChainPage() {
  const [activeTab, setActiveTab] = useState<Tab>('build')
  const [network, setNetwork] = useState('testnet')
  const [target, setTarget] = useState<InteractTarget | null>(null)
  const [railOpen, setRailOpen] = useState(false)
  const [chainLed, setChainLed] = useState<LedState>('idle')
  const [block, setBlock] = useState<number | null>(null)
  const [score, setScore] = useState<number | null>(null)
  const wallet = useChainWallet(network)
  const api = useApiHealth()
  const projects = useProjects(wallet.address)
  const mobile = useIsMobile()

  const net = netInfo(network)
  const fleet = !!net.fleet
  const hint = ALL_TABS.find(t => t.key === activeTab)?.hint || ''

  // The marquee's chain lamp — is the RPC we're pointed at actually answering?
  useEffect(() => {
    let cancelled = false
    const ping = () => probeNetwork(network).then(p => {
      if (cancelled) return
      setChainLed(!p.up ? 'dead' : p.chainId !== net.chainId ? 'warn' : 'live')
      setBlock(p.up ? p.block ?? null : null)
    })
    setChainLed('idle'); setBlock(null)
    ping()
    const iv = setInterval(ping, 30000)
    return () => { cancelled = true; clearInterval(iv) }
  }, [network, net.chainId])

  // How many contracts you have to your name — the cabinet's score line.
  useEffect(() => {
    let cancelled = false
    const q = wallet.address ? `?address=${wallet.address}` : ''
    chainApi(`/build/deployments${q}`)
      .then(d => { if (!cancelled) setScore(d.count ?? (d.deployments || []).length) })
      .catch(() => { if (!cancelled) setScore(null) })
    return () => { cancelled = true }
  }, [wallet.address, activeTab])

  const handoff = (t: InteractTarget) => {
    setTarget(t)
    setActiveTab('interact')
  }

  const offFleet = (
    <div style={{ ...panelStyle, padding: '16px', fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
      This panel describes the fleet&apos;s own deployment, and {net.name} has none.
      Switch to a fleet network to use it — everything else works here as normal.
    </div>
  )

  const rail = (
    <>
      <Sidebar projects={projects} address={wallet.address} onNavigate={() => setRailOpen(false)} />
      <Gallery projects={projects} address={wallet.address} onNavigate={() => setRailOpen(false)} />
    </>
  )

  const tabButton = (tab: TabDef, primary: boolean) => {
    const active = activeTab === tab.key
    const dim = tab.fleetOnly && !fleet
    return (
      <button
        key={tab.key}
        onClick={() => setActiveTab(tab.key)}
        className="arc-press arc-pixel"
        title={dim ? `${net.name} has no fleet deployment` : tab.hint}
        style={{
          fontFamily: PIXEL,
          fontSize: primary ? PX.sm : PX.xs,
          letterSpacing: '0.06em',
          lineHeight: 1.6,
          padding: primary ? (mobile ? '11px 12px' : '9px 14px') : '7px 10px',
          minHeight: primary ? (mobile ? '44px' : '40px') : '32px',
          flexShrink: 0,
          border: `${primary ? 3 : 2}px solid ${active ? ACCENT : 'transparent'}`,
          background: active ? `${ACCENT}1a` : 'transparent',
          color: active ? ACCENT : 'var(--text-tertiary)',
          boxShadow: active ? `${primary ? 3 : 2}px ${primary ? 3 : 2}px 0px 0px ${ACCENT}` : 'none',
          cursor: 'pointer',
          opacity: dim ? 0.4 : 1,
          textShadow: active && primary ? `0 0 10px ${ACCENT}` : 'none',
        }}
      >
        {tab.label}
      </button>
    )
  }

  const groupMark = (label: string) => (
    <span key={label} style={{
      fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.14em', flexShrink: 0,
      color: 'var(--text-tertiary)', opacity: 0.6, padding: '0 4px 0 8px',
      borderLeft: '2px solid var(--border-color)', lineHeight: 1,
    }}>
      {label}
    </span>
  )

  // width:0 + min-width:100% keeps this page from widening the app shell: the
  // shell's columns size to their content, so without it one wide row (the tab
  // strip on a phone) drags the whole page past the viewport.
  return (
    <div className="min-h-screen arc-cabinet" style={{
      fontFamily: TERM_FONT, backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)',
      width: 0, minWidth: '100%',
    }}>
      <ArcadeStyles />
      <div className="max-w-7xl mx-auto" style={{ padding: mobile ? '12px 12px 40px' : '16px 16px 48px' }}>

        <Marquee
          compact={mobile}
          title="CHAIN"
          subtitle="BUILD · TEST · DEPLOY · PLAY"
          controls={
            <>
              <NetworkPicker
                network={network} setNetwork={setNetwork} led={chainLed} block={block}
                onManage={() => setActiveTab('chains')}
              />
              <AccountPicker wallet={wallet} network={network} />
              <Balances wallet={wallet} network={network} />
            </>
          }
          readouts={mobile ? [] : [
            {
              label: 'SHIPPED',
              value: score === null ? '—' : String(score).padStart(3, '0'),
              color: NEON.p2,
            },
          ]}
        />

        {/* One banner for the whole console: every panel loads through chainApi
            and most swallow their errors, so a dead bridge used to render as a
            page of empty boxes. */}
        {api.down && (
          <Banner
            title="CHAIN API NOT ANSWERING"
            onRetry={() => window.location.reload()}
          >
            The console can&apos;t reach the chain module, so templates, projects and
            contracts are all coming back empty. Last failure: {api.detail}
          </Banner>
        )}

        {/* On a phone the rail is a drawer — one button, always showing what's open */}
        {mobile && (
          <button
            onClick={() => setRailOpen(true)}
            className="arc-press"
            style={{
              ...panelStyle, width: '100%', marginBottom: '12px', padding: '12px 14px',
              display: 'flex', alignItems: 'center', gap: '10px', minHeight: '48px',
              fontFamily: TERM_FONT, fontSize: '15px', color: 'var(--text-primary)', cursor: 'pointer',
            }}
          >
            <span style={{ color: ACCENT }}>☰</span>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {projects.project ? projects.project.name : 'PROJECTS'}
            </span>
            <span style={{ marginLeft: 'auto', fontSize: '13px', color: 'var(--text-tertiary)' }}>
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
            {/* Tabs — the big three on the left, the rest as a smaller tier
                that sits right or, when the window is narrow, drops onto its
                own line. Never a horizontal scroll: a tab off the edge is a
                tab nobody finds. */}
            <div style={{
              display: 'flex', gap: mobile ? '4px' : '6px', alignItems: 'center',
              borderBottom: '3px solid var(--border-color)', paddingBottom: '8px',
              flexWrap: 'wrap', rowGap: '8px',
            }}>
              <div style={{ display: 'flex', gap: mobile ? '4px' : '6px', alignItems: 'stretch' }}>
                {BUILDING.map(t => tabButton(t, true))}
              </div>
              <div style={{
                display: 'flex', gap: '4px', alignItems: 'center', marginLeft: mobile ? 0 : '8px',
              }}>
                {groupMark('MANAGE')}
                {MANAGING.map(t => tabButton(t, false))}
                {groupMark('FLEET')}
                {FLEET.map(t => tabButton(t, false))}
              </div>
            </div>

            <p style={{
              fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)',
              margin: '10px 0 16px', lineHeight: 1.5,
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
            {activeTab === 'contracts' && (
              <ContractsTab wallet={wallet} network={network} onInteract={handoff} onNetwork={setNetwork} />
            )}
            {activeTab === 'chains' && (
              <ChainsTab network={network} setNetwork={setNetwork} address={wallet.address} />
            )}
            {activeTab === 'deploy' && (fleet ? <DeployTab network={network} /> : offFleet)}
            {activeTab === 'config' && (fleet ? <ConfigTab network={network} /> : offFleet)}
          </div>
        </div>
      </div>
    </div>
  )
}
