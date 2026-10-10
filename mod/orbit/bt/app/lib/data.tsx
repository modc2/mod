'use client';
/* Shared market state: node info, the screener, headline stats and the
 * tracked-trader list. Lives in the root layout so every page — and the chat
 * agent driving the screen — reads the same rows without refetching. */
import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from 'react';
import { call, getJSON, NetworkInfo, Stats, SubnetRow, TraderRow, Usd } from './api';
import { Ccy } from './format';
import { usePoll, useStored } from './hooks';

interface Info { network?: string; block?: number; version?: string; tools?: number;
                 traders?: { tracked?: number; snapshots?: number; flows?: number } }

interface Screener { rows: SubnetRow[]; updated_at?: number; age_sec?: number; block?: number;
                     warming?: boolean; note?: string }

export interface Sort { key: keyof SubnetRow | string; dir: 1 | -1 }

interface Data {
  info: Info | null; online: boolean | null;
  screener: Screener | null; screenerError: string | null; reloadScreener: () => Promise<void>;
  stats: Stats | null;
  traders: TraderRow[] | null; tradersError: string | null; reloadTraders: () => Promise<void>;
  names: Record<number, string>;
  bySubnet: Record<number, SubnetRow>;
  syncAt: number | null; syncBlock: number | null;
  search: string; setSearch: (s: string) => void;
  sort: Sort; setSort: (s: Sort) => void;
  usd: Usd | null; network: NetworkInfo | null;
  ccy: Ccy; setCcy: (c: Ccy) => void;
  rate: number | null;              /* USD per τ, from the free tickers */
}

const Ctx = createContext<Data | null>(null);

export function DataProvider({ children }: { children: ReactNode }) {
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<Sort>({ key: 'market_cap', dir: -1 });

  const info = usePoll<Info>(() => getJSON('').then(j => j as Info), 60_000, [], 'info');
  const screener = usePoll<Screener>(async () =>
    (await call<Screener>('bt_screener', { sort_by: 'market_cap' })).result, 60_000, [], 'screener');
  const stats = usePoll<Stats>(async () => (await call<Stats>('bt_stats')).result, 60_000, [], 'stats');
  const traders = usePoll<TraderRow[]>(async () =>
    (await call<{ rows: TraderRow[] }>('bt_traders', { sort_by: 'total_tao' })).result.rows || [], 60_000, [], 'traders');
  const usd = usePoll<Usd>(async () => (await call<Usd>('bt_usd')).result, 120_000, [], 'usd');
  const network = usePoll<NetworkInfo>(async () =>
    (await call<NetworkInfo>('bt_network')).result, 300_000, [], 'network');
  const [ccy, setCcy] = useStored<Ccy>('bt.ccy', 'tao');

  const rows = screener.data?.rows;
  const names = useMemo(() => Object.fromEntries((rows || []).map(r => [r.netuid, r.name || ''])), [rows]);
  const bySubnet = useMemo(() => Object.fromEntries((rows || []).map(r => [r.netuid, r])), [rows]);

  /* the freshest snapshot any source reported */
  const syncAt = Math.max(stats.data?.updated_at || 0, screener.data?.updated_at || 0) || null;
  const syncBlock = stats.data?.block || screener.data?.block || null;

  const reloadScreener = useCallback(() => screener.refresh(), [screener]);
  const reloadTraders = useCallback(() => traders.refresh(), [traders]);

  const value: Data = {
    info: info.data, online: info.error ? false : info.data ? true : null,
    screener: screener.data, screenerError: screener.error, reloadScreener,
    stats: stats.data,
    traders: traders.data, tradersError: traders.error, reloadTraders,
    names, bySubnet, syncAt, syncBlock, search, setSearch, sort, setSort,
    usd: usd.data, network: network.data, ccy, setCcy,
    rate: usd.data?.usd ?? network.data?.usd?.usd ?? null,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useData() {
  const d = useContext(Ctx);
  if (!d) throw new Error('useData outside DataProvider');
  return d;
}
