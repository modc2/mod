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
 *      wallet proves it owns the address it is writing to. An agent call is
 *      signed for a stronger reason: it moves no dollars in, but it claims a
 *      share of real fee money out.
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
import { fmt, usd, pct, pxq, short, countdown, pairLabel } from '@/lib/fmt'
import { Section, Stat, Empty, Avatar, Tabs, Tag, Field, Label, Spinner } from '@/components/ui'
import Functions from '@/components/Functions'

const API = API_BASE_URL

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
const CONFIG_KINDS: Record<string, 'int' | 'float' | 'bool' | 'str' | 'json'> = {
  interval: 'int', entry_cutoff: 'int', fee_bps: 'int', spot_grace: 'int',
  tolerance: 'float', min_stake: 'float', max_stake: 'float', min_withdraw: 'float',
  model: 'str', model_params: 'json', auto_pay: 'bool', free_per_round: 'int', free_notional: 'float',
  agent_per_round: 'int', agent_share_bps: 'int', min_liquidity_usd: 'float',
}

/** Canonical JSON — sorted keys, no spaces — the form the server signs `model_params` in. */
function canonicalJson(value: any): string {
  const obj = typeof value === 'string' ? JSON.parse(value || '{}') : (value || {})
  const sorted: Record<string, any> = {}
  Object.keys(obj).sort().forEach(k => { sorted[k] = obj[k] })
  return JSON.stringify(sorted)
}

function pyStr(key: string, value: any): string {
  switch (CONFIG_KINDS[key]) {
    case 'int': return String(Math.round(Number(value)))
    case 'float': {
      const n = Number(value)
      return Number.isInteger(n) ? `${n}.0` : String(n)
    }
    case 'bool': return value ? 'True' : 'False'
    case 'json': return canonicalJson(value)
    default: return String(value)
  }
}

type View = 'round' | 'history' | 'board' | 'free' | 'agents' | 'functions' | 'admin'

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
  const [agentQuota, setAgentQuota] = useState<any>(null)
  const [agentBoard, setAgentBoard] = useState<any[]>([])
  const [view, setView] = useState<View>('round')

  // The pool settles on feeds that can answer "the price at the close":
  // Hyperliquid marks, Bittensor subnet prices, and DEX pools on Solana/Base
  // (hourly candles, behind the owner's liquidity floor).
  const hlMarkets = (markets || []).filter((m: any) =>
    (m.source === 'hyperliquid' || m.source === 'bittensor' || m.source === 'dex') && m.active)

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
    if (!address) {
      setBalance(null); setWithdrawals([]); setFreeQuota(null); setAgentQuota(null)
      return
    }
    const [b, w, f, a] = await Promise.all([
      get(`${API}/pool/balance/${address}`),
      get(`${API}/pool/withdrawals?address=${address}`),
      get(`${API}/pool/free/${address}`),
      get(`${API}/pool/agent/${address}`),
    ])
    if (b) setBalance(b)
    if (w) setWithdrawals(w)
    if (f) setFreeQuota(f)
    if (a) setAgentQuota(a)
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
    if (view === 'agents') get(`${API}/pool/agent/leaderboard`).then(r => r && setAgentBoard(r))
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

  if (!cfg) {
    return (
      <div className="card">
        <Empty msg={<span className="inline-flex items-center gap-2"><Spinner /> Loading the pool…</span>} />
      </div>
    )
  }

  const tokens: any[] = Object.values(vault?.tokens || {})
  const isOwner = owner?.owner && address && owner.owner.toLowerCase() === address.toLowerCase()

  return (
    <div className="space-y-5">
      <Hero stats={stats} round={round} cfg={cfg} vault={vault} markets={hlMarkets} />

      {!vault?.address ? (
        <div className="card p-6">
          <div className="text-sm text-white font-medium mb-1.5">The pool has no vault yet</div>
          <p className="note">
            Deposits need an address on {HYPEREVM.name} to land in. The owner creates one with{' '}
            <code className="mono t2">m prefi/pool-create-vault</code> (a custodial hot wallet
            this server holds the key to) or points the pool at an address they already control with{' '}
            <code className="mono t2">m prefi/pool-set-vault address=0x…</code>.
          </p>
        </div>
      ) : (
        <div className="grid lg:grid-cols-[1fr_1.25fr_1fr] gap-4 items-start">
          <Deposit address={address} vault={vault} tokens={tokens} walletClient={walletClient} onDone={reload} />
          <Stake address={address} balance={balance} cfg={cfg} round={round}
                 markets={hlMarkets} signFor={signFor} onDone={reload}
                 quota={freeQuota} agentQuota={agentQuota} />
          <Cashout address={address} balance={balance} tokens={tokens} cfg={cfg}
                   withdrawals={withdrawals} signFor={signFor} onDone={reload} />
        </div>
      )}

      <Tabs size="sm" value={view} onChange={setView} tabs={[
        { id: 'round', label: `Round ${round?.index ?? ''}`.trim(), count: round?.assets?.length },
        { id: 'history', label: 'Past rounds' },
        { id: 'board', label: 'Stakers' },
        { id: 'free', label: 'Free play' },
        { id: 'agents', label: 'Agents' },
        { id: 'functions', label: 'Functions' },
        { id: 'admin', label: isOwner ? 'Owner · you' : owner?.claimed ? 'Rules' : 'Owner' },
      ]} />

      {view === 'round' && <RoundView round={round} cfg={cfg} address={address} />}
      {view === 'history' && <HistoryView rounds={history} />}
      {view === 'board' && <BoardView board={board} address={address} />}
      {view === 'free' && <FreeBoardView board={freeBoard} cfg={cfg} address={address} />}
      {view === 'agents' && <AgentBoardView board={agentBoard} cfg={cfg} address={address}
                                            quota={agentQuota} round={round} />}
      {view === 'functions' && <Functions address={address} walletClient={walletClient} cfg={cfg}
                                          owner={owner} signFor={signFor} onDone={reload} />}
      {view === 'admin' && <OwnerView cfg={cfg} owner={owner} vault={vault} address={address}
                                      signFor={signFor} onDone={reload} />}
    </div>
  )
}


/* ─── Hero: the round, the pot, the clock ─────────────────────── */

