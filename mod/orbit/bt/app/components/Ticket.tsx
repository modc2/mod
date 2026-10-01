'use client';
/* The order ticket — every subnet is a market, and this is how you trade it.
 *
 * One fixed netuid, Buy (stake τ → α) or Sell (unstake α → τ), signed in the
 * connected browser wallet through the same review -> sign -> include path as
 * the Trade page (TxReview). Defaults are the sane ones: the subnet's biggest
 * validator, a 2% limit, your own position preselected when selling. Used in
 * the subnet (market) overlay and on /trade. */
import { useCallback, useEffect, useState } from 'react';
import { call, Position } from '@/lib/api';
import { useData } from '@/lib/data';
import { useWallet } from '@/lib/wallet';
import { fmt, fmtPrice, short } from '@/lib/format';
import { Spinner } from './ui';
import TxReview, { useTx } from './TxReview';

export interface Validator { uid: number; hotkey: string; stake: number; validator_permit?: boolean }

/* chain reads are slow (ws lock); keep the last answer per key for the session */
const ACCT: Record<string, Position[]> = {};
const VALS: Record<number, Validator[]> = {};

/* every alpha position the address holds — a chain read, cached per address */
export function useAccount(addr: string | null) {
  const [rows, setRows] = useState<Position[] | null>(addr ? ACCT[addr] || null : null);
  const [err, setErr] = useState('');
  const load = useCallback(async () => {
    if (!addr) return;
    setErr('');
    try { setRows(ACCT[addr] = (await call<{ positions: Position[] }>('bt_account', { address: addr })).result.positions || []); }
    catch (e) { setErr((e as Error).message); setRows(r => r || []); }
  }, [addr]);
  useEffect(() => { setRows(addr ? ACCT[addr] || null : null); load(); }, [addr, load]);
  return { rows, err, reload: load };
}

/* validators with stake on a subnet, biggest first */
export function useValidators(netuid: number | null) {
  const [v, setV] = useState<Validator[] | null>(netuid != null ? VALS[netuid] || null : null);
  useEffect(() => {
    if (netuid == null || isNaN(netuid)) { setV(null); return; }
    if (VALS[netuid]) { setV(VALS[netuid]); return; }
    let live = true;
    setV(null);
    call<{ top: Validator[] }>('bt_validators', { netuid, limit: 12 })
      .then(j => { VALS[netuid] = (j.result.top || []).filter(x => x.stake > 0); if (live) setV(VALS[netuid]); })
      .catch(() => live && setV([]));
    return () => { live = false; };
  }, [netuid]);
  return v;
}

type Side = 'stake' | 'unstake';

