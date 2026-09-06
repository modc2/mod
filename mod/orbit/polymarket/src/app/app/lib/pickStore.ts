"use client";

// THE PICK STORE — the finder's checked traders, owned here.
//
// The selection used to be FindTraders component state, which chained it to
// the desk page: the tray that replayed it rendered inline (a full-width
// block between the controls and the table), and the sidebar could only show
// a dead mirror. The store owns the picks now — the finder's checkboxes
// write here, and the SELECTION tray that replays and commits them is a
// block of the user sidebar (components/SelectionTray.tsx), visible on every
// page and scroll position.
//
// Module state, not localStorage: a selection is one tab's working
// shortlist, not something to leak across tabs or survive a reload.

import { useSyncExternalStore } from "react";

/** One checked trader: the query that found them is carried along, so a
    mixed selection (two from bitcoin, one from nba) keeps each name's gate. */
export interface TraderPick {
  address: string;
  usd: number;
  marketQuery: string;
}

export interface PickSnapshot {
  picks: TraderPick[];
  /** The window each pick's replay runs over — the finder's RANK OVER. */
  days: number;
}

const EMPTY: PickSnapshot = { picks: [], days: 1 };
let snapshot: PickSnapshot = EMPTY;
const listeners = new Set<() => void>();

function emit(next: PickSnapshot): void {
  snapshot = next.picks.length === 0 ? { ...EMPTY, days: next.days } : next;
  listeners.forEach((fn) => fn());
}

/** Check/uncheck one trader. A fresh pick keeps the $ and query it came with. */
export function togglePick(pick: TraderPick): void {
  const address = pick.address.toLowerCase();
  const has = snapshot.picks.some((p) => p.address === address);
  emit({
    ...snapshot,
    picks: has
      ? snapshot.picks.filter((p) => p.address !== address)
      : [...snapshot.picks, { ...pick, address }],
  });
}

/** Bulk check (the table's header checkbox). Already-picked names keep
    their pick — their $ may have been hand-edited on the tray. */
export function addPicks(picks: TraderPick[]): void {
  const have = new Set(snapshot.picks.map((p) => p.address));
  const add = picks
    .map((p) => ({ ...p, address: p.address.toLowerCase() }))
    .filter((p) => !have.has(p.address));
  if (add.length > 0) emit({ ...snapshot, picks: [...snapshot.picks, ...add] });
}

/** Bulk uncheck. */
export function removePicks(addresses: string[]): void {
  const drop = new Set(addresses.map((a) => a.toLowerCase()));
  emit({ ...snapshot, picks: snapshot.picks.filter((p) => !drop.has(p.address)) });
}

export function removePick(address: string): void {
  removePicks([address]);
}

/** Resize one pick — the tray's $ input. The replay re-runs off this. */
export function setPickUsd(address: string, usd: number): void {
  const a = address.toLowerCase();
  emit({
    ...snapshot,
    picks: snapshot.picks.map((p) => (p.address === a ? { ...p, usd } : p)),
  });
}

export function clearPicks(): void {
  emit({ ...EMPTY, days: snapshot.days });
}

/** The finder keeps the store told which window it ranked on, so the tray's
    replay answers the same question the leaderboard did. */
export function setPickDays(days: number): void {
  if (days !== snapshot.days) emit({ ...snapshot, days });
}

export function usePicks(): PickSnapshot {
  return useSyncExternalStore(
    (onChange) => {
      listeners.add(onChange);
      return () => listeners.delete(onChange);
    },
    () => snapshot,
    () => EMPTY,
  );
}
