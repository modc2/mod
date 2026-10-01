'use client';
/* The two detail overlays (a subnet, a trader) float over whatever page is
 * showing — the chat agent opens them mid-conversation without navigating
 * away. Their state is mirrored into the query string (?sn=12, ?tr=5G…) so
 * every overlay is a link you can share or reload. */
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

/* the subnet overlay's tabs — ?t= in the url, `tab` in bt_view */
export const SUBNET_TABS = ['overview', 'trades', 'validators', 'news'] as const;
export type SubnetTab = typeof SUBNET_TABS[number];
const asTab = (t?: string | null): SubnetTab =>
  (SUBNET_TABS as readonly string[]).includes(t || '') ? t as SubnetTab : 'overview';

interface OverlayCtx {
  subnet: number | null; trader: string | null; tab: SubnetTab;
  openSubnet: (netuid: number, tab?: string) => void; openTrader: (ss58: string) => void;
  setTab: (tab: SubnetTab) => void;
  close: () => void;
}

const Ctx = createContext<OverlayCtx | null>(null);

function writeQuery(sn: number | null, tr: string | null, tab: SubnetTab = 'overview') {
  const u = new URL(window.location.href);
  u.searchParams.delete('sn'); u.searchParams.delete('tr'); u.searchParams.delete('t');
  if (sn != null) u.searchParams.set('sn', String(sn));
  if (sn != null && tab !== 'overview') u.searchParams.set('t', tab);
  if (tr) u.searchParams.set('tr', tr);
  window.history.replaceState(window.history.state, '', u.pathname + u.search + u.hash);
}

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [subnet, setSubnet] = useState<number | null>(null);
  const [trader, setTrader] = useState<string | null>(null);
  const [tab, setTabState] = useState<SubnetTab>('overview');

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const sn = q.get('sn'), tr = q.get('tr');
    if (sn != null && sn !== '' && !isNaN(+sn)) { setSubnet(+sn); setTabState(asTab(q.get('t'))); }
    else if (tr) setTrader(tr);
  }, []);

  const openSubnet = useCallback((n: number, t?: string) => {
    const tb = asTab(t); setTrader(null); setSubnet(n); setTabState(tb); writeQuery(n, null, tb);
  }, []);
  const setTab = useCallback((t: SubnetTab) => {
    setTabState(t); if (subnet != null) writeQuery(subnet, null, t);
  }, [subnet]);
  const openTrader = useCallback((a: string) => { setSubnet(null); setTrader(a); writeQuery(null, a); }, []);
  const close = useCallback(() => { setSubnet(null); setTrader(null); writeQuery(null, null); }, []);

  /* one overlay open → the page under it holds still */
  const open = subnet != null || trader != null;
  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : '';
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    addEventListener('keydown', esc);
    return () => removeEventListener('keydown', esc);
  }, [open, close]);

  return <Ctx.Provider value={{ subnet, trader, tab, openSubnet, openTrader, setTab, close }}>{children}</Ctx.Provider>;
}

export function useOverlay() {
  const o = useContext(Ctx);
  if (!o) throw new Error('useOverlay outside OverlayProvider');
  return o;
}
