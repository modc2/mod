'use client';
/* The sidebar: brand ?-block, three groups of routes with live badges, the
 * wallet mini-card and the chain foot. On narrow screens it folds into a
 * sticky scrolling row under the top bar. */
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useData } from '@/lib/data';
import { useWallet } from '@/lib/wallet';
import { useChat } from '@/lib/chat';
import { BASE } from '@/lib/api';
import { compact, fmt, short } from '@/lib/format';
import { Ident } from './ui';

export const ROUTES: { group: string; items: { href: string; label: string; dot: string }[] }[] = [
  { group: 'Explore', items: [
    { href: '/markets', label: 'Markets', dot: '#fbd000' },
    { href: '/traders', label: 'Traders', dot: '#049cd8' },
    { href: '/account', label: 'Account', dot: '#e52521' },
    { href: '/news', label: 'News', dot: '#f2f2f2' },
  ] },
  { group: 'You', items: [
    { href: '/wallet', label: 'Wallet', dot: '#43b047' },
    { href: '/trade', label: 'Trade', dot: '#ff8c1a' },
  ] },
  { group: 'Tools', items: [
    { href: '/chat', label: 'Chat', dot: '#8f7bff' },
    { href: '/console', label: 'Console', dot: '#64d2ff' },
    { href: '/docs', label: 'Docs', dot: '#b07a3c' },
    { href: '/open', label: 'Open', dot: '#ff5a9e' },
    { href: '/mcp', label: 'MCP', dot: '#9aa3e8' },
  ] },
];

/* 16px line icons, one path each so they inherit currentColor */
const ICON: Record<string, string> = {
  '/markets': 'M2 13.5h12M3.5 11V7.5M6.5 11V4M9.5 11V6M12.5 11V2.5',
  '/traders': 'M6 7.2a2.4 2.4 0 1 0 0-4.8 2.4 2.4 0 0 0 0 4.8ZM1.8 13.6c.5-2.4 2.2-3.8 4.2-3.8s3.7 1.4 4.2 3.8M10.6 2.6a2.3 2.3 0 0 1 0 4.4M12 9.9c1.2.5 2 1.7 2.3 3.7',
  '/account': 'M8 8a2.7 2.7 0 1 0 0-5.4A2.7 2.7 0 0 0 8 8ZM2.8 14c.6-2.8 2.6-4.3 5.2-4.3s4.6 1.5 5.2 4.3',
  '/news': 'M2.5 3.5h8.5v9.5h-7a1.5 1.5 0 0 1-1.5-1.5v-8ZM11 6h2.5v5.5a1.5 1.5 0 0 1-3 0M4.8 6.2h3.9M4.8 8.5h3.9M4.8 10.8h2.4',
  '/wallet': 'M2.5 4.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-8a1.5 1.5 0 0 1 1.5-1.5h8M10.6 9h1.2',
  '/trade': 'M3 5.5h10.5L11 3M13 10.5H2.5L5 13',
  '/chat': 'M2.5 3.5h11v7.5H7l-3 2.5v-2.5H2.5v-7.5Z',
  '/console': 'M2 3h12v10H2V3ZM4.5 6.2 6.6 8l-2.1 1.8M8 10h3.5',
  '/docs': 'M4 1.8h5.5L12.5 5v9.2H4V1.8ZM9.3 1.8V5h3.2M6 8h4.5M6 10.5h4.5',
  '/open': 'M5.5 4.5 2 8l3.5 3.5M10.5 4.5 14 8l-3.5 3.5M9 2.5 7 13.5',
  '/mcp': 'M5.5 2v3.5M10.5 2v3.5M3.5 5.5h9v2.5a4.5 4.5 0 0 1-9 0V5.5ZM8 12.5V15',
};

export default function Rail() {
  const path = usePathname();
  const d = useData();
  const w = useWallet();
  const chat = useChat();
  const badge: Record<string, string | undefined> = {
    '/markets': d.screener?.rows?.length ? String(d.screener.rows.length) : undefined,
    '/traders': d.traders?.length ? String(d.traders.length) : undefined,
    '/chat': chat.running ? '●' : undefined,
  };
  const wal = w.wallet;

  return (
    <aside className="rail">
      {/* a plain anchor: Next's export fetches the "/" payload at /bt.txt, outside the gateway's /bt/* route */}
      <a className={'rail-brand' + (path === '/' ? ' active' : '')} href={BASE || '/'}>
        <span className="rb-mark">τ</span>
        <span className="rb-text"><b>bt</b><em>open explorer</em></span>
      </a>
      {ROUTES.map(g => (
        <div className="rail-group" key={g.group}>
          <span className="rail-label">{g.group}</span>
          {g.items.map(r => (
            <Link key={r.href} href={r.href} className={path === r.href ? 'active' : ''}
                  style={{ ['--dot' as string]: r.dot }}>
              <svg className="ri" viewBox="0 0 16 16" aria-hidden><path d={ICON[r.href]} /></svg>{r.label}{badge[r.href] && <em className="rbadge">{badge[r.href]}</em>}
            </Link>
          ))}
        </div>
      ))}
      <div className="rail-wallet">
        {wal ? (
          <Link className="rw" href="/wallet" title={`${wal.addr} — open your wallet`}>
            <Ident addr={wal.addr} size={26} />
            <span className="rw-t"><b>{wal.name || short(wal.addr)}</b>
              <i className={wal.tao ? '' : 'zero'}>{wal.tao == null ? 'τ —' : 'τ ' + fmt(wal.tao, 3)}</i></span>
          </Link>
        ) : (
          <button className="rw connect" onClick={() => w.setPopOpen(true)}>
            <span className="rw-ph" aria-hidden>τ</span>
            <span className="rw-t"><b>No wallet</b><i className="zero">connect or watch one</i></span>
          </button>
        )}
      </div>
      <div className="rail-foot">
        <span className={'netdot ' + (d.online == null ? '' : d.online ? 'on' : 'off')} />
        <span>{d.info?.network || 'finney'}</span>
        <span className="rail-block">{d.info?.block ? '#' + compact(d.info.block) : ''}</span>
      </div>
    </aside>
  );
}
