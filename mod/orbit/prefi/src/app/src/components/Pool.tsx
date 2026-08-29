'use client'

/**
 * The stake pool — deposit, stake (or call it free), watch the pot, cash out.
 *
 * Everything here moves real stablecoins on HyperEVM. Two rules the UI has to
 * respect and does not get to bend:
 *
 *   1. Spending actions are signed. The server hands back the exact text to
 *      sign (`/pool/sign`), the wallet shows it, and the signature is checked
 *      against a nonce so it cannot be replayed. The client never invents the
 *      message.
 *   2. A free call is still a signed call. It moves no money, so it is not a
 *      spending action, but it writes to a public accuracy record — so the
 *      wallet proves it owns the address it is writing to.
 *   3. Numbers are rendered the way the server will re-render them. The signed
 *      message is rebuilt server-side from the arguments, so "200" and
 *      "200.000000" are different messages — see `pyStr` and the fixed-decimal
 *      formatting on stake/withdraw.
 */

import { useCallback, useEffect, useState } from 'react'
import { useWalletClient } from 'wagmi'
import { toast } from 'react-toastify'
import { API_BASE_URL } from '@/lib/contracts'
import {
  HYPEREVM, addressUrl, depositToVault, ensureHyperEVM, signAction, txUrl, walletBalance,
} from '@/lib/hyperevm'

const API = API_BASE_URL

const fmt = (n: number, d = 2) =>
  n == null || Number.isNaN(n) ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })
const usd = (n: number, d = 2) => (n == null ? '—' : `$${fmt(n, d)}`)
const pct = (n: number, d = 1) => (n == null ? '—' : `${(n * 100).toFixed(d)}%`)
// Prices, unlike dollar amounts, span eight orders of magnitude here — the pool
// lists any Hyperliquid pair, and PUMP at $0.0045 must not render as "$0.00".
const px = (n: number) => (n == null ? '—' : usd(n, n === 0 ? 2 : n < 0.01 ? 6 : n < 1 ? 4 : 2))
const short = (a: string) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—')

