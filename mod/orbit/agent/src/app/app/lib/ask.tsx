'use client'

/* ask() — the in-app stand-in for window.confirm().
 *
 * The native dialog is off-brand, blocks the whole tab, is titled with the
 * host name ("modc2.com says") and can be silenced by the browser. This is
 * the same one-liner, awaited:
 *
 *   if (!(await ask({ title: 'Forget "x"?', ok: 'Forget', danger: true }))) return
 *
 * One <AskHost/> is mounted in the root layout; ask() resolves true on the
 * confirm button / Enter, false on cancel / Esc / backdrop. A second ask()
 * while one is open cancels the first so nothing is left dangling. */

import { useEffect, useRef, useState } from 'react'

export type AskOpts = {
  title: string
  body?: string
  ok?: string       // confirm label, default "OK"
  cancel?: string   // cancel label, default "Cancel"
  danger?: boolean  // red confirm button for destructive actions
}

type Pending = AskOpts & { resolve: (v: boolean) => void }

let show: ((p: Pending | null) => void) | null = null
let current: Pending | null = null

export function ask(opts: AskOpts | string): Promise<boolean> {
  const o = typeof opts === 'string' ? { title: opts } : opts
  return new Promise(resolve => {
    // no host mounted (shouldn't happen) — fail safe: don't do the thing
    if (!show) { resolve(false); return }
    current?.resolve(false)
    current = { ...o, resolve }
    show(current)
  })
}

export function AskHost() {
  const [p, setP] = useState<Pending | null>(null)
  const okRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    show = setP
    return () => { if (show === setP) show = null }
  }, [])

  const done = (v: boolean) => {
    p?.resolve(v)
    if (current === p) current = null
    setP(null)
  }

  useEffect(() => {
    if (!p) return
    okRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); done(false) }
      else if (e.key === 'Enter') { e.preventDefault(); done(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p])

  if (!p) return null
  return (
    <div className="fixed inset-0 z-[200] bg-black/70 backdrop-blur-sm flex items-center justify-center p-5"
      onClick={() => done(false)}>
      <div role="alertdialog" aria-modal="true" aria-label={p.title} onClick={e => e.stopPropagation()}
        className="w-full max-w-sm bg-surface-2 border border-white/10 rounded-xl shadow-2xl overflow-hidden">
        <div className="px-4 pt-3.5 pb-3">
          <div className="text-sm text-gray-100 font-medium break-words">{p.title}</div>
          {p.body && <div className="text-[11px] text-gray-500 mt-1.5 leading-relaxed">{p.body}</div>}
        </div>
        <div className="px-3 py-2.5 border-t border-white/[0.06] flex items-center justify-end gap-2">
          <button onClick={() => done(false)}
            className="px-3 py-1.5 rounded-lg text-[11px] text-gray-400 hover:text-gray-200 hover:bg-white/[0.05] transition">
            {p.cancel || 'Cancel'}
          </button>
          <button ref={okRef} onClick={() => done(true)}
            className={`px-3.5 py-1.5 rounded-lg text-[11px] font-medium text-white transition ${
              p.danger ? 'bg-red-600/90 hover:bg-red-500' : 'bg-emerald-600/90 hover:bg-emerald-500'}`}>
            {p.ok || 'OK'}
          </button>
        </div>
      </div>
    </div>
  )
}
