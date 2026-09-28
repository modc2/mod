'use client'

// /agent/arena — the arena board standing on its own.
//
// The board's front door is the arena mod: modc2.com/arena/agents (served by
// orbit/arena) frames this page, and the agent console's ARENA tab frames
// that. This route is the actual UI — the same <Arena> component the console
// used to mount inline — with just enough of the console's auth bootstrap to
// know who is looking: the signed-in session is read from the localStorage
// the console writes (same origin, shared storage — an iframe sees the same
// sign-in), re-checked against /whoami, and kept live via storage events so
// signing in over in the console lights this page up without a reload.
//
// Anything that needs the full console (signing in, building an agent) opens
// it in its own tab rather than half-reimplementing it here.

import { useEffect, useState } from 'react'
import Arena from '../components/Arena'
import { API_URL } from '../config'

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? '/agent'
const AUTH_KEY = 'agent_auth'
const TOKEN_TTL_MS = 23 * 3600 * 1000

type AuthInfo = { address: string; token: string; isOwner: boolean }

const tokenFresh = (token: string | null | undefined): boolean => {
  if (!token) return false
  try {
    const b64 = token.replace(/-/g, '+').replace(/_/g, '/')
    const env = JSON.parse(decodeURIComponent(escape(atob(b64))))
    return Date.now() - Number(env.time) * 1000 < TOKEN_TTL_MS
  } catch { return false }
}

const loadAuth = (): AuthInfo | null => {
  try {
    const raw = localStorage.getItem(AUTH_KEY)
    if (!raw) return null
    const v = JSON.parse(raw)
    if (!v?.address || !tokenFresh(v.token)) return null
    return { address: v.address, token: v.token, isOwner: !!v.isOwner }
  } catch { return null }
}

export default function ArenaPage() {
  const [auth, setAuth] = useState<AuthInfo | null>(null)
  const [owner, setOwner] = useState<string | null>(null)

  useEffect(() => {
    // restore the console's session, and follow it live: a sign-in or
    // sign-out in any same-origin tab (the console around this iframe)
    // fires a storage event here.
    const restore = () => {
      const v = loadAuth()
      setAuth(v)
      if (!v) return
      fetch(`${API_URL}/whoami?key=${encodeURIComponent(v.token)}`,
            { signal: AbortSignal.timeout(8000) })
        .then(r => r.json())
        .then(who => {
          if (who?.signed_in) setAuth(a => a ? { ...a, isOwner: !!who.is_owner } : a)
        })
        .catch(() => {})
    }
    restore()
    const onStorage = (e: StorageEvent) => { if (e.key === AUTH_KEY) restore() }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  useEffect(() => {
    fetch(`${API_URL}/owner`, { signal: AbortSignal.timeout(5000) })
      .then(r => r.json())
      .then(d => setOwner(typeof d?.owner === 'string' ? d.owner : ''))
      .catch(() => {})
  }, [])

  // same optimism as the console: signed out on an ownerless box, the API is
  // the gate and a refusal is the answer
  const isHost = auth ? auth.isOwner || owner === '' : true

  const openConsole = () => { window.open(BASE || '/', '_blank', 'noopener') }

  return (
    <main className="h-screen flex flex-col bg-surface-0">
      <div className="flex-1 min-h-0 flex">
        <Arena token={auth?.token} isHost={isHost} address={auth?.address}
          onSignIn={openConsole}
          onNewAgent={openConsole} />
      </div>
    </main>
  )
}
