'use client';
/* The connected wallet: one address the whole console follows.
 *
 * Three kinds — a local coldkey on this node (▣), a browser extension
 * account — SubWallet, Talisman, polkadot{.js} — via the standard
 * window.injectedWeb3 bridge (◈, see lib/injected.ts), or any ss58 you just
 * want to watch (◎). Extension accounts can sign: lib/signer.ts. The value is
 * cached in localStorage so the bar paints before the chain answers. */
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { call } from './api';
import * as inj from './injected';

export interface Wallet {
  addr: string; name: string | null; local: boolean; ext: string | null;
  extId?: string | null;          /* injectedWeb3 key — what can sign for it */
  tao: number | null; free?: number | null; staked?: number | null;
}

export const EXTS = inj.KNOWN;
const WKEY = 'bt.wallet', BAR_KEY = 'bt.wbar', RECENT_KEY = 'bt.recent';

export const kindGlyph = (w: Wallet) => (w.local ? '▣' : w.ext ? '◈' : '◎');
export const kindText = (w: Wallet) =>
  w.local ? `local wallet · ${w.name}` : w.ext ? `${w.ext}${w.name ? ' · ' + w.name : ''}` : 'watch-only';

export interface ExtAccount { addr: string; name: string; ext: string; extId: string }

interface WalletCtx {
  wallet: Wallet | null;
  connect: (addr: string, name?: string | null, local?: boolean, ext?: string | null, extId?: string | null) => void;
  disconnect: () => void;
  refresh: () => Promise<void>;
  refreshing: boolean;
  setBalance: (tao: number | null, free?: number | null, staked?: number | null) => void;
  recent: string[];
  barHidden: boolean; setBarHidden: (h: boolean) => void;
  popOpen: boolean; setPopOpen: (o: boolean) => void;
  extPresent: (id: string) => boolean;
  extList: () => (inj.ExtMeta & { installed: boolean })[];
  canSign: boolean;              /* connected through a wallet that is here now */
  note: string;
  extConnect: (id: string) => Promise<{ msg?: string; accounts?: ExtAccount[] }>;
  localWallets: { name: string; coldkey?: string; hotkeys?: string[] }[] | null;
}

const Ctx = createContext<WalletCtx | null>(null);

export function WalletProvider({ children }: { children: ReactNode }) {
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [recent, setRecent] = useState<string[]>([]);
  const [barHidden, setBarHiddenS] = useState(false);
  const [popOpen, setPopOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [localWallets, setLocal] = useState<WalletCtx['localWallets']>(null);
  const [, bump] = useState(0);
  const [note, setNote] = useState('');
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

  const connect = useCallback((addr: string, name: string | null = null, local = false,
                               ext: string | null = null, extId: string | null = null) => {
    const w: Wallet = { addr, name, local, ext, extId: extId || inj.metaFor(ext)?.id || null, tao: null };
    setNote('');
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
    let live = true;
    inj.whenInjected(undefined, 3000).then(() => { if (live) bump(n => n + 1); });
    return () => { live = false; };
  }, [connect]);

  const extConnect = useCallback(async (id: string) => {
    const meta = inj.metaFor(id) || { id, name: id, url: undefined };
    if (!(await inj.whenInjected(id, 1500))) {
      if (meta.url) window.open(meta.url, '_blank', 'noopener');
      return { msg: `${meta.name} is not installed in this browser — install it, then reload.` };
    }
    try {
      const accs = await inj.accounts(id);
      if (!accs.length) return { msg: `No Bittensor accounts shared. Open ${meta.name}, allow this site, and pick a Substrate (not Ethereum) account.` };
      if (accs.length === 1) { connect(accs[0].address, accs[0].name || meta.name, false, meta.name, id); return {}; }
      return { msg: `${meta.name} · pick an account`,
               accounts: accs.map(a => ({ addr: a.address, name: a.name || meta.name, ext: meta.name, extId: id })) };
    } catch (e) {
      return { msg: inj.rejectText(e, meta.name) };
    }
  }, [connect]);

  /* Follow the wallet: if the user stops sharing the connected account, keep
   * watching the address but stop offering to sign with it. */
  const extId = wallet?.extId || (wallet?.ext ? inj.metaFor(wallet.ext)?.id : null) || null;
  useEffect(() => {
    if (!extId) return;
    let un: (() => void) | null = null, live = true;
    inj.whenInjected(extId, 3000).then(ok => {
      if (!ok || !live) return;
      inj.subscribe(extId, accs => {
        const w = walletRef.current;
        if (!w || (w.extId !== extId && w.ext !== inj.metaFor(extId)?.name)) return;
        const hit = accs.find(a => a.address === w.addr);
        if (hit) { if (hit.name && hit.name !== w.name) save({ ...w, name: hit.name, extId }); else if (!w.extId) save({ ...w, extId }); }
        else if (accs.length) {   /* empty = locked/loading, not a revoke */
          save({ ...w, ext: null, extId: null });
          setNote(`${inj.metaFor(extId)?.name || 'The wallet'} no longer shares this account — watching it read-only.`);
        }
      }).then(u => { if (live) un = u; else u(); }).catch(() => { /* not authorised yet — connect asks */ });
    });
    return () => { live = false; if (un) un(); };
  }, [extId]);

  /* every new pick gets a fresh balance */
  const addr = wallet?.addr;
  useEffect(() => { if (addr) refresh(); }, [addr, refresh]);

  const value: WalletCtx = {
    wallet, connect, disconnect: () => save(null), refresh, refreshing, setBalance, recent,
    barHidden, setBarHidden, popOpen, setPopOpen,
    extPresent: inj.present, extList: inj.wallets, extConnect, localWallets,
    canSign: !!(extId && inj.present(extId)), note,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWallet() {
  const w = useContext(Ctx);
  if (!w) throw new Error('useWallet outside WalletProvider');
  return w;
}
