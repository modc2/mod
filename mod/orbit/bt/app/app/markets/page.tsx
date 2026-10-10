'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { call, FlowRow, SubnetRow } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { Ccy, compact, fmtPrice } from '@/lib/format';
import { usePoll, useStored } from '@/lib/hooks';
import { Pct, Section, Spark, Spinner, SubnetLogo, Tabs } from '@/components/ui';

const COLS: { key: keyof SubnetRow; label: string; num: boolean; nosort?: boolean; ccy?: boolean }[] = [
  { key: 'netuid', label: '#', num: true },
  { key: 'name', label: 'Market', num: false },
  { key: 'price', label: 'Price', num: true, ccy: true },
  { key: 'change_1h', label: '1h', num: true },
  { key: 'change_24h', label: '24h', num: true },
  { key: 'change_7d', label: '7d', num: true },
  { key: 'market_cap', label: 'Mcap', num: true, ccy: true },
  { key: 'vol_24h', label: 'Vol 24h', num: true, ccy: true },
  { key: 'tao_in', label: 'Liquidity', num: true, ccy: true },
  { key: 'spark', label: '24h trend', num: false, nosort: true },
  { key: 'symbol', label: '', num: false, nosort: true },
];

/* 24h move → tile color: green up, red down, saturating at ±15% */
function heatColor(v?: number | null) {
  if (v == null) return 'hsl(220,8%,22%)';
  const k = Math.sqrt(Math.min(Math.abs(v) / 15, 1));    /* sqrt: small moves still read */
  return v >= 0 ? `hsl(156,${18 + 50 * k}%,${21 + 14 * k}%)` : `hsl(357,${20 + 52 * k}%,${23 + 17 * k}%)`;
}

const FLOW_WINS = [['1', '1h'], ['24', '24h'], ['168', '7d']] as [string, string][];
const FLOW_COLS: { key: keyof FlowRow; label: string; ccy?: boolean; nosort?: boolean }[] = [
  { key: 'netuid', label: '#' },
  { key: 'name', label: 'Market' },
  { key: 'net_tao', label: 'Net flow', ccy: true },
  { key: 'net_tao', label: '', nosort: true },           /* the bar */
  { key: 'buy_tao', label: 'In', ccy: true },
  { key: 'sell_tao', label: 'Out', ccy: true },
  { key: 'trades', label: 'Trades' },
  { key: 'buyers', label: 'Buyers' },
  { key: 'sellers', label: 'Sellers' },
  { key: 'biggest_tao', label: 'Biggest', ccy: true },
];

/* where TAO is rotating, from the local chain-event trade index — the board
   tao.app sells, free, from data this box already has */
