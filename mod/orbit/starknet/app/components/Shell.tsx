'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BASE } from '@/lib/api';
import { useApi } from '@/lib/hooks';
import { useNet } from '@/lib/net';
import Search from './Search';

const NAV = [
  { href: '/', label: 'Explore' },
  { href: '/privacy', label: 'Privacy pool' },
  { href: '/developers', label: 'API & MCP' },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || '/';
  const { net, setNet } = useNet();
  const status = useApi<any>('status', 30000);
  const live = !!status.data;

  return (
    <div className="shell">
      <header className="top">
        <div className="top-inner">
          {/* plain <a>: the root route's RSC payload lives outside /starknet/* */}
          <a href={`${BASE}/`} className="brand">
            <span className="mark">S</span>
            <span className="word"><b>stark</b>net</span>
          </a>
          <nav>
            {NAV.map(n => {
              const on = n.href === '/' ? ['/', '/block', '/tx', '/address'].includes(path) : path.startsWith(n.href);
              return n.href === '/'
                ? <a key={n.href} href={`${BASE}/`} className={on ? 'on' : ''}>{n.label}</a>
                : <Link key={n.href} href={n.href} className={on ? 'on' : ''}>{n.label}</Link>;
            })}
          </nav>
          {path !== '/' && <div className="top-search"><Search /></div>}
          <div className="top-right">
            <span className={`live ${live ? 'on' : status.error ? 'off' : ''}`} title={status.data?.rpc || status.error || ''}>
              <span className="dot" />
              {live ? `#${status.data.block_number.toLocaleString()}` : status.error ? 'offline' : '…'}
            </span>
            <select value={net} onChange={e => setNet(e.target.value as any)} aria-label="network">
              <option value="mainnet">Mainnet</option>
              <option value="sepolia">Sepolia</option>
            </select>
          </div>
        </div>
      </header>
      <main className="main">{children}</main>
      <footer className="foot">
        Read-only — this app holds no keys and never signs.
        {' '}Data straight from Starknet RPC{status.data?.rpc ? ` (${status.data.rpc.replace(/^https?:\/\//, '')})` : ''}.
        {' '}<Link href="/developers">API &amp; MCP</Link>
      </footer>
    </div>
  );
}
