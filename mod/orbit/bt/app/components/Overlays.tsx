'use client';
/* Subnet + trader detail overlays. Mounted once in the shell; driven by
 * lib/overlay (and so by the chat agent's bt_view). */
import Link from 'next/link';
import { ReactNode, useCallback, useEffect, useState } from 'react';
import { call, Flow, Position, SubnetRow } from '@/lib/api';
import { useData } from '@/lib/data';
import { SUBNET_TABS, SubnetTab, useOverlay } from '@/lib/overlay';
import { compact, fmt, fmtPrice, money, RANGES, short, when } from '@/lib/format';
import LineChart, { Pt } from './LineChart';
import { Cells, Pct, Ranges, SideTag, Spinner, SubnetLogo } from './ui';
import { NewsItem, NewsList } from './News';
import RepoLine from './Repo';
import Trades from './Trades';
import Ticket from './Ticket';

function Shell({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div className="ovl open" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={'ovl-card' + (wide ? ' wide' : '')}>{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------- subnet */

interface Validators { neurons: number; top: { uid: number; hotkey?: string; stake?: number;
  validator_trust?: number; dividends?: number; emission?: number }[] }
const VAL_CACHE: Record<number, Validators> = {};

/* the subnet's latest headlines from the local news index; scrape-now on demand */
function SubnetNews({ netuid }: { netuid: number }) {
  const [items, setItems] = useState<NewsItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState('');
  const { close } = useOverlay();
  const load = useCallback(() => call('bt_news', { netuid, days: 0, limit: 6, kind: 'news,blog,social,release' })
    .then(j => { setItems(j.result.items); setTotal(j.result.total); })
    .catch(() => setItems([])), [netuid]);
  useEffect(() => { setItems(null); load(); }, [load]);
  const scrape = () => {
    setBusy('scraping…');
    call('bt_news_refresh', { netuid })
      .then(j => { setBusy(`+${j.result.added} new`); load(); })
      .catch(e => setBusy(e.message))
      .finally(() => setTimeout(() => setBusy(''), 4000));
  };
  return (
    <>
      <div className="nhead sn-tabhead">
        <span className="muted">Headlines from the local news index</span>
        <span className="nhead-r">
          {total > 0 && <Link href={`/news?netuid=${netuid}`} onClick={close}>all {total} →</Link>}
          <button className="linkish" onClick={scrape} disabled={busy === 'scraping…'}>{busy || 'scrape now'}</button>
        </span>
      </div>
      {items == null ? <Spinner /> : items.length
        ? <NewsList items={items} showSubnet={false} compact />
        : <span className="muted">No news indexed for this subnet yet — the scraper reaches every subnet within a few hours, or scrape now.</span>}
    </>
  );
}

/* top validators — a live chain read, so it only runs once its tab is opened */
function SubnetValidators({ netuid }: { netuid: number }) {
  const [vals, setVals] = useState<Validators | null>(VAL_CACHE[netuid] || null);
  const [valErr, setValErr] = useState('');
  useEffect(() => {
    let live = true;
    if (VAL_CACHE[netuid]) { setVals(VAL_CACHE[netuid]); return; }
    setVals(null); setValErr('');
    call<Validators>('bt_validators', { netuid, limit: 10 })
      .then(j => { VAL_CACHE[netuid] = j.result; if (live) setVals(j.result); })
      .catch(e => live && setValErr(e.message));
    return () => { live = false; };
  }, [netuid]);
  if (valErr) return <span className="muted">{valErr}</span>;
  if (!vals) return <p className="muted"><Spinner /> reading the metagraph from the chain…</p>;
  if (!vals.top?.length) return <span className="muted">No neurons found.</span>;
  return (
    <div className="scroll-x">
      <table><thead><tr><th>UID</th><th>Hotkey</th><th className="num">Stake τ</th>
        <th className="num">VTrust</th><th className="num">Dividends</th><th className="num">Emission</th></tr></thead>
        <tbody>{vals.top.map(n => (
          <tr key={n.uid}><td className="num">{n.uid}</td>
            <td className="num" title={n.hotkey}>{short(n.hotkey)}</td>
            <td className="num">{compact(n.stake)}</td>
            <td className="num">{fmt(n.validator_trust, 3)}</td>
            <td className="num">{fmt(n.dividends, 3)}</td>
            <td className="num">{fmt(n.emission, 3)}</td></tr>
        ))}</tbody></table>
      <p className="muted" style={{ marginTop: 8 }}>{vals.neurons} neurons registered on this subnet.</p>
    </div>
  );
}

function SubnetChart({ netuid }: { netuid: number }) {
  const [range, setRange] = useState(0);
  const [series, setSeries] = useState<Pt[] | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let live = true;
    setSeries(null); setErr('');
    call('bt_history', { netuid, hours: RANGES[range].hours, points: 400 })
      .then(j => { if (live) setSeries((j.result.series || []).map((p: any) => ({ t: p.t, v: p.price }))); })
      .catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [netuid, range]);
  return (
    <>
      <Ranges sel={range} onSel={setRange} />
      {err ? <div className="chart-empty muted">{err}</div>
        : series == null ? <div className="chart-empty muted"><Spinner /></div>
        : <LineChart series={series} hours={RANGES[range].hours} fmtY={v => `τ ${fmtPrice(v)}`} />}
    </>
  );
}

const TAB_LABEL: Record<SubnetTab, string> = {
  overview: 'Overview', trades: 'Trades', validators: 'Validators', news: 'News',
};

function SubnetOverlay({ netuid }: { netuid: number }) {
  const { bySubnet, reloadScreener, screener, ccy, rate } = useData();
  const { close, openTrader, openSubnet, tab, setTab } = useOverlay();
  const r: SubnetRow | undefined = bySubnet[netuid];
  /* a tab mounts the first time it is shown and then stays mounted (hidden),
   * so flipping back and forth never refetches */
  const [seen, setSeen] = useState<Set<SubnetTab>>(() => new Set([tab]));
  useEffect(() => { setSeen(s => s.has(tab) ? s : new Set(s).add(tab)); }, [tab]);

  useEffect(() => { if (!screener) reloadScreener(); }, [screener, reloadScreener]);

  /* 1-4 pick a tab, [ and ] step to the neighbouring subnet (same tab) */
  const ids = Object.keys(bySubnet).map(Number).sort((a, b) => a - b);
  const at = ids.indexOf(netuid);
  const prev = at > 0 ? ids[at - 1] : null, next = at >= 0 && at < ids.length - 1 ? ids[at + 1] : null;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable) return;
      const i = '1234'.indexOf(e.key);
      if (i >= 0) setTab(SUBNET_TABS[i]);
      else if (e.key === '[' && prev != null) openSubnet(prev, tab);
      else if (e.key === ']' && next != null) openSubnet(next, tab);
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, [setTab, openSubnet, prev, next, tab]);

  if (!r) return (
    <Shell onClose={close}>
      <div className="ovl-head"><h3>subnet {netuid}</h3>
        <button className="ovl-close" onClick={close} aria-label="Close">✕</button></div>
      <p className="muted" style={{ marginTop: 16 }}>{screener ? 'No such subnet in the index.' : <Spinner />}</p>
    </Shell>
  );

  const pane = (t: SubnetTab, body: ReactNode) => seen.has(t)
    ? <div role="tabpanel" id={`sn-pane-${t}`} aria-labelledby={`sn-tab-${t}`} hidden={tab !== t}>{body}</div>
    : null;

  /* a subnet IS a market: the pair is its alpha against τ, the ticket sits beside the chart */
  return (
    <Shell onClose={close} wide>
      <div className="ovl-head">
        <SubnetLogo logo={r.logo} symbol={r.symbol} big />
        <h3>{r.name || 'subnet ' + r.netuid}</h3>
        <span className="mkt-pair" title={`netuid ${r.netuid}`}>{r.symbol || 'α'}<i>/</i>τ<em>SN{r.netuid}</em></span>
        <span className="sn-step">
          <button onClick={() => prev != null && openSubnet(prev, tab)} disabled={prev == null}
                  title={prev != null ? `subnet ${prev}  [` : ''} aria-label="Previous subnet">‹</button>
          <button onClick={() => next != null && openSubnet(next, tab)} disabled={next == null}
                  title={next != null ? `subnet ${next}  ]` : ''} aria-label="Next subnet">›</button>
        </span>
        <button className="ovl-close" onClick={close} aria-label="Close">✕</button>
      </div>
      <div className="bigprice">
        <b>{money(r.price, ccy, rate, 'price')}</b>
        {ccy === 'tao' && rate != null && r.price != null &&
          <span className="muted" title="free public tickers">≈ ${fmtPrice(r.price * rate)}</span>}
        <span className="delta"><Pct v={r.change_1h} /> <span className="muted">1h</span></span>
        <span className="delta"><Pct v={r.change_24h} /> <span className="muted">24h</span></span>
        <span className="delta"><Pct v={r.change_7d} /> <span className="muted">7d</span></span>
        <span className="mkt-stats">
          <span><em>Vol 24h</em>{money(r.vol_24h, ccy, rate)}</span>
          <span><em>Liquidity</em>{money(r.tao_in, ccy, rate)}</span>
          <span><em>Mcap</em>{money(r.market_cap, ccy, rate)}</span>
        </span>
      </div>
      <div className="mkt">
      <div className="mkt-main">
      <div className="sn-tabs" role="tablist" aria-label="Subnet sections">
        {SUBNET_TABS.map((t, i) => (
          <button key={t} role="tab" id={`sn-tab-${t}`} aria-controls={`sn-pane-${t}`}
                  aria-selected={tab === t} className={tab === t ? 'on' : ''}
                  onClick={() => setTab(t)} title={`${TAB_LABEL[t]}  (${i + 1})`}>{TAB_LABEL[t]}</button>
        ))}
      </div>
      {pane('overview', <>
        <SubnetChart netuid={netuid} />
        <Cells items={[
          ['Market cap', money(r.market_cap, ccy, rate)],
          ['Vol 24h', money(r.vol_24h, ccy, rate)],
          ['TAO liquidity', compact(r.tao_in)],
          ['Alpha in pool', compact(r.alpha_in)],
          ['Alpha staked', compact(r.alpha_out)],
          ['Emission', fmt(r.emission, 4)],
          ['Tempo', r.tempo ?? '—'],
          ['Registered at block', r.registered_at ? fmt(r.registered_at, 0) : '—'],
        ]} />
        {r.description && <p className="desc">{r.description}</p>}
        <div className="idlinks">
          {r.github && <a href={r.github} target="_blank" rel="noopener noreferrer">⌥ GitHub</a>}
          {r.url && <a href={r.url} target="_blank" rel="noopener noreferrer">↗ Website</a>}
          {r.discord && (/^https?:\/\//.test(r.discord)
            ? <a href={r.discord} target="_blank" rel="noopener noreferrer">◆ Discord</a>
            : <span className="na" title="Discord">◆ {r.discord}</span>)}
          {r.owner && <button className="na linkish" onClick={() => openTrader(r.owner!)}
                              title={r.owner}>owner {short(r.owner)}</button>}
        </div>
        <RepoLine github={r.github} />
      </>)}
      {pane('trades', <Trades netuid={netuid} />)}
      {pane('validators', <SubnetValidators netuid={netuid} />)}
      {pane('news', <SubnetNews netuid={netuid} />)}
      </div>
      <aside className="mkt-side"><Ticket netuid={netuid} /></aside>
      </div>
    </Shell>
  );
}