function FlowBoard({ q, ccy, rate, cv, sign }: {
  q: string; ccy: Ccy; rate: number | null; cv: (v?: number | null) => number | undefined; sign: string;
}) {
  const [win, setWin] = useStored('bt.flows.win', '24');
  const [sort, setSort] = useState<{ key: keyof FlowRow; dir: 1 | -1 }>({ key: 'net_tao', dir: -1 });
  const f = usePoll<{ rows: FlowRow[]; coverage?: { complete?: boolean; gaps?: number } }>(
    async () => (await call('bt_flows', { hours: Number(win) })).result, 60_000, [win], 'flows' + win);

  const rows = useMemo(() => {
    let rs = f.data?.rows || [];
    if (q) rs = rs.filter(r => String(r.name || '').toLowerCase().includes(q)
      || String(r.symbol || '').toLowerCase().includes(q) || String(r.netuid) === q);
    return [...rs].sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      if (typeof av === 'string' || typeof bv === 'string')
        return sort.dir * String(av || '').localeCompare(String(bv || ''));
      return sort.dir * (((av as number) ?? -Infinity) - ((bv as number) ?? -Infinity));
    });
  }, [f.data, q, sort]);
  const { openSubnet } = useOverlay();
  const maxAbs = Math.max(1e-9, ...rows.map(r => Math.abs(r.net_tao)));
  const sortBy = (key: keyof FlowRow) =>
    setSort({ key, dir: sort.key === key ? (-sort.dir as 1 | -1) : -1 });

  if (!f.data) return <p className="muted">{f.error || <Spinner />}</p>;
  return (
    <>
      <div className="flow-head">
        <Tabs value={win} onChange={setWin} options={FLOW_WINS} />
        <span className="muted">TAO staked into vs out of each subnet&apos;s pool — every StakeAdded/StakeRemoved the chain emitted, indexed locally.</span>
      </div>
      <div className="screener-wrap">
        <table className="screener flows">
          <thead><tr>{FLOW_COLS.map((c, i) => (
            <th key={i} className={i > 1 ? 'num' : ''}
                onClick={c.nosort ? undefined : () => sortBy(c.key)}>
              {c.label}{c.ccy && c.label ? ' ' + sign : ''} {!c.nosort && sort.key === c.key && c.label &&
                <span className="arrow">{sort.dir < 0 ? '▼' : '▲'}</span>}
            </th>
          ))}</tr></thead>
          <tbody>
            {rows.length ? rows.map(r => (
              <tr key={r.netuid} onClick={() => openSubnet(r.netuid)}>
                <td className="num">{r.netuid}</td>
                <td><div className="sn-cell"><SubnetLogo logo={r.logo} symbol={r.symbol} />
                  <div><div className="sn-name">{r.name || '—'}</div><div className="sn-sym">{r.symbol}</div></div></div></td>
                <td className={'num flow-net ' + (r.net_tao >= 0 ? 'up' : 'down')}>
                  {r.net_tao >= 0 ? '+' : '−'}{compact(Math.abs(cv(r.net_tao)!))}</td>
                <td className="flow-barcell"><span className={'flow-bar ' + (r.net_tao >= 0 ? 'up' : 'down')}
                  style={{ width: `${Math.max(2, Math.abs(r.net_tao) / maxAbs * 100)}%` }} /></td>
                <td className="num">{compact(cv(r.buy_tao))}</td>
                <td className="num">{compact(cv(r.sell_tao))}</td>
                <td className="num">{r.trades}</td>
                <td className="num">{r.buyers}</td>
                <td className="num">{r.sellers}</td>
                <td className="num">{compact(cv(r.biggest_tao))}</td>
              </tr>
            )) : <tr><td colSpan={FLOW_COLS.length} className="muted">
              {q ? 'No matches.' : 'The trade indexer is on its first pass over the chain.'}</td></tr>}
          </tbody>
        </table>
      </div>
      {ccy === 'usd' && rate == null && <p className="muted" style={{ marginTop: 8 }}>USD rate loading — showing τ.</p>}
    </>
  );
}

