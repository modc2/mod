'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BASE } from '@/lib/api';
import { useApi } from '@/lib/hooks';
import { useNet } from '@/lib/net';
import Search from './Search';

const NAV = [
  { href: '/', label: 'Explore' },
  { href: '/apps', label: 'Apps' },
  { href: '/studio', label: 'Build' },
  { href: '/ask', label: 'Ask' },
  { href: '/developers', label: 'API' },
];

/** Pages that are apps: the nav lights Apps and an app bar sits on top. */
const APP_PAGES = ['/apps', '/mods', '/stablecoins', '/privacy', '/contracts'];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || '/';
  const { net, setNet } = useNet();
  const status = useApi<any>('status', 30000);
  const apps = useApi<any>('apps', 60000);
  const inApp = APP_PAGES.some(p => path.startsWith(p));
  const pageApps = (apps.data?.apps || []).filter((a: any) => a.kind === 'page');
  const live = !!status.data;

  return (
    <div className="shell">
      <header className="top">
        <div className="top-inner">
          {/* plain <a>: the root route's RSC payload lives outside /starknet/* */}
          <a href={`${BASE}/`} className="brand">
            <span className="mark">S</span>
            <span className="word"><b>stark</b><i>//</i>net</span>
          </a>
          <nav className="nav">
            {NAV.map(n => {
              const on = n.href === '/' ? ['/', '/block', '/tx', '/address'].includes(path)
                : n.href === '/apps' ? inApp : path.startsWith(n.href);
              const badge = n.href === '/apps' && apps.data
                ? <span className="nav-n">{apps.data.count}</span> : null;
              return n.href === '/'
                ? <a key={n.href} href={`${BASE}/`} className={on ? 'on' : ''}>{n.label}</a>
                : <Link key={n.href} href={n.href} className={on ? 'on' : ''}>{n.label}{badge}</Link>;
            })}
          </nav>
          <div className="top-right">
            <span className={`live ${live ? 'on' : status.error ? 'off' : ''}`} title={status.data?.rpc || status.error || ''}>
              <span className="dot" />
              {live ? `#${status.data.block_number.toLocaleString()}` : status.error ? 'offline' : '…'}
            </span>
            <select className="net" value={net} onChange={e => setNet(e.target.value as any)} aria-label="network">
              <option value="mainnet">Mainnet</option>
              <option value="sepolia">Sepolia</option>
            </select>
          </div>
          {path !== '/' && !path.startsWith('/apps') && !path.startsWith('/mods') && !path.startsWith('/studio') && <div className="top-search"><Search /></div>}
        </div>
      </header>
      <main className={`main ${path.startsWith('/studio') ? 'wide' : ''}`}>
        {inApp && !path.startsWith('/apps') && !path.startsWith('/mods') && (
          <nav className="appbar">
            <Link href="/apps" className="appbar-all">‹ All apps</Link>
            {pageApps.map((a: any) => (
              <Link key={a.name} href={a.page} className={path.startsWith(a.page) ? 'on' : ''} style={{ ['--tone' as any]: a.tone }}>
                <span className="appbar-i">{a.icon}</span>{a.title}
              </Link>
            ))}
          </nav>
        )}
        {children}
      </main>
      <footer className="foot">
        The explorer is read-only — it holds no keys and never signs.
        {' '}Data straight from Starknet RPC{status.data?.rpc ? ` (${status.data.rpc.replace(/^https?:\/\//, '')})` : ''}.
        {' '}<Link href="/apps">Apps on Starknet</Link> · <Link href="/developers">API &amp; MCP</Link>
      </footer>
    </div>
  );
}
