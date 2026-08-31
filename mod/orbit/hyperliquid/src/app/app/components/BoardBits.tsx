"use client";

// Small presentational pieces for the boards — KPI tiles with a data visual,
// identicons, meters and labelled filter controls. Nothing here fetches;
// every value is handed in by the page so a tile can never disagree with the
// table it sits above.

import type { ReactNode } from "react";

/** Deterministic two-hue gradient disc for an address — cheap identity so a
 *  row is recognisable at a glance without reading hex. */
export function Identicon({ address, size = 18 }: { address: string; size?: number }) {
  let h = 2166136261;
  for (let i = 0; i < address.length; i++) h = Math.imul(h ^ address.charCodeAt(i), 16777619) >>> 0;
  const h1 = h % 360;
  const h2 = (h1 + 40 + ((h >>> 8) % 100)) % 360;
  return (
    <span
      aria-hidden
      className="inline-block shrink-0 rounded-full ring-1 ring-white/10 align-middle"
      style={{
        width: size, height: size,
        background: `linear-gradient(135deg, hsl(${h1} 70% 58%), hsl(${h2} 75% 42%))`,
      }}
    />
  );
}

/** One thin bar per value, tallest = max. `hot` is drawn in the accent, the
 *  rest sit in the de-emphasis tint; negatives take the loss tint. `titles`
 *  give each bar a native hover tooltip. */
export function SparkBars({ values, titles, hot, height = 28 }: {
  values: number[]; titles?: string[]; hot?: number; height?: number;
}) {
  const max = Math.max(...values.map((v) => Math.abs(v)), 1e-9);
  return (
    <div className="flex items-end gap-px w-full" style={{ height }} aria-hidden>
      {values.map((v, i) => (
        <span
          key={i}
          title={titles?.[i]}
          className={`flex-1 min-w-[2px] rounded-t-[2px] ${
            i === hot ? "bg-accent shadow-glow" : v < 0 ? "bg-loss/40" : "bg-accent/25"
          }`}
          style={{ height: Math.max(2, Math.round((Math.abs(v) / max) * height)) }}
        />
      ))}
    </div>
  );
}

/** Two-segment split with a 2px surface gap — winners vs losers. */
export function SplitBar({ up, down }: { up: number; down: number }) {
  const total = Math.max(up + down, 1);
  return (
    <div className="flex h-1.5 w-full gap-[2px]" aria-hidden>
      <span className="rounded-full bg-win" style={{ width: `${(up / total) * 100}%` }} />
      <span className="rounded-full bg-loss" style={{ width: `${(down / total) * 100}%` }} />
    </div>
  );
}

/** Same-ramp meter: accent fill on a lighter accent track. */
export function Meter({ pct }: { pct: number }) {
  return (
    <div className="relative h-1.5 w-full rounded-full bg-accent/15 overflow-hidden" aria-hidden>
      <div className="absolute inset-y-0 left-0 rounded-full bg-accent-grad shadow-glow"
        style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}

/** A stat tile: eyebrow label, big value, one-line context, optional visual
 *  pinned to the bottom. Value is proportional-figure sans — a standalone
 *  number, not a column. */
export function Kpi({ label, value, sub, tone, children }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: "win" | "loss"; children?: ReactNode;
}) {
  const toneCls = tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : "text-ink";
  return (
    <div className="panel relative overflow-hidden px-4 pt-3.5 pb-4 flex flex-col gap-3.5">
      <div className="pointer-events-none absolute -top-12 -right-10 h-32 w-32 rounded-full bg-accent/10 blur-2xl" />
      <div className="relative">
        <div className="eyebrow">{label}</div>
        <div className={`mt-2 text-[26px] leading-none font-semibold tracking-tight ${toneCls}`}>{value}</div>
        {sub != null && <div className="mt-2 text-[11px] leading-snug text-muted">{sub}</div>}
      </div>
      {children != null && <div className="relative mt-auto">{children}</div>}
    </div>
  );
}

/** Page heading: gradient title, one-line description, and a status slot on
 *  the right (freshness, live dot). */
export function PageHead({ title, blurb, right }: { title: ReactNode; blurb?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
      <div className="min-w-0">
        <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight">{title}</h1>
        {blurb && <p className="mt-1 text-xs text-muted max-w-[64ch]">{blurb}</p>}
      </div>
      {right && <div className="flex items-center gap-2 text-[11px] text-muted whitespace-nowrap">{right}</div>}
    </div>
  );
}

/** Freshness pill for a PageHead's `right` slot. */
export function Freshness({ loading, label }: { loading: boolean; label: string }) {
  return (
    <>
      <span className={`h-1.5 w-1.5 rounded-full ${loading ? "bg-warn animate-pulse" : "bg-accent live-dot"}`} />
      {label}
    </>
  );
}

/** A labelled control in a filter bar. */
export function Field({ label, title, children }: { label: string; title?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1" title={title}>
      <span className="eyebrow">{label}</span>
      {children}
    </div>
  );
}

/** Compact toggle — replaces a bare checkbox in dense toolbars. */
export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className="inline-flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-muted hover:text-ink transition-colors">
      <span className={`relative h-3.5 w-6 rounded-full transition-colors ${on ? "bg-accent" : "bg-white/10"}`}>
        <span className={`absolute top-0.5 h-2.5 w-2.5 rounded-full bg-bg transition-all ${on ? "left-3" : "left-0.5"}`} />
      </span>
      {label}
    </button>
  );
}

/** Rank badge: #1 is the lit medal, 2–3 accent, the rest recede. */
export function Medal({ rank }: { rank: number }) {
  const cls = rank === 1 ? "bg-accent-grad text-bg border-transparent shadow-glow"
    : rank <= 3 ? "text-accent border-accent/40 bg-accent/10"
    : "text-dim border-white/[0.08]";
  return (
    <span className={`grid place-items-center h-5 min-w-[20px] px-1 shrink-0 rounded-md border text-[10px] font-mono font-semibold ${cls}`}>
      {rank}
    </span>
  );
}

/** Thin horizontal data bar for a table cell — value relative to `max`,
 *  tinted by sign. Right-aligned to sit under a numeric column. */
export function DataBar({ value, max, className = "" }: { value: number; max: number; className?: string }) {
  const pct = max > 0 ? Math.round((Math.abs(value) / max) * 100) : 0;
  return (
    <div className={`ml-auto mt-1 h-[3px] w-14 rounded-full bg-white/[0.05] overflow-hidden ${className}`} aria-hidden>
      <div className={`h-full rounded-full ${value >= 0 ? "bg-win/60" : "bg-loss/60"}`} style={{ width: `${pct}%` }} />
    </div>
  );
}
