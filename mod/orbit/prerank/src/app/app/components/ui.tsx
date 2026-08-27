"use client";

import React from "react";

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`card ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="text-[11px] tracking-[0.16em] text-muted uppercase">{title}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div>
      <div className="text-[10px] tracking-[0.14em] text-muted uppercase">{label}</div>
      <div className="text-lg mt-0.5">{value}</div>
      {hint && <div className="text-[11px] text-muted mt-0.5">{hint}</div>}
    </div>
  );
}

export function Note({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "warn" | "down" | "up" }) {
  const color = { muted: "text-muted", warn: "text-warn", down: "text-down", up: "text-up" }[tone];
  // A note always follows something — a stat row, a table, a form — so it
  // carries its own top margin rather than every caller remembering one.
  return <div className={`text-[11px] leading-relaxed mt-3 ${color}`}>{children}</div>;
}

/** Errors are shown, never swallowed — the API's refusals are the most
 *  informative thing this console has to say. */
export function Problem({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="mt-3 text-[12px] text-down border border-[color:var(--down)]/40 rounded-lg px-3 py-2">
      {error}
    </div>
  );
}

export function Ok({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="mt-3 text-[12px] text-up border border-[color:var(--up)]/40 rounded-lg px-3 py-2">
      {message}
    </div>
  );
}

export function PhaseBadge({ phase }: { phase: string }) {
  const tone: Record<string, string> = {
    open: "var(--up)",
    reveal: "var(--warn)",
    sealed: "var(--accent)",
    settled: "var(--muted)",
    voided: "var(--down)",
  };
  const color = tone[phase] || "var(--muted)";
  return (
    <span
      className="pill"
      style={{ color, borderColor: color }}
      title={
        {
          open: "bets are hashes — amounts public, models hidden",
          reveal: "commitments are being opened; no new money enters",
          sealed: "pools frozen and public; graders are ranking",
          settled: "paid out",
          voided: "the graders did not agree — everything refunded",
        }[phase] || ""
      }
    >
      {phase}
    </span>
  );
}
