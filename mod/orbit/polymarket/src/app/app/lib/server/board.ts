// THE BOARD — the live leaderboard, read once, shared by every agent that has
// to name traders.
//
// Both strat-writing agents (AUTO STRAT's factory and the lab's VIBE drafter)
// run with NO tools: a model with no tools cannot look a wallet up, so the
// only honest way to let it name one is to hand it the board and treat that
// list as the whitelist. `onBoard()` is the enforcement half — whatever the
// model answers is intersected with what was actually shown to it, so a
// hallucinated address dies before it reaches a bench or a strat.

import { API_BASE } from "../polymarket";
import { mintOwnerToken } from "./ownerToken";

export interface BoardRow {
  address: string;
  pnl?: number;
  volume?: number;
  winRate?: number;
  sharpe?: number;
  exitEntry?: number;
  positions?: number;
  decidedPositions?: number;
  trades?: number;
}

export interface BoardOpts {
  /** How the board is ranked before it is truncated. */
  sort?: string;
  /** How many rows the caller wants to show the model. */
  rows?: number;
  /** Activity floor — a trader who stopped trading is not copyable. */
  maxLastTradeHrs?: number;
}

/** The warm leaderboard, the same paged read `pm_top_traders` does — it
    answers from cache or reports cold. A cold board fails the caller rather
    than blocking on a minutes-long aggregation. */
export async function boardSnapshot(opts: BoardOpts = {}): Promise<BoardRow[]> {
  const token = mintOwnerToken();
  if (!token) throw new Error("no owner/secret on this deployment — cannot read the leaderboard");
  const rows = Math.min(Math.max(opts.rows ?? 40, 1), 100);
  const qs = new URLSearchParams({
    days: "7",
    pool: "2000",
    paged: "1",
    pageSize: String(rows),
    page: "0",
    sort: opts.sort ?? "sharpe",
    order: "desc",
    maxLastTradeHrs: String(opts.maxLastTradeHrs ?? 6),
  });
  const res = await fetch(`${API_BASE}/active-traders?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`leaderboard read failed: HTTP ${res.status}`);
  const body = (await res.json()) as { cold?: boolean; traders?: BoardRow[] };
  if (body.cold || !Array.isArray(body.traders) || body.traders.length === 0) {
    throw new Error("leaderboard cache is cold — the sync loop hasn't warmed it yet; retry in a few minutes");
  }
  return body.traders;
}

const num = (v: number | undefined, d = 0) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "?");

/** One board row as the prompt sees it. `winRate: -1` is this pipeline's
    "not enough decided positions to say" sentinel, not a zero — printing it
    as 0% would invent a losing trader out of an unknown one. */
export function boardLine(t: BoardRow): string {
  return `${t.address}  pnl7d=$${num(t.pnl)}  winRate=${t.winRate === -1 ? "?" : num(t.winRate ?? 0)}%`
    + `(${t.decidedPositions ?? 0} decided)  sharpe=${num(t.sharpe, 2)}  vol=$${num(t.volume)}`;
}

/** The whitelist as a predicate: lowercase addresses actually shown. */
export function onBoard(board: BoardRow[]): (address: string) => boolean {
  const valid = new Set(board.map((t) => t.address.toLowerCase()));
  return (address: string) => valid.has(address.toLowerCase());
}
