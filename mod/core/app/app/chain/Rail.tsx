"use client"

// The rail. Everything that used to be a banner across the top — the CHAIN
// wordmark, the four control pills, the score, and two tiers of tabs — is one
// column down the left, in the order you actually use it:
//
//   brand → what you're working on (PROJECT) → what you're doing (nav)
//         → who you are (ACCOUNT: player, chain, balance)
//
// The account block is pinned to the bottom the way every console with a
// signed-in user puts it there, and it is the ONLY place the console asks you
// about identity: pick the signer, pick the chain, read the balance, sign out.
//
// The pickers themselves are unchanged — NetworkPicker / AccountPicker /
// Balances / ProjectPicker are the same pills, just stacked instead of strung
// out. Their dropdowns hang off the rail and over the panel beside it, which
// is what a flyout should do at 268px wide.

import { CSSProperties, ReactNode } from 'react'
import { TERM_FONT, ACCENT, netInfo, useIsMobile } from './shared'
import { PIXEL, PX, NEON, Led, Strip, type LedState } from './arcade'
import { NetworkPicker } from './NetworkPicker'
import { AccountPicker } from './AccountPicker'
import { Balances } from './Balances'
import { ProjectPicker } from './ProjectPicker'
import type { ChainWallet } from './WalletBar'
import type { ProjectsApi } from './projects'

export type Tab = 'build' | 'test' | 'agent' | 'interact' | 'contracts' | 'chains' | 'deploy' | 'config'

export interface TabDef {
  key: Tab
  label: string
  /** the sentence under the panel — what this tab is for, in plain words */
  hint: string
  fleetOnly?: boolean
}

export interface TabGroup {
  title: string
  color: string
  tabs: TabDef[]
}

/**
 * Three groups, because they answer three different questions: what am I
 * building, what do I own, what does the fleet run. The old header flattened
 * these into one wrapping row of eight buttons; a column can afford to say
 * which is which.
 */
export const TAB_GROUPS: TabGroup[] = [
  {
    title: 'BUILD',
    color: ACCENT,
    tabs: [
      { key: 'build', label: 'BUILD', hint: 'Write it, compile it, deploy it with your wallet.' },
      { key: 'test', label: 'TEST', hint: 'Run the project’s tests on an in-process EVM — no wallet, no gas.' },
      { key: 'agent', label: 'AGENT', hint: 'Hand the project to Claude Code: it edits contracts and tests in a sandbox and runs the suite — through the agent module, like the build console.' },
      { key: 'interact', label: 'PLAY', hint: 'Call any contract: yours, the fleet’s, or one loaded by address / ABI CID.' },
    ],
  },
  {
    title: 'MANAGE',
    color: NEON.p2,
    tabs: [
      { key: 'contracts', label: 'CONTRACTS', hint: 'Every contract you’ve deployed or watched — rename, verify, forget, or take one to PLAY.' },
      { key: 'chains', label: 'CHAINS', hint: 'Every chain the console can reach: live block, latency, and the RPC behind it. Add your own.' },
    ],
  },
  {
    title: 'FLEET',
    color: NEON.p1,
    tabs: [
      { key: 'deploy', label: 'DEPLOY', hint: 'Re-deploy the fleet’s own contracts (owner tooling).', fleetOnly: true },
      { key: 'config', label: 'CONFIG', hint: 'What config.json records for this network.', fleetOnly: true },
    ],
  },
]

export const ALL_TABS: TabDef[] = TAB_GROUPS.flatMap(g => g.tabs)

export const RAIL_WIDTH = 268

