'use client';
/* The connected wallet: one address the whole console follows.
 *
 * Three kinds — a local coldkey on this node (▣), a browser extension
 * account via the standard window.injectedWeb3 bridge (◈), or any ss58 you
 * just want to watch (◎). Nothing is signed from here; the value is cached in
 * localStorage so the bar paints before the chain answers. */
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { call } from './api';

export interface Wallet {
  addr: string; name: string | null; local: boolean; ext: string | null;
  tao: number | null; free?: number | null; staked?: number | null;
}

export const EXTS = [
  { id: 'subwallet-js', name: 'SubWallet', url: 'https://www.subwallet.app/download.html' },
  { id: 'talisman', name: 'Talisman', url: 'https://talisman.xyz/download' },
] as const;
const DAPP = 'bt · Bittensor explorer';
const WKEY = 'bt.wallet', BAR_KEY = 'bt.wbar', RECENT_KEY = 'bt.recent';

export const kindGlyph = (w: Wallet) => (w.local ? '▣' : w.ext ? '◈' : '◎');
export const kindText = (w: Wallet) =>
  w.local ? `local wallet · ${w.name}` : w.ext ? `${w.ext}${w.name ? ' · ' + w.name : ''}` : 'watch-only';

interface ExtAccount { addr: string; name: string; ext: string }

interface WalletCtx {
  wallet: Wallet | null;
  connect: (addr: string, name?: string | null, local?: boolean, ext?: string | null) => void;
  disconnect: () => void;
  refresh: () => Promise<void>;
  refreshing: boolean;
  setBalance: (tao: number | null, free?: number | null, staked?: number | null) => void;
  recent: string[];
  barHidden: boolean; setBarHidden: (h: boolean) => void;
  popOpen: boolean; setPopOpen: (o: boolean) => void;
  extPresent: (id: string) => boolean;
  extConnect: (id: string) => Promise<{ msg?: string; accounts?: ExtAccount[] }>;
  localWallets: { name: string; coldkey?: string; hotkeys?: string[] }[] | null;
}

const Ctx = createContext<WalletCtx | null>(null);

const injected = (): Record<string, any> =>
  (typeof window !== 'undefined' && (window as any).injectedWeb3) || {};

export function WalletProvider({ children }: { children: ReactNode }) {
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [recent, setRecent] = useState<string[]>([]);
  const [barHidden, setBarHiddenS] = useState(false);
  const [popOpen, setPopOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [localWallets, setLocal] = useState<WalletCtx['localWallets']>(null);
  const [, bump] = useState(0);
  const walletRef = useRef<Wallet | null>(null);
  walletRef.current = wallet;

  const save = (w: Wallet | null) => {
    setWallet(w);
    try { w ? localStorage.setItem(WKEY, JSON.stringify(w)) : localStorage.removeItem(WKEY); } catch { /* */ }
  };

  const refresh = useCallback(async () => {
    const w = walletRef.current;
    if (!w) return;
    setRefreshing(true);
    try {
      const a = (await call('bt_account', { address: w.addr })).result;
      if (walletRef.current?.addr === w.addr)
        save({ ...walletRef.current, tao: a.total_value_tao ?? a.free_tao ?? null,
               free: a.free_tao ?? null, staked: a.staked_value_tao ?? null });
    } catch { /* keep the cached number rather than blanking the bar */ }
    setRefreshing(false);
  }, []);

  const connect = useCallback((addr: string, name: string | null = null, local = false, ext: string | null = null) => {
    const w: Wallet = { addr, name, local, ext, tao: null };
    if (!local && !ext) {
      setRecent(prev => {
        const r = [addr, ...prev.filter(a => a !== addr)].slice(0, 5);
        try { localStorage.setItem(RECENT_KEY, JSON.stringify(r)); } catch { /* */ }
        return r;
      });
    }
    walletRef.current = w;
    save(w);
    setPopOpen(false);
  }, []);

  const setBalance = useCallback((tao: number | null, free?: number | null, staked?: number | null) => {
    const w = walletRef.current;
    if (w) save({ ...w, tao, free: free ?? null, staked: staked ?? null });
  }, []);

  const setBarHidden = (h: boolean) => {
    setBarHiddenS(h); if (h) setPopOpen(false);
    try { h ? localStorage.setItem(BAR_KEY, 'off') : localStorage.removeItem(BAR_KEY); } catch { /* */ }
  };

  /* restore — then adopt the node's first local coldkey if nothing was picked */
  useEffect(() => {
    let saved: Wallet | null = null;
    try {
      saved = JSON.parse(localStorage.getItem(WKEY) || 'null');
      setRecent(JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'));
      setBarHiddenS(localStorage.getItem(BAR_KEY) === 'off');
    } catch { /* */ }
    if (saved) { walletRef.current = saved; setWallet(saved); }
    call('bt_wallets').then(j => {
      const ws = (j.result || []) as { name: string; coldkey?: string }[];
      setLocal(ws);
      if (!saved) { const w = ws.find(x => x.coldkey); if (w) connect(w.coldkey!, w.name, true); }
    }).catch(() => setLocal([]));
    /* extensions inject shortly after load — re-render once they have */
    const t = setTimeout(() => bump(n => n + 1), 1200);
    return () => clearTimeout(t);
  }, [connect]);

  const extConnect = useCallback(async (id: string) => {
    const meta = EXTS.find(e => e.id === id)!;
    const ext = injected()[id];
    if (!ext) { window.open(meta.url, '_blank', 'noopener'); return {}; }
    try {
      const inj = await ext.enable(DAPP);
      let accs: { address: string; name?: string }[] = [];
      if (inj.accounts?.get) accs = await inj.accounts.get();
      else accs = await new Promise((res, rej) => {
        const un = inj.accounts.subscribe((a: any) => { res(a); try { un && un(); } catch { /* */ } });
        setTimeout(() => rej(new Error('no answer from the extension')), 8000);
      });
      accs = (accs || []).filter(a => a && a.address && !a.address.startsWith('0x'));
      if (!accs.length) return { msg: `No substrate accounts shared. Open ${meta.name} and allow this site to see an account.` };
      if (accs.length === 1) { connect(accs[0].address, accs[0].name || meta.name, false, meta.name); return {}; }
      return { msg: `${meta.name} · pick an account`,
               accounts: accs.map(a => ({ addr: a.address, name: a.name || meta.name, ext: meta.name })) };
    } catch (e) {
      return { msg: `${meta.name}: ${(e as Error).message || 'connection rejected'}` };
    }
  }, [connect]);

  /* every new pick gets a fresh balance */
  const addr = wallet?.addr;
  useEffect(() => { if (addr) refresh(); }, [addr, refresh]);

  const value: WalletCtx = {
    wallet, connect, disconnect: () => save(null), refresh, refreshing, setBalance, recent,
    barHidden, setBarHidden, popOpen, setPopOpen,
    extPresent: id => !!injected()[id], extConnect, localWallets,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWallet() {
  const w = useContext(Ctx);
  if (!w) throw new Error('useWallet outside WalletProvider');
  return w;
}
