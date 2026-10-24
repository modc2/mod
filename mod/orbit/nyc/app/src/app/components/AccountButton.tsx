'use client'

import { useCallback, useEffect, useState } from 'react'
import { whoami, type WhoAmI } from '@/lib/api'
import { onAuthChange, signIn, signOut, storedToken } from '@/lib/auth'
import { Coin } from './Sprites'

type Props = {
  /** Phone drawer row instead of the HUD button + popover. */
  rail?: boolean
}

/**
 * The owner's sign-in, as one control: a wallet signature mints the same
 * mod-protocol token the rest of the fleet verifies, and /whoami says what
 * the signer is here. The server decides ownership — this button only shows
 * the answer. Signing in anywhere (here, YOUR DATA) updates everywhere:
 * state rides on the shared token + the auth-change event, not on props.
 */
export default function AccountButton({ rail = false }: Props) {
  const [who, setWho] = useState<WhoAmI | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Starts signed-out on purpose: localStorage does not exist in the server
  // render, so the first client render must agree with it and catch up here.
  const sync = useCallback(() => {
    const token = storedToken()
    if (!token) { setWho(null); setOpen(false); return }
    whoami(token).then(setWho).catch(() => setWho(null))
  }, [])
  useEffect(() => { sync(); return onAuthChange(sync) }, [sync])

  const connect = async () => {
    setError('')
    setBusy(true)
    try {
      await signIn() // announces; sync() fills `who` from /whoami
    } catch (e: any) {
      setError(String(e?.message ?? e).slice(0, 120))
    } finally {
      setBusy(false)
    }
  }

  const address = who?.address ?? null

  if (rail) {
    return (
      <div className="border-b border-white/10 px-3 py-2.5 text-[11px]">
        {address ? (
          <div className="flex items-center gap-2">
            {who?.is_owner && <Coin size={12} />}
            <span className="min-w-0 flex-1 truncate text-nes-ink2">
              {who?.is_owner ? 'Owner · ' : ''}{short(address)}
            </span>
            <button onClick={signOut} className="tap text-nes-ink3 underline hover:text-nes-ink2">
              sign out
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-nes-ink3">
              Own this deployment?
            </span>
            <button onClick={connect} disabled={busy}
                    className="btn pixel tap shrink-0 px-2.5 py-2 text-[10px]">
              {busy ? '...' : 'SIGN IN'}
            </button>
          </div>
        )}
        {error && <p className="mt-1.5 text-nes-red">{error}</p>}
      </div>
    )
  }

  return (
    <div className="relative">
      <button
        onClick={() => (address ? setOpen((v) => !v) : connect())}
        aria-expanded={address ? open : undefined}
        title={address ?? 'Sign in with your wallet'}
        className={`btn pixel tap flex items-center gap-1.5 px-2.5 py-3 text-[11px] md:px-3
                    ${who?.is_owner ? 'btn-on' : ''}`}
      >
        {who?.is_owner && <Coin size={11} />}
        {busy ? '...' : address ? short(address).toUpperCase() : 'SIGN IN'}
      </button>

      {open && address && (
        <div className="blk absolute right-0 top-[calc(100%+8px)] z-50 w-[248px] px-3.5 py-3 text-left">
          <p className="pixel text-[9px] uppercase tracking-wide text-nes-ink3">Signed in as</p>
          <p className="mt-1.5 break-all text-[11px] leading-snug text-white">{address}</p>
          <p className="mt-2.5 text-[11.5px] leading-relaxed text-nes-ink2">
            {who?.is_owner ? (
              <>
                <span className="text-nes-coin">Deployment owner.</span>{' '}
                You can save datasets as layers under YOUR DATA, and the ASK
                agent can save them for you when you ask.
              </>
            ) : (
              <>
                Reading is open to everyone; saving data belongs to the owner
                {who?.owner ? <> ({short(who.owner)})</> : null}. This
                signature identifies you — it does not grant writes.
              </>
            )}
          </p>
          <button
            onClick={() => { signOut(); setOpen(false) }}
            className="btn pixel tap mt-3 px-2.5 py-2 text-[10px]"
          >
            SIGN OUT
          </button>
        </div>
      )}
      {error && !address && (
        <p className="blk absolute right-0 top-[calc(100%+8px)] z-50 w-[220px] px-3 py-2 text-[11px] text-nes-red">
          {error}
        </p>
      )}
    </div>
  )
}

function short(a: string): string {
  return `${a.slice(0, 6)}…${a.slice(-4)}`
}
