'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { SubnetRow } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { compact, fmtPrice } from '@/lib/format';
import { useStored } from '@/lib/hooks';
import { Pct, Section, Spark, SubnetLogo, Tabs } from '@/components/ui';

const COLS: { key: keyof SubnetRow; label: string; num: boolean; nosort?: boolean }[] = [
  { key: 'netuid', label: '#', num: true },
  { key: 'name', label: 'Subnet', num: false },
  { key: 'price', label: 'Price τ', num: true },
  { key: 'change_1h', label: '1h', num: true },
  { key: 'change_24h', label: '24h', num: true },
  { key: 'change_7d', label: '7d', num: true },
  { key: 'market_cap', label: 'Mcap τ', num: true },
  { key: 'vol_24h', label: 'Vol 24h τ', num: true },
  { key: 'tao_in', label: 'Liquidity τ', num: true },
  { key: 'spark', label: '24h trend', num: false, nosort: true },
];

/* 24h move → tile color: green up, red down, saturating at ±15% */
function heatColor(v?: number | null) {
  if (v == null) return 'hsl(230,20%,45%)';
  const k = Math.min(Math.abs(v) / 15, 1);
  return v >= 0 ? `hsl(130,${35 + 30 * k}%,${42 - 12 * k}%)` : `hsl(2,${40 + 35 * k}%,${48 - 14 * k}%)`;
}

export default function Markets() {
  const { screener, screenerError, search, setSearch, sort, setSort } = useData();
  const { openSubnet } = useOverlay();
  const [mode, setMode] = useStored<'table' | 'heat'>('bt.markets.mode', 'table');
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
  if (screenerError && !screener) body = <p className="muted">{screenerError}</p>;
  else if (!screener) body = <p className="muted">Loading markets…</p>;
  else if (screener.warming) body = <p className="muted">{screener.note || 'Indexer warming up — first snapshot in progress.'}</p>;
  else if (mode === 'heat') body = (
    <div className="heat">
      {rows.filter(r => r.netuid !== 0).map(r => {
        const s = Math.sqrt((r.market_cap || 0) / maxCap);
        return (
          <div key={r.netuid} className="tile" onClick={() => openSubnet(r.netuid)}
               title={`${r.name} · τ ${fmtPrice(r.price)} · mcap τ ${compact(r.market_cap)}`}
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
            {c.label} {sort.key === c.key && <span className="arrow">{sort.dir < 0 ? '▼' : '▲'}</span>}
          </th>
        ))}</tr></thead>
        <tbody>
          {rows.length ? rows.map(r => (
            <tr key={r.netuid} onClick={() => openSubnet(r.netuid)}>
              <td className="num">{r.netuid}</td>
              <td><div className="sn-cell"><SubnetLogo logo={r.logo} symbol={r.symbol} />
                <div><div className="sn-name">{r.name || '—'}</div><div className="sn-sym">{r.symbol}</div></div></div></td>
              <td className="num">{fmtPrice(r.price)}</td>
              <td className="num"><Pct v={r.change_1h} /></td>
              <td className="num"><Pct v={r.change_24h} /></td>
              <td className="num"><Pct v={r.change_7d} /></td>
              <td className="num">{compact(r.market_cap)}</td>
              <td className="num">{r.vol_24h != null ? compact(r.vol_24h) : <span className="muted">—</span>}</td>
              <td className="num">{compact(r.tao_in)}</td>
              <td><Spark pts={r.spark} dir={r.change_24h} /></td>
            </tr>
          )) : <tr><td colSpan={10} className="muted">No matches.</td></tr>}
        </tbody>
      </table>
    </div>
  );

  return (
    <Section id="markets" title="Markets."
      lead="Every alpha market on the chain — live prices, changes, volume and 24-hour trend, straight from the built-in open indexer.">
      <div className="card">
        <div className="head-row">
          <input ref={box} value={search} onChange={e => setSearch(e.target.value)}
                 placeholder="Search name, symbol or netuid" spellCheck={false} />
          <span className="kbd" title="press / to search">/</span>
          <Tabs value={mode} onChange={setMode} options={[['table', 'Table'], ['heat', 'Heatmap']]} />
          <span className="grow" />
          <span className="updated">{rows.length} markets{age != null ? ` · updated ${age < 90 ? age + 's' : Math.round(age / 60) + 'm'} ago` : ''}</span>
        </div>
        {body}
      </div>
    </Section>
  );
}
