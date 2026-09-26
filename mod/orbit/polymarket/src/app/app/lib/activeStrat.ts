// THE ACTIVE STRAT — one answer to "which strategy am I on", for every screen.
//
// Three screens used to answer this independently (the workspace, the traders
// board, the old strat sidebar) and each of them, on a fresh browser, invented
// its own blank strat: "Default", "Strategy 1", an untitled index with no
// params. Three different empty shells, none of which was the console's actual
// strategy — so a first-time user's first backtest ran engine defaults under a
// name nobody chose.
//
// There is one rule now: if there is no strat, you get a TRADER INDEX (the
// console's default recipe — lib/traderIndex.ts, lib/defaultStrats.ts), seeded
// with the current top traders. Whatever screen you happened to open first.
//
// Split out of indexStore.ts rather than added to it because indexStore is the
// storage layer and `defaultStrats` imports it — putting the recipe-aware
// helpers here keeps that dependency one-way.

import {
  forkDefaultStrat,
  traderIndexTemplate,
  type StratTemplate,
} from "./defaultStrats";
import {
  getActiveIndexId,
  loadIndexes,
  setActiveIndexId,
} from "./indexStore";
import type { SavedIndex } from "./types";

/** Broadcast after any change to the strat store, so every mounted screen
    re-reads. The name predates this module; it is the console's convention. */
export const STRAT_UPDATED_EVENT = "strat-updated";

export function announceStratChange(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(STRAT_UPDATED_EVENT));
  }
}

/** The saved strats, most recently updated first — the order a board should
    show them in, since the one you just touched is the one you mean. */
export function listStrats(): SavedIndex[] {
  return [...loadIndexes()].sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
}

/** The active strat, or null. Never creates one — use `ensureActiveStrat` for
    that, so a read-only screen can't silently mint a strategy. */
export function getActiveStrat(): SavedIndex | null {
  const all = loadIndexes();
  if (all.length === 0) return null;
  const id = getActiveIndexId();
  return (id ? all.find((i) => i.id === id) : null) ?? all[0];
}

/** The active strat, creating the console's DEFAULT one if the store is empty.
 *
 *  The fork seeds its watchlist asynchronously (the leaderboard call), so the
 *  strat comes back with zero traders and fills in a moment later — callers
 *  that render a roster should listen for `strat-updated`, which
 *  `forkDefaultStrat`'s callback fires through `announceStratChange`. */
export function ensureActiveStrat(): SavedIndex {
  const existing = getActiveStrat();
  if (existing) {
    // Repair a dangling pointer: the active id can outlive the strat it names
    // (deleted in another tab), and every caller downstream assumes the two
    // agree.
    if (getActiveIndexId() !== existing.id) setActiveIndexId(existing.id);
    return existing;
  }
  return forkTemplate(traderIndexTemplate());
}

/** Fork a shelf recipe into a real strat, make it active, and tell the app.
    The single path for "give me a new strat" — the board's + NEW INDEX button
    and the template gallery both come through here. */
export function forkTemplate(t: StratTemplate): SavedIndex {
  const strat = forkDefaultStrat(t, () => announceStratChange());
  announceStratChange();
  return strat;
}

/** Make `id` the active strat and broadcast. Returns false when it's gone. */
export function activateStrat(id: string): boolean {
  if (!loadIndexes().some((i) => i.id === id)) return false;
  setActiveIndexId(id);
  announceStratChange();
  return true;
}
