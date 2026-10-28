"use client";

import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'react-toastify'
import { getStarkKey, poseidonHashMany, sign, utils as starkUtils } from '@scure/starknet'
import {
  EyeSlashIcon, KeyIcon, LockClosedIcon, LockOpenIcon, GiftIcon, ArrowPathIcon,
  ArrowUpTrayIcon, ArrowDownTrayIcon, PaperAirplaneIcon, ShieldCheckIcon, ClockIcon,
  ChevronDownIcon, DocumentDuplicateIcon,
} from '@heroicons/react/24/outline'

// PRIVATE — ShadowBloc, the Cairo twin of BlocTime (cairo/src/shadowbloc.cairo).
//
// Same economics as the Stake tab (BLOC = dollars x seconds, weekly Friday
// pot), but a position belongs to a one-time Stark key made right here in the
// browser instead of to a wallet address. The server only relays: it posts the
// stake from its own account and forwards signatures it cannot forge. The
// private keys live in localStorage (and in the backup you download) — never
// on the server, never on chain.

type Api = (fn: string, params?: Record<string, any>, method?: string) => Promise<any>

interface ShadowInfo {
  deployment: { network: string; shadow: string; token: string; relayer: string; payee: string }
  chainId: string
  domain: string
  denoms: string[]
  tiers: number[]
  quotes: string[][]
  buckets: number[][]
  stats: {
    totalStaked: string; totalBloc: string; openNotes: number; noteCount: number
    pot: string; distributed: string; nextDistribution: number; priceUsdMicro: number; now: number
  }
}

interface ChainNote {
  id: number; key: string; denom: number; tier: number; amount: string
  start: number; unlock: number; bloc: string; nonce: number; open: boolean; pending: string
}

interface LocalNote { id: number; sk: string; pk: string; contract: string; at: number }

const STORE = 'bloctime_shadow_notes'
const PAYOUT = 'bloctime_shadow_payout'
const ONE = 10n ** 18n

// ── tiny helpers ────────────────────────────────────────────────────────

const strFelt = (s: string) => BigInt('0x' + Array.from(new TextEncoder().encode(s)).map(b => b.toString(16).padStart(2, '0')).join(''))
const hex = (n: bigint) => '0x' + n.toString(16)
const short = (h: string, n = 6) => h.length > 2 * n + 2 ? `${h.slice(0, n + 2)}…${h.slice(-n)}` : h

function newKey(): { sk: string; pk: string } {
  const sk = hex(BigInt('0x' + Array.from(starkUtils.randomPrivateKey()).map(b => b.toString(16).padStart(2, '0')).join('')))
  return { sk, pk: getStarkKey(sk) }
}

function compact(wei: string | bigint, digits = 2): string {
  const v = Number(BigInt(wei) / 10n ** 12n) / 1e6
  if (!isFinite(v)) return '--'
  const units: [number, string][] = [[1e15, 'Q'], [1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']]
  for (const [n, u] of units) if (Math.abs(v) >= n) return `${(v / n).toFixed(digits)}${u}`
  return v.toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 4 : 2 })
}

function span(s: number): string {
  const d = Math.round(s / 86400)
  if (d >= 365) return `${Math.round(d / 365)}y`
  if (d >= 30) return `${d}d`
  if (d >= 7) return `${Math.round(d / 7)}w`
  if (d >= 1) return `${d}d`
  const h = Math.round(s / 3600)
  return h >= 1 ? `${h}h` : `${Math.max(0, Math.round(s / 60))}m`
}

