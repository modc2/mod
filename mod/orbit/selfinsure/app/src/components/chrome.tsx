'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ReactNode } from 'react'

// One array drives the nav, the footer and the home directory — add a route
// here and it appears everywhere at once (same pattern as openhouse).
export const ROUTES = [
  { href: '/', label: 'Overview', blurb: 'What a member-owned mutual is, and the live numbers.' },
  { href: '/pools', label: 'Pools', blurb: 'Every off-chain pool on this node: members, money, claims.' },
  { href: '/contract', label: 'On chain', blurb: 'The contract, its guarantees, and a live pool read off the chain.' },
  { href: '/preset', label: 'Templates', blurb: 'The US health mutual and the other presets, term by term.' },
]

export function SiteNav() {
  const pathname = usePathname()
  return (
    <header className="nav">
      <Link href="/" className="brand">
        <span className="brand-mark">SI</span> selfinsure
      </Link>
      <nav>
        {ROUTES.map((r) => (
          <Link key={r.href} href={r.href}
            className={pathname === r.href ? 'nav-link active' : 'nav-link'}>
            {r.label}
          </Link>
        ))}
      </nav>
    </header>
  )
}

export function SiteFooter() {
  return (
    <footer className="footer">
      <div>
        <strong>selfinsure</strong> — insurance as a mutual the members own.
        Runs on this node; the ledger is a local file, the contract is public source.
      </div>
      <div className="footer-links">
        {ROUTES.map((r) => <Link key={r.href} href={r.href}>{r.label}</Link>)}
        <a href="/selfinsure/api/health">api</a>
        <a href="/selfinsure/api/tools">mcp tools</a>
        <a href="/selfinsure/api/contract/source">contract source</a>
      </div>
    </footer>
  )
}

// ── small reusable pieces ──

export function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section className="section">
      <h2>{title}</h2>
      {sub && <p className="sub">{sub}</p>}
      {children}
    </section>
  )
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k} className="kv-row"><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  )
}

export function Note({ children, tone }: { children: ReactNode; tone?: 'error' }) {
  return <p className={tone === 'error' ? 'note note-error' : 'note'}>{children}</p>
}

export function Loading() {
  return <p className="note">Loading live data…</p>
}
