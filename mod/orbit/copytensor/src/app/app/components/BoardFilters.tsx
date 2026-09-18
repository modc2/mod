"use client";

import { useFilters, type SortKey } from "../context/FiltersContext";

/**
 * The two knobs that decide which traders you're looking at, shared by the
 * front page and the full board so the two can't disagree.
 *
 * Both used to be invisible. The board could only be re-sorted by clicking a
 * column header — which doesn't exist on a phone, or on the card grid — and
 * the front page silently threw away every wallet under 25 τ, so "why is this
 * trader missing" had no answer on screen.
 */

const RANKS: { k: SortKey; label: string; hint: string }[] = [
  { k: "market_pct", label: "RETURN", hint: "Price gains only — deposits stripped out. What copying would have earned you." },
  { k: "pnl_pct", label: "TOTAL %", hint: "Everything the account did, deposits included." },
  { k: "total_stake_tao", label: "SIZE", hint: "Biggest books first." },
  { k: "num_subnets", label: "SPREAD", hint: "Most subnets held first." },
];

export function RankBy({ className = "" }: { className?: string }) {
  const { sortKey, setSort } = useFilters();
  return (
    <div className={`rail no-scrollbar min-w-0 ${className}`}>
      <span className="window-rail-k">rank by</span>
      {RANKS.map((r) => (
        <button
          key={r.k}
          onClick={() => setSort(r.k, "desc")}
          aria-pressed={sortKey === r.k}
          title={r.hint}
          className={`pixel-btn window-chip ${
            sortKey === r.k ? "window-chip-on" : "text-pixel-gray-light"
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

// τ floors. A 3 τ wallet can post +400% on a move you could never copy at
// size, and those wallets are what topped a percentage board.
const FLOORS = [0, 25, 100, 1000];

export function StakeFloor({ className = "" }: { className?: string }) {
  const { minStake, setMinStake } = useFilters();
  return (
    <div className={`rail no-scrollbar min-w-0 ${className}`}>
      <span className="window-rail-k">book</span>
      {FLOORS.map((f) => (
        <button
          key={f}
          onClick={() => setMinStake(f)}
          aria-pressed={minStake === f}
          title={f === 0 ? "Every account, dust included" : `Only accounts holding ${f} τ or more`}
          className={`pixel-btn window-chip ${
            minStake === f ? "window-chip-on" : "text-pixel-gray-light"
          }`}
        >
          {f === 0 ? "ANY" : f >= 1000 ? `${f / 1000}K τ+` : `${f} τ+`}
        </button>
      ))}
    </div>
  );
}
