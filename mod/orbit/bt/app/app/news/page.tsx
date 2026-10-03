'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { call } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { usePoll, useStored } from '@/lib/hooks';
import { ago, compact } from '@/lib/format';
import { BuzzRow, NewsItem, NewsList, SOURCE_LABEL } from '@/components/News';
import { Section, Spinner, SubnetLogo, Tabs } from '@/components/ui';

type Kind = 'all' | 'press' | 'release' | 'commit' | 'social';
const KINDS: [Kind, string][] = [['all', 'All'], ['press', 'Press'], ['release', 'Releases'],
  ['commit', 'Code'], ['social', 'Social']];
const KIND_ARG: Record<Kind, string | undefined> = {
  all: 'news,blog,social,release', press: 'news,blog', release: 'release', commit: 'commit', social: 'social',
};
type Win = '7' | '30' | '90' | '0';
const WINS: [Win, string][] = [['7', '7D'], ['30', '30D'], ['90', '90D'], ['0', 'ALL']];
const PAGE = 40;

interface Status {
  items: number; new_24h: number; subnets_covered: number; subnets_known: number; site_feeds_found: number;
  sources: { name: string; label: string; enabled: boolean }[];
  feeds: { url: string; label?: string }[];
  runs: { source: string; runs: number; ok: number; added: number; last_ts: number; avg_ms: number }[];
  running: boolean;
}

