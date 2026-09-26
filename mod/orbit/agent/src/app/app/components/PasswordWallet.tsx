'use client'

// PasswordWallet — sign in with a password instead of a wallet extension.
//
// The password is never sent anywhere and, unless you ask for it, never
// written anywhere either: it is stretched (scrypt) into a private key in
// the tab, that key signs the sign-in message, and when the tab closes the
// wallet is gone until the password is typed again. Same password, same
// address, on any machine — which is the whole feature, and also the whole
// risk, so the form says both: a weak password is a weak key, and a
// forgotten one is an identity nobody can restore.
//
// Used from two places (the header's sign-in menu and the AuthGate modal),
// so it carries no chrome of its own beyond the field.
import { useState } from 'react'
import { isRecoveryPhrase } from '../lib/localWallet'

type Props = {
  onSubmit: (secret: string, remember: boolean) => void
  onCancel?: () => void
  busy?: boolean
  /** deriving takes a beat — say so rather than looking hung */
  deriving?: boolean
}

export default function PasswordWallet({ onSubmit, onCancel, busy, deriving }: Props) {
  const [secret, setSecret] = useState('')
  const [show, setShow] = useState(false)
  const [remember, setRemember] = useState(false)

  const text = secret.trim()
  const phrase = text.split(/\s+/).length >= 12 && isRecoveryPhrase(text)
  // a passing bar, not a score: length is what buys work against a guesser
  const weak = !!text && !phrase && text.length < 12
  const go = () => { if (text && !busy) onSubmit(text, remember) }

  return (
    <div className="p-2.5 space-y-2 border-t border-white/[0.06]">
      <div className="relative">
        <input
          autoFocus
          type={show ? 'text' : 'password'}
          value={secret}
          onChange={e => setSecret(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') go() }}
          placeholder="password, or a 12-word recovery phrase"
          spellCheck={false}
          autoComplete="off"
          className="w-full pl-2.5 pr-12 py-2 rounded-md bg-black/25 border border-white/10 text-xs text-gray-100 placeholder:text-gray-600 font-mono focus:outline-none focus:border-emerald-500/40"
        />
        <button
          type="button"
          onClick={() => setShow(v => !v)}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[9px] uppercase tracking-wider px-1.5 py-1 rounded text-gray-500 hover:text-gray-300"
        >
          {show ? 'hide' : 'show'}
        </button>
      </div>

      <label className="flex items-start gap-2 cursor-pointer select-none">
        <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)}
          className="mt-0.5 accent-emerald-500" />
        <span className="text-[10px] leading-relaxed text-gray-500">
          keep the key in this browser
          <span className="block text-gray-600">
            {remember
              ? 'signs you in on every visit — the key sits in this browser (your password itself is never stored).'
              : 'nothing is saved: the wallet is rebuilt from the password each time you type it, and closing the tab signs you out.'}
          </span>
        </span>
      </label>

      <div className="text-[10px] leading-relaxed text-gray-600">
        {phrase
          ? <span className="text-emerald-300/80">Recognised as a recovery phrase — this restores the wallet it came from.</span>
          : weak
            ? <span className="text-amber-400/90">Short password. The address is public, so anyone can guess against it — use a long one.</span>
            : 'The same password always makes the same address. Nobody can reset it for you.'}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={go}
          disabled={!text || busy}
          className="flex-1 px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-45 transition"
        >
          {deriving ? 'Deriving key…' : busy ? 'Signing…' : 'Sign in'}
        </button>
        {onCancel && (
          <button onClick={onCancel}
            className="px-2.5 py-1.5 rounded-md text-[10px] border border-white/10 text-gray-500 hover:text-gray-300 transition">
            back
          </button>
        )}
      </div>
    </div>
  )
}
