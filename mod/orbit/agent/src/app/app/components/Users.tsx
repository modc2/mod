'use client'

// User management — lives inside the ACCOUNT drawer, owner only.
//
// Two lists, because the module has exactly two kinds of standing to hand
// out. A CO-OWNER passes every owner gate and spends the owner's credit
// account — adding one is handing over the module, so only the primary
// owner can change that list (the server enforces it; the UI just says so).
// An ACCESS grant is the narrow one: a named address may run the agent (or
// hold full admin) without owner standing and without prepaying credits.
//
// The component owns its own data: it reads /owners and /acl when it opens
// and re-reads after every change, so the drawer never caches a stale roster.

import { useCallback, useEffect, useState } from 'react'
import { API_URL } from '../config'

export type UsersAuth = { address: string; token: string; isOwner: boolean }

type Grant = { actions: string[]; granted_by?: string }

const isAddr = (s: string) => /^0x[0-9a-fA-F]{40}$/.test(s.trim())
const shortAddr = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

export default function Users({ auth }: { auth: UsersAuth }) {
  const [owner, setOwner] = useState<string | null>(null)
  const [coOwners, setCoOwners] = useState<string[]>([])
  const [grants, setGrants] = useState<Record<string, Grant>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [coAddr, setCoAddr] = useState('')
  const [grantAddr, setGrantAddr] = useState('')
  const [grantScope, setGrantScope] = useState<'run' | 'admin'>('run')
  // adding a co-owner is handing over the module — one click is not enough
  const [confirmCo, setConfirmCo] = useState(false)

  const isPrimary = !!owner && auth.address.toLowerCase() === owner.toLowerCase()
  const q = `?key=${encodeURIComponent(auth.token)}`

  const refresh = useCallback(() => {
    setLoading(true)
    Promise.all([
      fetch(`${API_URL}/owners${q}`, { signal: AbortSignal.timeout(8000) })
        .then(r => r.json()).catch(() => null),
      fetch(`${API_URL}/acl${q}`, { signal: AbortSignal.timeout(8000) })
        .then(r => r.json()).catch(() => null),
    ]).then(([own, acl]) => {
      if (own && !own.error) {
        setOwner(own.owner || null)
        setCoOwners(own.co_owners || [])
      }
      if (acl && !acl.error) setGrants(acl.grants || {})
      const e = own?.error || acl?.error
      setErr(e ? String(e) : null)
    }).finally(() => setLoading(false))
  }, [q])

  useEffect(() => { refresh() }, [refresh])

  // every mutation goes through here: POST, surface the error, re-read
  const post = async (path: string, body: Record<string, unknown>) => {
    setBusy(true); setErr(null)
    try {
      const r = await fetch(`${API_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, key: auth.token }),
        signal: AbortSignal.timeout(10000),
      }).then(x => x.json())
      if (r?.error) { setErr(String(r.error)); return false }
      return true
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'request failed')
      return false
    } finally {
      setBusy(false)
      refresh()
    }
  }

  const addCoOwner = async () => {
    const a = coAddr.trim()
    if (!isAddr(a)) { setErr('a 0x address is required'); return }
    if (await post('/owners', { op: 'add', address: a })) { setCoAddr(''); setConfirmCo(false) }
  }
  const rmCoOwner = (a: string) => void post('/owners', { op: 'rm', address: a })

  const addGrant = async () => {
    const a = grantAddr.trim()
    if (!isAddr(a)) { setErr('a 0x address is required'); return }
    const actions = grantScope === 'admin' ? ['*'] : ['run', 'tool_run']
    if (await post('/grant', { address: a, actions })) setGrantAddr('')
  }
  const revoke = (a: string) => void post('/revoke', { address: a })

  const head = (label: string, note: string) => (
    <div className="pt-3 pb-1.5">
      <div className="text-[10px] text-gray-400 uppercase tracking-wider font-medium">{label}</div>
      <div className="text-[9.5px] text-gray-600 leading-relaxed mt-0.5">{note}</div>
    </div>
  )

  const row = (addr: string, tag: string, onRemove?: () => void, removeTitle?: string) => (
    <div key={addr}
      className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-black/20 border border-white/[0.06]">
      <span className="font-mono text-[11px] text-gray-200 truncate" title={addr}>{shortAddr(addr)}</span>
      {addr.toLowerCase() === auth.address.toLowerCase() && (
        <span className="text-[9px] text-emerald-400 shrink-0">you</span>
      )}
      <span className="ml-auto text-[9px] text-gray-500 shrink-0">{tag}</span>
      {onRemove && (
        <button onClick={onRemove} disabled={busy} title={removeTitle}
          className="w-5 h-5 flex items-center justify-center rounded text-gray-600 hover:text-red-300 hover:bg-red-500/10 disabled:opacity-40 transition shrink-0">
          ✕
        </button>
      )}
    </div>
  )

  const addrInput = (value: string, set: (v: string) => void, placeholder: string) => (
    <input
      value={value}
      onChange={e => set(e.target.value)}
      placeholder={placeholder}
      spellCheck={false}
      className="flex-1 min-w-0 bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-[11px] font-mono text-gray-200 placeholder:text-gray-600 outline-none focus:border-emerald-500/40 transition"
    />
  )

  if (loading && !owner) {
    return <div className="px-3 py-3 text-[10px] text-gray-600">loading users…</div>
  }

  return (
    <div className="px-3 pb-3">
      {/* ── co-owners: owner standing, shared credits ── */}
      {head('Co-owners', 'pass every owner gate and spend your credit account')}
      <div className="flex flex-col gap-1">
        {owner && row(owner, 'owner')}
        {coOwners.map(a => row(a, 'co-owner',
          isPrimary ? () => rmCoOwner(a) : undefined,
          'Remove co-owner — they lose owner standing immediately'))}
        {!coOwners.length && (
          <div className="px-2 py-1 text-[10px] text-gray-600">no co-owners</div>
        )}
      </div>
      {isPrimary ? (
        <div className="mt-1.5">
          <div className="flex gap-1.5">
            {addrInput(coAddr, v => { setCoAddr(v); setConfirmCo(false) }, '0x… add co-owner')}
            <button
              onClick={() => (confirmCo ? void addCoOwner() : setConfirmCo(true))}
              disabled={busy || !isAddr(coAddr)}
              className={`px-2.5 py-1.5 rounded-md text-[10px] border transition shrink-0 disabled:opacity-40 ${
                confirmCo
                  ? 'border-amber-500/50 text-amber-200 hover:bg-amber-500/15'
                  : 'border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/15'
              }`}>
              {confirmCo ? 'CONFIRM' : 'ADD'}
            </button>
          </div>
          {confirmCo && (
            <div className="mt-1 text-[9.5px] text-amber-300/80 leading-relaxed">
              A co-owner can do everything you can except manage this list —
              and their runs spend your provider keys.
            </div>
          )}
        </div>
      ) : (
        <div className="mt-1 text-[9.5px] text-gray-600 leading-relaxed">
          Only the primary owner can add or remove co-owners.
        </div>
      )}

      {/* ── access grants: run rights without owner standing ── */}
      {head('Access', 'may run the agent without owner standing or prepaid credits')}
      <div className="flex flex-col gap-1">
        {Object.entries(grants).map(([a, g]) => (
          <div key={a}
            className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-black/20 border border-white/[0.06]">
            <span className="font-mono text-[11px] text-gray-200 truncate" title={a}>{shortAddr(a)}</span>
            <span className="ml-auto text-[9px] text-gray-500 shrink-0">
              {(g.actions || []).includes('*') ? 'full admin' : (g.actions || []).join(' · ')}
            </span>
            <button onClick={() => revoke(a)} disabled={busy}
              title="Revoke access"
              className="w-5 h-5 flex items-center justify-center rounded text-gray-600 hover:text-red-300 hover:bg-red-500/10 disabled:opacity-40 transition shrink-0">
              ✕
            </button>
          </div>
        ))}
        {!Object.keys(grants).length && (
          <div className="px-2 py-1 text-[10px] text-gray-600">no grants — guests run on their own credits</div>
        )}
      </div>
      <div className="mt-1.5 flex gap-1.5">
        {addrInput(grantAddr, setGrantAddr, '0x… grant access')}
        <select
          value={grantScope}
          onChange={e => setGrantScope(e.target.value as 'run' | 'admin')}
          className="bg-black/30 border border-white/10 rounded-md px-1.5 py-1.5 text-[10px] text-gray-300 outline-none focus:border-emerald-500/40 transition shrink-0">
          <option value="run">run</option>
          <option value="admin">admin</option>
        </select>
        <button onClick={() => void addGrant()} disabled={busy || !isAddr(grantAddr)}
          className="px-2.5 py-1.5 rounded-md text-[10px] border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/15 disabled:opacity-40 transition shrink-0">
          GRANT
        </button>
      </div>

      {err && (
        <div className="mt-2 px-2 py-1.5 rounded-md bg-red-500/10 border border-red-500/20 text-[10px] text-red-300 leading-relaxed break-words">
          {err}
        </div>
      )}
    </div>
  )
}
