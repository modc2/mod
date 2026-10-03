'use client'

// A lightweight, client-only record of what *this browser* has done in the
// console — wallets made, addresses generated, ZEC sent, bridges opened,
// searches run. It is deliberately local: nothing here is authoritative chain
// state, it is a convenience timeline so a reader can see and re-copy the
// things they just did without hunting back through tabs.

import { useEffect, useState } from 'react'
import { C } from './ui'

export type ActKind =
  | 'search' | 'wallet' | 'address' | 'send' | 'shielded' | 'bridge' | 'reveal' | 'import'

export interface Act {
  id: string
  kind: ActKind
  title: string        // one line, what happened
  detail?: string      // secondary line (address, wallet name, note…)
  ref?: string         // a txid / address / order id worth re-copying
  ts: number
}

const KEY = 'zcash_activity_v1'
const EVT = 'zcash-activity'
const MAX = 120

function read(): Act[] {
  if (typeof window === 'undefined') return []
  try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] }
}

function write(list: Act[]) {
  if (typeof window === 'undefined') return
  localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)))
  window.dispatchEvent(new Event(EVT))
}

// Record something the user did. Safe to call from anywhere; de-dupes a rapid
// exact repeat (same kind+title+ref within 3s) so a double-click is one line.
export function logActivity(a: Omit<Act, 'id' | 'ts'>) {
  const list = read()
  const now = Date.now()
  const dupe = list[0]
  if (dupe && dupe.kind === a.kind && dupe.title === a.title
    && dupe.ref === a.ref && now - dupe.ts < 3000) return
  write([{ ...a, id: `${now}-${Math.random().toString(36).slice(2, 7)}`, ts: now }, ...list])
}

export function clearActivity() { write([]) }

