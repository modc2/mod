'use client'

// Shared site chrome + primitives used by every page: nav, footer,
// scroll-reveal, design glyphs and the design-card actions.

import { useEffect, useRef, useState, ReactNode, useCallback } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { api, FALLBACK_CATALOG, Design, StyleSpec } from '@/lib/modcity'

export function fmtUSD(n: number) {
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M'
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(0) + 'k'
  return '$' + (n || 0)
}
export const fmt = (n: number) => (n || 0).toLocaleString()

const TABS: Array<[string, string]> = [
  ['/build', 'Build'],
  ['/panels', 'Panels'],
  ['/mine', 'Mine'],
  ['/city', 'City'],
]

export function Nav() {
  const pathname = usePathname()
  return (
    <nav className="sticky top-0 z-50 backdrop-blur-xl bg-black/40 border-b border-white/5">
      <div className="max-w-7xl mx-auto px-5 h-14 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2 font-bold tracking-tight">
          <span className="inline-grid grid-cols-2 gap-[2px]">{['#00f5d4', '#48cae4', '#c77dff', '#ffd166'].map((c) => <span key={c} className="w-2 h-2 rounded-[2px]" style={{ background: c }} />)}</span>
          ModCity
        </Link>
        <div className="hidden sm:flex items-center gap-6 text-[13px]">
          {TABS.map(([href, label]) => {
            const active = pathname === href
            return <Link key={href} href={href} className={active ? 'text-white font-semibold' : 'text-white/60 hover:text-white transition'}>{label}</Link>
          })}
        </div>
        <Link href="/build" className="text-[13px] font-semibold px-3.5 py-1.5 rounded-lg bg-white text-black hover:bg-white/90 transition">Start building →</Link>
      </div>
    </nav>
  )
}

export function Footer() {
  return (
    <footer className="border-t border-white/5 py-10">
      <div className="max-w-7xl mx-auto px-5 flex flex-col sm:flex-row items-center justify-between gap-3 text-[12px] text-white/40">
        <div>ModCity — modular housing & cities · buildings stored content-addressed via <span className="font-mono text-white/60">localfs</span> · served at <span className="font-mono text-white/60">/modcity</span></div>
        <div className="flex gap-4 font-mono"><a href="/modcity/api" className="hover:text-white">/api</a><a href="/modcity/api/catalog" className="hover:text-white">/catalog</a><a href="/modcity/api/designs" className="hover:text-white">/designs</a></div>
      </div>
    </footer>
  )
}

export function Reveal({ children, className = '', delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current; if (!el) return
    const ob = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); ob.disconnect() } }, { threshold: 0.1 })
    ob.observe(el); return () => ob.disconnect()
  }, [])
  return <div ref={ref} className={className} style={{ opacity: seen ? 1 : 0, transform: seen ? 'none' : 'translateY(26px)', transition: `all .8s cubic-bezier(.16,1,.3,1) ${delay}ms` }}>{children}</div>
}

export function Mini({ v, l }: { v: string; l: string }) {
  return <div className="bg-white/5 rounded-md py-1"><div className="text-[12px] font-semibold leading-none">{v}</div><div className="text-[8px] uppercase tracking-wider text-white/35 mt-0.5">{l}</div></div>
}

export function DesignGlyph({ design, style }: { design: Design; style?: StyleSpec }) {
  const cells = design.cells || []
  return (
    <div className="absolute inset-0 flex items-end justify-center gap-[3px] p-3 opacity-90">
      {cells.slice(0, 10).map((c, i) => (
        <div key={i} className="flex flex-col-reverse gap-[2px]">
          {c.stack.slice(0, 9).map((id, j) => {
            const spec = FALLBACK_CATALOG.find((m) => m.id === id)
            return <span key={j} className="w-2.5 h-2.5 rounded-[1px]" style={{ background: spec?.color || style?.palette[spec?.tone || 'warm'] || '#888' }} />
          })}
        </div>
      ))}
    </div>
  )
}

// Design-card actions. Remixing now lands you on /build with the copy loaded.
export function useDesignActions(owner: string, refresh: () => void) {
  const router = useRouter()

  const remix = useCallback(async (d: Design) => {
    try {
      const copy = await api(`design/${d.id}/copy`, { method: 'POST', body: { owner } })
      router.push(`/build?d=${copy.id}`)
    } catch {}
  }, [owner, router])

  const togglePublish = useCallback(async (d: Design) => {
    try { await api(`design/${d.id}/publish`, { method: 'POST', body: { owner, public: !d.public } }); refresh() } catch {}
  }, [owner, refresh])

  const del = useCallback(async (d: Design) => {
    try { await api(`design/${d.id}?owner=${owner}`, { method: 'DELETE' }); refresh() } catch {}
  }, [owner, refresh])

  const share = useCallback((d: Design) => {
    const link = `${window.location.origin}/modcity/build?${d.cid ? 'cid=' + d.cid : 'd=' + d.id}`
    navigator.clipboard?.writeText(link)
  }, [])

  const download = useCallback(async (d: Design) => {
    try {
      const exp = await api(`design/${d.id}/export`)
      const blob = new Blob([JSON.stringify(exp.doc, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob); const a = document.createElement('a')
      a.href = url; a.download = exp.filename; a.click(); URL.revokeObjectURL(url)
    } catch {}
  }, [])

  return { remix, togglePublish, del, share, download }
}