// Hover/active behaviour that inline styles can't express. Scoped to the rail
// so nothing else in the app inherits it.
export const RAIL_CSS = `
.arc-rail { position: relative; }
/* The rail's own edge light — a thin neon seam between column and screen,
   the way a cabinet's side panel meets its bezel. */
.arc-rail::after {
  content: ''; position: absolute; top: 0; bottom: 0; right: -1px; width: 2px;
  background: linear-gradient(180deg,
    color-mix(in srgb, var(--accent-primary, #10b981) 55%, transparent),
    rgba(34,211,238,.35) 45%, rgba(255,46,136,.30) 100%);
  opacity: .7; pointer-events: none;
}
.arc-nav {
  display: flex; align-items: center; gap: 9px; width: 100%;
  text-align: left; cursor: pointer; position: relative;
  border-style: solid; border-width: 2px; background: transparent;
  transition: background .1s, border-color .1s, color .1s, box-shadow .08s steps(2), transform .08s steps(2);
}
.arc-nav[aria-pressed="false"]:hover:not(:disabled) {
  background: rgba(255,255,255,.045);
  border-color: var(--border-color) !important;
  color: var(--text-primary) !important;
  transform: translateX(2px);
}
.arc-nav:active:not(:disabled) { transform: translate(2px, 2px); box-shadow: none !important; }
/* The account block is pinned to the foot of the rail, so its dropdowns have
   to open UPWARD — Dropdown anchors to the top of its trigger, and a 380px
   list hung below a control 80px off the bottom of the screen is a list you
   can't read. Scoped to this wrapper, and capped so the flipped panel can't
   run off the top either. The selector is Dropdown's shape: wrapper div,
   whose only div child is the panel (the trigger is a button). */
.arc-dropup > div > div {
  top: auto !important; bottom: calc(100% + 6px) !important;
  max-height: min(58vh, 440px) !important;
}
/* The lit row's marker. Only the active row grows one, so the eye finds the
   current tab without reading a word. */
.arc-nav[aria-pressed="true"]::before {
  content: ''; position: absolute; left: -2px; top: -2px; bottom: -2px; width: 4px;
  background: var(--nav-c); box-shadow: 0 0 10px var(--nav-c);
}
`

function RailLabel({ children, color, right }: { children: ReactNode; color?: string; right?: ReactNode }) {
  const c = color || 'var(--text-tertiary)'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '7px',
      fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.18em',
      color: c, marginBottom: '8px', lineHeight: 1,
    }}>
      <span style={{ width: '3px', height: '9px', background: c, flexShrink: 0, boxShadow: `0 0 6px ${c}` }} />
      <span>{children}</span>
      <span style={{ flex: 1, height: '1px', background: 'var(--border-color)', opacity: 0.7 }} />
      {right}
    </div>
  )
}

/** The wordmark, at rail size. Same chromatic offset the marquee had. */
export function RailBrand({ onClick }: { onClick?: () => void }) {
  return (
    <button
      onClick={onClick}
      className="arc-shine arc-press"
      title="CHAIN — build, test, deploy, play"
      style={{
        display: 'flex', alignItems: 'flex-end', gap: '7px', width: '100%',
        border: 'none', background: 'transparent', cursor: onClick ? 'pointer' : 'default',
        padding: '2px 4px 2px 0', margin: 0,
      }}
    >
      <span className="arc-pixel" style={{
        fontFamily: PIXEL, fontSize: '20px', letterSpacing: '0.1em', lineHeight: 1, color: ACCENT,
        textShadow: `1px 0 0 ${NEON.p1}, -1px 0 0 ${NEON.p2}, 0 0 16px ${ACCENT}`,
      }}>
        CHAIN
      </span>
      <span className="arc-blink arc-pixel" style={{
        fontFamily: PIXEL, fontSize: '20px', lineHeight: 1, color: NEON.coin,
      }}>
        ▮
      </span>
    </button>
  )
}

export interface RailProps {
  activeTab: Tab
  setActiveTab: (t: Tab) => void
  network: string
  setNetwork: (n: string) => void
  wallet: ChainWallet
  projects: ProjectsApi
  led: LedState
  block: number | null
  /** contracts shipped by this player — the old SHIPPED readout, now a badge */
  score: number | null
  /** dismiss the phone drawer once a nav row is hit */
  onNavigate?: () => void
  /** inside the phone drawer: no brand row, no sticky column */
  inSheet?: boolean
}

