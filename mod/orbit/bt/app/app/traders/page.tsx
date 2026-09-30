'use client';
import { useState } from 'react';
import { BoardRow, call, Flow } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { usePoll, useNow, useStored } from '@/lib/hooks';
import { agoText, compact, fmt, short } from '@/lib/format';
import { Pct, Section, SideTag, Spark, Spinner, Tabs } from '@/components/ui';

function TrackForm() {
  const { reloadTraders } = useData();
  const [addr, setAddr] = useState('');
  const [label, setLabel] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const track = async () => {
    const a = addr.trim();
    if (!a) return;
    setBusy(true); setMsg('Reading the chain…');
    try {
      const r = (await call('bt_track', label.trim() ? { address: a, label: label.trim() } : { address: a })).result;
      setMsg(r.snapshot
        ? `Tracking ${label || short(a)} — τ ${fmt(r.snapshot.total_tao, 3)} across ${r.snapshot.positions} positions.`
        : `Tracking ${label || short(a)} — ${r.snapshot_error || 'first snapshot pending'}`);
      setAddr(''); setLabel(''); reloadTraders();
    } catch (e) { setMsg((e as Error).message); }
    setBusy(false);
  };
  return (
    <>
      <div className="row">
        <div style={{ flex: 3 }}><label>ss58 coldkey to track</label>
          <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="5Grw…" spellCheck={false}
                 onKeyDown={e => e.key === 'Enter' && track()} /></div>
        <div style={{ flex: 1 }}><label>Label (optional)</label>
          <input value={label} onChange={e => setLabel(e.target.value)} placeholder="whale #1" spellCheck={false} /></div>
        <div style={{ flex: 0 }}><button className="pill primary" onClick={track} disabled={busy}>
          {busy ? <Spinner /> : 'Track'}</button></div>
      </div>
      {msg && <p className="muted" style={{ marginTop: 8 }}>{msg}</p>}
    </>
  );
}