/* ------------------------------------------------------------- trader */

interface TraderDetail {
  ss58: string; label?: string | null; tracked?: boolean; total_tao?: number; free_tao?: number;
  staked_tao?: number; subnets?: number; change_24h?: number | null; change_7d?: number | null;
  pnl_24h?: number | null; hold_pnl_24h?: number | null; snapshots?: number; note?: string;
  positions?: Position[]; flows?: Flow[];
  series?: { t: number; total_tao: number; staked_tao?: number }[];
}

function TraderOverlay({ ss58 }: { ss58: string }) {
  const { close, openSubnet } = useOverlay();
  const { reloadTraders, rate } = useData();
  const [range, setRange] = useState(1);
  const [t, setT] = useState<TraderDetail | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const j = await call<TraderDetail>('bt_trader', { address: ss58, hours: RANGES[range].hours });
      setT(j.result); setErr('');
    } catch (e) { setErr((e as Error).message); }
  }, [ss58, range]);
  useEffect(() => { setT(null); load(); }, [load]);

  const snapshot = async () => {
    setBusy(true);
    try { await call(t?.tracked ? 'bt_trader_snapshot' : 'bt_track', { address: ss58 }); } catch { /* */ }
    await load(); reloadTraders(); setBusy(false);
  };

  const series: Pt[] = (t?.series || []).map(p => ({ t: p.t, v: p.total_tao, sub: `${fmt(p.staked_tao, 2)} staked` }));
  const up = series.length > 1 ? series[series.length - 1].v >= series[0].v : true;

  return (
    <Shell onClose={close}>
      <div className="ovl-head">
        <span className="sn-avatar big">{(t?.label || 'τ').slice(0, 2)}</span>
        <h3>{t?.label || short(ss58)}</h3>
        <span className="tag" title={ss58}>{short(ss58)}</span>
        {t && !t.tracked && <span className="tag warn">not tracked</span>}
        <button className="ovl-close" onClick={close} aria-label="Close">✕</button>
      </div>
      {err && <p className="muted" style={{ marginTop: 16 }}>{err}</p>}
      {!t && !err && <p style={{ marginTop: 16 }}><Spinner /></p>}
      {t && <>
        <div className="bigprice">
          <b>τ {fmt(t.total_tao, 3)}</b>
          {rate != null && t.total_tao != null &&
            <span className="muted" title="free public tickers">≈ ${compact(t.total_tao * rate)}</span>}
          <span className="delta"><Pct v={t.change_24h} /> <span className="muted">24h</span></span>
          <span className="delta"><Pct v={t.change_7d} /> <span className="muted">7d</span></span>
          <button className="pill ghost small" style={{ marginLeft: 'auto' }} onClick={snapshot} disabled={busy}>
            {busy ? <Spinner /> : t.tracked ? 'Snapshot now' : 'Track'}</button>
        </div>
        <Ranges sel={range} onSel={setRange} />
        <LineChart series={series} hours={RANGES[range].hours} height={220}
                   color={up ? 'var(--good)' : 'var(--bad)'} fmtY={v => `τ ${fmt(v, 3)}`}
                   empty="Not enough history yet — the indexer snapshots tracked traders every 15 minutes." />
        <Cells items={[
          ['Free τ', fmt(t.free_tao, 3)], ['Staked τ', fmt(t.staked_tao, 3)], ['Subnets', t.subnets || 0],
          ['PnL 24h τ', t.pnl_24h != null ? fmt(t.pnl_24h, 3) : '—'],
          ['Price-only PnL 24h τ', t.hold_pnl_24h != null ? fmt(t.hold_pnl_24h, 3) : '—'],
          ['Snapshots', t.snapshots || 0],
        ]} />
        <div className="ovl-sub">Positions</div>
        {t.positions?.length ? (
          <div className="scroll-x"><table><thead><tr><th>Subnet</th><th className="num">Alpha</th>
            <th className="num">Price τ</th><th className="num">Value τ</th><th className="num">Weight</th></tr></thead>
            <tbody>{t.positions.map(p => (
              <tr key={p.netuid + (p.hotkey || '')} className="click" onClick={() => openSubnet(p.netuid)}>
                <td>{p.name} <span className="sn-sym">#{p.netuid}</span></td>
                <td className="num">{fmt(p.alpha, 3)}</td><td className="num">{fmtPrice(p.price)}</td>
                <td className="num">{fmt(p.value_tao, 3)}</td><td className="num">{fmt(p.pct_of_total, 1)}%</td></tr>
            ))}</tbody></table></div>
        ) : <span className="muted">{t.note || 'No alpha positions.'}</span>}
        <div className="ovl-sub">Inferred trades</div>
        {t.flows?.length ? (
          <div className="scroll-x"><table><thead><tr><th>When</th><th>Side</th><th>Subnet</th>
            <th className="num">Alpha</th><th className="num">Value τ</th></tr></thead>
            <tbody>{t.flows.map((f, i) => (
              <tr key={i} className="click" onClick={() => openSubnet(f.netuid)}>
                <td className="muted">{when(f.ts)}</td><td><SideTag side={f.side} /></td>
                <td>{f.name || ''} <span className="sn-sym">#{f.netuid}</span></td>
                <td className="num">{fmt(f.alpha, 3)}</td><td className="num">{fmt(f.tao_value, 3)}</td></tr>
            ))}</tbody></table>
            <p className="muted" style={{ marginTop: 8 }}>Inferred from snapshot deltas — not extrinsics.</p></div>
        ) : <span className="muted">No moves recorded in this window.</span>}
      </>}
    </Shell>
  );
}

export default function Overlays() {
  const { subnet, trader } = useOverlay();
  if (subnet != null) return <SubnetOverlay key={'s' + subnet} netuid={subnet} />;
  if (trader) return <TraderOverlay key={'t' + trader} ss58={trader} />;
  return null;
}
