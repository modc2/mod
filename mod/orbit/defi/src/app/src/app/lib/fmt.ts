/// Number words the whole console shares — dollars at a glance, a percent
/// with a dash when nothing is quoted.
export const money = (value: number | null | undefined) => {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}b`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}m`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}k`;
  return `$${value.toFixed(0)}`;
};

export const pct = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || !isFinite(Number(value)) ? "—" : `${Number(value).toFixed(digits)}%`;
