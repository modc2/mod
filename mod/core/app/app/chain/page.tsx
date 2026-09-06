"use client"

import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { TERM_FONT, ACCENT, netInfo, probeNetwork, chainApi, useIsMobile, useApiHealth } from './shared'
import { Banner, Sheet, panelStyle } from './ui'
import { ArcadeStyles, PIXEL, NEON, type LedState } from './arcade'
import { Rail, RailBar, RAIL_CSS, RAIL_WIDTH, ALL_TABS, type Tab } from './Rail'
import { useChainWallet } from './WalletBar'
import { useProjects } from './projects'
import { BuildTab } from './BuildTab'
import { TestTab } from './TestTab'
import { DeployTab } from './DeployTab'
import { ContractsTab } from './ContractsTab'
import { ChainsTab } from './ChainsTab'
import { InteractTab, type InteractTarget } from './InteractTab'
import { ConfigTab } from './ConfigTab'
import { AgentTab } from './AgentTab'

export const dynamic = 'force-dynamic'

export default function ChainPage() {
  const [activeTab, setActiveTab] = useState<Tab>('build')
  const [network, setNetwork] = useState('testnet')
  const [target, setTarget] = useState<InteractTarget | null>(null)
  const [chainLed, setChainLed] = useState<LedState>('idle')
  const [block, setBlock] = useState<number | null>(null)
  const [score, setScore] = useState<number | null>(null)
  const [drawer, setDrawer] = useState(false)
  const wallet = useChainWallet(network)
  const api = useApiHealth()
  const projects = useProjects(wallet.address)
  const mobile = useIsMobile()

  const net = netInfo(network)
  const fleet = !!net.fleet
  const tab = ALL_TABS.find(t => t.key === activeTab)

  // The rail's chain lamp — is the RPC we're pointed at actually answering?
  useEffect(() => {
    let cancelled = false
    let misses = 0
    const ping = () => probeNetwork(network).then(p => {
      if (cancelled) return
      if (p.up) {
        misses = 0
        setChainLed(p.chainId !== net.chainId ? 'warn' : 'live')
        setBlock(p.block ?? null)
        return
      }
      // public RPCs drop the odd request — keep the last block on screen and
      // only call the chain dead on the second miss in a row
      misses += 1
      if (misses >= 2) { setChainLed('dead'); setBlock(null) }
    })
    setChainLed('idle'); setBlock(null)
    ping()
    const iv = setInterval(ping, 30000)
    return () => { cancelled = true; clearInterval(iv) }
  }, [network, net.chainId])

  // How many contracts you have to your name — badged on the CONTRACTS row.
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

  const railProps = {
    activeTab, setActiveTab, network, setNetwork, wallet, projects,
    led: chainLed, block, score,
  }

  const panels = (
    <>
      {activeTab === 'build' && (
        <BuildTab wallet={wallet} network={network} projects={projects} onInteract={handoff} />
      )}
      {activeTab === 'test' && <TestTab projects={projects} address={wallet.address} />}
      {activeTab === 'agent' && <AgentTab wallet={wallet} network={network} projects={projects} />}
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
    </>
  )

  // width:0 + min-width:100% keeps this page from widening the app shell: the
  // shell's columns size to their content, so without it one wide row drags
  // the whole page past the viewport.
  return (
    <div className="min-h-screen arc-cabinet" style={{
      fontFamily: TERM_FONT, backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)',
      width: 0, minWidth: '100%',
    }}>
      <ArcadeStyles />
      <style dangerouslySetInnerHTML={{ __html: RAIL_CSS }} />

      <div style={{
        display: 'flex', alignItems: 'flex-start',
        maxWidth: mobile ? undefined : `${1180 + RAIL_WIDTH}px`,
        margin: '0 auto',
        padding: mobile ? '10px 10px 40px' : '14px 18px 48px',
        gap: 0,
      }}>

        {/* The rail. Brand, workspace, nav, account — one column, in that
            order. On a phone it's a drawer behind ☰ instead. */}
        {!mobile && <Rail {...railProps} />}

        {/* The screen. */}
        <div style={{ flex: 1, minWidth: 0, paddingLeft: mobile ? 0 : '22px' }}>

          {mobile && (
            <RailBar
              tab={tab} onOpen={() => setDrawer(true)}
              wallet={wallet} network={network} led={chainLed}
            />
          )}

          {/* One banner for the whole console: every panel loads through
              chainApi and most swallow their errors, so a dead bridge used to
              render as a page of empty boxes. */}
          {api.down && (
            <Banner
              title="CHAIN API NOT ANSWERING"
              onRetry={() => window.location.reload()}
            >
              The console can&apos;t reach the chain module, so templates, projects and
              contracts are all coming back empty. Last failure: {api.detail}
            </Banner>
          )}

          {/* The panel's own title row — what the marquee used to shout, said
              once, where the thing it names actually is. */}
          {!mobile && (
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: '14px', flexWrap: 'wrap',
              borderBottom: '2px solid var(--border-color)', paddingBottom: '12px', marginBottom: '16px',
            }}>
              <h1 className="arc-pixel" style={{
                fontFamily: PIXEL, fontSize: '14px', letterSpacing: '0.1em', lineHeight: 1.4,
                color: ACCENT, textShadow: `0 0 14px ${ACCENT}55`, margin: 0,
              }}>
                {tab?.label}
              </h1>
              <p style={{
                fontFamily: TERM_FONT, fontSize: '15px', color: 'var(--text-secondary)',
                lineHeight: 1.5, margin: 0, flex: 1, minWidth: '220px',
              }}>
                <span style={{ color: NEON.p2, marginRight: '8px' }}>»</span>
                {tab?.hint}
              </p>
            </div>
          )}

          {mobile && (
            <p style={{
              fontFamily: TERM_FONT, fontSize: '15px', color: 'var(--text-secondary)',
              margin: '0 0 14px', lineHeight: 1.5,
            }}>
              <span style={{ color: NEON.p2, marginRight: '8px' }}>»</span>
              {tab?.hint}
            </p>
          )}

          {panels}
        </div>
      </div>

      {/* Phone drawer: the same rail, in a slide-over.

          Portalled to <body> on purpose. The cabinet sets `isolation: isolate`
          so its scanline overlay stacks correctly, which also traps anything
          inside it — the sheet's z-100 lost to the app shell's fixed z-70 top
          bar and the drawer opened with its own close button underneath it. */}
      {mobile && drawer && typeof document !== 'undefined' && createPortal(
        // Outside the cabinet the drawer also loses the cabinet's contrast
        // lifts, so it carries its own copy — muddy 25%-white labels are what
        // those overrides exist to prevent.
        <div style={{
          fontFamily: TERM_FONT,
          ['--text-secondary' as any]: 'rgba(255,255,255,0.68)',
          ['--text-tertiary' as any]: 'rgba(255,255,255,0.45)',
          ['--border-color' as any]: 'rgba(148,163,184,0.28)',
        }}>
          <Sheet open onClose={() => setDrawer(false)} title="CHAIN">
            <div style={{ paddingBottom: '20px' }}>
              <Rail {...railProps} inSheet onNavigate={() => setDrawer(false)} />
            </div>
          </Sheet>
        </div>,
        document.body,
      )}
    </div>
  )
}