export default function Ticket({ netuid, initial = 'stake' }: { netuid: number; initial?: Side }) {
  const w = useWallet();
  const { bySubnet } = useData();
  const r = bySubnet[netuid];
  const addr = w.canSign ? w.wallet!.addr : null;
  const acct = useAccount(addr);
  const tx = useTx();
  const [side, setSide] = useState<Side>(initial);
  const [amt, setAmt] = useState('');
  const [slip, setSlip] = useState('2');
  const [hotkey, setHotkey] = useState('');
  const [more, setMore] = useState(false);
  const vals = useValidators(side === 'stake' && addr ? netuid : null);
  const mine = (acct.rows || []).filter(p => p.netuid === netuid);
  const held = mine.find(p => p.hotkey === hotkey);
  const heldAlpha = mine.reduce((s, p) => s + (p.alpha || 0), 0);
  const free = w.wallet?.free ?? null;

  useEffect(() => { if (tx.phase === 'done') acct.reload(); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tx.phase]);
  /* buy → the biggest validator; sell → the hotkey you actually hold */
  useEffect(() => {
    if (side === 'stake' && vals?.length && !vals.some(v => v.hotkey === hotkey)) setHotkey(vals[0].hotkey);
    if (side === 'unstake' && mine.length && !held) setHotkey(mine[0].hotkey || '');
  }, // eslint-disable-next-line react-hooks/exhaustive-deps
  [side, vals, acct.rows]);

  if (!w.canSign) {
    const installed = w.extList().filter(e => e.installed);
    return (
      <div className="tk">
        <div className="tk-h"><b>Trade {r?.symbol || 'α'}</b><span className="muted">SN{netuid}</span></div>
        <p className="muted tk-note">
          {w.wallet ? <>Watching <span className="num">{short(w.wallet.addr)}</span> read-only. </> : null}
          Connect SubWallet, Talisman or polkadot{'{.js}'} to buy and sell this market from your own account.
        </p>
        <button className="pill primary tk-go" onClick={() => w.setPopOpen(true)}>
          Connect {installed[0]?.name || 'wallet'}</button>
        {!installed.length && <a className="tk-get" href="https://www.subwallet.app/download.html"
                                 target="_blank" rel="noopener noreferrer">Get SubWallet ↗</a>}
      </div>
    );
  }

  const a = +amt;
  const est = r?.price && a > 0 ? (side === 'stake' ? a / r.price : a * r.price) : null;
  const cap = side === 'stake' ? free : held?.alpha ?? null;
  const ready = a > 0 && !!hotkey;
  const busy = !['idle', 'done', 'error'].includes(tx.phase);
  const go = () => {
    if (!ready) return;
    const s = slip.trim() === '' ? 0 : +slip;
    if (side === 'stake') tx.review('stake', { netuid, hotkey, amount_tao: a, slippage_pct: s });
    else tx.review('unstake', { netuid, hotkey, amount_alpha: a, slippage_pct: s });
  };
  const flip = (s: Side) => { setSide(s); setAmt(''); setHotkey(''); tx.reset(); };
  /* nothing to sell: say so instead of offering an empty form */
  const empty = side === 'unstake' && acct.rows != null && !mine.length;

  return (
    <div className={'tk ' + (side === 'stake' ? 'buy' : 'sell')}>
      <div className="tk-side">
        <button className={side === 'stake' ? 'on buy' : ''} onClick={() => flip('stake')}>Buy</button>
        <button className={side === 'unstake' ? 'on sell' : ''} onClick={() => flip('unstake')}>Sell</button>
      </div>

      <label className="tk-l">
        <span>{side === 'stake' ? 'Pay' : 'Sell'}</span>
        <em>{side === 'stake'
          ? (free != null ? `free τ ${fmt(free, 4)}` : '')
          : (acct.rows == null ? 'reading…' : `held ${fmt(heldAlpha, 4)} α`)}</em>
      </label>
      <div className="tk-amt">
        <input type="number" inputMode="decimal" step="any" min="0" value={amt} placeholder="0.0"
               onChange={e => setAmt(e.target.value)} disabled={busy || empty} />
        <span>{side === 'stake' ? 'τ' : 'α'}</span>
      </div>
      {cap != null && cap > 0 && (
        <div className="tk-pcts">{[25, 50, 100].map(p => (
          <button key={p} disabled={busy} onClick={() => setAmt(String(+(cap * p / 100).toFixed(side === 'stake' ? 4 : 6)))}>
            {p === 100 ? 'max' : p + '%'}</button>
        ))}</div>
      )}

      <div className="tk-rows">
        <div><span>Price</span><b>τ {fmtPrice(r?.price)}</b></div>
        <div><span>You get ≈</span><b>{est != null ? (side === 'stake' ? `${fmt(est, 4)} α` : `τ ${fmt(est, 4)}`) : '—'}</b></div>
        <div><span>Limit</span><b>{+slip > 0 ? `±${slip}%` : 'market'}</b></div>
        <div><span>Validator</span>
          <button className="linkish" onClick={() => setMore(m => !m)} title={hotkey}>
            {hotkey ? short(hotkey) : side === 'stake' && vals == null ? 'loading…' : '—'} {more ? '▴' : '▾'}</button></div>
      </div>

      {more && (
        <div className="tk-more">
          <label>Validator {side === 'stake' ? '(where the stake earns)' : '(the one you staked to)'}</label>
          {side === 'stake' ? (
            vals == null ? <p className="muted"><Spinner /> loading validators…</p> : (
              <select value={hotkey} onChange={e => setHotkey(e.target.value)}>
                {vals.map(v => <option key={v.hotkey} value={v.hotkey}>uid {v.uid} · {short(v.hotkey)} · {fmt(v.stake, 0)} stake</option>)}
                {!vals.length && <option value="">no validators found</option>}
              </select>)
          ) : mine.length ? (
            <select value={hotkey} onChange={e => setHotkey(e.target.value)}>
              {mine.map(p => <option key={p.hotkey} value={p.hotkey}>{short(p.hotkey)} · {fmt(p.alpha, 4)} α</option>)}
            </select>
          ) : <input value={hotkey} onChange={e => setHotkey(e.target.value)} placeholder="validator hotkey ss58" spellCheck={false} />}
          <label>Slippage limit %</label>
          <input type="number" step="0.5" min="0" value={slip} onChange={e => setSlip(e.target.value)}
                 title="0 = market order, no limit" />
        </div>
      )}

      {empty && <p className="muted tk-note">You hold no {r?.symbol || 'alpha'} on SN{netuid} yet — buy first.</p>}
      {!busy && tx.phase !== 'done' && !empty && (
        <button className={'pill primary tk-go ' + (side === 'stake' ? 'buy' : 'sell')} disabled={!ready} onClick={go}>
          {side === 'stake' ? 'Buy' : 'Sell'} {r?.symbol || 'α'}</button>
      )}
      <TxReview tx={tx} />

      {mine.length > 0 && (
        <div className="tk-pos">
          <span className="muted">Your position</span>
          <b>{fmt(heldAlpha, 4)} α</b>
          <span className="muted">≈ τ {fmt(r?.price ? heldAlpha * r.price : null, 4)}</span>
        </div>
      )}
      <p className="muted tk-note">Real TAO. {tx.walletName} signs; this node only builds and broadcasts.</p>
    </div>
  );
}
