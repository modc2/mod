'use client';
/* The chain-event trade tape (bt_trades) for one subnet, one coldkey, or the
 * whole network: window + side filters, a summary of the whole window, the
 * biggest traders, and every trade newest-first with "load more" paging.
 * The first page re-polls, so new blocks slide in on top. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { call } from '@/lib/api';
import { useOverlay } from '@/lib/overlay';
import { useNow } from '@/lib/hooks';
import { agoText, compact, fmt, fmtPrice, short, when } from '@/lib/format';
import { Ident, SideTag, Spinner, Tabs } from './ui';

export interface Trade {
  block: number; ev: number; ts: number; netuid: number; side: 'buy' | 'sell';
  kind: 'stake' | 'swap'; coldkey: string; hotkey: string;
  tao: number; alpha: number; price: number; fee: number;
}
interface Reply {
  count: number; more: boolean; next_before_block: number | null; trades: Trade[];
  summary: { trades: number; buys: number; sells: number; buy_tao: number; sell_tao: number;
             net_tao: number; traders: number };
  top: { coldkey: string; net_tao: number; gross_tao: number; trades: number }[];
  coverage: { from_block: number | null; to_block: number | null; from_ts: number | null;
              blocks: number; complete: boolean; gaps: number };
}

const WINDOWS: [string, string][] = [['1', '1H'], ['24', '24H'], ['168', '7D'], ['0', 'ALL']];
const SIDES: [string, string][] = [['', 'All'], ['buy', 'Buys'], ['sell', 'Sells']];
const PAGE = 50;
const POLL_MS = 15000;

export default function Trades({ netuid, coldkey, showSubnet = false }:
  { netuid?: number; coldkey?: string; showSubnet?: boolean }) {
  const { openTrader, openSubnet } = useOverlay();
  const now = useNow(5000);
  const [hours, setHours] = useState('24');
  const [side, setSide] = useState('');
  const [rows, setRows] = useState<Trade[] | null>(null);
  const [meta, setMeta] = useState<Reply | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const paged = useRef(false);

  const args = useCallback((before?: number | null) => ({
    hours: Number(hours), limit: PAGE, ...(netuid != null ? { netuid } : {}),
    ...(coldkey ? { coldkey } : {}), ...(side ? { side } : {}),
    ...(before ? { before_block: before } : {}),
  }), [hours, side, netuid, coldkey]);

  const head = useCallback(() => call<Reply>('bt_trades', args()).then(j => {
    const r = j.result;
    setMeta(m => ({ ...r, more: paged.current ? (m?.more ?? r.more) : r.more,
                    next_before_block: paged.current ? (m?.next_before_block ?? null) : r.next_before_block }));
    setRows(prev => {
      if (!paged.current || !prev?.length) return r.trades;
      const key = (t: Trade) => `${t.block}:${t.ev}`;
      const seen = new Set(prev.map(key));
      return [...r.trades.filter(t => !seen.has(key(t)) && t.block >= prev[0].block), ...prev];
    });
    setErr('');
  }).catch(e => setErr(e.message)), [args]);

  useEffect(() => {
    paged.current = false;
    setRows(null); setMeta(null);
    head();
    const id = setInterval(() => { if (document.visibilityState === 'visible') head(); }, POLL_MS);
    return () => clearInterval(id);
  }, [head]);

  const more = () => {
    if (!meta?.next_before_block) return;
    setBusy(true);
    call<Reply>('bt_trades', args(meta.next_before_block)).then(j => {
      paged.current = true;
      setRows(prev => [...(prev || []), ...j.result.trades]);
      setMeta(m => m && { ...m, more: j.result.more, next_before_block: j.result.next_before_block });
    }).catch(e => setErr(e.message)).finally(() => setBusy(false));
  };

  const s = meta?.summary;
  const gross = s ? s.buy_tao + s.sell_tao : 0;
  const cov = meta?.coverage;

  return (
    <div className="trades">
      <div className="trades-bar">
        <Tabs value={hours} options={WINDOWS} onChange={setHours} />
        <Tabs value={side} options={SIDES} onChange={setSide} />
      </div>

      {s && (
        <div className="trades-sum">
          <span><b>{fmt(s.trades, 0)}</b> trades</span>
          <span className="up"><b>τ {compact(s.buy_tao)}</b> bought · {fmt(s.buys, 0)}</span>
          <span className="down"><b>τ {compact(s.sell_tao)}</b> sold · {fmt(s.sells, 0)}</span>
          <span>net <b className={s.net_tao >= 0 ? 'up' : 'down'}>{s.net_tao >= 0 ? '+' : '−'}τ {compact(Math.abs(s.net_tao))}</b></span>
          <span><b>{fmt(s.traders, 0)}</b> wallets</span>
          {gross > 0 && (
            <span className="trades-pressure" title={`${Math.round(100 * s.buy_tao / gross)}% of volume was buying`}>
              <i style={{ width: `${100 * s.buy_tao / gross}%` }} />
            </span>
          )}
        </div>
      )}

      {meta && meta.top.length > 0 && (
        <div className="trades-top">
          <span className="muted">Biggest wallets</span>
          {meta.top.slice(0, 6).map(t => (
            <button key={t.coldkey} className="trades-who" onClick={() => openTrader(t.coldkey)} title={t.coldkey}>
              <Ident addr={t.coldkey} size={16} /> {short(t.coldkey)}
              <b className={t.net_tao >= 0 ? 'up' : 'down'}>{t.net_tao >= 0 ? '+' : '−'}{compact(Math.abs(t.net_tao))}</b>
            </button>
          ))}
        </div>
      )}

      {err && !rows ? <span className="muted">{err}</span> : rows == null ? <Spinner /> : rows.length ? (
        <div className="scroll-x">
          <table className="trades-tbl">
            <thead><tr>
              <th>When</th><th>Side</th>{showSubnet && <th>Subnet</th>}
              <th className="num">TAO</th><th className="num">Alpha</th><th className="num">Price τ</th>
              <th>Wallet</th><th className="num">Block</th>
            </tr></thead>
            <tbody>{rows.map(t => (
              <tr key={`${t.block}:${t.ev}`}>
                <td title={when(t.ts)}>{agoText(Math.max(0, now - t.ts))}</td>
                <td><SideTag side={t.side} />{t.kind === 'swap' && <span className="trades-swap">swap</span>}</td>
                {showSubnet && <td><button className="linkish" onClick={() => openSubnet(t.netuid)}>SN{t.netuid}</button></td>}
                <td className={`num ${t.side === 'buy' ? 'up' : 'down'}`}>{fmt(t.tao, t.tao >= 100 ? 1 : t.tao >= 0.001 ? 3 : 6)}</td>
                <td className="num">{compact(t.alpha)}</td>
                <td className="num">{fmtPrice(t.price)}</td>
                <td><button className="trades-who bare" onClick={() => openTrader(t.coldkey)} title={t.coldkey}>
                  <Ident addr={t.coldkey} size={16} /> {short(t.coldkey)}</button></td>
                <td className="num muted" title={`extrinsic ${t.block}-${t.ev}`}>{fmt(t.block, 0)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <span className="muted">No trades in this window{cov?.blocks ? '' : ' yet — the indexer is still catching up'}.</span>}

      {meta?.more && rows && rows.length > 0 && (
        <button className="trades-more" onClick={more} disabled={busy}>{busy ? 'loading…' : `Load ${PAGE} more`}</button>
      )}

      {cov && (
        <p className="muted trades-cov">
          Read straight from chain events and indexed on this node
          {cov.from_block != null && <> — blocks {fmt(cov.from_block, 0)} to {fmt(cov.to_block, 0)}</>}
          {cov.from_ts != null && <> (since {when(cov.from_ts)})</>}
          {!cov.complete && cov.gaps > 0 && <>, {fmt(cov.gaps, 0)} blocks still backfilling</>}.
          Hotkey moves and transfers are not counted as trades.
        </p>
      )}
    </div>
  );
}
