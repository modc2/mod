"use client";

import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'react-toastify'
import {
  ScaleIcon, CpuChipIcon, UserGroupIcon, BoltIcon, ArrowPathIcon, GiftIcon,
} from '@heroicons/react/24/outline'

// YUMA — bloctime.yuma (yuma/ + contracts/yuma/YumaBlocTime.sol).
//
// Yuma consensus where a validator's stake is its BlocTime: BLOC held plus
// BLOC delegated to it. Validators weight miners, the stake-weighted
// kappa-quantile clips anyone voting off-consensus, miners earn by clipped
// rank and validators by the bonds they built. This tab drives the shared
// paper subnet; in LIVE mode stakes are read from the BlocTime contract.

type Api = (fn: string, params?: Record<string, any>, method?: string) => Promise<any>

interface Validator {
  uid: number; address: string; bloctime: string | number; last_stake: string | number
  dividend: string | number; weights: (string | number)[]; bonds: (string | number)[]; pending: string | number
}
interface Miner { uid: number; address: string; incentive: string | number; consensus: string | number; pending: string | number }
interface YState {
  params: Record<string, string | number>
  source: 'paper' | 'live'
  epoch_count: number; next_epoch_time: number; now: number
  total_emitted: string | number; owner_pending: string | number
  validators: Validator[]; miners: Miner[]
  history: { epoch: number; time: number; emission: string | number; to_miners: string | number; to_validators: string | number; to_owner: string | number }[]
  bloctime: { rpc: string; address: string }
}
interface Preview {
  stake: (string | number)[]; incentive: (string | number)[]; dividends: (string | number)[]
  consensus: (string | number)[]; miner_pay: (string | number)[]; validator_pay: (string | number)[]; owner_pay: string | number
}

const ONE = 10n ** 18n
const big = (x: string | number | undefined) => BigInt(x ?? 0)
const tok = (x: string | number | undefined, dp = 2) => {
  const v = big(x)
  const n = Number(v / 10n ** 12n) / 1e6
  if (n >= 1e9) return `${(n / 1e9).toFixed(dp)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(dp)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(dp)}K`
  return n.toFixed(dp)
}
const pct = (x: string | number | undefined) => `${(Number(big(x) * 10000n / ONE) / 100).toFixed(1)}%`
const short = (a: string) => /^0x[0-9a-fA-F]{40}$/.test(a) ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
const countdown = (s: number) => s <= 0 ? 'due now' : s < 3600 ? `${Math.ceil(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`