function countdown(seconds: number) {
  if (seconds == null) return '—'
  if (seconds <= 0) return 'now'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m ${seconds % 60}s`
}

const get = async (url: string) => {
  try { const r = await fetch(url); return r.ok ? r.json() : null } catch { return null }
}

async function post(url: string) {
  const r = await fetch(url, { method: 'POST' })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body?.detail || body?.error || `HTTP ${r.status}`)
  return body
}

/**
 * Python's `str()`, in TypeScript. The owner's signed config message is
 * rebuilt with it server-side, so 5 must be signed as "5.0" for a float field
 * and true as "True" — get this wrong and the signature silently fails to
 * verify against a message the user never saw.
 */
const CONFIG_KINDS: Record<string, 'int' | 'float' | 'bool' | 'str'> = {
  interval: 'int', entry_cutoff: 'int', fee_bps: 'int', spot_grace: 'int',
  tolerance: 'float', min_stake: 'float', max_stake: 'float', min_withdraw: 'float',
  model: 'str', auto_pay: 'bool', free_per_round: 'int', free_notional: 'float',
}

function pyStr(key: string, value: any): string {
  switch (CONFIG_KINDS[key]) {
    case 'int': return String(Math.round(Number(value)))
    case 'float': {
      const n = Number(value)
      return Number.isInteger(n) ? `${n}.0` : String(n)
    }
    case 'bool': return value ? 'True' : 'False'
    default: return String(value)
  }
}


export default function Pool({ address, markets, onAction }: any) {
  const { data: walletClient } = useWalletClient()
  const [cfg, setCfg] = useState<any>(null)
  const [vault, setVault] = useState<any>(null)
  const [stats, setStats] = useState<any>(null)
  const [round, setRound] = useState<any>(null)
  const [balance, setBalance] = useState<any>(null)
  const [history, setHistory] = useState<any[]>([])
  const [board, setBoard] = useState<any[]>([])
  const [withdrawals, setWithdrawals] = useState<any[]>([])
  const [owner, setOwner] = useState<any>(null)
  const [freeQuota, setFreeQuota] = useState<any>(null)
  const [freeBoard, setFreeBoard] = useState<any[]>([])
  const [view, setView] = useState<'round' | 'history' | 'board' | 'free' | 'admin'>('round')

  const hlMarkets = (markets || []).filter((m: any) => m.source === 'hyperliquid' && m.active)

  const refresh = useCallback(async () => {
    const [c, v, s, r, o] = await Promise.all([
      get(`${API}/pool/config`), get(`${API}/pool/vault`), get(`${API}/pool`),
      get(`${API}/pool/round${address ? `?address=${address}` : ''}`),
      get(`${API}/pool/owner`),
    ])
    if (c) setCfg(c)
    if (v) setVault(v)
    if (s) setStats(s)
    if (r) setRound(r)
    if (o) setOwner(o)
  }, [address])

  const refreshUser = useCallback(async () => {
    if (!address) { setBalance(null); setWithdrawals([]); setFreeQuota(null); return }
    const [b, w, f] = await Promise.all([
      get(`${API}/pool/balance/${address}`),
      get(`${API}/pool/withdrawals?address=${address}`),
      get(`${API}/pool/free/${address}`),
    ])
    if (b) setBalance(b)
    if (w) setWithdrawals(w)
    if (f) setFreeQuota(f)
  }, [address])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 15000)
    return () => clearInterval(timer)
  }, [refresh])
  useEffect(() => { refreshUser() }, [refreshUser])
  useEffect(() => {
    if (view === 'history') get(`${API}/pool/rounds?limit=20`).then(r => r && setHistory(r))
    if (view === 'board') get(`${API}/pool/leaderboard`).then(r => r && setBoard(r))
    if (view === 'free') get(`${API}/pool/free/leaderboard`).then(r => r && setFreeBoard(r))
  }, [view])

  const reload = () => { refresh(); refreshUser(); onAction?.() }

  /** Fetch the message for a spending action and sign it with the wallet. */
  const signFor = useCallback(async (action: string, fields: Record<string, string>) => {
    const query = new URLSearchParams({ action, address, ...fields })
    const req = await get(`${API}/pool/sign?${query}`)
    if (!req) throw new Error('could not reach the pool')
    if (!req.required) return { nonce: req.nonce, signature: '' }
    const signature = await signAction(walletClient, req.message)
    return { nonce: req.nonce, signature }
  }, [address, walletClient])

  if (!cfg) return <div className="card p-8 text-center text-sm text-zinc-500">Loading pool…</div>

  const tokens: any[] = Object.values(vault?.tokens || {})

  return (
    <div className="space-y-5">
      <PoolStats stats={stats} round={round} cfg={cfg} vault={vault} />

      {!vault?.address ? (
        <div className="card p-5">
          <div className="text-sm text-white font-medium mb-1">The pool has no vault yet</div>
          <p className="text-xs text-zinc-500 leading-relaxed">
            Deposits need an address on {HYPEREVM.name} to land in. The owner creates one with{' '}
            <code className="text-zinc-400">m prefi/pool-create-vault</code> (a custodial hot wallet
            this server holds the key to) or points the pool at an address they already control with{' '}
            <code className="text-zinc-400">m prefi/pool-set-vault address=0x…</code>.
          </p>
        </div>
      ) : (
        <div className="grid md:grid-cols-3 gap-4">
          <Deposit address={address} vault={vault} tokens={tokens} walletClient={walletClient}
                   onDone={reload} />
          <Stake address={address} balance={balance} cfg={cfg} round={round}
                 markets={hlMarkets} signFor={signFor} onDone={reload}
                 quota={freeQuota} />
          <Cashout address={address} balance={balance} tokens={tokens} cfg={cfg}
                   withdrawals={withdrawals} signFor={signFor} onDone={reload} />
        </div>
      )}

      <nav className="flex items-center gap-1.5">
        {([['round', 'This round'], ['history', 'Past rounds'],
           ['board', 'Stakers'], ['free', 'Free play'],
           ['admin', 'Owner']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setView(id)}
            className={`btn btn-ghost text-xs px-3 py-1.5 ${view === id ? 'active' : ''}`}>
            {label}
          </button>
        ))}
      </nav>

      {view === 'round' && <RoundView round={round} cfg={cfg} address={address} />}
      {view === 'history' && <HistoryView rounds={history} />}
      {view === 'board' && <BoardView board={board} address={address} />}
      {view === 'free' && <FreeBoardView board={freeBoard} cfg={cfg} address={address} />}
      {view === 'admin' && <OwnerView cfg={cfg} owner={owner} vault={vault} address={address}
                                      signFor={signFor} onDone={reload} />}
    </div>
  )
}


/* ─── Header stats ────────────────────────────────────────────── */

function PoolStats({ stats, round, cfg, vault }: any) {
  const s = stats || {}
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <Stat label="In the pool" value={usd(s.tvl || 0)}
            sub={`${usd(s.at_stake || 0)} at stake · ${s.stakers || 0} stakers` +
                 (s.free_callers ? ` · ${s.free_callers} free` : '')} />
      <Stat label={`Round ${round?.index ?? s.round ?? 0}`}
            value={countdown(round?.seconds_to_close)}
            sub={round?.entries_open
              ? `entries close in ${countdown(round?.seconds_to_deadline)}`
              : 'entries closed — settling next'} />
      <Stat label="Interval" value={`${cfg?.interval_days ?? 7}d`}
            sub={`${cfg?.model} · tolerance ${cfg?.tolerance}`} />
      <Stat label="Vault"
            value={vault?.address ? short(vault.address) : 'none'}
            sub={vault?.solvent === false
              ? `⚠ holds ${usd(vault?.held_total || 0)} against ${usd(vault?.owed?.total || 0)} owed`
              : vault?.address ? `${HYPEREVM.name} · covered` : 'not configured'}
            tone={vault?.solvent === false ? 'bad' : undefined} />
    </div>
  )
}

function Stat({ label, value, sub, tone }: any) {
  return (
    <div className="stat-card">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className={`text-xl font-semibold mt-1 ${tone === 'bad' ? 'down' : 'text-white'}`}>{value}</div>
      {sub && <div className="text-[10px] text-zinc-500 mt-1">{sub}</div>}
    </div>
  )
}


/* ─── Deposit ─────────────────────────────────────────────────── */

function Deposit({ address, vault, tokens, walletClient, onDone }: any) {
  const [symbol, setSymbol] = useState<string>(tokens[0]?.symbol || 'USDC')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [held, setHeld] = useState<number | null>(null)
  const [manual, setManual] = useState('')
  const token = tokens.find((t: any) => t.symbol === symbol) || tokens[0]

  useEffect(() => {
    if (!address || !token) return
    walletBalance(walletClient, token.address, token.decimals, address)
      .then(setHeld).catch(() => setHeld(null))
  }, [address, token, walletClient])

  const send = async () => {
    if (!address) return toast.error('Connect a wallet first')
    if (!(Number(amount) > 0)) return toast.error('Enter an amount')
    setBusy(true)
    try {
      await ensureHyperEVM(walletClient)
      const hash = await depositToVault(walletClient, token.address, token.decimals,
                                        vault.address, amount)
      toast.info('Deposit sent — waiting for the block')
      // Credit as soon as it is mined. The chain sweep would find it eventually;
      // this just makes the balance show up now.
      for (let attempt = 0; attempt < 20; attempt++) {
        await new Promise(r => setTimeout(r, 1500))
        try {
          const out = await post(`${API}/pool/deposit?tx=${hash}`)
          if (out?.credited?.length) {
            toast.success(`Credited ${usd(out.total)}`)
            setAmount('')
            onDone()
            return
          }
        } catch { /* not mined yet — keep waiting */ }
      }
      toast.warn('Still confirming. Paste the hash below once it lands.')
      setManual(hash)
    } catch (err: any) {
      toast.error(err?.shortMessage || err?.message || 'Deposit failed')
    } finally {
      setBusy(false)
    }
  }

  const credit = async () => {
    setBusy(true)
    try {
      const out = await post(`${API}/pool/deposit?tx=${manual.trim()}`)
      toast.success(out?.credited?.length ? `Credited ${usd(out.total)}` : 'Nothing new to credit')
      setManual('')
      onDone()
    } catch (err: any) {
      toast.error(err.message)
    } finally { setBusy(false) }
  }

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm text-white font-medium">Deposit</div>
        <a href={addressUrl(vault.address)} target="_blank" rel="noreferrer"
           className="text-[10px] text-zinc-500 hover:text-zinc-300">
          vault {short(vault.address)} ↗
        </a>
      </div>

      <div className="flex gap-1.5">
        {tokens.map((t: any) => (
          <button key={t.symbol} onClick={() => setSymbol(t.symbol)}
            className={`btn btn-ghost text-[10px] px-2 py-1 ${symbol === t.symbol ? 'active' : ''}`}>
            {t.symbol}
          </button>
        ))}
      </div>

      <div className="flex gap-2">
        <input className="input flex-1" placeholder="0.00" value={amount}
               onChange={e => setAmount(e.target.value)} inputMode="decimal" />
        <button className="btn btn-ghost text-[10px] px-2"
                onClick={() => held != null && setAmount(String(held))}>MAX</button>
      </div>
      <div className="text-[10px] text-zinc-500">
        {held == null ? `${HYPEREVM.name} balance unavailable` : `You hold ${fmt(held, 2)} ${symbol} on ${HYPEREVM.name}`}
      </div>

      <button className="btn btn-blue w-full text-xs py-2" disabled={busy || !address} onClick={send}>
        {busy ? 'Depositing…' : `Deposit ${symbol}`}
      </button>

      <details className="text-[10px] text-zinc-500">
        <summary className="cursor-pointer hover:text-zinc-300">Already sent one? Credit by hash</summary>
        <div className="flex gap-2 mt-2">
          <input className="input flex-1 text-[10px]" placeholder="0x…" value={manual}
                 onChange={e => setManual(e.target.value)} />
          <button className="btn btn-ghost text-[10px] px-2" disabled={busy || !manual} onClick={credit}>
            Credit
          </button>
        </div>
      </details>
    </div>
  )
}


/* ─── Stake ───────────────────────────────────────────────────── */

function Stake({ address, balance, cfg, round, markets, signFor, onDone, quota }: any) {
  const [asset, setAsset] = useState<string>(markets[0]?.symbol || '')
  const [price, setPrice] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState<'paid' | 'free' | null>(null)

  useEffect(() => {
    if (!asset && markets[0]) setAsset(markets[0].symbol)
  }, [markets, asset])

  const freeOn = (quota?.enabled ?? (cfg?.free_per_round > 0)) !== false
  const freeLeft = quota?.remaining ?? cfg?.free_per_round ?? 0
  const funded = (balance?.available || 0) >= (cfg?.min_stake || 0)

  // Someone who cannot stake anything yet should land on the mode they can
  // actually use — that is the whole point of free play.
  const effective = mode ?? (freeOn && !funded ? 'free' : 'paid')
  const free = effective === 'free'
  const assetUsed = (quota?.assets_used || []).includes(asset)

  const market = markets.find((m: any) => m.symbol === asset)
  const mark = market?.price_usd
  const move = mark && Number(price) > 0 ? (Number(price) - mark) / mark * 100 : null
  const notional = quota?.notional ?? cfg?.free_notional ?? 0

  const submit = async () => {
    if (!address) return toast.error('Connect a wallet first')
    const called = Number(price)
    if (!(called > 0)) return toast.error('Enter a price')
    const stake = Number(amount)
    if (!free && !(stake > 0)) return toast.error('Enter an amount to stake')
    setBusy(true)
    try {
      // These strings are the signed ones — the server re-renders the message
      // with the same fixed precision, so they have to match exactly.
      const priceStr = called.toFixed(8)
      const roundStr = String(round?.index ?? 0)
      if (free) {
        const { signature, nonce } = await signFor('free_stake', {
          asset, price: priceStr, round: roundStr,
        })
        const query = new URLSearchParams({
          address, asset, predicted_price: priceStr, nonce: String(nonce),
        })
        if (signature) query.set('signature', signature)
        const out = await post(`${API}/pool/free?${query}`)
        toast.success(`Free call on ${out.asset} at ${px(out.predicted_price)} — ` +
                      `scored at the close, ${out.free_remaining} left this round`)
      } else {
        const amountStr = stake.toFixed(6)
        const { signature, nonce } = await signFor('stake', {
          amount: amountStr, asset, price: priceStr, round: roundStr,
        })
        const query = new URLSearchParams({
          address, asset, predicted_price: priceStr, amount: amountStr, nonce: String(nonce),
        })
        if (signature) query.set('signature', signature)
        const out = await post(`${API}/pool/stake?${query}`)
        toast.success(`Staked ${usd(out.staked)} on ${out.asset} at ${px(out.predicted_price)}`)
      }
      setAmount(''); setPrice('')
      onDone()
    } catch (err: any) {
      toast.error(err.message || (free ? 'Free call failed' : 'Stake failed'))
    } finally { setBusy(false) }
  }

  const open = round?.entries_open !== false
  const blocked = free && (!freeOn || freeLeft <= 0 || assetUsed)
  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 p-0.5 rounded-md bg-white/[0.04]">
          {([['paid', 'Stake'], ['free', 'Free']] as const).map(([id, label]) => (
            <button key={id} onClick={() => setMode(id)} disabled={id === 'free' && !freeOn}
              className={`text-[11px] px-2.5 py-1 rounded ${
                effective === id ? 'bg-white/10 text-white' : 'text-zinc-500 hover:text-zinc-300'
              } ${id === 'free' && !freeOn ? 'opacity-40 cursor-not-allowed' : ''}`}>
              {label}
            </button>
          ))}
        </div>
        <div className="text-[10px] text-zinc-500">
          {free ? `${freeLeft} free call${freeLeft === 1 ? '' : 's'} left`
                : `balance ${usd(balance?.available || 0)}`}
        </div>
      </div>

      <select className="input w-full" value={asset} onChange={e => setAsset(e.target.value)}>
        {markets.length === 0 && <option value="">No Hyperliquid markets listed</option>}
        {markets.map((m: any) => (
          <option key={m.symbol} value={m.symbol}>
            {m.hl_kind === 'spot' || m.symbol.includes('/') ? m.symbol : `${m.symbol}-PERP`}
          </option>
        ))}
      </select>

      <div>
        <input className="input w-full" placeholder={mark ? `close price, mark ${px(mark).slice(1)}` : 'close price'}
               value={price} onChange={e => setPrice(e.target.value)} inputMode="decimal" />
        {move != null && (
          <div className={`text-[10px] mt-1 ${move >= 0 ? 'up' : 'down'}`}>
            {move >= 0 ? '+' : ''}{move.toFixed(2)}% from the mark
          </div>
        )}
      </div>

      {free ? (
        <div className="input w-full flex items-center justify-between text-xs text-zinc-400">
          <span>No money down</span>
          <span className="text-zinc-500">scored as {usd(notional, 0)}</span>
        </div>
      ) : (
        <input className="input w-full" placeholder={`$ stake (min ${fmt(cfg?.min_stake || 0, 0)})`}
               value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" />
      )}

      <button className={`btn w-full text-xs py-2 ${free ? 'btn-blue' : 'btn-green'}`}
              disabled={busy || !address || !open || blocked || markets.length === 0}
              onClick={submit}>
        {busy ? 'Signing…' : !open ? 'Entries closed' : free ? 'Call it free' : 'Stake'}
      </button>

      {free ? (
        <p className="text-[10px] text-zinc-500 leading-relaxed">
          {!freeOn ? 'Free play is switched off — stake to enter a round.'
            : assetUsed ? `You already have a free call on ${asset} this round — one per asset, so the board means something.`
            : freeLeft <= 0 ? `Out of free calls. They reset when the round does, in ${countdown(round?.seconds_to_close)}.`
            : <>Costs nothing and wins nothing: the call never enters the pot, so it cannot
              dilute anyone who staked. It is scored by the same rule, ranks on the free
              board, and reports what {usd(notional, 0)} on it{' '}
              <span className="text-zinc-300">would have won</span>.</>}
        </p>
      ) : (
        <p className="text-[10px] text-zinc-500 leading-relaxed">
          Your score is <span className="text-zinc-300">dollars × accuracy</span>, where accuracy is{' '}
          {cfg?.model === 'linear' && cfg?.tolerance === 1
            ? '1 − |called − actual| / actual'
            : `${cfg?.model}(|called − actual| / actual, ${cfg?.tolerance})`}.
          The pot pays out pro-rata by score when the round closes.
          {freeOn && !funded && (
            <> No balance yet?{' '}
              <button className="text-blue-400 hover:underline" onClick={() => setMode('free')}>
                call one free
              </button>.
            </>
          )}
        </p>
      )}
    </div>
  )
}


/* ─── Withdraw ────────────────────────────────────────────────── */

function Cashout({ address, balance, tokens, cfg, withdrawals, signFor, onDone }: any) {
  const [amount, setAmount] = useState('')
  const [symbol, setSymbol] = useState<string>(tokens[0]?.symbol || 'USDC')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!address) return toast.error('Connect a wallet first')
    const value = Number(amount)
    if (!(value > 0)) return toast.error('Enter an amount')
    setBusy(true)
    try {
      const amountStr = value.toFixed(6)
      const { signature, nonce } = await signFor('withdraw', { amount: amountStr, token: symbol })
      const query = new URLSearchParams({
        address, amount: amountStr, token: symbol, nonce: String(nonce),
      })
      if (signature) query.set('signature', signature)
      const out = await post(`${API}/pool/withdraw?${query}`)
      toast.success(out.status === 'sent'
        ? `Sent ${usd(out.amount)} ${out.token}`
        : `Queued ${usd(out.amount)} ${out.token} — the operator releases it`)
      setAmount('')
      onDone()
    } catch (err: any) {
      toast.error(err.message || 'Withdrawal failed')
    } finally { setBusy(false) }
  }

  return (
    <div className="card p-4 space-y-3">
      <div className="text-sm text-white font-medium">Your balance</div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <Field label="Available" value={usd(balance?.available || 0)} />
        <Field label="At stake" value={usd(balance?.at_stake || 0)} />
        <Field label="Won" value={usd(balance?.won || 0)} tone={(balance?.won || 0) > 0 ? 'up' : ''} />
        <Field label="Deposited" value={usd(balance?.deposited || 0)} />
      </div>

      <div className="flex gap-2">
        <input className="input flex-1" placeholder={`$ (min ${fmt(cfg?.min_withdraw || 1, 2)})`}
               value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" />
        <select className="input w-24" value={symbol} onChange={e => setSymbol(e.target.value)}>
          {tokens.map((t: any) => <option key={t.symbol} value={t.symbol}>{t.symbol}</option>)}
        </select>
      </div>
      <button className="btn btn-ghost w-full text-xs py-2" disabled={busy || !address} onClick={submit}>
        {busy ? 'Signing…' : 'Withdraw'}
      </button>

      {withdrawals?.length > 0 && (
        <div className="space-y-1 pt-1">
          {withdrawals.slice(0, 3).map((w: any) => (
            <div key={w.id} className="flex items-center justify-between text-[10px]">
              <span className="text-zinc-500">#{w.id} {usd(w.amount)} {w.token}</span>
              {w.tx
                ? <a className="up" href={txUrl(w.tx)} target="_blank" rel="noreferrer">{w.status} ↗</a>
                : <span className={w.status === 'failed' ? 'down' : 'text-zinc-400'}>{w.status}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Field({ label, value, tone }: any) {
  return (
    <div>
      <div className="text-[10px] text-zinc-500">{label}</div>
      <div className={`tabular-nums ${tone || 'text-white'}`}>{value}</div>
    </div>
  )
}


/* ─── This round ──────────────────────────────────────────────── */

function RoundView({ round, cfg, address }: any) {
  if (!round) return null
  if (!round.assets?.length) {
    return (
      <div className="card p-8 text-center">
        <div className="text-sm text-zinc-400">Nothing staked in round {round.index} yet</div>
        <div className="text-[10px] text-zinc-500 mt-1">
          The pot opens with the first stake · closes in {countdown(round.seconds_to_close)}
          {round.free_per_round > 0 && ' · free calls are scored either way'}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {round.assets.map((pot: any) => (
        <div key={pot.asset} className="card overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-[10px] font-bold text-emerald-400">
                {pot.asset[0]}
              </div>
              <div>
                <div className="text-sm text-white font-medium">{pot.asset} pot</div>
                <div className="text-[10px] text-zinc-500">
                  {pot.stakers} staker{pot.stakers === 1 ? '' : 's'} · {usd(pot.gross)} in
                  {pot.fee > 0 && ` · ${usd(pot.fee)} fee`}
                </div>
              </div>
            </div>
            <div className="text-right">
              <div className="text-sm text-white tabular-nums">
                {pot.actual_price ? px(pot.actual_price) : '—'}
              </div>
              <div className={`text-[10px] ${pot.provisional ? 'text-amber-400/80' : 'text-zinc-500'}`}>
                {pot.provisional ? 'live mark · provisional' : `settled · ${pot.price_mode}`}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-[1fr_90px_80px_70px_90px] gap-2 px-4 py-2 text-[10px] text-zinc-500 font-medium">
            <span>Staker</span><span className="text-right">Called</span>
            <span className="text-right">Off by</span><span className="text-right">Score</span>
            <span className="text-right">{pot.provisional ? 'Would take' : 'Paid'}</span>
          </div>
          {pot.entries.map((e: any) => {
            const mine = address && e.address?.toLowerCase() === address.toLowerCase()
            return (
              <div key={e.id} className={`table-row grid-cols-[1fr_90px_80px_70px_90px] gap-2 ${mine ? 'bg-blue-500/[0.06]' : ''}`}>
                <span className="text-xs text-zinc-300">
                  {mine ? <span className="text-blue-400">you</span> : short(e.address)}
                  <span className="text-zinc-600 ml-2">{usd(e.amount)}</span>
                </span>
                <span className="text-right text-xs text-white tabular-nums">{px(e.predicted_price)}</span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">
                  {e.rel_error == null ? '—' : pct(e.rel_error, 2)}
                </span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">
                  {e.score == null ? '—' : fmt(e.score, 1)}
                </span>
                <span className={`text-right text-xs tabular-nums ${
                  e.payout == null ? 'text-zinc-500'
                    : e.payout > e.amount ? 'up' : e.payout < e.amount ? 'down' : 'text-zinc-300'}`}>
                  {e.payout == null ? '—' : usd(e.payout)}
                </span>
              </div>
            )
          })}

          {pot.free?.length > 0 && <FreeCalls pot={pot} address={address} />}
        </div>
      ))}
      <p className="text-[10px] text-zinc-500 px-1">
        Each asset has its own pot — a call on one never pays out of another&apos;s.
        Free calls sit outside the pot entirely, so they never dilute it.
        Round {round.index} settles at the {cfg?.model} score against the Hyperliquid mark at close.
      </p>
    </div>
  )
}


function FreeCalls({ pot, address }: any) {
  return (
    <div className="border-t border-white/5 bg-white/[0.015]">
      <div className="grid grid-cols-[1fr_90px_80px_70px_90px] gap-2 px-4 py-2 text-[10px] text-zinc-600 font-medium">
        <span>
          Free calls
          <span className="text-zinc-700 ml-2 font-normal">outside the pot — nothing paid</span>
        </span>
        <span className="text-right">Called</span>
        <span className="text-right">Off by</span>
        <span className="text-right" />
        <span className="text-right">Would have won</span>
      </div>
      {pot.free.map((e: any) => {
        const mine = address && e.address?.toLowerCase() === address.toLowerCase()
        return (
          <div key={e.id} className={`table-row grid-cols-[1fr_90px_80px_70px_90px] gap-2 ${mine ? 'bg-blue-500/[0.06]' : ''}`}>
            <span className="text-xs text-zinc-500">
              {mine ? <span className="text-blue-400">you</span> : short(e.address)}
              <span className="text-zinc-700 ml-2">free</span>
            </span>
            <span className="text-right text-xs text-zinc-400 tabular-nums">{px(e.predicted_price)}</span>
            <span className="text-right text-xs text-zinc-500 tabular-nums">
              {e.rel_error == null ? '—' : pct(e.rel_error, 2)}
            </span>
            <span />
            <span className="text-right text-xs tabular-nums text-zinc-500">
              {e.would_win == null ? '—' : (
                <>
                  {usd(e.would_win)}
                  <span className={`ml-1.5 ${e.would_net > 0 ? 'up' : e.would_net < 0 ? 'down' : ''}`}>
                    {e.would_net > 0 ? '+' : ''}{fmt(e.would_net, 2)}
                  </span>
                </>
              )}
            </span>
          </div>
        )
      })}
    </div>
  )
}


/* ─── Past rounds ─────────────────────────────────────────────── */

function HistoryView({ rounds }: any) {
  if (!rounds?.length) return <div className="card p-8 text-center text-sm text-zinc-500">No settled rounds yet</div>
  return (
    <div className="space-y-3">
      {rounds.map((r: any) => (
        <div key={r.index} className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-white font-medium">Round {r.index}</div>
            <div className="text-[10px] text-zinc-500">
              {new Date(r.closes * 1000).toLocaleString()} · {r.status}
            </div>
          </div>
          {Object.keys(r.assets || {}).length === 0 ? (
            <div className="text-[10px] text-zinc-500">{usd(r.staked)} staked · not settled yet</div>
          ) : (
            <div className="space-y-1">
              {Object.entries(r.assets).map(([asset, a]: any) => (
                <div key={asset} className="flex items-center justify-between text-xs">
                  <span className="text-zinc-300">{asset}</span>
                  <span className="text-zinc-500">closed {px(a.actual_price)}</span>
                  <span className="text-zinc-400">{usd(a.pot)} pot</span>
                  <span className={a.mode === 'refund' ? 'text-amber-400/80' : 'up'}>
                    {a.mode === 'refund'
                      ? 'no winner — refunded'
                      : `${short(a.winner?.address)} took ${usd(a.winner?.payout)}`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}


/* ─── Stakers ─────────────────────────────────────────────────── */

function BoardView({ board, address }: any) {
  if (!board?.length) return <div className="card p-8 text-center text-sm text-zinc-500">No settled stakes yet</div>
  return (
    <div className="card overflow-hidden">
      <div className="grid grid-cols-[40px_1fr_90px_90px_80px_70px] gap-2 px-4 py-2 text-[10px] text-zinc-500 font-medium">
        <span>#</span><span>Staker</span><span className="text-right">Staked</span>
        <span className="text-right">PnL</span><span className="text-right">Accuracy</span>
        <span className="text-right">Win rate</span>
      </div>
      {board.map((row: any, i: number) => {
        const mine = address && row.address?.toLowerCase() === address.toLowerCase()
        return (
          <div key={row.address} className={`table-row grid-cols-[40px_1fr_90px_90px_80px_70px] gap-2 ${mine ? 'bg-blue-500/[0.06]' : ''}`}>
            <span className="text-xs text-zinc-500">{i + 1}</span>
            <span className="text-xs text-zinc-300">{mine ? <span className="text-blue-400">you</span> : short(row.address)}</span>
            <span className="text-right text-xs text-zinc-400 tabular-nums">{usd(row.staked)}</span>
            <span className={`text-right text-xs tabular-nums ${row.pnl >= 0 ? 'up' : 'down'}`}>
              {row.pnl >= 0 ? '+' : ''}{usd(row.pnl)}
            </span>
            <span className="text-right text-xs text-zinc-400 tabular-nums">{pct(row.avg_accuracy, 1)}</span>
            <span className="text-right text-xs text-zinc-400 tabular-nums">{row.win_rate}%</span>
          </div>
        )
      })}
    </div>
  )
}


function FreeBoardView({ board, cfg, address }: any) {
  if (!board?.length) {
    return (
      <div className="card p-8 text-center">
        <div className="text-sm text-zinc-400">No free calls yet</div>
        <div className="text-[10px] text-zinc-500 mt-1">
          {cfg?.free_per_round > 0
            ? `Everyone gets ${cfg.free_per_round} a round, one per asset — no deposit, no gas.`
            : 'Free play is switched off.'}
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <div className="card overflow-hidden">
        <div className="grid grid-cols-[40px_1fr_60px_80px_90px_90px] gap-2 px-4 py-2 text-[10px] text-zinc-500 font-medium">
          <span>#</span><span>Caller</span><span className="text-right">Calls</span>
          <span className="text-right">Accuracy</span><span className="text-right">Would have won</span>
          <span className="text-right">vs notional</span>
        </div>
        {board.map((row: any, i: number) => {
          const mine = address && row.address?.toLowerCase() === address.toLowerCase()
          return (
            <div key={row.address} className={`table-row grid-cols-[40px_1fr_60px_80px_90px_90px] gap-2 ${mine ? 'bg-blue-500/[0.06]' : ''}`}>
              <span className="text-xs text-zinc-500">{i + 1}</span>
              <span className="text-xs text-zinc-300">
                {mine ? <span className="text-blue-400">you</span> : short(row.address)}
                {row.staker && <span className="text-[9px] text-emerald-400/70 ml-2">stakes too</span>}
              </span>
              <span className="text-right text-xs text-zinc-400 tabular-nums">
                {row.settled}/{row.calls}
              </span>
              <span className="text-right text-xs text-zinc-400 tabular-nums">
                {row.settled ? pct(row.avg_accuracy, 1) : '—'}
              </span>
              <span className="text-right text-xs text-zinc-400 tabular-nums">{usd(row.would_win)}</span>
              <span className={`text-right text-xs tabular-nums ${row.would_net >= 0 ? 'up' : 'down'}`}>
                {row.would_net >= 0 ? '+' : ''}{usd(row.would_net)}
              </span>
            </div>
          )
        })}
      </div>
      <p className="text-[10px] text-zinc-500 px-1 leading-relaxed">
        Ranked by accuracy, because free calls win no money. &ldquo;Would have won&rdquo; prices each
        call at {usd(cfg?.free_notional || 0, 0)} against the pot that actually formed — including
        the {usd(cfg?.free_notional || 0, 0)} it would have taken to place, which is why a call that
        lands mid-field comes out slightly negative.
      </p>
    </div>
  )
}


/* ─── Owner ───────────────────────────────────────────────────── */

function OwnerView({ cfg, owner, vault, address, signFor, onDone }: any) {
  const [form, setForm] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState(false)
  const isOwner = owner?.owner && address && owner.owner.toLowerCase() === address.toLowerCase()
  const unclaimed = !owner?.claimed

  const set = (key: string, value: any) => setForm(f => ({ ...f, [key]: value }))

  const save = async () => {
    const patch = Object.entries(form).filter(([, v]) => v !== '' && v != null)
    if (!patch.length) return toast.info('Nothing changed')
    setBusy(true)
    try {
      const fields: Record<string, string> = {}
      patch.forEach(([k, v]) => { fields[k] = pyStr(k, v) })
      const { signature, nonce } = unclaimed
        ? { signature: '', nonce: 0 }
        : await signFor('set_config', fields)
      const query = new URLSearchParams(fields)
      if (signature) { query.set('signature', signature); query.set('owner', address) }
      const out = await post(`${API}/pool/config?${query}`)
      toast.success(out.note || 'Pool updated')
      setForm({})
      onDone()
    } catch (err: any) {
      toast.error(err.message || 'Update failed')
    } finally { setBusy(false) }
  }

  const claim = async () => {
    if (!address) return toast.error('Connect a wallet first')
    try {
      const out = await post(`${API}/pool/owner/claim?address=${address}`)
      toast.success(`Claimed. Owner secret: ${out.secret}`)
      onDone()
    } catch (err: any) { toast.error(err.message) }
  }

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm text-white font-medium">Pool rules</div>
          <div className="text-[10px] text-zinc-500">
            {unclaimed ? 'unclaimed — anyone can configure it'
              : isOwner ? 'you own this pool' : `owner ${short(owner.owner)}`}
          </div>
        </div>

        <div className="grid md:grid-cols-3 gap-3">
          <Setting label="Round interval" hint={`now ${cfg.interval_days}d`}>
            <select className="input w-full" value={form.interval ?? ''}
                    onChange={e => set('interval', e.target.value)}>
              <option value="">unchanged</option>
              <option value={3600}>hourly</option>
              <option value={86400}>daily</option>
              <option value={604800}>weekly</option>
              <option value={1209600}>fortnightly</option>
              <option value={2592000}>monthly</option>
            </select>
          </Setting>

          <Setting label="Scoring curve" hint={`now ${cfg.model}`}>
            <select className="input w-full" value={form.model ?? ''}
                    onChange={e => set('model', e.target.value)}>
              <option value="">unchanged</option>
              {Object.keys(cfg.models || {}).map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </Setting>

          <Setting label="Tolerance" hint={`now ${cfg.tolerance} · 1.0 = pure 1 − relL1`}>
            <input className="input w-full" placeholder="unchanged" inputMode="decimal"
                   value={form.tolerance ?? ''} onChange={e => set('tolerance', e.target.value)} />
          </Setting>

          <Setting label="Min stake" hint={`now ${usd(cfg.min_stake)}`}>
            <input className="input w-full" placeholder="unchanged" inputMode="decimal"
                   value={form.min_stake ?? ''} onChange={e => set('min_stake', e.target.value)} />
          </Setting>

          <Setting label="Protocol fee" hint={`now ${(cfg.fee_bps / 100).toFixed(2)}% · max 5%`}>
            <input className="input w-full" placeholder="basis points" inputMode="numeric"
                   value={form.fee_bps ?? ''} onChange={e => set('fee_bps', e.target.value)} />
          </Setting>

          <Setting label="Free calls / round"
                   hint={cfg.free_per_round ? `now ${cfg.free_per_round} per address` : 'off'}>
            <input className="input w-full" placeholder="0 switches free play off" inputMode="numeric"
                   value={form.free_per_round ?? ''} onChange={e => set('free_per_round', e.target.value)} />
          </Setting>

          <Setting label="Free notional" hint={`now ${usd(cfg.free_notional, 0)} per free call`}>
            <input className="input w-full" placeholder="paper stake for would-have-won" inputMode="decimal"
                   value={form.free_notional ?? ''} onChange={e => set('free_notional', e.target.value)} />
          </Setting>

          <Setting label="Entry cutoff" hint={`now ${countdown(cfg.entry_cutoff)} before close`}>
            <input className="input w-full" placeholder="seconds" inputMode="numeric"
                   value={form.entry_cutoff ?? ''} onChange={e => set('entry_cutoff', e.target.value)} />
          </Setting>
        </div>

        <div className="flex items-center gap-2 mt-4">
          <button className="btn btn-blue text-xs px-3 py-1.5" disabled={busy} onClick={save}>
            {busy ? 'Saving…' : 'Save rules'}
          </button>
          {unclaimed && (
            <button className="btn btn-ghost text-xs px-3 py-1.5" onClick={claim}>
              Claim ownership
            </button>
          )}
        </div>
        <p className="text-[10px] text-zinc-500 mt-3 leading-relaxed">
          A new interval takes effect at the next boundary — the round people have already
          staked into keeps the length it was sold with, and its scoring params are frozen
          from the moment it opened.
        </p>
      </div>

      <div className="card p-4 space-y-2">
        <div className="text-sm text-white font-medium">Vault</div>
        <div className="grid md:grid-cols-4 gap-3 text-xs">
          <Field label="Address" value={vault?.address ? short(vault.address) : 'none'} />
          <Field label="Holds" value={usd(vault?.held_total || 0)} />
          <Field label="Owes" value={usd(vault?.owed?.total || 0)}
                 tone={vault?.solvent === false ? 'down' : ''} />
          <Field label="Gas" value={vault?.gas == null ? '—' : `${fmt(vault.gas, 4)} HYPE`} />
        </div>
        <p className="text-[10px] text-zinc-500 leading-relaxed">
          {vault?.hot_key
            ? `This server holds the key — withdrawals ${vault.auto_pay ? 'send automatically' : 'queue until an owner releases them'}. Keep HYPE in it for gas.`
            : 'Watch-only vault: deposits credit, withdrawals queue for the operator to pay by hand.'}
        </p>
      </div>
    </div>
  )
}

function Setting({ label, hint, children }: any) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">{label}</div>
      {children}
      {hint && <div className="text-[10px] text-zinc-600 mt-1">{hint}</div>}
    </div>
  )
}
