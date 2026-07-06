"use client";

import Link from 'next/link'
import { ArrowPathIcon } from '@heroicons/react/24/outline'
import { WalletButton } from './Wallet'

// ── Shared page shell: sticky glass nav + aurora page frame ────────────────

const NAV = [
  { key: 'hub', label: 'Hub', href: '/' },
  { key: 'interact', label: 'Interact', href: '/interact' },
  { key: 'contracts', label: 'Contracts', href: '/contracts' },
  { key: 'control', label: 'Control', href: '/control' },
  { key: 'protocol', label: 'Protocol', href: '/protocol' },
  { key: 'admin', label: 'Owner', href: '/admin' },
] as const

export type NavKey = typeof NAV[number]['key']

export function Shell({ active, right, children, footer }: {
  active: NavKey
  right?: React.ReactNode
  children: React.ReactNode
  footer?: React.ReactNode
}) {
  return (
    <div className="min-h-screen">
      <header className="navbar sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 md:px-6 h-14 flex items-center gap-4">
          {/* Brand */}
          <Link href="/" className="flex items-center gap-2.5 shrink-0 group">
            <span className="relative w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/80 to-violet-500/80 shadow-[0_0_18px_-4px_rgba(34,211,238,0.7)] transition-shadow group-hover:shadow-[0_0_24px_-4px_rgba(34,211,238,0.9)]">
              <span className="absolute inset-[3px] rounded-[5px] bg-[#05050a]/85" />
              <span className="absolute inset-0 flex items-center justify-center text-[11px] font-bold text-cyan-200">⬢</span>
            </span>
            <span className="text-[14px] font-semibold tracking-tight text-white/90">chain</span>
          </Link>

          {/* Nav */}
          <nav className="hidden md:flex items-center gap-0.5 ml-2">
            {NAV.map(n => (
              <Link key={n.key} href={n.href}
                className={`navlink ${active === n.key ? 'navlink-active' : ''}`}>
                {n.label}
              </Link>
            ))}
          </nav>

          <div className="flex-1" />

          {/* Page-specific controls */}
          <div className="flex items-center gap-2">{right}<WalletButton /></div>
        </div>

        {/* Mobile nav */}
        <div className="md:hidden overflow-x-auto px-4 pb-2 flex gap-1">
          {NAV.map(n => (
            <Link key={n.key} href={n.href}
              className={`navlink whitespace-nowrap ${active === n.key ? 'navlink-active' : ''}`}>
              {n.label}
            </Link>
          ))}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 md:px-6 py-6 md:py-8 space-y-5">
        {children}
      </main>

      <footer className="max-w-7xl mx-auto px-6 pb-10 pt-4 text-center">
        <p className="text-[11px] tracking-[0.2em] uppercase text-white/15">
          {footer || 'chain — mod protocol'}
        </p>
      </footer>
    </div>
  )
}

// ── Shared nav-bar controls ─────────────────────────────────────────────────

export function NetworkSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="input !w-auto !py-1.5 !px-3 !text-[12px] !font-sans !rounded-full">
      <option value="testnet">Base Sepolia</option>
      <option value="ganache">Ganache</option>
      <option value="mainnet">Base Mainnet</option>
    </select>
  )
}

export function RefreshBtn({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return (
    <button onClick={onClick} disabled={loading} title="Refresh"
      className="btn btn-ghost !rounded-full !p-2">
      <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
    </button>
  )
}

export function BlockChip({ block, pulse }: { block: number | null; pulse?: boolean }) {
  if (block === null || block === undefined) return null
  return (
    <div className="hidden sm:flex items-center gap-2 pl-3 pr-3.5 py-1.5 rounded-full bg-white/[0.04] border border-white/[0.08]">
      <span className={`dot dot-live ${pulse ? 'animate-ping' : ''}`} />
      <span className="text-[12px] font-medium text-emerald-300/90 tabular-nums font-mono">
        {block.toLocaleString()}
      </span>
    </div>
  )
}
