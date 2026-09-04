'use client'

// AuthGate — the one sign-in prompt, raised at the moment something needs
// an identity instead of after it has already refused. Anything in the
// console can await requireAuth(need) (page.tsx): if the current session
// already satisfies the need it resolves at once, otherwise this modal
// opens, sign-in happens right here (wallet popup or a browser-local key),
// the need is re-checked against the fresh /whoami answer, and the blocked
// action continues. Nobody is sent outside the app to authenticate.
//
// Two kinds of need, because they gate differently:
//   signin  — any signed identity will do (saving, creating, credits)
//   harness — the run leaves this module for a CLI on the host's own shell,
//             so only the host or the address the harness's console vouches
//             for passes; a browser-local pseudonym can never qualify, so
//             that path is not offered for it.

export type AuthNeed =
  | { kind: 'signin'; reason?: string }
  | { kind: 'harness'; harness: string; agentLabel?: string }

type GateAuth = { address: string; isOwner: boolean; local?: boolean; harnesses?: string[] }

type Props = {
  ask: AuthNeed | null
  auth: GateAuth | null      // set but unsatisfying = "signed in, not vouched"
  busy: boolean
  err: string | null
  onWallet: () => void
  onLocal: () => void
  onCancel: () => void
  onFallback?: () => void    // harness only: run a native agent instead
}

const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

const Lock = ({ size = 16, className = '' }: { size?: number; className?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </svg>
)

export default function AuthGate({ ask, auth, busy, err, onWallet, onLocal, onCancel, onFallback }: Props) {
  if (!ask) return null
  const harness = ask.kind === 'harness' ? ask.harness : null
  const hasWallet = typeof window !== 'undefined' && !!(window as any).ethereum
  // signed in already, so the plain fact of an identity isn't the problem —
  // this address just doesn't carry the standing the harness wants
  const unvouched = !!(harness && auth)

  return (
    <div className="fixed inset-0 z-[110] bg-black/70 backdrop-blur-sm flex items-center justify-center p-5"
      onClick={onCancel}>
      <div onClick={e => e.stopPropagation()}
        className="w-full max-w-sm bg-surface-2 border border-white/10 rounded-xl shadow-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-white/[0.06] flex items-start gap-2.5">
          <Lock className={`shrink-0 mt-0.5 ${harness ? 'text-violet-300' : 'text-emerald-300'}`} />
          <div className="min-w-0">
            <div className="text-sm text-gray-100 font-medium">
              {harness
                ? `${(ask as any).agentLabel || 'This agent'} needs the host's sign-in`
                : 'Sign in to continue'}
            </div>
            <div className="text-[11px] text-gray-500 mt-1 leading-relaxed">
              {harness ? (
                unvouched ? (
                  <>You&apos;re signed in as <span className="font-mono text-gray-400">{short(auth!.address)}</span>,
                    which isn&apos;t vouched for the <span className="text-violet-300/90">{harness}</span> harness.
                    The run goes to a CLI on the host&apos;s own shell, so it takes the host&apos;s wallet — or one
                    the {harness} console&apos;s owner has. Switch accounts in your wallet and sign again, right here.</>
                ) : (
                  <>It hands the whole run to the <span className="text-violet-300/90">{harness}</span> CLI on the
                    host&apos;s own shell, so it&apos;s held to the host and that console&apos;s owner. If that&apos;s
                    you, sign in with your wallet without leaving this page.</>
                )
              ) : (
                (ask as any).reason || 'This needs a signed identity so your work is filed under your address.'
              )}
            </div>
          </div>
        </div>

        <div className="p-3 space-y-2">
          {err && <div className="text-[11px] text-red-400 leading-relaxed">{err}</div>}
          <button onClick={onWallet} disabled={busy || !hasWallet}
            className="w-full px-3 py-2 rounded-md text-xs font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-50 transition text-left">
            {busy ? 'Waiting for your wallet…'
              : unvouched ? 'Sign in with a different wallet account'
              : 'Sign in with wallet'}
            <span className="block text-[10px] font-normal text-emerald-200/50 mt-0.5">
              {hasWallet
                ? 'Opens your wallet to sign a message — no transaction, nothing leaves this page.'
                : 'No browser wallet found — install MetaMask to use this.'}
            </span>
          </button>
          {/* a browser-local key is a device pseudonym: fine as an identity,
              structurally never the host — so it isn't offered for a harness */}
          {!harness && (
            <button onClick={onLocal} disabled={busy}
              className="w-full px-3 py-2 rounded-md text-xs font-medium border border-white/10 text-gray-300 hover:text-white hover:border-white/25 disabled:opacity-50 transition text-left">
              Use a browser key instead
              <span className="block text-[10px] font-normal text-gray-600 mt-0.5">
                A keypair made and kept in this browser — no extension needed.
              </span>
            </button>
          )}
        </div>

        <div className="px-3 py-2 border-t border-white/[0.06] flex items-center gap-2">
          {harness && onFallback && (
            <button onClick={onFallback}
              className="text-[10px] px-2 py-1 rounded-md border border-violet-400/25 text-violet-200/90 hover:bg-violet-400/10 transition">
              use a native agent instead
            </button>
          )}
          <button onClick={onCancel}
            className="ml-auto text-[10px] px-2 py-1 rounded-md border border-white/10 text-gray-500 hover:text-gray-300 transition">
            not now
          </button>
        </div>
      </div>
    </div>
  )
}