function Tracked() {
  const { traders, tradersError, reloadTraders } = useData();
  const { openTrader, close, trader } = useOverlay();
  const untrack = async (ss58: string) => {
    if (!confirm(`Stop tracking ${short(ss58)}?\n\nRecorded history is kept.`)) return;
    try { await call('bt_untrack', { address: ss58 }); } catch { /* */ }
    if (trader === ss58) close();
    reloadTraders();
  };
  return (
    <div className="scroll-x" style={{ marginTop: 14 }}>
      <table>
        <thead><tr><th>Trader</th><th className="num">Total τ</th><th className="num">Free τ</th>
          <th className="num">Staked τ</th><th className="num">Subnets</th><th className="num">24h</th>
          <th className="num">7d</th><th className="num">Moves 24h</th><th>Equity</th><th /></tr></thead>
        <tbody>
          {tradersError && !traders ? <tr><td colSpan={10} className="muted">{tradersError}</td></tr>
            : !traders ? <tr><td colSpan={10} className="muted">Loading…</td></tr>
            : !traders.length ? <tr><td colSpan={10} className="muted">No traders tracked yet — paste a coldkey above, or pick one off the board.</td></tr>
            : traders.map(t => {
              const warming = t.warming || t.total_tao == null;
              return (
                <tr key={t.ss58} className="trow" onClick={() => openTrader(t.ss58)}>
                  <td><div className="sn-cell"><span className="sn-avatar">{(t.label || 'τ').slice(0, 2)}</span>
                    <div><div className="sn-name">{t.label || short(t.ss58)}</div>
                      <div className="sn-sym" title={t.ss58}>{short(t.ss58)}</div></div></div></td>
                  <td className="num">{warming ? <span className="muted">…</span> : fmt(t.total_tao, 3)}</td>
                  <td className="num">{warming ? '—' : fmt(t.free_tao, 3)}</td>
                  <td className="num">{warming ? '—' : fmt(t.staked_tao, 3)}</td>
                  <td className="num">{t.subnets || 0}</td>
                  <td className="num"><Pct v={t.change_24h} /></td>
                  <td className="num"><Pct v={t.change_7d} /></td>
                  <td className="num">{t.flows_24h || <span className="muted">0</span>}</td>
                  <td><Spark pts={t.spark} dir={t.change_24h} /></td>
                  <td className="num"><button className="copybtn" style={{ position: 'static' }}
                    onClick={e => { e.stopPropagation(); untrack(t.ss58); }}>Untrack</button></td>
                </tr>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}

/* the whole index ranked in one SQL pass — market PnL is the default because
 * raw PnL crowns whoever wired stake in most recently */
function Board() {
  const { openTrader } = useOverlay();
  const [days, setDays] = useStored<'1' | '7' | '30'>('bt.board.days', '7');
  const [sortBy, setSortBy] = useStored<'market_pct' | 'pnl_pct' | 'market_pnl_tao' | 'total_stake_tao'>('bt.board.sort', 'market_pct');
  const board = usePoll<BoardRow[]>(async () =>
    (await call('bt_trader_board', { days: +days, top: 100, sort_by: sortBy, min_subnets: 1, sparks: true })).result.rows || [],
    300_000, [days, sortBy]);
  return (
    <>
      <div className="head-row" style={{ marginTop: 14 }}>
        <Tabs value={days} onChange={setDays} options={[['1', '1d'], ['7', '7d'], ['30', '30d']]} />
        <Tabs value={sortBy} onChange={setSortBy} options={[['market_pct', 'Skill %'], ['pnl_pct', 'PnL %'],
          ['market_pnl_tao', 'Skill τ'], ['total_stake_tao', 'Size']]} />
      </div>
      <div className="scroll-x">
        <table>
          <thead><tr><th>#</th><th>Trader</th><th className="num">Book τ</th><th className="num">Market PnL</th>
            <th className="num">Market %</th><th className="num">Flows τ</th><th className="num">Total %</th>
            <th>Market · flow</th><th>Top subnet</th><th>Equity</th></tr></thead>
          <tbody>
            {board.error && !board.data ? <tr><td colSpan={10} className="muted">{board.error}</td></tr>
              : !board.data ? <tr><td colSpan={10}><Spinner /></td></tr>
              : board.data.map((b, i) => {
                const m = Math.abs(b.market_pnl_tao), f = Math.abs(b.flow_tao), tot = m + f || 1;
                return (
                  <tr key={b.ss58} className="trow" onClick={() => openTrader(b.ss58)}>
                    <td className="num">{i + 1}</td>
                    <td><div className="sn-cell"><span className="sn-avatar">{(b.label || 'τ').slice(0, 2)}</span>
                      <div><div className="sn-name">{b.label || short(b.ss58)}</div>
                        <div className="sn-sym">{short(b.ss58)} · {b.num_subnets} subnets</div></div></div></td>
                    <td className="num">{compact(b.total_stake_tao + b.free_tao)}</td>
                    <td className="num"><span className={b.market_pnl_tao >= 0 ? 'up' : 'down'}>{fmt(b.market_pnl_tao, 2)}</span></td>
                    <td className="num">{b.baseline ? <Pct v={b.market_pct} /> : <span className="muted">warming</span>}</td>
                    <td className="num muted">{fmt(b.flow_tao, 2)}</td>
                    <td className="num"><Pct v={b.baseline ? b.pnl_pct : null} /></td>
                    <td><span className="split" title={`market ${fmt(b.market_pnl_tao, 2)} τ · flows ${fmt(b.flow_tao, 2)} τ`}>
                      <i className={b.market_pnl_tao >= 0 ? 'm' : 'neg'} style={{ width: `${(m / tot) * 100}%` }} />
                      <i className="f" style={{ width: `${(f / tot) * 100}%` }} /></span></td>
                    <td>{b.top_subnet_name ? <>{b.top_subnet_name} <span className="sn-sym">#{b.top_subnet}</span></> : <span className="muted">—</span>}</td>
                    <td><Spark pts={b.spark} dir={b.market_pnl_tao} /></td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ marginTop: 12 }}>Market PnL = Σ alpha held × price move — what trading earned. Flows = stake wired in or out. Book = start + market + flow, exactly. <span className="tag">bt_trader_board</span></p>
    </>
  );
}

export default function Traders() {
  const [tab, setTab] = useStored<'tracked' | 'board'>('bt.traders.tab', 'tracked');
  const { openTrader, openSubnet } = useOverlay();
  const t = useNow(30_000);
  const flows = usePoll<Flow[]>(async () =>
    (await call('bt_trader_flows', { hours: 168, limit: 25 })).result.flows || [], 120_000);
  return (
    <Section id="traders" title="Traders."
      lead="Track any coldkey and the indexer keeps its history — portfolio value over time, PnL, and a trade tape inferred from what actually moved. The accounts you follow, indexed the same way subnets are.">
      <div className="card">
        <TrackForm />
        <div style={{ marginTop: 16 }}>
          <Tabs value={tab} onChange={setTab} options={[['tracked', 'Tracked'], ['board', 'Leaderboard']]} />
        </div>
        {tab === 'tracked' ? <Tracked /> : <Board />}
        {tab === 'tracked' && <p className="muted" style={{ marginTop: 12 }}>Snapshots run every 15 minutes. Change and PnL columns fill in as history accumulates. <span className="tag">bt_track</span> <span className="tag">bt_traders</span> <span className="tag">bt_trader</span></p>}
      </div>
      <div className="card">
        <h3 className="t">The tape</h3>
        <p className="muted" style={{ marginBottom: 12 }}>Buys and sells inferred from how tracked portfolios changed between snapshots, not from extrinsics — moves under 2% of a position (or 0.05 τ) are dropped as emission drift, and a large dividend payout can still read as a small buy.</p>
        {flows.error && !flows.data ? <span className="muted">{flows.error}</span> : !flows.data ? <Spinner /> : flows.data.length ? (
          <div className="scroll-x"><table><thead><tr><th>When</th><th>Trader</th><th>Side</th><th>Subnet</th>
            <th className="num">Alpha</th><th className="num">Value τ</th></tr></thead>
            <tbody>{flows.data.map((f, i) => (
              <tr key={i}>
                <td className="muted">{agoText(Math.max(0, t - f.ts))}</td>
                <td className="click" style={{ cursor: 'pointer' }} title={f.ss58}
                    onClick={() => f.ss58 && openTrader(f.ss58)}>{f.label || short(f.ss58)}</td>
                <td><SideTag side={f.side} /></td>
                <td className="click" style={{ cursor: 'pointer' }} onClick={() => openSubnet(f.netuid)}>
                  {f.name || 'subnet ' + f.netuid} <span className="sn-sym">#{f.netuid}</span></td>
                <td className="num">{fmt(f.alpha, 3)}</td><td className="num">{fmt(f.tao_value, 3)}</td></tr>
            ))}</tbody></table></div>
        ) : <span className="muted">Nothing yet — moves appear once a tracked portfolio changes between two snapshots.</span>}
      </div>
    </Section>
  );
}
