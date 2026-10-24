"use client";

// THE SCORE BUS — the board's computed score for every trader it has seen,
// shared with the side panel.
//
// The SCORE on a trader card is computed in CopyTrading from the live formula
// (expression, JS ƒ or PY ƒ) over the board's windowed stats. The sidebar's
// bench roster and selection tray want to print the same number next to the
// same address — but they have neither the formula nor the stats, and
// recomputing either in a 340px column would mean a second copy of the score
// pipeline that can drift from the one the user is actually ranking on.
//
// So the board PUBLISHES: after each rank pass it drops (address → score)
// here, tagged with the column label ("WIN RATE", "SCORE", …) and the window.
// Sidebar rows read it and print "—" for an address the board hasn't scored
// (never streamed, or hidden by a FUNCTION filter returning null).
//
// Module state, same rules as pickStore: one tab's view of one board, not
// something to persist.

import { useSyncExternalStore } from "react";

export interface ScoreBoard {
  /** Column label the board is showing for the score ("WIN RATE", "SCORE"). */
  label: string;
  /** Window the scores were ranked over, in days. */
  days: number;
  /** addressLower → score. null = the user's score FUNCTION hid this trader. */
  scores: Map<string, number | null>;
}

const EMPTY: ScoreBoard = { label: "SCORE", days: 0, scores: new Map() };

let snapshot: ScoreBoard = EMPTY;
const listeners = new Set<() => void>();

/** The board calls this after ranking — one new snapshot, everyone re-reads. */
export function publishScores(label: string, days: number, entries: [string, number | null][]): void {
  snapshot = { label, days, scores: new Map(entries) };
  listeners.forEach((fn) => fn());
}

export function useScoreBoard(): ScoreBoard {
  return useSyncExternalStore(
    (onChange) => {
      listeners.add(onChange);
      return () => listeners.delete(onChange);
    },
    () => snapshot,
    () => EMPTY,
  );
}

/** One trader's board score. undefined = the board hasn't scored them. */
export function boardScoreFor(board: ScoreBoard, address: string): number | null | undefined {
  return board.scores.get(address.toLowerCase());
}
