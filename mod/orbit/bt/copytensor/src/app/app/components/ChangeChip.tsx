"use client";

/**
 * Percent-change pill. `null` reads as "—" (the index has no history for
 * that window yet) — never as a green 0.00%.
 */
export default function ChangeChip({
  pct,
  size = "md",
  arrow = true,
  bare = false,
  label,
}: {
  pct: number | null | undefined;
  size?: "xs" | "sm" | "md";
  arrow?: boolean;
  bare?: boolean;
  label?: string;
}) {
  const pad =
    size === "xs" ? "px-1.5 py-[1px] text-[10px]"
    : size === "sm" ? "px-2 py-[2px] text-[11px]"
    : "px-2.5 py-[3px] text-[12px]";

  if (pct == null || !isFinite(pct)) {
    return (
      <span className={`font-mono text-pixel-gray ${bare ? "" : pad}`} title="no history indexed yet">
        —
      </span>
    );
  }

  const up = pct > 0;
  const flat = Math.abs(pct) < 0.005;
  const tone = flat
    ? { fg: "text-pixel-gray-light", bg: "bg-pixel-white/5", bd: "border-pixel-white/10" }
    : up
      ? { fg: "text-green-400", bg: "bg-green-400/10", bd: "border-green-400/25" }
      : { fg: "text-red-400", bg: "bg-red-400/10", bd: "border-red-400/25" };

  // A hair below zero rounds to "-0.00%", which reads as a bug. Anything
  // inside the flat band prints unsigned.
  const text = `${flat ? "" : up ? "+" : ""}${(flat ? Math.abs(pct) : pct).toFixed(2)}%`;

  if (bare) {
    return (
      <span className={`font-mono tabular-nums ${tone.fg}`}>
        {arrow && !flat ? (up ? "▲ " : "▼ ") : ""}{text}
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border font-mono font-semibold tabular-nums ${pad} ${tone.fg} ${tone.bg} ${tone.bd}`}
      title={label}
    >
      {arrow && !flat && <span aria-hidden>{up ? "▲" : "▼"}</span>}
      {text}
    </span>
  );
}
