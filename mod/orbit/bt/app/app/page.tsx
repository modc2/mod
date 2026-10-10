'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { BoardRow, call, Flow, FlowRow, SubnetRow } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { useChat } from '@/lib/chat';
import { usePoll, useNow } from '@/lib/hooks';
import { agoText, compact, fmt, fmtPrice, money, short } from '@/lib/format';
import { Pct, SideTag, Spinner, Stat, SubnetLogo } from '@/components/ui';
import { NewsItem, NewsList } from '@/components/News';

function Synced() {
  const { syncAt, syncBlock } = useData();
  const t = useNow();
  if (syncAt == null) return <div className="synced stale"><i /><em>last sync — warming up</em></div>;
  const age = Math.max(0, t - syncAt);
  return (
    <div className={'synced' + (age > 900 ? ' dead' : age > 300 ? ' stale' : '')}
         title={`Local indexer snapshot · ${new Date(syncAt * 1000).toLocaleString()}`}>
      <i /><em>synced {agoText(age)} · {syncBlock ? `block #${syncBlock.toLocaleString()}` :
        new Date(syncAt * 1000).toLocaleTimeString()}</em>
    </div>
  );
}

function MoverList({ rows, value }: { rows: SubnetRow[]; value: (r: SubnetRow) => React.ReactNode }) {
  const { openSubnet } = useOverlay();
  if (!rows.length) return <span className="muted">Waiting on the indexer…</span>;
  return <>{rows.map(r => (
    <div key={r.netuid} className="mrow" onClick={() => openSubnet(r.netuid)}>
      <SubnetLogo logo={r.logo} symbol={r.symbol} />
      <span className="nm">{r.name || 'subnet ' + r.netuid} <span className="sn-sym">#{r.netuid}</span></span>
      <span className="v">{value(r)}</span>
    </div>
  ))}</>;
}

/* the network line taostats leads with: supply, halving clock, staked — free */
function NetLine() {
  const { network: n } = useData();
  if (!n?.total_issuance_tao) return null;
  const nextIn = n.est_days_to_halving;
  const when = nextIn ? new Date(Date.now() + nextIn * 86400e3)
    .toLocaleDateString(undefined, { month: 'short', year: 'numeric' }) : null;
  return (
    <div className="netline" title={`block #${n.block?.toLocaleString()} · emission ${n.block_emission_tao} τ/block`}>
      <span><em>issued</em> {compact(n.total_issuance_tao)} / 21M τ</span>
      <span className="netbar"><i style={{ width: `${n.pct_issued?.toFixed(1)}%` }} /></span>
      <span>{n.pct_issued?.toFixed(1)}%</span>
      <span><em>halvings</em> {n.halvings}</span>
      {when && <span><em>next</em> ~{when}</span>}
      {n.staked_pct != null && <span><em>staked</em> {n.staked_pct.toFixed(0)}%</span>}
    </div>
  );
}

