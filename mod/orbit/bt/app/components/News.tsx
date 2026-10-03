'use client';
/* News rows — shared by the /news page, the subnet overlay and the home card.
 * Every item comes from bt_news: the module's own scraper index on local
 * SQLite, so this paints in milliseconds and never calls a third party. */
import { SubnetRow } from '@/lib/api';
import { useOverlay } from '@/lib/overlay';
import { ago } from '@/lib/format';
import { SubnetLogo } from './ui';

export interface NewsItem {
  netuid: number; subnet?: string | null; source: string; kind: string;
  title: string; url: string; summary?: string; author?: string; publisher?: string;
  ts: number; first_seen: number; focus: number;
}

export interface BuzzRow {
  netuid: number; subnet?: string | null; items: number; press: number; releases: number;
  commits: number; prev_items: number; last_ts: number;
  headline?: { title: string; url: string; ts: number; source: string } | null;
}

export const KIND_LABEL: Record<string, string> = {
  news: 'press', blog: 'blog', social: 'social', release: 'release', commit: 'code',
};

export const SOURCE_LABEL: Record<string, string> = {
  github: 'GitHub', site: 'own blog', gnews: 'Google News', reddit: 'Reddit',
  hn: 'Hacker News', bing: 'Bing News', feeds: 'outlet feed',
};

export function KindTag({ kind }: { kind: string }) {
  return <span className={'ntag k-' + kind}>{KIND_LABEL[kind] || kind}</span>;
}

export function NewsList({ items, bySubnet, showSubnet = true, compact = false }:
  { items: NewsItem[]; bySubnet?: Record<number, SubnetRow>; showSubnet?: boolean; compact?: boolean }) {
  const { openSubnet } = useOverlay();
  return (
    <div className={'news' + (compact ? ' compact' : '')}>
      {items.map(it => {
        const r = bySubnet?.[it.netuid];
        return (
          <article key={it.netuid + it.url} className={'nrow' + (it.focus ? '' : ' aside')}>
            <div className="nmeta">
              <span className="muted" title={new Date(it.ts * 1000).toLocaleString()}>{ago(it.ts)}</span>
              <KindTag kind={it.kind} />
              {showSubnet && (
                <button className="nsub" onClick={() => openSubnet(it.netuid)} title={'open subnet ' + it.netuid}>
                  {it.netuid ? <SubnetLogo logo={r?.logo} symbol={r?.symbol} /> : <span className="sn-avatar">τ</span>}
                  {it.netuid ? (it.subnet || r?.name || 'subnet ' + it.netuid) : 'Bittensor'}
                  {it.netuid > 0 && <span className="sn-sym">#{it.netuid}</span>}
                </button>
              )}
              <span className="npub">{it.publisher || SOURCE_LABEL[it.source] || it.source}</span>
              {!it.focus && <span className="muted" title="The subnet is named in the post, not the headline">· mention</span>}
            </div>
            <a className="ntitle" href={it.url} target="_blank" rel="noopener noreferrer">{it.title}</a>
            {!compact && it.summary && <p className="nsum">{it.summary}</p>}
          </article>
        );
      })}
    </div>
  );
}