export default function Markets() {
  const { screener, screenerError, search, setSearch, sort, setSort, ccy, rate } = useData();
  const { openSubnet } = useOverlay();
  const [mode, setMode] = useStored<'table' | 'heat' | 'flows'>('bt.markets.mode', 'table');
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
        e.preventDefault(); box.current?.focus();
      }
    };
    addEventListener('keydown', k);
    return () => removeEventListener('keydown', k);
  }, []);

  /* τ → display currency; headers carry the unit so cells stay bare numbers */
  const usd = ccy === 'usd' && rate != null;
  const cv = (v?: number | null) => (v == null ? undefined : usd ? v * rate! : v);
  const sign = usd ? '$' : 'τ';

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rs = screener?.rows || [];
    if (q) rs = rs.filter(r => String(r.name || '').toLowerCase().includes(q)
      || String(r.symbol || '').toLowerCase().includes(q) || String(r.netuid) === q);
    const key = sort.key as keyof SubnetRow;
    return [...rs].sort((a, b) => {
      const av = a[key], bv = b[key];
      if (typeof av === 'string' || typeof bv === 'string') {
        const x = String(av || '').toLowerCase(), y = String(bv || '').toLowerCase();
        return sort.dir * (x < y ? -1 : x > y ? 1 : 0);
      }
      return sort.dir * (((av as number) ?? -Infinity) - ((bv as number) ?? -Infinity));
    });
  }, [screener, search, sort]);

  const sortBy = (key: string) => setSort({ key, dir: sort.key === key ? (-sort.dir as 1 | -1) : -1 });
  const age = screener?.age_sec;
  const maxCap = Math.max(1, ...rows.map(r => r.market_cap || 0));

  let body: React.ReactNode;
  if (mode === 'flows') body = (
    <FlowBoard q={search.trim().toLowerCase()} ccy={ccy} rate={rate} cv={cv} sign={sign} />
  );
  else if (screenerError && !screener) body = <p className="muted">{screenerError}</p>;
  else if (!screener) body = <p className="muted">Loading markets…</p>;
  else if (screener.warming) body = <p className="muted">{screener.note || 'Indexer warming up — first snapshot in progress.'}</p>;
  else if (mode === 'heat') body = (
    <div className="heat">
      {rows.filter(r => r.netuid !== 0).map(r => {
        const s = Math.sqrt((r.market_cap || 0) / maxCap);
        return (
          <div key={r.netuid} className="tile" onClick={() => openSubnet(r.netuid)}
               title={`${r.name} · ${sign} ${fmtPrice(cv(r.price))} · mcap ${sign} ${compact(cv(r.market_cap))}`}
               style={{ background: heatColor(r.change_24h), flexBasis: `${64 + s * 260}px`, minHeight: 52 + s * 70 }}>
            <b>{r.name || '#' + r.netuid}</b>
            <span>{r.change_24h == null ? '—' : (r.change_24h >= 0 ? '+' : '') + r.change_24h.toFixed(1) + '%'}</span>
          </div>
        );
      })}
    </div>
  );
  else body = (
    <div className="screener-wrap">
      <table className="screener">
        <thead><tr>{COLS.map(c => (
          <th key={c.key} className={c.num ? 'num' : ''} onClick={c.nosort ? undefined : () => sortBy(c.key)}>
            {c.label}{c.ccy ? ' ' + sign : ''} {sort.key === c.key && <span className="arrow">{sort.dir < 0 ? '▼' : '▲'}</span>}
          </th>
        ))}</tr></thead>
        <tbody>
          {rows.length ? rows.map(r => (
            <tr key={r.netuid} onClick={() => openSubnet(r.netuid)}>
              <td className="num">{r.netuid}</td>
              <td><div className="sn-cell"><SubnetLogo logo={r.logo} symbol={r.symbol} />
                <div><div className="sn-name">{r.name || '—'}</div><div className="sn-sym">{r.symbol}</div></div></div></td>
              <td className="num">{fmtPrice(cv(r.price))}</td>
              <td className="num"><Pct v={r.change_1h} /></td>
              <td className="num"><Pct v={r.change_24h} /></td>
              <td className="num"><Pct v={r.change_7d} /></td>
              <td className="num">{compact(cv(r.market_cap))}</td>
              <td className="num">{r.vol_24h != null ? compact(cv(r.vol_24h)) : <span className="muted">—</span>}</td>
              <td className="num">{compact(cv(r.tao_in))}</td>
              <td><Spark pts={r.spark} dir={r.change_24h} /></td>
              <td className="mkt-go"><button onClick={e => { e.stopPropagation(); openSubnet(r.netuid); }}>Trade</button></td>
            </tr>
          )) : <tr><td colSpan={COLS.length} className="muted">No matches.</td></tr>}
        </tbody>
      </table>
    </div>
  );

  return (
    <Section id="markets" title="Markets."
      lead="Every subnet is a market: its alpha trades against τ in an on-chain pool. Live prices, volume, trend and where TAO is rotating — all from the open indexer, free.">
      <div className="card">
        <div className="head-row">
          <input ref={box} value={search} onChange={e => setSearch(e.target.value)}
                 placeholder="Search name, symbol or netuid" spellCheck={false} />
          <span className="kbd" title="press / to search">/</span>
          <Tabs value={mode} onChange={setMode} options={[['table', 'Table'], ['heat', 'Heatmap'], ['flows', 'Flows']]} />
          <span className="grow" />
          <span className="updated">{rows.length} markets{age != null ? ` · updated ${age < 90 ? age + 's' : Math.round(age / 60) + 'm'} ago` : ''}</span>
        </div>
        {body}
      </div>
    </Section>
  );
}
