import type { ReactNode } from "react";

export type Tone = "up" | "down" | "accent";

/**
 * One readout in a scoreboard cluster: label, big number, footnote.
 *
 * MarketStrip and Leaderboard each carried their own copy of this and
 * they had already drifted (one took `accent`, the other `tone`), which
 * is how the two rows of tiles on /subnets ended up on different
 * baselines. Styling is `.stat-tile` in globals.css.
 */
export default function StatTile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: Tone;
}) {
  const color =
    tone === "up" ? "text-green-400"
    : tone === "down" ? "text-red-400"
    : tone === "accent" ? "text-cyan-400"
    : "text-pixel-white";
  return (
    <div className={`stat-tile ${tone ? `stat-tile-${tone}` : ""}`}>
      <p className="stat-tile-label">{label}</p>
      <p className={`stat-tile-value ${color}`}>{value}</p>
      {sub && <p className="stat-tile-sub">{sub}</p>}
    </div>
  );
}
