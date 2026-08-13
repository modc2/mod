// Which TOKEN a fill is in — the key everything that books inventory must use.
//
// A Polymarket market ("Will X happen?") is one `conditionId` but TWO tradable
// CTF outcome tokens (Yes / No). They are separate assets with separate order
// books and opposite payoffs: at resolution one is worth $1 and the other $0.
// The live engine has always known this — `EngineState.positions` is keyed by
// `token_id` (api/src/live_engine.rs) — but the backtest and the FIFO P&L
// engine keyed their books by `conditionId` alone, so a leader's Yes fills and
// No fills in the same market collapsed into ONE position:
//
//   • the mark came from whichever leg traded last, and a mark taken off the
//     other leg is roughly `1 − correct` — a 6¢ No hold marked at the 94¢ the
//     Yes leg last printed;
//   • a SELL of one leg closed shares held in the other, so FIFO matched a
//     No exit against a Yes entry and booked the difference as realized P&L.
//
// 19% of the markets in the cached leader feeds have both legs traded, so this
// was not a corner case. The feed carries `outcome` on each row (data-api's
// activity rows) and each position, which is enough to separate them without a
// token-id lookup: same market + same outcome name = same token.
//
// Rows that predate the outcome field (or endpoints that don't return it) fall
// back to the bare market key — the old behavior, for the rows where we can't
// do better, rather than a second bug.
export function legKey(conditionId: string | undefined, outcome?: string): string {
  const market = conditionId || "";
  if (!outcome) return market;
  return `${market}|${outcome.trim().toLowerCase()}`;
}

/** The `outcome` half of a leg key, for display ("Yes" / "No" / ""). */
export function legOutcome(key: string): string {
  const i = key.indexOf("|");
  return i < 0 ? "" : key.slice(i + 1);
}

/** The `conditionId` half of a leg key. */
export function legMarket(key: string): string {
  const i = key.indexOf("|");
  return i < 0 ? key : key.slice(0, i);
}