export default function Yuma({ api, account }: { api: Api; account: string }) {
  const [st, setSt] = useState<YState | null>(null)
  const [pv, setPv] = useState<Preview | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [me, setMe] = useState(account || '')
  const [bloc, setBloc] = useState('1000')
  const [draft, setDraft] = useState<Record<number, string>>({})
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000))

  useEffect(() => { if (account) setMe(account) }, [account])
  useEffect(() => { const t = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000); return () => clearInterval(t) }, [])

  const refresh = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api('yuma', {}, 'GET'), api('yuma/preview', {}, 'GET')])
      setSt(s); setPv(p); setErr('')
    } catch (e: any) { setErr(e.message || String(e)) }
  }, [api])
  useEffect(() => { refresh(); const t = setInterval(refresh, 15000); return () => clearInterval(t) }, [refresh])

  const run = async (label: string, fn: string, params: Record<string, any> = {}) => {
    setBusy(label)
    try {
      const r = await api(fn, params)
      await refresh()
      return r
    } catch (e: any) { toast.error(e.message || String(e)) } finally { setBusy('') }
  }

  const myV = useMemo(() => st?.validators.find(v => v.address.toLowerCase() === me.toLowerCase()), [st, me])
  const myM = useMemo(() => st?.miners.find(m => m.address.toLowerCase() === me.toLowerCase()), [st, me])
  const myPending = big(myV?.pending) + big(myM?.pending)

  // seed the weight editor from my row whenever it changes on the server
  useEffect(() => {
    if (!myV) return
    const row: Record<number, string> = {}
    myV.weights.forEach((w, j) => { row[j] = String(w) })
    setDraft(row)
  }, [myV?.uid, myV?.weights.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps

  if (err && !st) {
    return (
      <div className="card card-body text-sm text-down animate-fade-up">
        <p className="lbl text-down mb-1">Yuma offline</p>{err}
        <button onClick={refresh} className="btn btn-sm mt-3"><ArrowPathIcon className="w-4 h-4" />Retry</button>
      </div>
    )
  }
  if (!st) return <div className="card card-body text-sm text-mute animate-fade-up">Loading the Yuma subnet…</div>

  const live = st.source === 'live'
  const due = now >= st.next_epoch_time
  const totalStake = (pv?.stake || []).reduce((a, x) => a + big(x), 0n)

  return (
    <div className="space-y-3 animate-fade-up">
      {/* ── Hero ───────────────────────────────────────────────────── */}
      <section className="card overflow-hidden">
        <div className="px-5 py-6 md:px-7 md:py-7 grid md:grid-cols-[1.4fr_1fr] gap-6 items-center">
          <div>
            <p className="lbl text-accent mb-2 flex items-center gap-1.5">
              <ScaleIcon className="w-3.5 h-3.5" />Yuma · {live ? 'live BLOC' : 'paper BLOC'}
            </p>
            <h2 className="text-xl md:text-3xl font-bold text-ink leading-tight">Rewards follow BlocTime.</h2>
            <p className="mt-3 text-sm text-ink2 leading-relaxed max-w-xl">
              Validators score miners. Each validator's vote counts by its <strong className="text-ink">BlocTime</strong>,
              the BLOC it holds plus what's delegated to it. Since BLOC is dollars × time locked, a long lock
              outvotes a big wallet. A score far from what most BlocTime agrees on gets cut back. Miners are paid
              by their consensus score, and validators by how early they backed the miners who earned.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-px bg-hair rounded-lg overflow-hidden border border-line">
            {[
              ['Epoch', `#${st.epoch_count}`, 'accent'],
              ['Validator BLOC', tok(String(totalStake), 1), 'gold'],
              ['Per epoch', tok(st.params.emission_per_epoch, 0), 'up'],
              [due ? 'Epoch' : 'Next epoch', countdown(st.next_epoch_time - now), 'iris'],
            ].map(([l, v, t]) => (
              <div key={l} className="stat bg-panel" style={{ ['--tone' as any]: `var(--${t})` }}>
                <p className="stat-val">{v}</p><span className="stat-lab">{l}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="grid lg:grid-cols-[1fr_1.4fr] gap-3">
        {/* ── You ───────────────────────────────────────────────────── */}
        <section className="card">
          <div className="card-head"><BoltIcon className="w-4 h-4 text-accent" /><span className="lbl text-ink2">You</span>
            {myPending > 0n && <span className="ml-auto text-[11px] font-mono text-up">{tok(String(myPending))} to claim</span>}
          </div>
          <div className="card-body space-y-3">
            <input className="input w-full font-mono text-xs" value={me} onChange={e => setMe(e.target.value.trim())}
              placeholder={live ? 'Your wallet address (0x…)' : 'Any name or address'} />
            {!live && (
              <div className="flex gap-1.5 items-center">
                <input className="input flex-1 text-xs" value={bloc} onChange={e => setBloc(e.target.value)} placeholder="Paper BLOC" />
                <button className="btn btn-sm" disabled={!me || !!busy} onClick={() => run('stake', 'yuma/set_stake', { address: me, bloc })}>
                  Set paper BLOC
                </button>
              </div>
            )}
            {live && (
              <p className="text-[11px] text-mute leading-relaxed">
                Stake is your real BlocTime from the Stake tab (held + delegated).
              </p>
            )}
            <div className="grid grid-cols-3 gap-1.5">
              <button className="btn btn-sm justify-center" disabled={!me || !!myV || !!busy}
                onClick={() => run('v', 'yuma/register_validator', { address: me })}>
                <UserGroupIcon className="w-4 h-4" />{myV ? `Validator ${myV.uid}` : 'Validate'}
              </button>
              <button className="btn btn-sm justify-center" disabled={!me || !!myM || !!busy}
                onClick={() => run('m', 'yuma/register_miner', { address: me })}>
                <CpuChipIcon className="w-4 h-4" />{myM ? `Miner ${myM.uid}` : 'Mine'}
              </button>
              <button className="btn btn-sm btn-accent justify-center" disabled={myPending === 0n || !!busy}
                onClick={() => run('claim', 'yuma/claim', { address: me }).then(r => r && toast.success(`Claimed ${tok(r.claimed)}`))}>
                <GiftIcon className="w-4 h-4" />Claim
              </button>
            </div>

            {myV && st.miners.length > 0 && (
              <div className="rounded-lg border border-hair bg-panel2/60 p-3 space-y-2">
                <p className="lbl-dim">Your scores for each miner</p>
                <div className="grid grid-cols-4 gap-1.5">
                  {st.miners.map(m => (
                    <label key={m.uid} className="text-[11px] text-mute">
                      <span className="block truncate mb-0.5">#{m.uid} {short(m.address)}</span>
                      <input className="input w-full text-xs tabular-nums" inputMode="numeric" value={draft[m.uid] ?? '0'}
                        onChange={e => setDraft(d => ({ ...d, [m.uid]: e.target.value.replace(/[^0-9]/g, '') }))} />
                    </label>
                  ))}
                </div>
                <button className="btn btn-sm w-full justify-center" disabled={!!busy}
                  onClick={() => run('w', 'yuma/set_weights', {
                    address: me, weights: Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v || 0)])),
                  }).then(r => r && toast.success('Scores saved'))}>
                  Save scores
                </button>
              </div>
            )}

            <button className="btn w-full justify-center" disabled={!due || !!busy}
              onClick={() => run('epoch', 'yuma/epoch').then(r => r && toast.success(`Epoch #${r.epoch}: ${tok(r.emission)} paid`))}>
              <ArrowPathIcon className="w-4 h-4" />{due ? 'Run epoch' : `Epoch in ${countdown(st.next_epoch_time - now)}`}
            </button>
            <p className="text-[11px] text-faint leading-relaxed">
              {st.params.owner_cut_bps === 0 ? '' : `${Number(st.params.owner_cut_bps) / 100}% to the subnet owner, then `}
              {Number(st.params.miner_share_bps) / 100}% to miners, the rest to validators. Anyone can run an epoch once it's due.
            </p>
          </div>
        </section>

        {/* ── The subnet ─────────────────────────────────────────────── */}
        <section className="card flex flex-col">
          <div className="card-head"><ScaleIcon className="w-4 h-4 text-gold" /><span className="lbl text-ink2">Next epoch, if run now</span></div>
          <div className="card-body space-y-4 flex-1 overflow-x-auto">
            {st.validators.length === 0 && st.miners.length === 0 ? (
              <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-mute">
                Nobody here yet. Set some BLOC and press Validate, or press Mine.
              </div>
            ) : (
              <>
                <table className="w-full text-xs tabular-nums">
                  <thead><tr className="text-mute text-left">
                    <th className="font-normal py-1">Validator</th><th className="font-normal">BlocTime</th>
                    <th className="font-normal">Vote</th><th className="font-normal">Gets</th><th className="font-normal text-right">To claim</th>
                  </tr></thead>
                  <tbody>
                    {st.validators.map((v, i) => (
                      <tr key={v.uid} className={`border-t border-hair ${v.address === me ? 'text-accent' : 'text-ink2'}`}>
                        <td className="py-1.5 font-mono">#{v.uid} {short(v.address)}</td>
                        <td>{tok(v.bloctime, 1)}</td>
                        <td>{totalStake > 0n ? pct(String(big(pv?.stake[i]) * ONE / totalStake)) : '—'}</td>
                        <td className="text-gold">{tok(pv?.validator_pay[i])}</td>
                        <td className="text-right">{tok(v.pending)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <table className="w-full text-xs tabular-nums">
                  <thead><tr className="text-mute text-left">
                    <th className="font-normal py-1">Miner</th><th className="font-normal">Score</th>
                    <th className="font-normal">Agreed cap</th><th className="font-normal">Gets</th><th className="font-normal text-right">To claim</th>
                  </tr></thead>
                  <tbody>
                    {st.miners.map((m, j) => (
                      <tr key={m.uid} className={`border-t border-hair ${m.address === me ? 'text-accent' : 'text-ink2'}`}>
                        <td className="py-1.5 font-mono">#{m.uid} {short(m.address)}</td>
                        <td>{pct(pv?.incentive[j])}</td>
                        <td className="text-mute">{pct(pv?.consensus[j])}</td>
                        <td className="text-up">{tok(pv?.miner_pay[j])}</td>
                        <td className="text-right">{tok(m.pending)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {st.validators.length > 0 && st.miners.length > 0 && (
                  <WeightGrid st={st} pv={pv} />
                )}
              </>
            )}
            {st.history.length > 0 && (
              <div>
                <p className="lbl-dim mb-1">Past epochs</p>
                <div className="space-y-0.5 text-[11px] font-mono text-mute">
                  {st.history.slice(-5).reverse().map(h => (
                    <div key={h.epoch} className="flex gap-3">
                      <span className="text-ink2">#{h.epoch}</span>
                      <span>{new Date(h.time * 1000).toLocaleString()}</span>
                      <span className="ml-auto">miners {tok(h.to_miners)} · validators {tok(h.to_validators)} · owner {tok(h.to_owner)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

// Rows = validators, columns = miners. Each cell is that validator's share of
// its own scores; a cell above the agreed cap (consensus) is struck — that
// part of the vote is clipped away and earns nothing.
function WeightGrid({ st, pv }: { st: YState; pv: Preview | null }) {
  return (
    <div>
      <p className="lbl-dim mb-1">Who scored whom</p>
      <div className="grid gap-px bg-hair rounded border border-hair overflow-hidden text-[10px] tabular-nums"
        style={{ gridTemplateColumns: `auto repeat(${st.miners.length}, minmax(2.5rem, 1fr))` }}>
        <div className="bg-panel px-1.5 py-1 text-mute" />
        {st.miners.map(m => <div key={m.uid} className="bg-panel px-1.5 py-1 text-mute text-center">m{m.uid}</div>)}
        {st.validators.map((v, i) => {
          const row = v.weights.map(big)
          const sum = row.reduce((a, x) => a + x, 0n)
          const voiced = big(pv?.stake[i]) > 0n
          return [
            <div key={`h${i}`} className="bg-panel px-1.5 py-1 text-mute">v{v.uid}</div>,
            ...row.map((w, j) => {
              const share = sum > 0n ? w * ONE / sum : 0n
              const clipped = voiced && share > big(pv?.consensus[j])
              const a = Number(share * 100n / ONE) / 100
              return (
                <div key={`${i}-${j}`} className={`px-1.5 py-1 text-center ${voiced ? 'text-ink2' : 'text-faint'} ${clipped ? 'line-through decoration-down' : ''}`}
                  style={{ background: `rgb(var(--accent-rgb) / ${voiced ? (0.06 + a * 0.5).toFixed(2) : 0.03})` }}
                  title={clipped ? 'Above what the rest agree on — clipped' : undefined}>
                  {share > 0n ? `${Math.round(a * 100)}` : '·'}
                </div>
              )
            }),
          ]
        })}
      </div>
    </div>
  )
}