function Hero({ stats, round, cfg, vault, markets }: any) {
  const s = stats || {}
  const total = round ? round.closes - round.opens : 0
  const elapsed = round ? Math.max(0, Math.min(total, total - round.seconds_to_close)) : 0
  const progress = total ? elapsed / total : 0
  const cutoff = total && round ? (round.entry_deadline - round.opens) / total : 1
  const open = round?.entries_open !== false
  const insolvent = vault?.solvent === false
  const count = (src: string) => markets.filter((m: any) => m.source === src).length

  return (
    <div className="hero p-6 md:p-7">
      <div className="grid lg:grid-cols-[1.3fr_1fr] gap-8">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <Tag tone={open ? 'accent' : 'warn'}>
              <span className={`dot ${open ? 'dot-live' : ''}`} style={open ? {} : { background: 'var(--warn)' }} />
              {open ? 'entries open' : 'entries closed'}
            </Tag>
            <span className="text-xs t3">round <span className="mono t2">{round?.index ?? s.round ?? 0}</span> · every {cfg?.interval_days ?? 7}d</span>
          </div>

          <div className="mt-4 flex items-end gap-4 flex-wrap">
            <div>
              <div className="stat-label">Closes in</div>
              <div className="num text-[44px] leading-none font-medium text-white mt-2 tracking-tight">{countdown(round?.seconds_to_close)}</div>
            </div>
            <div className="pb-1 text-xs t3 leading-relaxed">
              {open
                ? <>entries close in <span className="t2 mono">{countdown(round?.seconds_to_deadline)}</span></>
                : <>settling at the close</>}
              <br />
              {round?.closes && <span>{new Date(round.closes * 1000).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>}
            </div>
          </div>

          <div className="mt-5">
            <div className="bar">
              <div className="bar-fill" style={{ width: `${progress * 100}%` }} />
              <div className="bar-mark" style={{ left: `${cutoff * 100}%` }} title="entry cutoff" />
            </div>
            <div className="flex justify-between text-[10.5px] t3 mt-2 mono">
              <span>opened {round?.opens ? new Date(round.opens * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—'}</span>
              <span className="warn">entry cutoff</span>
              <span>close</span>
            </div>
          </div>

          <p className="note mt-5 max-w-lg">
            Stake USDC on where a market closes. Every asset is its own pot; at the close the pot splits{' '}
            <b>pro-rata by dollars × accuracy</b> — {cfg?.model} scoring, tolerance {cfg?.tolerance}.
            {cfg?.free_per_round > 0 && <> {cfg.free_per_round} free calls a round need no deposit.</>}
            {cfg?.agent_per_round > 0 && <> Agents call with <b>bloctime</b> instead of dollars and split{' '}
              {(cfg.agent_share_bps / 100).toFixed(0)}% of the fee.</>}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 content-start">
          <Stat label="In the pool" value={usd(s.tvl || 0)}
            sub={`${usd(s.at_stake || 0)} at stake this round`} />
          <Stat label="Stakers" value={s.stakers || 0}
            sub={[
              s.free_callers && `${s.free_callers} free`,
              s.agent_callers && `${s.agent_callers} agent`,
            ].filter(Boolean).join(' · ') || `${s.entries_open || 0} open entries`} />
          <Stat label="Markets" value={markets.length}
            sub={[
              count('hyperliquid') && `${count('hyperliquid')} Hyperliquid`,
              count('bittensor') && `${count('bittensor')} Bittensor`,
              count('dex') && `${count('dex')} DEX`,
            ].filter(Boolean).join(' · ') || 'none listed yet'} />
          <Stat label="Vault" tone={insolvent ? 'down' : undefined}
            value={vault?.address ? <a className="link" href={addressUrl(vault.address)} target="_blank" rel="noreferrer">{short(vault.address)}</a> : 'none'}
            sub={insolvent
              ? `holds ${usd(vault?.held_total || 0)} against ${usd(vault?.owed?.total || 0)} owed`
              : vault?.address ? `${HYPEREVM.name} · ${usd(vault?.held_total || 0)} held · covered` : 'not configured'} />
        </div>
      </div>
    </div>
  )
}


/* ─── Action card frame ───────────────────────────────────────── */

function ActionCard({ step, title, aside, accent, children }: any) {
  return (
    <div className={`card ${accent ? 'card-accent' : ''}`}>
      <div className="section-head">
        <div className="flex items-center gap-2.5">
          <span className={`step ${accent ? 'on' : ''}`}>{step}</span>
          <span className="section-title">{title}</span>
        </div>
        {aside}
      </div>
      <div className="p-[18px] space-y-3">{children}</div>
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
    <ActionCard step="1" title="Deposit" aside={
      <a href={addressUrl(vault.address)} target="_blank" rel="noreferrer" className="text-[11px] t3 hover:text-white mono">
        vault {short(vault.address)} ↗
      </a>
    }>
      <div className="seg">
        {tokens.map((t: any) => (
          <button key={t.symbol} onClick={() => setSymbol(t.symbol)} className={`seg-btn ${symbol === t.symbol ? 'active' : ''}`}>
            {t.symbol}
          </button>
        ))}
      </div>

      <div className="field-group">
        <input className="input input-lg pr-20" placeholder="0.00" value={amount}
               onChange={e => setAmount(e.target.value)} inputMode="decimal" />
        <button className="btn btn-ghost btn-xs absolute right-2 top-1/2 -translate-y-1/2"
                onClick={() => held != null && setAmount(String(held))} disabled={held == null}>MAX</button>
      </div>
      <div className="text-[11px] t3">
        {!address ? `Deposits land on ${HYPEREVM.name}`
          : held == null ? `${HYPEREVM.name} balance unavailable`
          : <>You hold <span className="mono t2">{fmt(held, 2)} {symbol}</span> on {HYPEREVM.name}</>}
      </div>

      <button className="btn btn-secondary w-full" disabled={busy || !address} onClick={send}>
        {busy ? <><Spinner /> Depositing…</> : `Deposit ${symbol}`}
      </button>

      <details className="text-[11px] t3">
        <summary className="hover:text-white">Already sent one? Credit by hash</summary>
        <div className="flex gap-2 mt-2">
          <input className="input flex-1 text-xs mono py-2" placeholder="0x…" value={manual}
                 onChange={e => setManual(e.target.value)} />
          <button className="btn btn-ghost btn-sm" disabled={busy || !manual} onClick={credit}>Credit</button>
        </div>
      </details>
    </ActionCard>
  )
}


/* ─── Stake ───────────────────────────────────────────────────── */

/**
 * Three ways into a round, ranked by what they are worth to the caller:
 * dollars at risk, locked time, or nothing at all. They share one form
 * because they are one question — where does this close — and differ only
 * in what backs the answer.
 */
type Mode = 'paid' | 'free' | 'agent'
const MODES: { id: Mode; label: string }[] = [
  { id: 'paid', label: 'Stake' },
  { id: 'agent', label: 'Agent' },
  { id: 'free', label: 'Free' },
]
const MODE_TONE: Record<Mode, string> = { paid: 'accent', agent: 'teal', free: 'violet' }


function Stake({ address, balance, cfg, round, markets, signFor, onDone, quota, agentQuota }: any) {
  const [asset, setAsset] = useState<string>(markets[0]?.symbol || '')
  const [price, setPrice] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState<Mode | null>(null)

  useEffect(() => {
    if (!asset && markets[0]) setAsset(markets[0].symbol)
  }, [markets, asset])

  const freeOn = (quota?.enabled ?? (cfg?.free_per_round > 0)) !== false
  const freeLeft = quota?.remaining ?? cfg?.free_per_round ?? 0
  const funded = (balance?.available || 0) >= (cfg?.min_stake || 0)

  // Agent play needs the bloctime reader up AND locked value overlapping this
  // round. `enabled` is the deployment's answer, `eligible` is this address's.
  const agentOn = (agentQuota?.enabled ?? (cfg?.agent_per_round > 0)) !== false
  const agentLeft = agentQuota?.remaining ?? cfg?.agent_per_round ?? 0
  const locked = agentQuota?.usd_seconds
  const agentEligible = agentQuota?.eligible === true

  // Land on the mode this caller can actually use. Money first if they have
  // it; then agent play, which pays real dollars for locked time; then free,
  // which pays none. That order is the ranking by what the mode is worth.
  const effective = mode ?? (funded ? 'paid'
    : agentOn && agentEligible ? 'agent'
    : freeOn ? 'free' : 'paid')
  const free = effective === 'free'
  const agent = effective === 'agent'
  const nomoney = free || agent
  const activeQuota = agent ? agentQuota : quota
  const assetUsed = (activeQuota?.assets_used || []).includes(asset)

  const market = markets.find((m: any) => m.symbol === asset)
  const quote = market?.quote
  const mark = market?.price ?? market?.price_usd
  const move = mark && Number(price) > 0 ? (Number(price) - mark) / mark * 100 : null
  const notional = quota?.notional ?? cfg?.free_notional ?? 0

  const submit = async () => {
    if (!address) return toast.error('Connect a wallet first')
    const called = Number(price)
    if (!(called > 0)) return toast.error('Enter a price')
    const stake = Number(amount)
    if (!nomoney && !(stake > 0)) return toast.error('Enter an amount to stake')
    setBusy(true)
    try {
      // These strings are the signed ones — the server re-renders the message
      // with the same fixed precision, so they have to match exactly.
      const priceStr = called.toFixed(8)
      const roundStr = String(round?.index ?? 0)
      if (agent) {
        // Same signed shape as a free call — asset, price, round. What differs
        // is what it claims: a cut of the fee, weighted by locked time.
        const { signature, nonce } = await signFor('agent_stake', {
          asset, price: priceStr, round: roundStr,
        })
        const query = new URLSearchParams({
          address, asset, predicted_price: priceStr, nonce: String(nonce),
        })
        if (signature) query.set('signature', signature)
        const out = await post(`${API}/pool/agent?${query}`)
        toast.success(`Agent call on ${out.asset} at ${pxq(out.predicted_price, out.quote)} — ` +
                      `${fmt(out.usd_seconds, 0)} usd·s of bloctime behind it, ` +
                      `${out.agent_remaining} left this round`)
      } else if (free) {
        const { signature, nonce } = await signFor('free_stake', {
          asset, price: priceStr, round: roundStr,
        })
        const query = new URLSearchParams({
          address, asset, predicted_price: priceStr, nonce: String(nonce),
        })
        if (signature) query.set('signature', signature)
        const out = await post(`${API}/pool/free?${query}`)
        toast.success(`Free call on ${out.asset} at ${pxq(out.predicted_price, out.quote)} — ` +
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
        toast.success(`Staked ${usd(out.staked)} on ${out.asset} at ${pxq(out.predicted_price, out.quote)}`)
      }
      setAmount(''); setPrice('')
      onDone()
    } catch (err: any) {
      toast.error(err.message ||
        (agent ? 'Agent call failed' : free ? 'Free call failed' : 'Stake failed'))
    } finally { setBusy(false) }
  }

  const open = round?.entries_open !== false
  const blocked = (free && (!freeOn || freeLeft <= 0 || assetUsed))
    || (agent && (!agentOn || !agentEligible || agentLeft <= 0 || assetUsed))
  const nudge = (p: number) => mark && setPrice(String(Number((mark * (1 + p / 100)).toPrecision(8))))

  return (
    <ActionCard step="2" accent
      title={agent ? 'Call it with time' : free ? 'Call it free' : 'Stake'}
      aside={
        <div className="seg">
          {MODES.map(({ id, label }) => {
            const off = (id === 'free' && !freeOn) || (id === 'agent' && !agentOn)
            const left = id === 'free' ? freeLeft : agentLeft
            return (
              <button key={id} onClick={() => setMode(id)} disabled={off}
                title={id === 'agent' ? 'No dollars down — weighted by bloctime locked across the round' : undefined}
                className={`seg-btn ${effective === id ? `active ${MODE_TONE[id]}` : ''}`}>
                {label}{id !== 'paid' && !off && ` · ${left}`}
              </button>
            )
          })}
        </div>
      }>
      <div>
        <Label>Market</Label>
        <select className="input" value={asset} onChange={e => setAsset(e.target.value)}>
          {markets.length === 0 && <option value="">No Hyperliquid, Bittensor, Solana or Base markets listed</option>}
          {markets.map((m: any) => (
            <option key={m.symbol} value={m.symbol} disabled={m.source === 'dex' && m.eligible === false}>
              {pairLabel(m)}
              {m.source === 'bittensor' ? ' (TAO)' : m.source === 'dex' && m.eligible === false ? ' — under the liquidity floor' : ''}
            </option>
          ))}
        </select>
      </div>

      <div>
        <Label hint={mark ? <>mark <span className="mono t2">{pxq(mark, quote)}</span></> : undefined}>
          Close price{quote === 'TAO' ? ' in TAO' : ''}
        </Label>
        <div className="field-group">
          <input className="input input-lg" placeholder={mark ? pxq(mark, quote).slice(1) : '0.00'}
                 value={price} onChange={e => setPrice(e.target.value)} inputMode="decimal" />
          {move != null && <span className={`suffix mono ${move >= 0 ? 'up' : 'down'}`}>{move >= 0 ? '+' : ''}{move.toFixed(2)}%</span>}
        </div>
        {mark && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {[-5, -2, -1, 0, 1, 2, 5].map(p => (
              <button key={p} onClick={() => nudge(p)} className="chip mono">{p === 0 ? 'mark' : `${p > 0 ? '+' : ''}${p}%`}</button>
            ))}
          </div>
        )}
      </div>

      <div>
        <Label hint={nomoney
          ? (agent ? <>bloctime <span className="mono t2">{locked == null ? '—' : `${fmt(locked, 0)} usd·s`}</span></> : undefined)
          : <>balance <span className="mono t2">{usd(balance?.available || 0)}</span></>}>
          {agent ? 'Weight' : free ? 'Stake' : 'Amount'}
        </Label>
        {agent ? (
          <div className="input flex items-center justify-between text-sm">
            <span className="teal font-medium">Locked time, not dollars</span>
            <span className="t3 text-xs">
              {locked == null ? 'bloctime unreachable'
                : locked > 0 ? `${fmt(locked, 0)} usd·s this round`
                : 'nothing locked'}
            </span>
          </div>
        ) : free ? (
          <div className="input flex items-center justify-between text-sm">
            <span className="violet font-medium">No money down</span>
            <span className="t3 text-xs">scored as {usd(notional, 0)}</span>
          </div>
        ) : (
          <div className="field-group">
            <input className="input input-lg pr-24" placeholder={`min ${fmt(cfg?.min_stake || 0, 0)}`}
                   value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" />
            <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1.5">
              <span className="text-xs t3">USD</span>
              <button className="btn btn-ghost btn-xs" disabled={!(balance?.available > 0)}
                onClick={() => setAmount(String(balance.available))}>MAX</button>
            </div>
          </div>
        )}
      </div>

      <button className={`btn btn-lg w-full ${agent ? 'btn-teal' : free ? 'btn-violet' : 'btn-primary'}`}
              disabled={busy || !address || !open || blocked || markets.length === 0}
              onClick={submit}>
        {busy ? <><Spinner /> Signing…</>
          : !open ? 'Entries closed'
          : agent ? 'Call it with time'
          : free ? 'Call it free'
          : `Stake${amount ? ` ${usd(Number(amount))}` : ''}`}
      </button>

      <p className="note">
        {!address ? 'Connect a wallet — the call is signed so the board knows it is yours.'
          : agent ? (
            !agentOn ? 'Agent play is switched off, or this deployment has no bloctime reader.'
            : locked == null ? 'The bloctime feed is unreachable, so locked time cannot be verified right now. Try again in a moment.'
            : !agentEligible ? <>Nothing locked across this round. Lock value on <b>bloctime</b> first —
              your weight is <span className="mono">USD locked × seconds</span> inside the round, exactly what
              bloctime mints BLOC for.</>
            : assetUsed ? `You already have an agent call on ${asset} this round — one per asset, so the board means something.`
            : agentLeft <= 0 ? `Out of agent calls. They reset with the round, in ${countdown(round?.seconds_to_close)}.`
            : !(cfg?.fee_bps > 0) ? <>Your call is scored and ranked, but this pool takes <b>no protocol fee</b> —
              and the agent pot is a slice of that fee, so there is nothing to pay out yet. The owner sets a fee
              to fund it. You carry <span className="mono t2">{fmt(locked, 0)} usd·s</span>.</>
            : <>No dollars in, real dollars out: agents split <b>{pct((agentQuota?.agent_share_bps ?? cfg?.agent_share_bps ?? 0) / 10000, 0)}</b> of
              each pot&apos;s protocol fee, divided by <span className="mono">usd·seconds × accuracy</span>. Never touches the
              stakers&apos; pot. You carry <span className="mono t2">{fmt(locked, 0)} usd·s</span>.</>
          )
          : free ? (
            !freeOn ? 'Free play is switched off — stake to enter a round.'
            : assetUsed ? `You already have a free call on ${asset} this round — one per asset, so the board means something.`
            : freeLeft <= 0 ? `Out of free calls. They reset with the round, in ${countdown(round?.seconds_to_close)}.`
            : <>Costs nothing, wins nothing: it never enters the pot. Scored by the same rule, ranked on the free board,
              and reports what <b>{usd(notional, 0)} would have won</b>.</>
          ) : (
            <>Score is <b>dollars × accuracy</b>, accuracy is{' '}
              <span className="mono">{cfg?.model === 'linear' && cfg?.tolerance === 1 ? '1 − |called − actual| / actual' : `${cfg?.model}(|called − actual| / actual, ${cfg?.tolerance})`}</span>.
              The pot pays pro-rata by score at the close.
              {freeOn && !funded && <> No balance yet? <button className="link" onClick={() => setMode('free')}>Call one free</button>.</>}
            </>
          )}
      </p>
    </ActionCard>
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

  const won = balance?.won || 0
  return (
    <ActionCard step="3" title="Cash out" aside={
      address && <span className="text-[11px] t3">available <span className="mono t2">{usd(balance?.available || 0)}</span></span>
    }>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 p-3 rounded-[10px] bg-black/25 border border-white/[0.05]">
        <Field label="Available" value={usd(balance?.available || 0)} />
        <Field label="At stake" value={usd(balance?.at_stake || 0)} />
        <Field label="Won" value={`${won > 0 ? '+' : ''}${usd(won)}`} tone={won > 0 ? 'up' : ''} />
        <Field label="Deposited" value={usd(balance?.deposited || 0)} />
      </div>

      <div className="flex gap-2">
        <div className="field-group flex-1">
          <input className="input" placeholder={`min ${fmt(cfg?.min_withdraw || 1, 2)}`}
                 value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" />
          <button className="btn btn-ghost btn-xs absolute right-2 top-1/2 -translate-y-1/2"
            disabled={!(balance?.available > 0)} onClick={() => setAmount(String(balance.available))}>MAX</button>
        </div>
        <select className="input w-28" value={symbol} onChange={e => setSymbol(e.target.value)}>
          {tokens.map((t: any) => <option key={t.symbol} value={t.symbol}>{t.symbol}</option>)}
        </select>
      </div>
      <button className="btn btn-secondary w-full" disabled={busy || !address} onClick={submit}>
        {busy ? <><Spinner /> Signing…</> : 'Withdraw'}
      </button>

      {withdrawals?.length > 0 ? (
        <div className="space-y-1.5 pt-1">
          <div className="label mb-0">Recent</div>
          {withdrawals.slice(0, 3).map((w: any) => (
            <div key={w.id} className="flex items-center justify-between text-[11px]">
              <span className="t3 mono">#{w.id} · {usd(w.amount)} {w.token}</span>
              {w.tx
                ? <a className="link" href={txUrl(w.tx)} target="_blank" rel="noreferrer">{w.status} ↗</a>
                : <Tag tone={w.status === 'failed' ? 'down' : w.status === 'sent' ? 'up' : 'warn'}>{w.status}</Tag>}
            </div>
          ))}
        </div>
      ) : (
        <p className="note">Withdrawals are signed and {cfg?.auto_pay ? 'paid automatically from the vault' : 'released by the operator'}.</p>
      )}
    </ActionCard>
  )
}


/* ─── This round ──────────────────────────────────────────────── */

const POT_COLS = 'grid-cols-[1fr_110px_90px_80px_110px]'

function RoundView({ round, cfg, address }: any) {
  if (!round) return null
  if (!round.assets?.length) {
    return (
      <div className="card">
        <Empty title={`Nothing staked in round ${round.index} yet`}
          msg={<>The first stake opens a pot. Closes in <span className="mono t2">{countdown(round.seconds_to_close)}</span>
            {round.free_per_round > 0 && <> — free calls are scored either way</>}.</>} />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {round.assets.map((pot: any) => (
        <div key={pot.asset} className="card overflow-hidden">
          <div className="section-head">
            <div className="flex items-center gap-3">
              <Avatar symbol={pot.asset} />
              <div>
                <div className="flex items-center gap-2">
                  <span className="section-title">{pot.asset}</span>
                  <span className="count">{usd(pot.gross)} pot</span>
                </div>
                <div className="text-[11px] t3 mt-0.5">
                  {pot.stakers} staker{pot.stakers === 1 ? '' : 's'}
                  {pot.fee > 0 && ` · ${usd(pot.fee)} fee`}
                  {pot.free?.length > 0 && ` · ${pot.free.length} free call${pot.free.length === 1 ? '' : 's'}`}
                  {pot.agent?.length > 0 && ` · ${pot.agent.length} agent call${pot.agent.length === 1 ? '' : 's'}`}
                </div>
              </div>
            </div>
            <div className="text-right">
              <div className="num text-base text-white">{pot.actual_price ? pxq(pot.actual_price, pot.quote) : '—'}</div>
              <div className={`text-[11px] ${pot.provisional ? 'warn' : 't3'}`}>
                {pot.provisional ? 'live mark · provisional' : `settled · ${pot.price_mode}`}
              </div>
            </div>
          </div>

          <div className={`thead ${POT_COLS}`}>
            <span>Staker</span><span className="text-right">Called</span>
            <span className="text-right">Off by</span><span className="text-right">Score</span>
            <span className="text-right">{pot.provisional ? 'Would take' : 'Paid'}</span>
          </div>
          {pot.entries.map((e: any) => {
            const mine = address && e.address?.toLowerCase() === address.toLowerCase()
            return (
              <div key={e.id} className={`trow ${POT_COLS} ${mine ? 'mine' : ''}`}>
                <span className="text-xs">
                  {mine ? <span className="accent font-medium">you</span> : <span className="mono t2">{short(e.address)}</span>}
                  <span className="t3 ml-2 mono">{usd(e.amount)}</span>
                </span>
                <span className="text-right num text-xs text-white">{pxq(e.predicted_price, pot.quote)}</span>
                <span className="text-right num text-xs t2">{e.rel_error == null ? '—' : pct(e.rel_error, 2)}</span>
                <span className="text-right num text-xs t2">{e.score == null ? '—' : fmt(e.score, 1)}</span>
                <span className={`text-right num text-xs ${
                  e.payout == null ? 't3' : e.payout > e.amount ? 'up' : e.payout < e.amount ? 'down' : 't2'}`}>
                  {e.payout == null ? '—' : usd(e.payout)}
                </span>
              </div>
            )
          })}

          {pot.free?.length > 0 && <FreeCalls pot={pot} address={address} />}
          {pot.agent?.length > 0 && <AgentCalls pot={pot} address={address} />}
        </div>
      ))}
      <p className="note px-1">
        Each asset has its own pot — a call on one never pays out of another&apos;s. Free and agent calls sit
        outside the pot entirely, so they never dilute it; agents are paid out of the protocol&apos;s own fee
        instead. Round {round.index} settles at the {cfg?.model} score against the
        mark at close — Hyperliquid for pairs, the Bittensor indexer for subnets (in TAO), the pool&apos;s hourly
        candle for Solana and Base tokens.
      </p>
    </div>
  )
}


function FreeCalls({ pot, address }: any) {
  return (
    <div className="border-t border-white/[0.06] bg-black/20">
      <div className={`thead ${POT_COLS} border-b-0`}>
        <span className="violet">Free calls <span className="t3 normal-case tracking-normal font-normal ml-1">outside the pot — nothing paid</span></span>
        <span className="text-right">Called</span>
        <span className="text-right">Off by</span>
        <span />
        <span className="text-right">Would have won</span>
      </div>
      {pot.free.map((e: any) => {
        const mine = address && e.address?.toLowerCase() === address.toLowerCase()
        return (
          <div key={e.id} className={`trow ${POT_COLS} ${mine ? 'mine' : ''}`}>
            <span className="text-xs flex items-center gap-2">
              {mine ? <span className="accent font-medium">you</span> : <span className="mono t2">{short(e.address)}</span>}
              <Tag tone="violet">free</Tag>
            </span>
            <span className="text-right num text-xs t2">{pxq(e.predicted_price, pot.quote)}</span>
            <span className="text-right num text-xs t3">{e.rel_error == null ? '—' : pct(e.rel_error, 2)}</span>
            <span />
            <span className="text-right num text-xs t2">
              {e.would_win == null ? '—' : (
                <>{usd(e.would_win)}
                  <span className={`ml-1.5 ${e.would_net > 0 ? 'up' : e.would_net < 0 ? 'down' : 't3'}`}>
                    {e.would_net > 0 ? '+' : ''}{fmt(e.would_net, 2)}
                  </span></>
              )}
            </span>
          </div>
        )
      })}
    </div>
  )
}


/**
 * Agent calls under a pot. They are not in it — the money column is the fee
 * share they take out of the protocol's cut, not a slice of anyone's stake —
 * so the weight that earned it is shown where a staker's dollars would be.
 */
function AgentCalls({ pot, address }: any) {
  return (
    <div className="border-t border-white/[0.06] bg-black/20">
      <div className={`thead ${POT_COLS} border-b-0`}>
        <span className="teal">Agent calls <span className="t3 normal-case tracking-normal font-normal ml-1">
          paid from the fee{pot.agent_pot != null ? ` — ${usd(pot.agent_pot)} pot` : ''}</span></span>
        <span className="text-right">Called</span>
        <span className="text-right">Off by</span>
        <span className="text-right">usd·s</span>
        <span className="text-right">{pot.provisional ? 'Would take' : 'Earned'}</span>
      </div>
      {pot.agent.map((e: any) => {
        const mine = address && e.address?.toLowerCase() === address.toLowerCase()
        return (
          <div key={e.id} className={`trow ${POT_COLS} ${mine ? 'mine' : ''}`}>
            <span className="text-xs flex items-center gap-2">
              {mine ? <span className="accent font-medium">you</span> : <span className="mono t2">{short(e.address)}</span>}
              <Tag tone="teal">agent</Tag>
            </span>
            <span className="text-right num text-xs t2">{pxq(e.predicted_price, pot.quote)}</span>
            <span className="text-right num text-xs t3">{e.rel_error == null ? '—' : pct(e.rel_error, 2)}</span>
            <span className="text-right num text-xs t3">{fmt(e.usd_seconds || 0, 0)}</span>
            <span className={`text-right num text-xs ${e.payout ? 'up' : 't3'}`}>
              {e.payout == null ? '—' : usd(e.payout)}
            </span>
          </div>
        )
      })}
    </div>
  )
}


/* ─── Past rounds ─────────────────────────────────────────────── */

function HistoryView({ rounds }: any) {
  if (!rounds?.length) return <div className="card"><Empty title="No settled rounds yet" msg="Each round lands here with its close price and who took the pot." /></div>
  return (
    <div className="space-y-3">
      {rounds.map((r: any) => (
        <div key={r.index} className="card overflow-hidden">
          <div className="section-head">
            <div className="flex items-center gap-2.5">
              <span className="section-title">Round {r.index}</span>
              <Tag tone={r.status === 'settled' ? 'up' : r.status === 'open' ? 'accent' : 'neutral'}>{r.status}</Tag>
            </div>
            <span className="text-[11px] t3 mono">{new Date(r.closes * 1000).toLocaleString()}</span>
          </div>
          {Object.keys(r.assets || {}).length === 0 ? (
            <div className="px-[18px] py-3 text-xs t3">{usd(r.staked)} staked · not settled yet</div>
          ) : (
            <div>
              {Object.entries(r.assets).map(([asset, a]: any) => (
                <div key={asset} className="trow grid-cols-[1fr_120px_100px_1fr] text-xs">
                  <span className="flex items-center gap-2"><Avatar symbol={asset} size="sm" /><span className="text-white font-medium">{asset}</span></span>
                  <span className="t3">closed <span className="num t2">{pxq(a.actual_price, a.quote)}</span></span>
                  <span className="num t2">{usd(a.pot)} pot</span>
                  <span className={`text-right ${a.mode === 'refund' ? 'warn' : 'up'}`}>
                    {a.mode === 'refund' ? 'no winner — refunded' : <><span className="mono">{short(a.winner?.address)}</span> took {usd(a.winner?.payout)}</>}
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
  if (!board?.length) return <div className="card"><Empty title="No settled stakes yet" msg="Stakers rank here by profit once a round settles." /></div>
  const cols = 'grid-cols-[36px_1fr_100px_100px_90px_80px]'
  return (
    <div className="card overflow-hidden">
      <div className={`thead ${cols}`}>
        <span>#</span><span>Staker</span><span className="text-right">Staked</span>
        <span className="text-right">PnL</span><span className="text-right">Accuracy</span>
        <span className="text-right">Win rate</span>
      </div>
      {board.map((row: any, i: number) => {
        const mine = address && row.address?.toLowerCase() === address.toLowerCase()
        return (
          <div key={row.address} className={`trow ${cols} ${mine ? 'mine' : ''}`}>
            <span className={`rank rank-${i + 1}`}>{i + 1}</span>
            <span className="text-xs">{mine ? <span className="accent font-medium">you</span> : <span className="mono t2">{short(row.address)}</span>}</span>
            <span className="text-right num text-xs t2">{usd(row.staked)}</span>
            <span className={`text-right num text-xs ${row.pnl >= 0 ? 'up' : 'down'}`}>{row.pnl >= 0 ? '+' : ''}{usd(row.pnl)}</span>
            <span className="text-right num text-xs t2">{pct(row.avg_accuracy, 1)}</span>
            <span className="text-right num text-xs t2">{row.win_rate}%</span>
          </div>
        )
      })}
    </div>
  )
}


function FreeBoardView({ board, cfg, address }: any) {
  if (!board?.length) {
    return (
      <div className="card">
        <Empty title="No free calls yet"
          msg={cfg?.free_per_round > 0
            ? `Everyone gets ${cfg.free_per_round} a round, one per asset — no deposit, no gas.`
            : 'Free play is switched off.'} />
      </div>
    )
  }
  const cols = 'grid-cols-[36px_1fr_70px_90px_120px_100px]'
  return (
    <div className="space-y-2">
      <div className="card overflow-hidden">
        <div className={`thead ${cols}`}>
          <span>#</span><span>Caller</span><span className="text-right">Calls</span>
          <span className="text-right">Accuracy</span><span className="text-right">Would have won</span>
          <span className="text-right">vs notional</span>
        </div>
        {board.map((row: any, i: number) => {
          const mine = address && row.address?.toLowerCase() === address.toLowerCase()
          return (
            <div key={row.address} className={`trow ${cols} ${mine ? 'mine' : ''}`}>
              <span className={`rank rank-${i + 1}`}>{i + 1}</span>
              <span className="text-xs flex items-center gap-2">
                {mine ? <span className="accent font-medium">you</span> : <span className="mono t2">{short(row.address)}</span>}
                {row.staker && <Tag tone="up">stakes too</Tag>}
              </span>
              <span className="text-right num text-xs t2">{row.settled}/{row.calls}</span>
              <span className="text-right num text-xs t2">{row.settled ? pct(row.avg_accuracy, 1) : '—'}</span>
              <span className="text-right num text-xs t2">{usd(row.would_win)}</span>
              <span className={`text-right num text-xs ${row.would_net >= 0 ? 'up' : 'down'}`}>{row.would_net >= 0 ? '+' : ''}{usd(row.would_net)}</span>
            </div>
          )
        })}
      </div>
      <p className="note px-1">
        Ranked by accuracy, because free calls win no money. &ldquo;Would have won&rdquo; prices each
        call at {usd(cfg?.free_notional || 0, 0)} against the pot that actually formed — including
        the {usd(cfg?.free_notional || 0, 0)} it would have taken to place, which is why a call that
        lands mid-field comes out slightly negative.
      </p>
    </div>
  )
}


/**
 * The agent board. Ranked by dollars, like the staker board and unlike the
 * free one, because agents do earn — the difference is what bought the
 * earnings: time locked rather than money staked.
 */
function AgentBoardView({ board, cfg, address, quota, round }: any) {
  const share = (round?.agent_share_bps ?? cfg?.agent_share_bps ?? 0) / 10000
  const head = (
    <div className="card p-[18px]">
      <div className="grid sm:grid-cols-3 gap-4">
        <Field label="Fee share to agents" value={pct(share, 0)} />
        <Field label="Calls per agent / round" value={cfg?.agent_per_round || 'off'} />
        <Field label="Your weight this round"
               value={quota?.usd_seconds == null ? '—' : `${fmt(quota.usd_seconds, 0)} usd·s`}
               tone={quota?.eligible ? 'teal' : ''} />
      </div>
      <p className="note mt-4">
        An agent puts no dollars in. It qualifies by locking value on <b>bloctime</b>, and its weight is
        that lock measured the way bloctime measures it — <span className="mono">USD locked × seconds</span>,
        counted only inside the round. Agents never enter the money pot; they split {pct(share, 0)} of each
        settled pot&apos;s protocol fee by <span className="mono">weight × accuracy</span>, credited as real
        dollars you can withdraw. Locking more, for longer, or calling better all move the same number.
      </p>
      {!(cfg?.fee_bps > 0) && (
        <p className="note mt-3">
          This pool&apos;s protocol fee is <b className="warn">0%</b>, and the agent pot is a slice of that
          fee — so calls are scored and ranked but pay nothing until the owner sets one.
        </p>
      )}
    </div>
  )

  if (!board?.length) {
    return (
      <div className="space-y-2">
        {head}
        <div className="card">
          <Empty title="No agent calls yet"
            msg={cfg?.agent_per_round > 0
              ? 'Lock on bloctime, then call a price — the first agent call opens this board.'
              : 'Agent play is switched off.'} />
        </div>
      </div>
    )
  }

  const cols = 'grid-cols-[36px_1fr_70px_90px_120px_100px]'
  return (
    <div className="space-y-2">
      {head}
      <div className="card overflow-hidden">
        <div className={`thead ${cols}`}>
          <span>#</span><span>Agent</span><span className="text-right">Calls</span>
          <span className="text-right">Accuracy</span><span className="text-right">Avg weight</span>
          <span className="text-right">Earned</span>
        </div>
        {board.map((row: any, i: number) => {
          const mine = address && row.address?.toLowerCase() === address.toLowerCase()
          return (
            <div key={row.address} className={`trow ${cols} ${mine ? 'mine' : ''}`}>
              <span className={`rank rank-${i + 1}`}>{i + 1}</span>
              <span className="text-xs flex items-center gap-2">
                {mine ? <span className="accent font-medium">you</span> : <span className="mono t2">{short(row.address)}</span>}
                <Tag tone="teal">agent</Tag>
              </span>
              <span className="text-right num text-xs t2">{row.settled}/{row.calls}</span>
              <span className="text-right num text-xs t2">{row.settled ? pct(row.avg_accuracy, 1) : '—'}</span>
              <span className="text-right num text-xs t3">{fmt(row.avg_usd_seconds, 0)}</span>
              <span className={`text-right num text-xs ${row.earned > 0 ? 'up' : 't3'}`}>{usd(row.earned)}</span>
            </div>
          )
        })}
      </div>
      <p className="note px-1">
        &ldquo;Avg weight&rdquo; is the usd·seconds behind a settled call — the same quantity that mints BLOC
        on bloctime. Earnings come out of the protocol&apos;s fee, never out of a staker&apos;s pot, so an
        agent cannot cost a staker a cent.
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
  const dirty = Object.values(form).some(v => v !== '' && v != null)

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
    <div className="grid lg:grid-cols-[1fr_320px] gap-4 items-start">
      <Section title="Pool rules" action={
        <Tag tone={unclaimed ? 'warn' : isOwner ? 'accent' : 'neutral'}>
          {unclaimed ? 'unclaimed — anyone can configure' : isOwner ? 'you own this pool' : `owner ${short(owner.owner)}`}
        </Tag>
      }>
        <div className="p-[18px]">
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <Setting label="Round interval" hint={`now ${cfg.interval_days}d`}>
              <select className="input" value={form.interval ?? ''} onChange={e => set('interval', e.target.value)}>
                <option value="">unchanged</option>
                <option value={3600}>hourly</option>
                <option value={86400}>daily</option>
                <option value={604800}>weekly</option>
                <option value={1209600}>fortnightly</option>
                <option value={2592000}>monthly</option>
              </select>
            </Setting>

            <Setting label="Scoring curve" hint={`now ${cfg.model}`}>
              <select className="input mono" value={form.model ?? ''} onChange={e => set('model', e.target.value)}>
                <option value="">unchanged</option>
                {Object.keys(cfg.models || {}).map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </Setting>

            <Setting label="Tolerance" hint={`now ${cfg.tolerance} · sets the function's tol`}>
              <input className="input mono" placeholder="unchanged" inputMode="decimal"
                     value={form.tolerance ?? ''} onChange={e => set('tolerance', e.target.value)} />
            </Setting>

            <Setting label="Function params" hint={`now ${JSON.stringify(cfg.model_params || {})} · JSON, by name`}>
              <input className="input mono" placeholder='{"power": 4}' spellCheck={false}
                     value={form.model_params ?? ''} onChange={e => set('model_params', e.target.value)} />
            </Setting>

            <Setting label="Min stake" hint={`now ${usd(cfg.min_stake)}`}>
              <input className="input mono" placeholder="unchanged" inputMode="decimal"
                     value={form.min_stake ?? ''} onChange={e => set('min_stake', e.target.value)} />
            </Setting>

            <Setting label="Protocol fee" hint={`now ${(cfg.fee_bps / 100).toFixed(2)}% · max 5%`}>
              <input className="input mono" placeholder="basis points" inputMode="numeric"
                     value={form.fee_bps ?? ''} onChange={e => set('fee_bps', e.target.value)} />
            </Setting>

            <Setting label="Free calls / round" hint={cfg.free_per_round ? `now ${cfg.free_per_round} per address` : 'off'}>
              <input className="input mono" placeholder="0 switches free play off" inputMode="numeric"
                     value={form.free_per_round ?? ''} onChange={e => set('free_per_round', e.target.value)} />
            </Setting>

            <Setting label="Free notional" hint={`now ${usd(cfg.free_notional, 0)} per free call`}>
              <input className="input mono" placeholder="paper stake for would-have-won" inputMode="decimal"
                     value={form.free_notional ?? ''} onChange={e => set('free_notional', e.target.value)} />
            </Setting>

            <Setting label="Agent calls / round" hint={cfg.agent_per_round ? `now ${cfg.agent_per_round} per address` : 'off'}>
              <input className="input mono" placeholder="0 switches agent play off" inputMode="numeric"
                     value={form.agent_per_round ?? ''} onChange={e => set('agent_per_round', e.target.value)} />
            </Setting>

            <Setting label="Agent fee share"
                     hint={`now ${(cfg.agent_share_bps / 100).toFixed(0)}% of each pot's fee — the rest goes to the treasury`}>
              <input className="input mono" placeholder="basis points (5000 = 50%)" inputMode="numeric"
                     value={form.agent_share_bps ?? ''} onChange={e => set('agent_share_bps', e.target.value)} />
            </Setting>

            <Setting label="Entry cutoff" hint={`now ${countdown(cfg.entry_cutoff)} before close`}>
              <input className="input mono" placeholder="seconds" inputMode="numeric"
                     value={form.entry_cutoff ?? ''} onChange={e => set('entry_cutoff', e.target.value)} />
            </Setting>

            <Setting label="Min DEX liquidity"
                     hint={cfg.min_liquidity_usd ? `now ${usd(cfg.min_liquidity_usd, 0)} · Solana & Base tokens under it can't be listed or staked` : 'no floor — any pool is listable'}>
              <input className="input mono" placeholder="dollars in the pool (0 = no floor)" inputMode="decimal"
                     value={form.min_liquidity_usd ?? ''} onChange={e => set('min_liquidity_usd', e.target.value)} />
            </Setting>
          </div>

          <div className="flex items-center gap-2 mt-5">
            <button className={`btn ${dirty ? 'btn-primary' : 'btn-ghost'}`} disabled={busy || !dirty} onClick={save}>
              {busy ? <><Spinner /> Saving…</> : 'Save rules'}
            </button>
            {unclaimed && <button className="btn btn-secondary" onClick={claim}>Claim ownership</button>}
          </div>
          <p className="note mt-3">
            A new interval takes effect at the next boundary — the round people have already
            staked into keeps the length it was sold with, and its scoring params are frozen
            from the moment it opened.
          </p>
        </div>
      </Section>

      <Section title="Vault" action={
        vault?.address && <a className="text-[11px] link mono" href={addressUrl(vault.address)} target="_blank" rel="noreferrer">{short(vault.address)} ↗</a>
      }>
        <div className="p-[18px] space-y-4">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Field label="Holds" value={usd(vault?.held_total || 0)} />
            <Field label="Owes" value={usd(vault?.owed?.total || 0)} tone={vault?.solvent === false ? 'down' : ''} />
            <Field label="Gas" value={vault?.gas == null ? '—' : `${fmt(vault.gas, 4)} HYPE`} />
            <Field label="Key" value={vault?.hot_key ? 'server-held' : 'watch-only'} />
          </div>
          <div className="divider" />
          <p className="note">
            {vault?.hot_key
              ? `This server holds the key — withdrawals ${vault.auto_pay ? 'send automatically' : 'queue until an owner releases them'}. Keep HYPE in it for gas.`
              : 'Watch-only vault: deposits credit, withdrawals queue for the operator to pay by hand.'}
          </p>
        </div>
      </Section>
    </div>
  )
}

function Setting({ label, hint, children }: any) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
      {hint && <div className="text-[11px] t3 mt-1.5">{hint}</div>}
    </div>
  )
}
