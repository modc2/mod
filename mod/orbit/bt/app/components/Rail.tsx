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
              <i />{r.label}{badge[r.href] && <em className="rbadge">{badge[r.href]}</em>}
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
          <button className="rw connect" onClick={() => w.setPopOpen(true)}>Connect wallet</button>
        )}
      </div>
      <div className="rail-foot">
        <span className={'netdot ' + (d.online == null ? '' : d.online ? 'on' : 'off')} />
        <span>{d.info?.network || 'finney'}</span>
        <span className="rail-block">{d.info?.block ? '#' + compact(d.info.block) : ''}</span>
        {w.barHidden && <button onClick={() => w.setBarHidden(false)}>show wallet</button>}
      </div>
    </aside>
  );
}
