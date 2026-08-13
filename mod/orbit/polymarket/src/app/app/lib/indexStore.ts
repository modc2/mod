import { SavedIndex, IndexTrader } from "./types";
import { DEFAULT_STOP_LOSS } from "./strats/strat";

// Equal-weight a list of addresses into IndexTrader rows (remainder cents go
// to the earliest entries) — shared by every "seed a strat with N traders"
// call site so weights always sum to exactly 100.
export function equalWeightTraders(addrs: string[]): IndexTrader[] {
  const n = addrs.length;
  if (n === 0) return [];
  const base = Math.floor(100 / n);
  const remainder = 100 - base * n;
  return addrs.map((address, i) => ({ address, weight: (base + (i < remainder ? 1 : 0)) / 100 }));
}

const STORAGE_KEY = "poly8bit_indexes";
const ACTIVE_KEY = "poly8bit_active_index";

// Quota recovery: when a strat write throws QuotaExceededError, the disposable
// trade/market caches are evicted so the (tiny, critical) strat data wins.
// Without this, users hit silent write failures once the cache grows past
// ~5MB and adding/removing traders appears completely broken.
const DISPOSABLE_PREFIXES = ["poly_cid_", "poly_hr_", "poly_mkt_"];

function freeDisposableCache(): number {
  if (typeof window === "undefined") return 0;
  const toRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && DISPOSABLE_PREFIXES.some((p) => k.startsWith(p))) toRemove.push(k);
  }
  for (const k of toRemove) localStorage.removeItem(k);
  return toRemove.length;
}

function isQuotaError(e: unknown): boolean {
  if (!(e instanceof Error)) return false;
  return e.name === "QuotaExceededError" || /quota/i.test(e.message);
}

function setItemSafe(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    if (!isQuotaError(e)) throw e;
    const freed = freeDisposableCache();
    // eslint-disable-next-line no-console
    console.warn(`[indexStore] localStorage full — evicted ${freed} cache entries and retrying strat write`);
    localStorage.setItem(key, value); // retry; if this throws, let it surface
  }
}

// One-time default migration: 0.5 was the blanket stop-loss default every
// strat save wrote out verbatim, so an explicit 0.5 on a stored strat is
// (with overwhelming likelihood) the old default, not a choice — bump it to
// the current default (0.75). Any OTHER explicit value (including 0 = off)
// was user-set and is left alone. Read-side so imported/shared strats
// migrate too; the next save persists the bumped value.
const LEGACY_DEFAULT_STOP_LOSS = 0.5;
// Candle-mode momentum strats (BTC 5-MIN DELTA and forks of it) defend 80% of
// entry, not the house 75% — see the template in defaultStrats.ts. Strats
// forked BEFORE that template carried a stop-loss have no `stopLoss` field at
// all, so they silently inherit the looser default; give them the tighter one
// too. Only ever fills an ABSENT value — an explicit number (including 0 =
// off) is a choice and is left alone.
const CANDLE_STOP_LOSS = 0.8;
function migrateIndex(idx: SavedIndex): SavedIndex {
  if (idx && idx.stopLoss === LEGACY_DEFAULT_STOP_LOSS) {
    return { ...idx, stopLoss: DEFAULT_STOP_LOSS };
  }
  if (idx && idx.stopLoss === undefined && idx.momentum?.candles) {
    return { ...idx, stopLoss: CANDLE_STOP_LOSS };
  }
  return idx;
}

export function loadIndexes(): SavedIndex[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(migrateIndex) : [];
  } catch {
    return [];
  }
}

export function saveIndex(index: SavedIndex): void {
  const all = loadIndexes();
  all.push(index);
  setItemSafe(STORAGE_KEY, JSON.stringify(all));
}

export function deleteIndex(id: string): void {
  const all = loadIndexes().filter((idx) => idx.id !== id);
  setItemSafe(STORAGE_KEY, JSON.stringify(all));
}

export function updateIndex(id: string, patch: Partial<SavedIndex>): void {
  const all = loadIndexes();
  const idx = all.findIndex((i) => i.id === id);
  if (idx === -1) return;
  all[idx] = { ...all[idx], ...patch };
  setItemSafe(STORAGE_KEY, JSON.stringify(all));
}

/** "CRYPTO MAJORS" → "CRYPTO MAJORS 2" when the name is taken. Shared by the
    fork paths so two strats can never collide on a name. */
export function uniqueIndexName(base: string): string {
  const names = new Set(loadIndexes().map((i) => i.name));
  if (!names.has(base)) return base;
  for (let n = 2; n < 1000; n++) {
    const cand = `${base} ${n}`;
    if (!names.has(cand)) return cand;
  }
  return `${base} ${Date.now().toString(36)}`;
}

/** Fork a saved strat into a fresh, independent copy the user owns.
 *
 *  Everything that describes the STRATEGY comes along — watchlist, weights,
 *  every param, filters. Everything that describes that strat's RUN does not:
 *  a fork starts stopped, un-funded (`liveEnabled: false`), with no cached
 *  backtest numbers, so it can't be mistaken for having a track record it
 *  never earned. `forkedFrom` keeps the lineage.
 *
 *  Forking is the honest way to try a variation: the original keeps running
 *  untouched with its own ledger (the engine is keyed per strat id), and the
 *  fork gets its own session the moment it's funded.
 *
 *  Returns the new strat, or null when `id` doesn't exist. */
export function forkIndex(id: string, name?: string): SavedIndex | null {
  const source = loadIndexes().find((i) => i.id === id);
  if (!source) return null;
  const now = Date.now();
  const {
    // Dropped on purpose — see above. `visibility`/`owner` too: publishing
    // is a per-strat act, so a fork of a public strat starts private and
    // unattributed until its new owner publishes it themselves.
    lastPnl: _p, lastPnlAfterCosts: _pc, lastRoi1k: _r,
    lastTradeCount: _tc, lastBacktestAt: _ba,
    visibility: _v, owner: _o,
    ...strategy
  } = source;
  const fork: SavedIndex = {
    ...strategy,
    // Deep-copy the containers so editing the fork's watchlist or filters
    // can't reach back into the original through a shared reference.
    traders: source.traders.map((t) => ({ ...t })),
    ...(source.tradeFilters && { tradeFilters: { ...source.tradeFilters } }),
    ...(source.filter && { filter: { ...source.filter } }),
    ...(source.momentum && { momentum: { ...source.momentum } }),
    id: now.toString(36),
    name: uniqueIndexName(name?.trim() || `${source.name} COPY`),
    forkedFrom: source.id,
    liveEnabled: false,
    createdAt: now,
    updatedAt: now,
  };
  saveIndex(fork);
  return fork;
}

export function getActiveIndexId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}

export function setActiveIndexId(id: string | null): void {
  try {
    if (id) localStorage.setItem(ACTIVE_KEY, id);
    else localStorage.removeItem(ACTIVE_KEY);
  } catch {}
}
