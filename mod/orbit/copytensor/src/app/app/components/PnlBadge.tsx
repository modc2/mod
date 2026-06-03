"use client";

import { fmtPct } from "../lib/api";
import { useCurrency, fmtPnlValue } from "../context/CurrencyContext";

export default function PnlBadge({
  tao,
  pct,
  size = "md",
}: {
  tao: number;
  pct: number;
  size?: "sm" | "md" | "lg";
}) {
  const { currency, usdPerTao } = useCurrency();
  const positive = tao >= 0;
  const color = positive ? "text-green-400" : "text-red-400";
  const fontSize =
    size === "sm" ? "text-[11px]" :
    size === "lg" ? "text-lg font-bold" :
    "text-[13px]";

  return (
    <span className={`${color} ${fontSize} font-mono tabular-nums whitespace-nowrap`}>
      {fmtPnlValue(tao, currency, usdPerTao)}
      <span className="opacity-60 ml-1">({fmtPct(pct)})</span>
    </span>
  );
}