export default function News() {
  const { screener, reloadScreener, bySubnet } = useData();
  const { openSubnet } = useOverlay();
  const [netuid, setNetuid] = useState<number | null>(null);
  const [kind, setKind] = useStored<Kind>('bt.news.kind', 'all');
  const [win, setWin] = useStored<Win>('bt.news.days', '30');
  const [focused, setFocused] = useStored<boolean>('bt.news.focused', false);
  const [q, setQ] = useState('');
  const [items, setItems] = useState<NewsItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  const [tick, setTick] = useState(0);

  useEffect(() => { if (!screener) reloadScreener(); }, [screener, reloadScreener]);
  /* ?netuid=64 deep-links one subnet's news (?sn= is the overlay's) */
  useEffect(() => {
    const n = new URLSearchParams(window.location.search).get('netuid');
    if (n != null && n !== '' && !isNaN(+n)) setNetuid(+n);
  }, []);
  const pick = useCallback((n: number | null) => {
    setNetuid(n);
    const u = new URL(window.location.href);
    if (n == null) u.searchParams.delete('netuid'); else u.searchParams.set('netuid', String(n));
    window.history.replaceState(window.history.state, '', u.pathname + u.search);
  }, []);

  const args = useMemo(() => ({
    days: +win, kind: KIND_ARG[kind], focused, limit: PAGE,
    ...(netuid != null ? { netuid } : {}), ...(q.trim() ? { search: q.trim() } : {}),
  }), [win, kind, focused, netuid, q]);

  useEffect(() => {
    let live = true;
    setErr('');
    const t = setTimeout(() => {
      call('bt_news', args).then(j => {
        if (!live) return;
        setItems(j.result.items); setTotal(j.result.total);
      }).catch(e => live && setErr(e.message));
    }, q ? 250 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [args, q, tick]);

  const more = () => {
    if (!items) return;
    call('bt_news', { ...args, offset: items.length })
      .then(j => setItems([...items, ...j.result.items])).catch(e => setErr(e.message));
  };

  const buzz = usePoll<BuzzRow[]>(async () =>
    (await call('bt_news_buzz', { days: 7, limit: 14 })).result.subnets || [], 300_000);
  const status = usePoll<Status>(async () => (await call('bt_news_sources')).result, 60_000, [tick]);

  const scrape = async () => {
    setBusy(netuid == null ? 'polling outlet feeds…' : 'scraping…');
    try {
      const j = await call('bt_news_refresh', netuid == null ? {} : { netuid });
      setBusy(`+${j.result.added} new`);
      setTick(t => t + 1);
    } catch (e: any) { setBusy(e.message); }
    setTimeout(() => setBusy(''), 4000);
  };

  const subs = (screener?.rows || []).slice().sort((a, b) => a.netuid - b.netuid);
  const sel = netuid != null ? bySubnet[netuid] : undefined;
  const st = status.data;

  return (
    <Section id="news" title="News"
      lead="What the internet is saying about every subnet — releases, commits, blog posts, press and threads, scraped from open sources into this module's own index. No API keys, no feed vendor: the scraper is a Python file you can read and run.">
      <div className="card buzz">
        <h3>◎ Talked about this week</h3>
        {buzz.data == null ? <Spinner /> : buzz.data.length ? (
          <div className="buzz-row">{buzz.data.filter(b => b.netuid).map(b => {
            const r = bySubnet[b.netuid];
            return (
              <button key={b.netuid} className={'bchip' + (netuid === b.netuid ? ' on' : '')}
                      onClick={() => pick(netuid === b.netuid ? null : b.netuid)}
                      title={b.headline?.title || ''}>
                <SubnetLogo logo={r?.logo} symbol={r?.symbol} />
                <span>{b.subnet || r?.name || '#' + b.netuid}</span>
                <b>{b.press || b.items}</b>
                {b.items > b.prev_items && <i className="up">▲</i>}
              </button>
            );
          })}</div>
        ) : <span className="muted">The scraper is on its first pass — subnets fill in one by one.</span>}
      </div>

      <div className="news-controls">
        <input className="nsearch" placeholder="Search headlines…" value={q} onChange={e => setQ(e.target.value)} />
        <select value={netuid ?? ''} onChange={e => pick(e.target.value === '' ? null : +e.target.value)}>
          <option value="">Every subnet</option>
          <option value="0">Bittensor (network-wide)</option>
          {subs.filter(r => r.netuid).map(r =>
            <option key={r.netuid} value={r.netuid}>#{r.netuid} {r.name}</option>)}
        </select>
        <Tabs value={kind} options={KINDS} onChange={setKind} />
        <Tabs value={win} options={WINS} onChange={setWin} />
        <label className="ncheck" title="Hide posts that only mention the subnet in passing">
          <input type="checkbox" checked={focused} onChange={e => setFocused(e.target.checked)} /> headline only
        </label>
      </div>

      {sel && (
        <div className="nsel">
          <SubnetLogo logo={sel.logo} symbol={sel.symbol} />
          <b>{sel.name}</b> <span className="sn-sym">#{sel.netuid}</span>
          <button className="linkish" onClick={() => openSubnet(sel.netuid)}>open subnet →</button>
          <button className="linkish" onClick={() => pick(null)}>✕ all subnets</button>
        </div>
      )}

      {err ? <p className="muted">{err}</p> : items == null ? <Spinner /> : items.length ? (
        <>
          <p className="muted ncount">{total.toLocaleString()} item{total === 1 ? '' : 's'}</p>
          <NewsList items={items} bySubnet={bySubnet} showSubnet={netuid == null} />
          {items.length < total && <button className="pill ghost nmore" onClick={more}>Load more</button>}
        </>
      ) : (
        <p className="muted">Nothing here yet{netuid != null ? ' for this subnet' : ''} — widen the window, or scrape now.</p>
      )}

      <div className="card nstatus">
        <h3>The scraper
          <button className="pill ghost" onClick={scrape} disabled={!!busy && busy.endsWith('…')}>
            {busy || (netuid == null ? 'Poll outlet feeds now' : `Scrape ${sel?.name || '#' + netuid} now`)}
          </button>
        </h3>
        {!st ? <Spinner /> : (
          <>
            <p>
              <span className="tag">{compact(st.items)} items</span>{' '}
              <span className="tag">+{st.new_24h} in 24h</span>{' '}
              <span className="tag">{st.subnets_covered}/{st.subnets_known} subnets scraped</span>{' '}
              <span className="tag">{st.site_feeds_found} subnet blogs found</span>{' '}
              <span className={'tag' + (st.running ? '' : ' warn')}>{st.running ? 'background pass running' : 'background pass off'}</span>
            </p>
            <div className="scroll-x"><table>
              <thead><tr><th>Source</th><th className="num">Runs</th><th className="num">OK</th>
                <th className="num">Items added</th><th className="num">Avg</th><th>Last run</th></tr></thead>
              <tbody>{st.sources.map(s => {
                const r = st.runs.find(x => x.source === s.name);
                return (
                  <tr key={s.name} className={s.enabled ? '' : 'muted'}>
                    <td><b>{SOURCE_LABEL[s.name] || s.name}</b> <span className="muted">{s.label}{s.enabled ? '' : ' · off'}</span></td>
                    <td className="num">{r?.runs ?? '—'}</td><td className="num">{r ? r.ok : '—'}</td>
                    <td className="num">{r?.added ?? '—'}</td>
                    <td className="num">{r ? (r.avg_ms / 1000).toFixed(1) + 's' : '—'}</td>
                    <td className="muted">{r ? ago(r.last_ts) : 'never'}</td>
                  </tr>
                );
              })}
              {(() => { const r = st.runs.find(x => x.source === 'feeds'); return (
                <tr><td><b>Outlet feeds</b> <span className="muted">{st.feeds.map(f => f.label || f.url).join(' · ')}</span></td>
                  <td className="num">{r?.runs ?? '—'}</td><td className="num">{r ? r.ok : '—'}</td>
                  <td className="num">{r?.added ?? '—'}</td>
                  <td className="num">{r ? (r.avg_ms / 1000).toFixed(1) + 's' : '—'}</td>
                  <td className="muted">{r ? ago(r.last_ts) : 'never'}</td></tr>); })()}
              </tbody></table></div>
            <p className="muted" style={{ marginTop: 8 }}>
              Add any RSS or Atom feed with the <code className="inline">bt_news_add_feed</code> tool. Items are filed under the subnets they name.
            </p>
          </>
        )}
      </div>
    </Section>
  );
}