export function useActivity(): Act[] {
  const [list, setList] = useState<Act[]>([])
  useEffect(() => {
    const sync = () => setList(read())
    sync()
    window.addEventListener(EVT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(EVT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])
  return list
}

// ── presentation ─────────────────────────────────────────────────────────────

const META: Record<ActKind, { label: string, color: string, glyph: string }> = {
  search:   { label: 'Search',  color: C.blue,      glyph: '⌕' },
  wallet:   { label: 'Wallet',  color: C.gold,      glyph: '◈' },
  address:  { label: 'Address', color: C.gold,      glyph: '＋' },
  send:     { label: 'Send',    color: C.green,     glyph: '↗' },
  shielded: { label: 'Shielded', color: '#a78bfa',  glyph: '❋' },
  bridge:   { label: 'Bridge',  color: C.blue,      glyph: '⇄' },
  reveal:   { label: 'Reveal',  color: C.red,       glyph: '◉' },
  import:   { label: 'Import',  color: C.gold,      glyph: '↓' },
}

function relTime(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000)
  if (s < 45) return 'just now'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d ago`
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function Row({ a }: { a: Act }) {
  const m = META[a.kind]
  const [copied, setCopied] = useState(false)
  const copy = () => {
    if (!a.ref) return
    navigator.clipboard?.writeText(a.ref)
    setCopied(true); setTimeout(() => setCopied(false), 1200)
  }
  return (
    <div
      onClick={copy}
      title={a.ref ? 'click to copy' : undefined}
      style={{
        display: 'flex', gap: 10, padding: '10px 4px',
        borderTop: `1px solid ${C.line}`,
        cursor: a.ref ? 'pointer' : 'default',
      }}>
      <div style={{
        flexShrink: 0, width: 24, height: 24, borderRadius: 6, marginTop: 1,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: `${m.color}1c`, color: m.color, fontSize: 13, fontWeight: 700,
      }}>{m.glyph}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline',
        }}>
          <span style={{ fontSize: 12.5, color: C.text, fontWeight: 600 }}>{a.title}</span>
          <span style={{ fontSize: 10, color: C.dim, whiteSpace: 'nowrap' }}>{relTime(a.ts)}</span>
        </div>
        {a.detail && (
          <div style={{
            fontSize: 11, color: C.dim, marginTop: 2, overflow: 'hidden',
            textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            fontFamily: a.ref ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
          }}>{a.detail}</div>
        )}
        {copied && <div style={{ fontSize: 10, color: C.green, marginTop: 2 }}>copied ref</div>}
      </div>
    </div>
  )
}

// The panel body — reused by the docked sidebar and the mobile drawer.
export function ActivityList({ compact }: { compact?: boolean }) {
  const list = useActivity()
  // Tick so relative times stay fresh while the panel is open.
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30000)
    return () => clearInterval(id)
  }, [])

  const [filter, setFilter] = useState<ActKind | 'all'>('all')
  const kinds = Array.from(new Set(list.map(a => a.kind)))
  const shown = filter === 'all' ? list : list.filter(a => a.kind === filter)

  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 12,
      }}>
        <h2 style={{
          margin: 0, fontSize: 12, letterSpacing: 1.4, textTransform: 'uppercase',
          color: C.dim, fontWeight: 600,
        }}>Activity</h2>
        {list.length > 0 && (
          <button onClick={() => clearActivity()} style={{
            background: 'transparent', border: `1px solid ${C.line}`, color: C.dim,
            borderRadius: 4, fontSize: 10, padding: '2px 8px', cursor: 'pointer',
          }}>clear</button>
        )}
      </div>

      {list.length === 0 ? (
        <div style={{
          fontSize: 12, color: C.dim, lineHeight: 1.6, padding: '18px 0',
          textAlign: 'center',
        }}>
          Nothing yet.<br />
          <span style={{ opacity: 0.75 }}>
            Searches, wallets, sends and bridges you run here show up in this
            timeline.
          </span>
        </div>
      ) : (
        <>
          {kinds.length > 1 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 6 }}>
              {(['all', ...kinds] as (ActKind | 'all')[]).map(k => {
                const on = filter === k
                const label = k === 'all' ? 'all' : META[k].label.toLowerCase()
                const col = k === 'all' ? C.gold : META[k].color
                return (
                  <button key={k} onClick={() => setFilter(k)} style={{
                    padding: '3px 9px', borderRadius: 999, fontSize: 10.5, cursor: 'pointer',
                    background: on ? `${col}22` : 'transparent',
                    border: `1px solid ${on ? col : C.line}`,
                    color: on ? col : C.dim, textTransform: 'lowercase',
                  }}>{label}</button>
                )
              })}
            </div>
          )}
          <div style={{ maxHeight: compact ? undefined : '70vh', overflowY: 'auto' }}>
            {shown.map(a => <Row key={a.id} a={a} />)}
          </div>
        </>
      )}
    </div>
  )
}

// Docked panel for wide viewports — sticky so it trails the reader down the page.
export function ActivitySidebar() {
  return (
    <aside style={{
      width: 300, flexShrink: 0, position: 'sticky', top: 20, alignSelf: 'flex-start',
      background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: 16,
    }}>
      <ActivityList />
    </aside>
  )
}

// Floating button + slide-over drawer for narrow viewports.
export function ActivityDrawer() {
  const [open, setOpen] = useState(false)
  const list = useActivity()
  return (
    <>
      <button onClick={() => setOpen(true)} style={{
        position: 'fixed', right: 16, bottom: 16, zIndex: 40,
        display: 'flex', alignItems: 'center', gap: 8, padding: '11px 16px',
        borderRadius: 999, border: 'none', cursor: 'pointer',
        background: C.gold, color: '#12141a', fontSize: 13, fontWeight: 700,
        boxShadow: '0 6px 20px rgba(0,0,0,0.45)',
      }}>
        Activity
        {list.length > 0 && (
          <span style={{
            background: '#12141a22', borderRadius: 999, padding: '1px 7px', fontSize: 11,
          }}>{list.length}</span>
        )}
      </button>
      {open && (
        <div onClick={() => setOpen(false)} style={{
          position: 'fixed', inset: 0, zIndex: 50, background: 'rgba(0,0,0,0.55)',
          display: 'flex', justifyContent: 'flex-end',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: 'min(360px, 90vw)', height: '100%', overflowY: 'auto',
            background: C.panel, borderLeft: `1px solid ${C.line}`, padding: 18,
          }}>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
              <button onClick={() => setOpen(false)} style={{
                background: 'transparent', border: 'none', color: C.dim,
                fontSize: 20, cursor: 'pointer', lineHeight: 1,
              }}>×</button>
            </div>
            <ActivityList compact />
          </div>
        </div>
      )}
    </>
  )
}

export function useWide(breakpoint = 1120) {
  const [wide, setWide] = useState(true)
  useEffect(() => {
    const on = () => setWide(window.innerWidth >= breakpoint)
    on()
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [breakpoint])
  return wide
}
