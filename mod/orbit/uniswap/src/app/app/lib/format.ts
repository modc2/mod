/**
 * Format USD value with appropriate precision.
 */
export function fmtUsd(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";

  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  if (abs >= 1) return `${sign}$${abs.toFixed(2)}`;
  return `${sign}$${abs.toFixed(4)}`;
}

/**
 * Format percentage.
 */
export function fmtPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

/**
 * Shorten address: 0x1234...abcd
 */
export function shortAddr(address: string): string {
  if (!address || address.length < 10) return address;
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}

/**
 * Format large numbers compactly.
 */
export function fmtNum(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toFixed(0);
}

/**
 * PnL color class.
 */
export function pnlColor(value: number): string {
  if (value > 0) return "text-uni-green";
  if (value < 0) return "text-uni-red";
  return "text-uni-muted";
}

/**
 * Score to color gradient.
 */
export function scoreColor(score: number): string {
  if (score >= 60) return "text-uni-green";
  if (score >= 30) return "text-yellow-400";
  return "text-uni-muted";
}