function countdown(s: number): string {
  if (s <= 0) return 'now'
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`
}

function loadNotes(): LocalNote[] {
  try { return JSON.parse(localStorage.getItem(STORE) || '[]') } catch { return [] }
}
function saveNotes(n: LocalNote[]) { localStorage.setItem(STORE, JSON.stringify(n)) }

// A portable note: whoever holds this string owns the stake.
const encodeNote = (n: LocalNote) => `shadowbloc:${n.contract}:${n.id}:${n.sk}`
function decodeNote(s: string): LocalNote | null {
  const m = s.trim().match(/^shadowbloc:(0x[0-9a-fA-F]+):(\d+):(0x[0-9a-fA-F]+)$/)
  if (!m) return null
  return { contract: m[1], id: Number(m[2]), sk: m[3], pk: getStarkKey(m[3]), at: Date.now() }
}

function download(name: string, text: string) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}

// ── component ───────────────────────────────────────────────────────────

export default function Private({ api }: { api: Api }) {
  const [info, setInfo] = useState<ShadowInfo | null>(null)
  const [ledger, setLedger] = useState<ChainNote[]>([])
  const [err, setErr] = useState('')
  const [local, setLocal] = useState<LocalNote[]>([])
  const [denom, setDenom] = useState(1)
  const [tier, setTier] = useState(3)
  const [busy, setBusy] = useState<string>('')
  const [payout, setPayout] = useState('')
  const [importText, setImportText] = useState('')
  const [handoff, setHandoff] = useState<string>('')
  const [showHow, setShowHow] = useState(false)
  const [tick, setTick] = useState(0)
  const owner = typeof window !== 'undefined' && !!localStorage.getItem('bloctime_api_token')

  useEffect(() => { setLocal(loadNotes()); setPayout(localStorage.getItem(PAYOUT) || '') }, [])
  useEffect(() => { const t = setInterval(() => setTick(x => x + 1), 1000); return () => clearInterval(t) }, [])

  const refresh = useCallback(async () => {
    try {
      const [i, l] = await Promise.all([api('shadow', {}, 'GET'), api('shadow/notes?limit=100', {}, 'GET')])
      setInfo(i); setLedger(l.notes || []); setErr('')
    } catch (e: any) { setErr(e.message || 'ShadowBloc is unreachable') }
  }, [api])
  useEffect(() => { refresh(); const t = setInterval(refresh, 15000); return () => clearInterval(t) }, [refresh])

  const contract = info?.deployment.shadow || ''
  const mine = useMemo(() => local.filter(n => BigInt(n.contract) === BigInt(contract || '0')), [local, contract])
  const byId = useMemo(() => new Map(ledger.map(n => [n.id, n])), [ledger])
  // chain time keeps moving between polls (and the devnet clock can be ahead of ours)
  const now = info ? info.stats.now + tick : Math.floor(Date.now() / 1000)
  useEffect(() => setTick(0), [info])

  const persist = (next: LocalNote[]) => { setLocal(next); saveNotes(next) }

  // poseidon(domain, contract, action, id, nonce, arg) — identical to ShadowBloc.digest
  const signAction = (n: LocalNote, action: string, nonce: number, arg: string) => {
    const msg = poseidonHashMany([strFelt(info!.domain), BigInt(contract), strFelt(action), BigInt(n.id), BigInt(nonce), BigInt(arg)])
    const sig = sign(hex(msg), n.sk)
    return { r: hex(sig.r), s: hex(sig.s) }
  }

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label)
    try { await fn() } catch (e: any) { toast.error(e.message || `${label} failed`) } finally { setBusy(''); refresh() }
  }

  const createStake = () => run('stake', async () => {
    const k = newKey()
    const r = await api('shadow/stake', { key: k.pk, denom, tier })
    const n: LocalNote = { id: r.id, sk: k.sk, pk: k.pk, contract, at: Date.now() }
    persist([...loadNotes(), n])
    toast.success(`Private note #${r.id} created — back it up`)
  })

  const needPayout = () => {
    const p = payout.trim()
    if (!/^0x[0-9a-fA-F]{1,64}$/.test(p)) throw new Error('Set a Starknet payout address first')
    localStorage.setItem(PAYOUT, p)
    return p
  }

  const claim = (n: LocalNote) => run(`claim-${n.id}`, async () => {
    const to = needPayout()
    const c = byId.get(n.id)!
    await api('shadow/claim', { id: n.id, arg: to, ...signAction(n, 'claim', c.nonce, to) })
    toast.success(`Claimed ${compact(c.pending)} NAT to ${short(to, 4)}`)
  })

  const unstake = (n: LocalNote) => run(`unstake-${n.id}`, async () => {
    const to = needPayout()
    const c = byId.get(n.id)!
    await api('shadow/unstake', { id: n.id, arg: to, ...signAction(n, 'unstake', c.nonce, to) })
    toast.success(`Note #${n.id} closed — principal + pot sent to ${short(to, 4)}`)
  })

  // Hand the note to someone else: re-key to a fresh key and give them the
  // new note string. The old key is dead the moment this lands; no address
  // is involved at any point.
  const transfer = (n: LocalNote) => run(`rekey-${n.id}`, async () => {
    const c = byId.get(n.id)!
    const k = newKey()
    await api('shadow/rekey', { id: n.id, arg: k.pk, ...signAction(n, 'rekey', c.nonce, k.pk) })
    const moved: LocalNote = { ...n, sk: k.sk, pk: k.pk, at: Date.now() }
    persist(loadNotes().filter(x => !(x.id === n.id && x.contract === n.contract)))
    setHandoff(encodeNote(moved))
    toast.success(`Note #${n.id} re-keyed — send the new note string`)
  })

  const importNote = () => {
    const n = decodeNote(importText)
    if (!n) return toast.error('Not a shadowbloc note string')
    const c = byId.get(n.id)
    if (c && BigInt(c.key) !== BigInt(n.pk)) return toast.error(`Note #${n.id} is controlled by a different key now`)
    persist([...loadNotes().filter(x => !(x.id === n.id && x.contract === n.contract)), n])
    setImportText('')
    toast.success(`Note #${n.id} imported`)
  }

  const forget = (n: LocalNote) => persist(loadNotes().filter(x => !(x.id === n.id && x.contract === n.contract)))

  if (err && !info) {
    return (
      <div className="card card-body text-sm text-down animate-fade-up">
        <p className="lbl text-down mb-1">ShadowBloc offline</p>{err}
        <button onClick={refresh} className="btn btn-sm mt-3"><ArrowPathIcon className="w-4 h-4" />Retry</button>
      </div>
    )
  }
  if (!info) return <div className="card card-body text-sm text-mute animate-fade-up">Loading the private pool…</div>

  const s = info.stats
  const set = info.buckets[denom]?.[tier] ?? 0
  const due = now >= s.nextDistribution
  const potLive = BigInt(s.pot) >= ONE / 1000n // below that it's rounding dust riding to next week
  const myBloc = mine.reduce((a, n) => a + (byId.get(n.id)?.open ? BigInt(byId.get(n.id)!.bloc) : 0n), 0n)
  const myPending = mine.reduce((a, n) => a + BigInt(byId.get(n.id)?.pending || '0'), 0n)
  const share = BigInt(s.totalBloc) > 0n ? Number((myBloc * 10000n) / BigInt(s.totalBloc)) / 100 : 0

  return (
    <div className="space-y-3 animate-fade-up">
      {/* ── Hero ───────────────────────────────────────────────────── */}
      <section className="card overflow-hidden">
        <div className="px-5 py-6 md:px-7 md:py-7 grid md:grid-cols-[1.4fr_1fr] gap-6 items-center">
          <div>
            <p className="lbl text-iris mb-2 flex items-center gap-1.5"><EyeSlashIcon className="w-3.5 h-3.5" />Private · Cairo · Starknet</p>
            <h2 className="text-xl md:text-3xl font-bold text-ink leading-tight">Stake without a name.</h2>
            <p className="mt-3 text-sm text-ink2 leading-relaxed max-w-xl">
              Each stake is a <strong className="text-ink">note</strong> owned by a secret key made in this browser.
              The chain stores the note, not you. It never learns which wallet staked, who holds which note, or whose
              share of the Friday pot is whose.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-px bg-hair rounded-lg overflow-hidden border border-line">
            {[
              ['Open notes', String(s.openNotes), 'accent'],
              ['Pool BLOC', compact(s.totalBloc), 'gold'],
              ['Friday pot', `${compact(s.pot)} NAT`, 'up'],
              [due ? 'Payout' : 'Next payout', !due ? countdown(s.nextDistribution - now) : potLive ? 'due now' : 'pot empty', 'iris'],
            ].map(([l, v, t]) => (
              <div key={l} className="stat bg-panel" style={{ ['--tone' as any]: `var(--${t})` }}>
                <p className="stat-val">{v}</p><span className="stat-lab">{l}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="grid lg:grid-cols-[1fr_1.15fr] gap-3">
        {/* ── New stake ─────────────────────────────────────────────── */}
        <section className="card">
          <div className="card-head"><LockClosedIcon className="w-4 h-4 text-accent" /><span className="lbl text-ink2">New private stake</span></div>
          <div className="card-body space-y-4">
            <div>
              <p className="lbl-dim mb-1.5">Amount</p>
              <div className="grid grid-cols-4 gap-1.5">
                {info.denoms.map((d, i) => (
                  <button key={d} onClick={() => setDenom(i)} className={`btn btn-sm justify-center ${denom === i ? 'btn-accent' : ''}`}>
                    {compact(d, 0)}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="lbl-dim mb-1.5">Lock</p>
              <div className="grid grid-cols-6 gap-1.5">
                {info.tiers.map((t, i) => (
                  <button key={t} onClick={() => setTier(i)} className={`btn btn-sm justify-center px-0 ${tier === i ? 'btn-accent' : ''}`}>
                    {span(t)}
                  </button>
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-hair bg-panel2/60 p-3 grid grid-cols-2 gap-3">
              <div>
                <p className="lbl-dim">You get</p>
                <p className="text-lg font-semibold text-gold tabular-nums">{compact(info.quotes[denom][tier])} <span className="text-xs text-mute">BLOC</span></p>
              </div>
              <div>
                <p className="lbl-dim">Hides among</p>
                <p className="text-lg font-semibold text-iris tabular-nums">{set} <span className="text-xs text-mute">{set === 1 ? 'note' : 'notes'}</span></p>
              </div>
              <p className="col-span-2 text-[11px] text-mute leading-relaxed">
                Amounts and locks come from a fixed menu, so your note looks exactly like every other note in its
                bucket. Bigger buckets mean better cover.
              </p>
            </div>
            <button onClick={createStake} disabled={!!busy} className="btn btn-accent w-full justify-center">
              <KeyIcon className="w-4 h-4" />{busy === 'stake' ? 'Creating note…' : `Stake ${compact(info.denoms[denom], 0)} NAT privately`}
            </button>
            <p className="text-[11px] text-faint leading-relaxed">
              Paper NAT on a Starknet devnet. The relayer pays the stake, so the chain shows the same sender for everyone.
            </p>
          </div>
        </section>

        {/* ── My notes ──────────────────────────────────────────────── */}
        <section className="card flex flex-col">
          <div className="card-head">
            <ShieldCheckIcon className="w-4 h-4 text-up" /><span className="lbl text-ink2">Your notes</span>
            <span className="ml-auto text-[11px] font-mono text-mute">{compact(myBloc)} BLOC · {share}% of pool</span>
          </div>
          <div className="card-body space-y-3 flex-1">
            <div className="flex gap-1.5">
              <input
                className="input flex-1 font-mono text-xs"
                placeholder="Payout address (Starknet 0x…) — use a fresh one"
                value={payout}
                onChange={e => setPayout(e.target.value)}
              />
              <button className="btn btn-sm" title="Use the devnet demo payee" onClick={() => setPayout(info.deployment.payee)}>Demo</button>
            </div>

            {mine.length === 0 ? (
              <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-mute">
                No notes in this browser yet.<br />Create one, or import a note string below.
              </div>
            ) : (
              <div className="space-y-1.5">
                {mine.map(n => {
                  const c = byId.get(n.id)
                  const open = !!c?.open
                  const unlocked = c ? now >= c.unlock : false
                  const stolen = c && BigInt(c.key) !== BigInt(n.pk)
                  return (
                    <div key={n.id} className={`row rounded-lg border border-hair bg-panel2/40 p-2.5 ${open ? '' : 'opacity-60'}`}>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-xs text-ink">#{n.id}</span>
                        {c && <span className="chip">{compact(c.amount, 0)} NAT · {span(info.tiers[c.tier])}</span>}
                        {c && <span className="text-[11px] font-mono text-gold">{compact(c.bloc)} BLOC</span>}
                        <span className="ml-auto text-[11px] font-mono text-mute flex items-center gap-1">
                          {!c ? 'pending…' : stolen ? 'key rotated elsewhere' : !open ? 'closed'
                            : unlocked ? <><LockOpenIcon className="w-3 h-3 text-up" />unlocked</>
                            : <><ClockIcon className="w-3 h-3" />{countdown(c.unlock - now)}</>}
                        </span>
                      </div>
                      {c && open && !stolen && (
                        <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                          <span className="text-[11px] text-up font-mono mr-auto">{compact(c.pending)} NAT earned</span>
                          <button className="btn btn-sm btn-up" disabled={!!busy || BigInt(c.pending) === 0n} onClick={() => claim(n)}>
                            <GiftIcon className="w-3.5 h-3.5" />{busy === `claim-${n.id}` ? '…' : 'Claim'}
                          </button>
                          <button className="btn btn-sm" disabled={!!busy || !unlocked} onClick={() => unstake(n)} title={unlocked ? '' : 'Still locked'}>
                            <LockOpenIcon className="w-3.5 h-3.5" />{busy === `unstake-${n.id}` ? '…' : 'Unstake'}
                          </button>
                          <button className="btn btn-sm" disabled={!!busy} onClick={() => transfer(n)} title="Re-key to a new secret and hand it over">
                            <PaperAirplaneIcon className="w-3.5 h-3.5" />{busy === `rekey-${n.id}` ? '…' : 'Give'}
                          </button>
                        </div>
                      )}
                      {(!open || stolen) && c && (
                        <button className="mt-1 text-[11px] text-faint hover:text-mute" onClick={() => forget(n)}>forget</button>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {handoff && (
              <div className="rounded-lg border border-gold/40 bg-gold/[0.07] p-2.5 space-y-1.5">
                <p className="lbl text-gold">Hand-off note — send this to the new owner</p>
                <p className="font-mono text-[11px] text-ink2 break-all">{handoff}</p>
                <div className="flex gap-1.5">
                  <button className="btn btn-sm" onClick={() => { navigator.clipboard.writeText(handoff); toast.success('Copied') }}><DocumentDuplicateIcon className="w-3.5 h-3.5" />Copy</button>
                  <button className="btn btn-sm" onClick={() => setHandoff('')}>Done</button>
                </div>
              </div>
            )}

            <div className="flex gap-1.5 pt-1">
              <input className="input flex-1 font-mono text-xs" placeholder="shadowbloc:0x…:id:0x… (import a note)" value={importText} onChange={e => setImportText(e.target.value)} />
              <button className="btn btn-sm" onClick={importNote} disabled={!importText.trim()}><ArrowDownTrayIcon className="w-3.5 h-3.5" />Import</button>
              <button className="btn btn-sm" disabled={!mine.length} title="Download a backup of your keys"
                onClick={() => download('bloctime-private-notes.json', JSON.stringify(mine.map(encodeNote), null, 2))}>
                <ArrowUpTrayIcon className="w-3.5 h-3.5" />Backup
              </button>
            </div>
            {myPending > 0n && <p className="text-[11px] text-mute">Total earned across notes: <span className="text-up font-mono">{compact(myPending)} NAT</span></p>}
          </div>
        </section>
      </div>

      {/* ── The public view ────────────────────────────────────────── */}
      <section className="card">
        <div className="card-head">
          <EyeSlashIcon className="w-4 h-4 text-iris" /><span className="lbl text-ink2">What the chain sees</span>
          <span className="ml-auto text-[11px] text-mute hidden sm:inline">Every note on record. No owner column exists.</span>
          {due && s.openNotes > 0 && potLive && (
            <button className="btn btn-sm btn-gold" disabled={!!busy} onClick={() => run('distribute', async () => { await api('shadow/distribute'); toast.success('Friday pot distributed') })}>
              Pay out pot
            </button>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left border-b border-hair">
                {['Note', 'Key (one-time)', 'Bucket', 'BLOC', 'Unlocks', 'State'].map(h => <th key={h} className="lbl-dim font-semibold px-4 py-2">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {ledger.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-mute">No notes yet. Be the first in the pool.</td></tr>}
              {ledger.map(n => {
                const yours = mine.some(m => m.id === n.id)
                return (
                  <tr key={n.id} className="row border-b border-hair/60 last:border-0 hover:bg-panel2/50">
                    <td className="px-4 py-2 font-mono text-ink">#{n.id}{yours && <span className="ml-2 chip text-[10px] py-0">yours</span>}</td>
                    <td className="px-4 py-2 font-mono text-mute">{short(n.key)}</td>
                    <td className="px-4 py-2 font-mono text-ink2">{compact(n.amount, 0)} · {span(info.tiers[n.tier])}</td>
                    <td className="px-4 py-2 font-mono text-gold">{compact(n.bloc)}</td>
                    <td className="px-4 py-2 font-mono text-mute">{n.open ? (now >= n.unlock ? 'unlocked' : countdown(n.unlock - now)) : '—'}</td>
                    <td className="px-4 py-2"><span className={`chip text-[10px] py-0 ${n.open ? 'text-up' : 'text-faint'}`}>{n.open ? 'open' : 'closed'}</span></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── How + honest limits ────────────────────────────────────── */}
      <section className="card">
        <button className="card-head w-full text-left" onClick={() => setShowHow(v => !v)}>
          <ShieldCheckIcon className="w-4 h-4 text-accent" /><span className="lbl text-ink2">How the privacy works</span>
          <ChevronDownIcon className={`w-4 h-4 ml-auto text-mute transition-transform ${showHow ? 'rotate-180' : ''}`} />
        </button>
        {showHow && (
          <div className="card-body grid md:grid-cols-2 gap-4 text-[13px] text-ink2 leading-relaxed">
            <div className="space-y-2">
              <p><strong className="text-ink">Keys, not wallets.</strong> Your browser makes a one-time Stark key per note. The Cairo
                contract stores only its public half. Claims, unstakes and hand-offs are signatures from that key, checked
                on-chain with Starknet&apos;s native ECDSA.</p>
              <p><strong className="text-ink">Anyone can post, nobody can steal.</strong> The relayer submits every transaction, so your
                account never touches the contract. The signature covers the payout address and a nonce, so the relayer
                can&apos;t redirect funds and can&apos;t replay a signature.</p>
              <p><strong className="text-ink">Hand-offs leave no trace.</strong> &ldquo;Give&rdquo; moves a note to a new key. The chain sees a key change,
                with no addresses on either side.</p>
            </div>
            <div className="space-y-2">
              <p className="lbl text-gold">What is still public</p>
              <ul className="list-disc pl-4 space-y-1 text-mute">
                <li>Totals, the pot, and each note&apos;s bucket and BLOC. The books stay auditable.</li>
                <li>The address a payout goes to. Use a fresh address if you don&apos;t want it tied to you.</li>
                <li>Notes stay traceable from stake to payout. This hides <em>who</em>, not <em>what</em>. It is not a mixer.</li>
                <li>The relayer sees your IP and a note&apos;s public key, but never its secret.</li>
                <li>Your keys live in this browser. Clear it without a backup and the note is gone.</li>
              </ul>
              <p className="text-[11px] text-faint font-mono pt-1">contract {short(contract)} · {info.deployment.network}</p>
            </div>
          </div>
        )}
      </section>

      {owner && (
        <section className="card card-body flex items-center gap-2 flex-wrap">
          <span className="lbl text-mute mr-auto">Devnet owner</span>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run('fund', async () => { await api('shadow/fund_pot', { amount: 1000 }); toast.success('Pot +1,000 NAT') })}>Fund pot +1,000</button>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run('warp', async () => { await api('shadow/warp', { to: 'friday' }); toast.success('Devnet clock → Friday 17:00 UTC') })}>Warp to Friday</button>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => run('warp', async () => { await api('shadow/warp', { to: String(30 * 86400) }); toast.success('Devnet clock +30 days') })}>+30 days</button>
        </section>
      )}
    </div>
  )
}
