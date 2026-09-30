'use client';
/* Subnet + trader detail overlays. Mounted once in the shell; driven by
 * lib/overlay (and so by the chat agent's bt_view). */
import { useCallback, useEffect, useState } from 'react';
import { call, Flow, Position, SubnetRow } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { compact, fmt, fmtPrice, RANGES, short, when } from '@/lib/format';
import LineChart, { Pt } from './LineChart';
import { Cells, Pct, Ranges, SideTag, Spinner, SubnetLogo } from './ui';

function Shell({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="ovl open" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="ovl-card">{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------- subnet */

interface Validators { neurons: number; top: { uid: number; hotkey?: string; stake?: number;
  validator_trust?: number; dividends?: number; emission?: number }[] }
const VAL_CACHE: Record<number, Validators> = {};

function SubnetOverlay({ netuid }: { netuid: number }) {
  const { bySubnet, reloadScreener, screener } = useData();
  const { close, openTrader } = useOverlay();
  const r: SubnetRow | undefined = bySubnet[netuid];
  const [range, setRange] = useState(0);
  const [series, setSeries] = useState<Pt[] | null>(null);
  const [err, setErr] = useState('');
  const [vals, setVals] = useState<Validators | null>(VAL_CACHE[netuid] || null);
  const [valErr, setValErr] = useState('');

  useEffect(() => { if (!screener) reloadScreener(); }, [screener, reloadScreener]);

  useEffect(() => {
    let live = true;
    setSeries(null); setErr('');
    call('bt_history', { netuid, hours: RANGES[range].hours, points: 400 })
      .then(j => { if (live) setSeries((j.result.series || []).map((p: any) => ({ t: p.t, v: p.price }))); })
      .catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [netuid, range]);

  useEffect(() => {
    let live = true;
    if (VAL_CACHE[netuid]) { setVals(VAL_CACHE[netuid]); return; }
    setVals(null); setValErr('');
    call<Validators>('bt_validators', { netuid, limit: 10 })
      .then(j => { VAL_CACHE[netuid] = j.result; if (live) setVals(j.result); })
      .catch(e => live && setValErr(e.message));
    return () => { live = false; };
  }, [netuid]);

  if (!r) return (
    <Shell onClose={close}>
      <div className="ovl-head"><h3>subnet {netuid}</h3>
        <button className="ovl-close" onClick={close} aria-label="Close">✕</button></div>
      <p className="muted" style={{ marginTop: 16 }}>{screener ? 'No such subnet in the index.' : <Spinner />}</p>
    </Shell>
  );

  return (
    <Shell onClose={close}>
      <div className="ovl-head">
        <SubnetLogo logo={r.logo} symbol={r.symbol} big />
        <h3>{r.name || 'subnet ' + r.netuid}</h3>
        <span className="tag">netuid {r.netuid}</span>
        <span className="sn-sym" style={{ fontSize: 14 }}>{r.symbol}</span>
        <button className="ovl-close" onClick={close} aria-label="Close">✕</button>
      </div>
      <div className="idlinks">
        {r.github && <a href={r.github} target="_blank" rel="noopener noreferrer">⌥ GitHub</a>}
        {r.url && <a href={r.url} target="_blank" rel="noopener noreferrer">↗ Website</a>}
        {r.discord && (/^https?:\/\//.test(r.discord)
          ? <a href={r.discord} target="_blank" rel="noopener noreferrer">◆ Discord</a>
          : <span className="na" title="Discord">◆ {r.discord}</span>)}
        {!r.github && <span className="na">no public repo — ask them why</span>}
        {r.owner && <button className="na linkish" onClick={() => openTrader(r.owner!)}
                            title={r.owner}>owner {short(r.owner)}</button>}
      </div>
      <div className="bigprice">
        <b>τ {fmtPrice(r.price)}</b>
        <span className="delta"><Pct v={r.change_1h} /> <span className="muted">1h</span></span>
        <span className="delta"><Pct v={r.change_24h} /> <span className="muted">24h</span></span>
        <span className="delta"><Pct v={r.change_7d} /> <span className="muted">7d</span></span>
      </div>
      <Ranges sel={range} onSel={setRange} />
      {err ? <div className="chart-empty muted">{err}</div>
        : series == null ? <div className="chart-empty muted"><Spinner /></div>
        : <LineChart series={series} hours={RANGES[range].hours} fmtY={v => `τ ${fmtPrice(v)}`} />}
      <Cells items={[
        ['Market cap τ', compact(r.market_cap)],
        ['Vol 24h τ', r.vol_24h != null ? compact(r.vol_24h) : '—'],
        ['TAO liquidity', compact(r.tao_in)],
        ['Alpha in pool', compact(r.alpha_in)],
        ['Alpha staked', compact(r.alpha_out)],
        ['Emission', fmt(r.emission, 4)],
        ['Tempo', r.tempo ?? '—'],
        ['Registered at block', r.registered_at ? fmt(r.registered_at, 0) : '—'],
      ]} />
      {r.description && <p className="desc">{r.description}</p>}
      <div className="ovl-sub">Top validators</div>
      {valErr ? <span className="muted">{valErr}</span> : !vals ? <Spinner /> : vals.top?.length ? (
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
      ) : <span className="muted">No neurons found.</span>}
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
  const { reloadTraders } = useData();
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