export default function Home() {
  const router = useRouter();
  const chat = useChat();
  const { stats, screener, bySubnet, usd, ccy, rate } = useData();
  const { openTrader, openSubnet } = useOverlay();
  const t = useNow(30_000);
  const rows = (screener?.rows || []).filter(r => r.netuid !== 0);
  const by = (k: keyof SubnetRow, dir = -1) => [...rows]
    .filter(r => typeof r[k] === 'number').sort((a, b) => dir * ((a[k] as number) - (b[k] as number)));

  const tape = usePoll<Flow[]>(async () =>
    (await call('bt_trader_flows', { hours: 24, limit: 8 })).result.flows || [], 120_000);
  const board = usePoll<BoardRow[]>(async () =>
    ((await call('bt_trader_board', { days: 7, top: 6, sort_by: 'market_pct', min_subnets: 1 })).result.rows || [])
      .filter((r: BoardRow) => r.baseline), 300_000);
  const headlines = usePoll<NewsItem[]>(async () =>
    (await call('bt_news', { days: 7, limit: 8, kind: 'news,blog', focused: true })).result.items || [], 300_000);
  const flows = usePoll<FlowRow[]>(async () =>
    (await call('bt_flows', { hours: 24 })).result.rows || [], 120_000, [], 'homeflows');

  return (
    <>
      <div className="hero page">
        <span className="world"><span className="coin" />live on finney · {stats?.subnets || '—'} subnet markets</span>
        <h1>The <span className="tao">open</span> Bittensor explorer.</h1>
        <p className="sub">Every subnet, price, validator and account — indexed locally, served instantly.<br />
          No closed backend. No API key. The whole stack is open source.</p>
        <div className="cta">
          <button className="pill primary" onClick={() => router.push('/markets')}>Explore markets</button>
          <button className="pill ghost" onClick={chat.newChat} disabled={chat.running}>Chat with the network</button>
        </div>
        <div className="stats">
          <Stat label={usd?.change_24h != null
              ? `TAO · ${usd.change_24h >= 0 ? '+' : ''}${usd.change_24h.toFixed(1)}% 24h` : 'TAO price'}
            value={usd ? '$' + fmt(usd.usd, 2) : '—'} />
          <Stat label="Markets" value={stats?.subnets || '—'} />
          <Stat label="Alpha mcap" value={money(stats?.total_market_cap_tao, ccy, rate)} />
          <Stat label="24h volume" value={stats?.volume_24h_tao != null ? money(stats.volume_24h_tao, ccy, rate) : 'soon'} />
          <Stat label="TAO in pools" value={compact(stats?.total_tao_in_pools)} />
        </div>
        <NetLine />
        <Synced />
      </div>

      <div className="dash">
        <div className="card">
          <h3>▲ Top gainers 24h <Link href="/markets">all →</Link></h3>
          <MoverList rows={by('change_24h').slice(0, 6)} value={r => <Pct v={r.change_24h} />} />
        </div>
        <div className="card">
          <h3>▼ Top losers 24h <Link href="/markets">all →</Link></h3>
          <MoverList rows={by('change_24h', 1).slice(0, 6)} value={r => <Pct v={r.change_24h} />} />
        </div>
        <div className="card">
          <h3>◆ Most traded 24h <Link href="/markets">all →</Link></h3>
          <MoverList rows={by('vol_24h').slice(0, 6)} value={r => <>{money(r.vol_24h, ccy, rate)}</>} />
        </div>
        <div className="card">
          <h3>⇄ TAO flows 24h <Link href="/markets">board →</Link></h3>
          {flows.data == null ? <Spinner /> : flows.data.length ? (() => {
            const sorted = [...flows.data].sort((a, b) => b.net_tao - a.net_tao);
            const pick = [...sorted.slice(0, 3), ...sorted.slice(-3)];
            return pick.map(f => (
              <div key={f.netuid} className="mrow" onClick={() => openSubnet(f.netuid)}>
                <SubnetLogo logo={f.logo} symbol={f.symbol} />
                <span className="nm">{f.name || 'subnet ' + f.netuid} <span className="sn-sym">#{f.netuid}</span></span>
                <span className="v" style={{ color: f.net_tao >= 0 ? 'var(--good)' : 'var(--bad)' }}>
                  {f.net_tao >= 0 ? '+' : '−'}{money(Math.abs(f.net_tao), ccy, rate)}</span>
              </div>
            ));
          })() : <span className="muted">The trade indexer is on its first pass.</span>}
        </div>
        <div className="card">
          <h3>★ Best traders 7d <Link href="/traders">board →</Link></h3>
          {board.data == null ? <Spinner /> : board.data.length ? board.data.map(b => (
            <div key={b.ss58} className="mrow" onClick={() => openTrader(b.ss58)}>
              <span className="sn-avatar">{(b.label || 'τ').slice(0, 2)}</span>
              <span className="nm">{b.label || short(b.ss58)}
                <span className="sn-sym"> · τ {compact(b.total_stake_tao + b.free_tao)}</span></span>
              <span className="v"><Pct v={b.market_pct} /></span>
            </div>
          )) : <span className="muted">The trader index is still building its first week.</span>}
        </div>
        <div className="card" style={{ gridColumn: '1 / -1' }}>
          <h3>◎ In the news — this week <Link href="/news">all news →</Link></h3>
          {headlines.data == null ? <Spinner /> : headlines.data.length
            ? <NewsList items={headlines.data} bySubnet={bySubnet} compact />
            : <span className="muted">The news scraper is on its first pass over the subnets.</span>}
        </div>
        <div className="card" style={{ gridColumn: '1 / -1' }}>
          <h3>● The tape — last 24h <Link href="/traders">traders →</Link></h3>
          {tape.data == null ? <Spinner /> : tape.data.length ? (
            <div className="scroll-x"><table><tbody>{tape.data.map((f, i) => (
              <tr key={i}>
                <td className="muted">{agoText(Math.max(0, t - f.ts))}</td>
                <td className="click" onClick={() => f.ss58 && openTrader(f.ss58)} title={f.ss58}
                    style={{ cursor: 'pointer' }}>{f.label || short(f.ss58)}</td>
                <td><SideTag side={f.side} /></td>
                <td className="click" style={{ cursor: 'pointer' }} onClick={() => openSubnet(f.netuid)}>
                  {f.name || 'subnet ' + f.netuid} <span className="sn-sym">#{f.netuid}</span></td>
                <td className="num">{money(f.tao_value, ccy, rate, 'fixed')}</td>
                <td className="num muted">@ {fmtPrice(f.alpha ? f.tao_value / f.alpha : undefined)}</td>
              </tr>
            ))}</tbody></table></div>
          ) : <span className="muted">Quiet — no inferred moves in the last day.</span>}
        </div>
      </div>
    </>
  );
}
