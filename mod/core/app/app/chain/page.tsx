"use client"

import { useState, useEffect } from 'react'
import { TERM_FONT, ACCENT, netInfo, probeNetwork, chainApi, short, useIsMobile, useApiHealth } from './shared'
import { Banner, panelStyle } from './ui'
import { ArcadeStyles, Marquee, PIXEL, PX, NEON, type LedState } from './arcade'
import { useChainWallet } from './WalletBar'
import { AccountPicker } from './AccountPicker'
import { NetworkPicker } from './NetworkPicker'
import { Balances } from './Balances'
import { ProjectPicker } from './ProjectPicker'
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

type Tab = 'build' | 'test' | 'agent' | 'deploy' | 'contracts' | 'chains' | 'interact' | 'config'

interface TabDef { key: Tab; label: string; hint: string; fleetOnly?: boolean }

// Two tiers, because they answer different questions: the big three are what
// you're doing right now; the rest is what you own and what the fleet runs.
// The second tier wraps under the first when the window is narrow — one
// clean line under another, never a row scrolled off the edge.
const BUILDING: TabDef[] = [
  { key: 'build', label: 'BUILD', hint: 'Write it, compile it, deploy it with your wallet.' },
  { key: 'test', label: 'TEST', hint: 'Run the project’s tests on an in-process EVM — no wallet, no gas.' },
  { key: 'agent', label: 'AGENT', hint: 'Hand the project to Claude Code: it edits contracts and tests in a sandbox and runs the suite — through the agent module, like the build console.' },
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

  const tabButton = (tab: TabDef, primary: boolean) => {
    const active = activeTab === tab.key
    const dim = tab.fleetOnly && !fleet
    return (
      <button
        key={tab.key}
        onClick={() => setActiveTab(tab.key)}
        className="arc-press arc-pixel arc-tab"
        aria-pressed={active}
        title={dim ? `${net.name} has no fleet deployment` : tab.hint}
        style={{
          fontFamily: PIXEL,
          fontSize: primary ? (mobile ? PX.md : PX.sm) : PX.xs,
          letterSpacing: '0.06em',
          lineHeight: 1.6,
          padding: primary ? (mobile ? '12px 8px' : '9px 14px') : (mobile ? '9px 10px' : '7px 10px'),
          minHeight: primary ? (mobile ? '48px' : '40px') : (mobile ? '38px' : '32px'),
          flex: mobile && primary ? '1 1 0' : '0 0 auto',
          // every tab is a physical button — a resting tab keeps its bezel
          // and shadow, it just isn't lit. Bare text next to a boxed BUILD
          // read as labels, not as the other two-thirds of the controls.
          border: `${primary ? 3 : 2}px solid ${active ? ACCENT : 'var(--border-color)'}`,
          background: active ? `${ACCENT}1a` : 'var(--bg-secondary)',
          color: active ? ACCENT : 'var(--text-secondary)',
          boxShadow: active
            ? `${primary ? 3 : 2}px ${primary ? 3 : 2}px 0px 0px ${ACCENT}`
            : `${primary ? 3 : 2}px ${primary ? 3 : 2}px 0px 0px rgba(0,0,0,0.4)`,
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
      color: 'var(--text-tertiary)', padding: '0 4px 0 10px',
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
      <div className="max-w-6xl mx-auto" style={{ padding: mobile ? '10px 10px 40px' : '16px 16px 48px' }}>

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
              <ProjectPicker projects={projects} address={wallet.address} />
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

        {/* Tabs — the big three fill the row on a phone; the smaller tier
            wraps under them. Never a horizontal scroll: a tab off the edge is
            a tab nobody finds. */}
        <div style={{
          display: 'flex', columnGap: mobile ? '4px' : '6px', rowGap: '8px', alignItems: 'center',
          borderBottom: '3px solid var(--border-color)', paddingBottom: '8px',
          flexWrap: 'wrap',
        }}>
          <div style={{
            display: 'flex', gap: mobile ? '6px' : '6px', alignItems: 'stretch',
            width: mobile ? '100%' : undefined,
          }}>
            {BUILDING.map(t => tabButton(t, true))}
          </div>
          <div style={{
            display: 'flex', columnGap: '4px', rowGap: '6px', alignItems: 'center', marginLeft: mobile ? 0 : '8px',
            flexWrap: 'wrap',
          }}>
            <span style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              {groupMark('MANAGE')}
              {MANAGING.map(t => tabButton(t, false))}
            </span>
            <span style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              {groupMark('FLEET')}
              {FLEET.map(t => tabButton(t, false))}
            </span>
          </div>
        </div>

        <p style={{
          fontFamily: TERM_FONT, fontSize: '15px', color: 'var(--text-secondary)',
          margin: '10px 0 16px', lineHeight: 1.5,
        }}>
          <span style={{ color: ACCENT, marginRight: '8px' }}>»</span>
          {hint}
        </p>

        {/* Panels */}
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

        {/* The cabinet's base plate. One line, in character: who's at the
            controls — or the words every cabinet says when nobody is. */}
        <div style={{
          marginTop: '48px', paddingTop: '14px', borderTop: '2px solid var(--border-color)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px',
        }}>
          <span style={{ color: NEON.p1, fontSize: '8px' }}>▲</span>
          <span
            className={wallet.address ? undefined : 'arc-blink-soft'}
            style={{
              fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.22em',
              color: 'var(--text-tertiary)', lineHeight: 1.8, textAlign: 'center',
            }}
          >
            {wallet.address
              ? `PLAYER ${short(wallet.address, 6, 4)} · READY`
              : 'INSERT COIN TO CONTINUE'}
          </span>
          <span style={{ color: NEON.p2, fontSize: '8px' }}>▲</span>
        </div>
      </div>
    </div>
  )
}
