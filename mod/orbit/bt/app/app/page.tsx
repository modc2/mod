'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { BoardRow, call, Flow, SubnetRow } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { useChat } from '@/lib/chat';
import { usePoll, useNow } from '@/lib/hooks';
import { agoText, compact, fmt, fmtPrice, short } from '@/lib/format';
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

export default function Home() {
  const router = useRouter();
  const chat = useChat();
  const { stats, screener, bySubnet } = useData();
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
          <Stat label="Markets" value={stats?.subnets || '—'} />
          <Stat label="Alpha mcap τ" value={compact(stats?.total_market_cap_tao)} />
          <Stat label="24h volume τ" value={stats?.volume_24h_tao != null ? compact(stats.volume_24h_tao) : 'soon'} />
          <Stat label="TAO in pools" value={compact(stats?.total_tao_in_pools)} />
        </div>
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
          <MoverList rows={by('vol_24h').slice(0, 6)} value={r => <>τ {compact(r.vol_24h)}</>} />
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
                <td className="num">τ {fmt(f.tao_value, 3)}</td>
                <td className="num muted">@ {fmtPrice(f.alpha ? f.tao_value / f.alpha : undefined)}</td>
              </tr>
            ))}</tbody></table></div>
          ) : <span className="muted">Quiet — no inferred moves in the last day.</span>}
        </div>
      </div>
    </>
  );
}
