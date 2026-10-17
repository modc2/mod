'use client';
/* Development activity: each subnet's own GitHub, from the local git index
 * (bt/git.py). `SubnetCode` is the subnet overlay's Code tab; `DevBoard` is
 * the Markets page's Dev mode — every subnet ranked by who is shipping, with
 * a one-click export of every subnet's full profile. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { call } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { ago, compact, fmt, when } from '@/lib/format';
import { usePoll } from '@/lib/hooks';
import { Cells, Spinner, SubnetLogo } from './ui';
import RepoLine from './Repo';

export interface DevRow {
  netuid: number; url?: string | null; repo?: string | null; resolved_from?: string | null;
  description?: string | null; homepage?: string | null;
  stars?: number | null; forks?: number | null; watchers?: number | null;
  open_issues?: number | null; closed_issues?: number | null; open_prs?: number | null; merged_prs?: number | null;
  license?: string | null; language?: string | null;
  languages?: { name: string; color?: string | null; pct?: number | null }[] | null;
  topics?: string[] | null; created_at?: number | null; pushed_at?: number | null;
  archived?: number | null; size_kb?: number | null; default_branch?: string | null;
  commits_total?: number | null; commits_7d?: number | null; commits_30d?: number | null; commits_90d?: number | null;
  authors_30d?: number | null; bot_pct_30d?: number | null; contributors?: number | null;
  last_commit_ts?: number | null; last_commit_msg?: string | null; last_commit_author?: string | null; last_commit_sha?: string | null;
  releases?: number | null; latest_release?: string | null; latest_release_ts?: number | null;
  weekly?: number[] | null; owner?: string | null; owner_kind?: string | null; owner_repos?: number | null;
  owner_top?: { name: string; stars: number; pushed_at?: number | null; description?: string | null }[] | null;
  stars_gain_7d?: number | null; stars_gain_30d?: number | null; commits_gain_7d?: number | null;
  dev_score?: number | null; days_since_commit?: number | null; age_days?: number | null;
  fetched_ts?: number | null; error?: string | null; rank?: number | null; ranked_of?: number | null;
  history?: { day: string; stars: number; forks: number; commits_total: number }[];
}

/* 52 weekly commit counts as bars — the shape of a team's year */
function Weekly({ w, h = 46 }: { w?: number[] | null; h?: number }) {
  if (!w || !w.length) return <span className="muted">no weekly series yet</span>;
  const max = Math.max(1, ...w);
  return (
    <div className="dv-weeks" style={{ height: h }} title={`${w.reduce((a, b) => a + b, 0)} commits in 52 weeks`}>
      {w.map((v, i) => <i key={i} style={{ height: `${Math.max(v ? 6 : 2, v / max * 100)}%` }}
                          className={v ? '' : 'z'} title={`${52 - i}w ago: ${v}`} />)}
    </div>
  );
}

function Score({ v }: { v?: number | null }) {
  if (v == null) return <span className="muted">—</span>;
  const tier = v >= 60 ? 'hi' : v >= 30 ? 'mid' : 'lo';
  return <span className={'dv-score ' + tier} title="dev score 0-100: recent commits, authors, freshness, stars, contributors">{v.toFixed(0)}</span>;
}

const gh = (repo: string, path = '') => `https://github.com/${repo}${path}`;

