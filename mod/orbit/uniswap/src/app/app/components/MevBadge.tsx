"use client";
import { MevIndicators } from "../lib/types";

interface Props {
  isMev: boolean;
  indicators: MevIndicators;
}

export default function MevBadge({ isMev, indicators }: Props) {
  if (!isMev) return null;

  const reasons: string[] = [];
  if (indicators.sandwich_count > 5) reasons.push(`${indicators.sandwich_count} sandwiches`);
  if (indicators.arb_count > 3) reasons.push(`${indicators.arb_count} arbs`);
  if (indicators.avg_swaps_per_day > 50) reasons.push(`${indicators.avg_swaps_per_day.toFixed(0)}/day`);
  if (indicators.min_swap_interval_sec < 3) reasons.push(`<3s interval`);

  return (
    <span className="badge badge-mev" title={reasons.join(", ")}>
      MEV
    </span>
  );
}