export function Rail({
  activeTab, setActiveTab, network, setNetwork, wallet, projects,
  led, block, score, onNavigate, inSheet,
}: RailProps) {
  const net = netInfo(network)
  const fleet = !!net.fleet
  const mobile = useIsMobile()

  const go = (t: Tab) => { setActiveTab(t); onNavigate?.() }

  // In the drawer there is nothing below to flip away from — and a sheet
  // scrolls, so an upward panel would just hang off its own top.
  const dropUp = inSheet ? undefined : 'arc-dropup'

  // Which wallet is signing, said once at the head of the block.
  const signerTag = !wallet.kind
    ? { text: 'SIGNED OUT', color: NEON.coin }
    : wallet.kind === 'browser'
      ? { text: 'METAMASK', color: '#f59e0b' }
      : { text: wallet.localKeys.find(k => k.address === wallet.address)?.label || 'LOCAL KEY', color: ACCENT }

  const navRow = (tab: TabDef, color: string) => {
    const active = activeTab === tab.key
    const dim = tab.fleetOnly && !fleet
    return (
      <button
        key={tab.key}
        onClick={() => go(tab.key)}
        aria-pressed={active}
        className="arc-nav arc-pixel"
        title={dim ? `${net.name} has no fleet deployment — switch to a fleet network` : tab.hint}
        style={{
          // `--nav-c` feeds the ::before marker; inline custom props are the
          // only way to hand a per-row colour to a stylesheet rule.
          ['--nav-c' as any]: color,
          fontFamily: PIXEL,
          fontSize: PX.sm,
          letterSpacing: '0.07em',
          lineHeight: 1.5,
          padding: mobile ? '11px 10px' : '9px 10px',
          minHeight: mobile ? '42px' : '34px',
          borderColor: active ? color : 'transparent',
          background: active ? `${color}1a` : 'transparent',
          color: active ? color : 'var(--text-secondary)',
          boxShadow: active ? `3px 3px 0 0 ${color}` : 'none',
          textShadow: active ? `0 0 10px ${color}` : 'none',
          opacity: dim ? 0.4 : 1,
        }}
      >
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {tab.label}
        </span>
        {tab.key === 'contracts' && score !== null && score > 0 && (
          <span
            title={`${score} contract${score === 1 ? '' : 's'} deployed from this account`}
            style={{
              fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.08em', lineHeight: 1,
              padding: '4px 5px', flexShrink: 0,
              color: NEON.coin, border: `1px solid ${NEON.coin}66`, background: `${NEON.coin}14`,
            }}
          >
            {String(score).padStart(3, '0')}
          </span>
        )}
        {tab.key === 'chains' && (
          <Led state={led} size={6} />
        )}
      </button>
    )
  }

  const block1: CSSProperties = { marginBottom: '18px' }

  return (
    <div
      className={inSheet ? undefined : 'arc-rail'}
      style={{
        display: 'flex', flexDirection: 'column',
        width: inSheet ? '100%' : `${RAIL_WIDTH}px`,
        flexShrink: 0,
        ...(inSheet ? {} : {
          position: 'sticky',
          top: '12px',
          alignSelf: 'flex-start',
          maxHeight: 'calc(100vh - 24px)',
          paddingRight: '16px',
          // A sticky box is its own stacking context, so the pickers' z-60
          // panels are trapped inside it — and the rail is painted BEFORE the
          // screen beside it, which put every dropdown behind the editor.
          // Lifting the whole rail is what puts them back in front.
          zIndex: 40,
        }),
      }}
    >
      {!inSheet && (
        <div style={{ marginBottom: '16px' }}>
          <RailBrand onClick={() => go('build')} />
          <div style={{
            fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.16em',
            color: 'var(--text-tertiary)', marginTop: '7px', lineHeight: 1.6,
          }}>
            SMART CONTRACT CONSOLE
          </div>
        </div>
      )}

      {/* WORKSPACE — the one thing every panel below is scoped to. */}
      <div style={block1}>
        <RailLabel color={NEON.p2}>WORKSPACE</RailLabel>
        <ProjectPicker projects={projects} address={wallet.address} />
      </div>

      {/* NAV — the only part allowed to scroll, so the pickers above and below
          can hang their dropdowns without being clipped by an overflow box. */}
      <div style={{
        flex: inSheet ? '0 0 auto' : '1 1 auto', minHeight: 0,
        overflowY: inSheet ? 'visible' : 'auto', overflowX: 'visible',
        marginBottom: '18px',
      }}>
        {TAB_GROUPS.map((g, i) => (
          <div key={g.title} style={{ marginBottom: i === TAB_GROUPS.length - 1 ? 0 : '16px' }}>
            <RailLabel color={g.color}>{g.title}</RailLabel>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
              {g.tabs.map(t => navRow(t, g.color))}
            </div>
          </div>
        ))}
      </div>

      {/* ACCOUNT — pinned to the foot of the rail. Who signs, on what chain,
          holding what. Three controls, and every identity action lives in
          one of their dropdowns. */}
      <div style={{
        borderTop: '2px solid var(--border-color)', paddingTop: '14px', flexShrink: 0,
      }}>
        <RailLabel
          color={wallet.kind ? NEON.p1 : NEON.coin}
          right={
            <span
              className={wallet.kind ? undefined : 'arc-blink-soft'}
              title={wallet.address || 'nobody is signed in'}
              style={{
                fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.1em', lineHeight: 1,
                color: signerTag.color, maxWidth: '96px',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
            >
              {signerTag.text}
            </span>
          }
        >
          ACCOUNT
        </RailLabel>
        {/* Strip makes the pills set denser type and drop their secondary
            hints — at 252px the PLAYER pill's METAMASK badge was clipping
            mid-word. It moved up to the label instead, where it fits. */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
          <div className={dropUp}>
            <Strip.Provider value={true}>
              <AccountPicker wallet={wallet} network={network} />
            </Strip.Provider>
          </div>
          <div className={dropUp}>
            <NetworkPicker
              network={network} setNetwork={setNetwork} led={led} block={block}
              onManage={() => go('chains')}
            />
          </div>
          {wallet.address && (
            <div className={dropUp}>
              <Balances wallet={wallet} network={network} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * Phone header. The rail has nowhere to stand under ~760px, so it becomes a
 * drawer behind one button — and the bar keeps the two facts you'd otherwise
 * have to open the drawer for: which tab, and whether anyone is signed in.
 */
export function RailBar({
  tab, onOpen, wallet, network, led,
}: {
  tab: TabDef | undefined
  onOpen: () => void
  wallet: ChainWallet
  network: string
  led: LedState
}) {
  const net = netInfo(network)
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '10px',
      borderBottom: `2px solid var(--border-color)`, paddingBottom: '10px', marginBottom: '12px',
    }}>
      <button
        onClick={onOpen}
        aria-label="open console menu"
        className="arc-press arc-pixel"
        style={{
          fontFamily: PIXEL, fontSize: PX.md, lineHeight: 1,
          padding: '10px 11px', minHeight: '40px', flexShrink: 0,
          border: `2px solid ${ACCENT}`, background: `${ACCENT}1a`, color: ACCENT,
          boxShadow: `3px 3px 0 0 ${ACCENT}`, cursor: 'pointer',
        }}
      >
        ☰
      </button>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="arc-pixel" style={{
          fontFamily: PIXEL, fontSize: PX.md, letterSpacing: '0.08em', lineHeight: 1.4,
          color: ACCENT, textShadow: `0 0 10px ${ACCENT}66`,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {tab?.label || 'CHAIN'}
        </div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '6px', marginTop: '4px',
          fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)',
        }}>
          <Led state={led} size={6} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {net.name}
          </span>
        </div>
      </div>
      <span
        className={wallet.kind ? undefined : 'arc-blink-soft'}
        style={{
          fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.1em', lineHeight: 1,
          padding: '5px 6px', flexShrink: 0,
          color: wallet.kind ? NEON.p1 : NEON.coin,
          border: `1px solid ${wallet.kind ? NEON.p1 : NEON.coin}66`,
          background: `${wallet.kind ? NEON.p1 : NEON.coin}14`,
        }}
      >
        {wallet.kind ? 'READY' : 'NO COIN'}
      </span>
    </div>
  )
}