export function SubnetCode({ netuid, github }: { netuid: number; github?: string | null }) {
  const [d, setD] = useState<DevRow | null | undefined>(undefined);
  const [busy, setBusy] = useState('');
  const load = useCallback(() => call<DevRow>('bt_git', { netuid })
    .then(j => setD(j.result)).catch(() => setD(null)), [netuid]);
  useEffect(() => { setD(undefined); load(); }, [load]);
  const refetch = () => {
    setBusy('fetching…');
    call('bt_git_refresh', { netuid })
      .then(() => { setBusy('updated'); load(); })
      .catch(e => setBusy(e.message))
      .finally(() => setTimeout(() => setBusy(''), 3000));
  };

  if (d === undefined) return <Spinner />;
  const head = (
    <div className="nhead sn-tabhead">
      <span className="muted">{d?.fetched_ts ? `GitHub, fetched ${ago(d.fetched_ts)}` : 'GitHub activity'}</span>
      <span className="nhead-r"><button className="linkish" onClick={refetch} disabled={busy === 'fetching…'}>{busy || 'refresh'}</button></span>
    </div>
  );
  if (!d || !d.repo) return (
    <>{head}<RepoLine github={github} />
      <p className="muted">{d?.error || 'Not fetched yet — the indexer reaches every subnet within minutes, or refresh.'}</p></>
  );
  const r = d.repo;
  return (
    <>
      {head}
      <div className="dv-top">
        <div className="dv-id">
          <a className="dv-repo" href={gh(r)} target="_blank" rel="noopener noreferrer">{r}</a>
          {d.archived ? <span className="dv-tag bad">archived</span> : null}
          {d.resolved_from && <span className="dv-tag" title={d.resolved_from}>org's top repo</span>}
          {d.description && <p className="desc">{d.description}</p>}
          {d.topics && d.topics.length > 0 && <div className="dv-topics">{d.topics.map(t => <span key={t}>{t}</span>)}</div>}
        </div>
        <div className="dv-scorebox">
          <Score v={d.dev_score} />
          <span className="muted">{d.rank ? `#${d.rank} of ${d.ranked_of} by dev score` : 'dev score'}</span>
        </div>
      </div>

      <div className="dv-kpis">
        <div><b>{compact(d.stars)}</b><span>stars{d.stars_gain_7d ? <em> +{d.stars_gain_7d} 7d</em> : null}</span></div>
        <div><b>{compact(d.forks)}</b><span>forks</span></div>
        <div><b>{compact(d.contributors)}</b><span>contributors</span></div>
        <div><b>{fmt(d.commits_30d, 0)}</b><span>commits 30d{d.bot_pct_30d ? <em className="dv-bot"> {Math.round(d.bot_pct_30d)}% bot</em> : null}</span></div>
        <div><b>{fmt(d.authors_30d, 0)}</b><span>devs 30d</span></div>
        <div><b>{d.days_since_commit == null ? '—' : d.days_since_commit < 1 ? 'today' : `${Math.round(d.days_since_commit)}d`}</b><span>last commit</span></div>
      </div>

      <div className="dv-sect"><span className="dv-k">Commits per week, last 52</span><Weekly w={d.weekly} /></div>

      {d.languages && d.languages.length > 0 && (
        <div className="dv-sect">
          <div className="dv-langbar">{d.languages.map(l =>
            <i key={l.name} style={{ width: `${l.pct || 0}%`, background: l.color || 'var(--ink3)' }} title={`${l.name} ${l.pct}%`} />)}</div>
          <div className="dv-langs">{d.languages.map(l =>
            <span key={l.name}><i style={{ background: l.color || 'var(--ink3)' }} />{l.name} <em>{l.pct}%</em></span>)}</div>
        </div>
      )}

      <Cells items={[
        ['Commits 7d / 90d', `${fmt(d.commits_7d, 0)} / ${fmt(d.commits_90d, 0)}`],
        ['Commits all-time', fmt(d.commits_total, 0)],
        ['Open issues', <a key="i" href={gh(r, '/issues')} target="_blank" rel="noopener noreferrer">{fmt(d.open_issues, 0)}</a>],
        ['Closed issues', fmt(d.closed_issues, 0)],
        ['Open PRs', <a key="p" href={gh(r, '/pulls')} target="_blank" rel="noopener noreferrer">{fmt(d.open_prs, 0)}</a>],
        ['Merged PRs', fmt(d.merged_prs, 0)],
        ['Releases', d.releases ? <a key="r" href={gh(r, '/releases')} target="_blank" rel="noopener noreferrer">
          {fmt(d.releases, 0)}{d.latest_release ? ` · ${d.latest_release}` : ''}</a> : '0'],
        ['Watchers', fmt(d.watchers, 0)],
        ['License', d.license || '—'],
        ['Created', d.created_at ? `${when(d.created_at).split(',')[0]} (${d.age_days}d)` : '—'],
        ['Size', d.size_kb != null ? `${compact(d.size_kb / 1024)} MB` : '—'],
        ['Branch', d.default_branch || '—'],
      ]} />

      {d.last_commit_msg && (
        <div className="dv-last">
          <span className="dv-k">Latest commit</span>
          <a href={gh(r, `/commit/${d.last_commit_sha}`)} target="_blank" rel="noopener noreferrer">
            <code>{d.last_commit_sha?.slice(0, 7)}</code> {d.last_commit_msg}</a>
          <span className="muted"> — {d.last_commit_author} · {ago(d.last_commit_ts)}</span>
        </div>
      )}

      {d.owner_top && d.owner_top.length > 0 && (
        <div className="dv-sect">
          <span className="dv-k">{d.owner} · {d.owner_repos} public repos · recently pushed</span>
          <div className="dv-owner">{d.owner_top.map(o => (
            <a key={o.name} href={gh(`${d.owner}/${o.name}`)} target="_blank" rel="noopener noreferrer" title={o.description || ''}>
              <b>{o.name}</b><span>★ {compact(o.stars)} · {o.pushed_at ? ago(o.pushed_at) : '—'}</span></a>
          ))}</div>
        </div>
      )}
      <RepoLine github={github} />
      {d.error && <p className="muted">last refresh failed: {d.error}</p>}
    </>
  );
}

/* ------------------------------------------------------------ the board */

const COLS: { key: keyof DevRow; label: string; title?: string }[] = [
  { key: 'netuid', label: '#' },
  { key: 'repo', label: 'Subnet · repo' },
  { key: 'dev_score', label: 'Score' },
  { key: 'commits_30d', label: 'Commits 30d' },
  { key: 'authors_30d', label: 'Devs 30d', title: 'distinct human commit authors in 30 days' },
  { key: 'weekly', label: '52 weeks' },
  { key: 'stars', label: 'Stars' },
  { key: 'stars_gain_7d', label: '★ 7d' },
  { key: 'forks', label: 'Forks' },
  { key: 'contributors', label: 'Contrib.' },
  { key: 'open_issues', label: 'Issues' },
  { key: 'open_prs', label: 'PRs' },
  { key: 'releases', label: 'Releases' },
  { key: 'last_commit_ts', label: 'Last commit' },
];

interface Board { rows: DevRow[]; with_repo: number; subnets_known: number; no_repo: number[];
  active_7d: number; commits_30d_total: number }

function download(name: string, text: string, type: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* every subnet's market + dev + news + flow as one flat file */
function useExport() {
  const [busy, setBusy] = useState('');
  const run = (fmtKind: 'csv' | 'json') => {
    setBusy(fmtKind);
    call('bt_subnet_profiles', {}).then(j => {
      const rows: Record<string, any>[] = j.result.rows;
      const day = new Date().toISOString().slice(0, 10);
      if (fmtKind === 'json') return download(`bt-subnets-${day}.json`, JSON.stringify(rows, null, 1), 'application/json');
      const flat = rows.map(r => {
        const o: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(r)) {
          if (v && typeof v === 'object' && !Array.isArray(v)) for (const [k2, v2] of Object.entries(v)) o[`${k}.${k2}`] = v2;
          else if (v === null && ['dev', 'news_7d', 'flow_24h'].includes(k)) continue;
          else o[k] = v;
        }
        return o;
      });
      const cols = Array.from(new Set(flat.flatMap(o => Object.keys(o))));
      const esc = (v: unknown) => v == null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
      download(`bt-subnets-${day}.csv`, [cols.join(','), ...flat.map(o => cols.map(c => esc(o[c])).join(','))].join('\n'), 'text/csv');
    }).then(() => setBusy(''), e => { setBusy(e.message); setTimeout(() => setBusy(''), 4000); });
  };
  return { busy, run };
}

export function DevBoard({ q }: { q: string }) {
  const { bySubnet } = useData();
  const { openSubnet } = useOverlay();
  const [sort, setSort] = useState<{ key: keyof DevRow; dir: 1 | -1 }>({ key: 'dev_score', dir: -1 });
  const b = usePoll<Board>(async () => (await call('bt_git_board', {})).result, 300_000, [], 'gitboard');
  const ex = useExport();

  const rows = useMemo(() => {
    let rs = (b.data?.rows || []).filter(r => r.repo);
    if (q) rs = rs.filter(r => {
      const s = bySubnet[r.netuid];
      return String(s?.name || '').toLowerCase().includes(q) || String(r.repo).toLowerCase().includes(q)
        || String(s?.symbol || '').toLowerCase().includes(q) || String(r.netuid) === q
        || String(r.language || '').toLowerCase() === q;
    });
    const k = sort.key;
    return [...rs].sort((a, c) => {
      const av = k === 'weekly' ? (a.weekly || []).reduce((x, y) => x + y, 0) : a[k];
      const cv = k === 'weekly' ? (c.weekly || []).reduce((x, y) => x + y, 0) : c[k];
      if (typeof av === 'string' || typeof cv === 'string') return sort.dir * String(av || '').localeCompare(String(cv || ''));
      return sort.dir * (((av as number) ?? -Infinity) - ((cv as number) ?? -Infinity));
    });
  }, [b.data, q, sort, bySubnet]);
  const sortBy = (key: keyof DevRow) => setSort({ key, dir: sort.key === key ? (-sort.dir as 1 | -1) : -1 });

  if (!b.data) return <p className="muted">{b.error || <Spinner />}</p>;
  const d = b.data;
  return (
    <>
      <div className="flow-head dv-head">
        <span className="muted">
          {d.with_repo} of {d.subnets_known} subnets publish a repo · {d.active_7d} committed this week ·
          {' '}{compact(d.commits_30d_total)} commits in 30 days — from each subnet&apos;s GitHub, indexed locally.
        </span>
        <span className="grow" />
        <span className="dv-export">Export every subnet
          <button onClick={() => ex.run('csv')} disabled={!!ex.busy}>{ex.busy === 'csv' ? '…' : 'CSV'}</button>
          <button onClick={() => ex.run('json')} disabled={!!ex.busy}>{ex.busy === 'json' ? '…' : 'JSON'}</button>
          {ex.busy && !['csv', 'json'].includes(ex.busy) && <span className="muted">{ex.busy}</span>}
        </span>
      </div>
      <div className="screener-wrap">
        <table className="screener dev">
          <thead><tr>{COLS.map((c, i) => (
            <th key={c.key} className={i > 1 ? 'num' : ''} title={c.title} onClick={() => sortBy(c.key)}>
              {c.label} {sort.key === c.key && <span className="arrow">{sort.dir < 0 ? '▼' : '▲'}</span>}
            </th>
          ))}</tr></thead>
          <tbody>
            {rows.length ? rows.map(r => {
              const s = bySubnet[r.netuid];
              return (
                <tr key={r.netuid} onClick={() => openSubnet(r.netuid, 'code')}>
                  <td className="num">{r.netuid}</td>
                  <td><div className="sn-cell"><SubnetLogo logo={s?.logo} symbol={s?.symbol} />
                    <div><div className="sn-name">{s?.name || '—'}{r.archived ? <span className="dv-tag bad">archived</span> : null}</div>
                      <div className="sn-sym">{r.repo}{r.language ? <> · {r.language}</> : null}</div></div></div></td>
                  <td className="num"><Score v={r.dev_score} /></td>
                  <td className="num" title={r.bot_pct_30d ? `${Math.round(r.bot_pct_30d)}% of recent commits by bots` : ''}>
                    {fmt(r.commits_30d, 0)}{r.bot_pct_30d && r.bot_pct_30d >= 50 ? <span className="dv-botmark"> bot</span> : null}</td>
                  <td className="num">{fmt(r.authors_30d, 0)}</td>
                  <td className="num"><Weekly w={r.weekly} h={22} /></td>
                  <td className="num">{compact(r.stars)}</td>
                  <td className="num">{r.stars_gain_7d ? <span className="up">+{r.stars_gain_7d}</span> : <span className="muted">—</span>}</td>
                  <td className="num">{compact(r.forks)}</td>
                  <td className="num">{compact(r.contributors)}</td>
                  <td className="num">{compact(r.open_issues)}</td>
                  <td className="num">{compact(r.open_prs)}</td>
                  <td className="num">{compact(r.releases)}</td>
                  <td className="num" title={r.last_commit_msg || ''}>{r.last_commit_ts ? ago(r.last_commit_ts) : '—'}</td>
                </tr>
              );
            }) : <tr><td colSpan={COLS.length} className="muted">{q ? 'No matches.' : 'The GitHub indexer is on its first pass.'}</td></tr>}
          </tbody>
        </table>
      </div>
      {d.no_repo.length > 0 && (
        <p className="muted dv-norepo">No public repo in the on-chain identity:{' '}
          {d.no_repo.map((n, i) => <span key={n}>{i ? ', ' : ''}<button className="linkish" onClick={() => openSubnet(n)}>
            {bySubnet[n]?.name || 'SN' + n}</button></span>)}</p>
      )}
    </>
  );
}
