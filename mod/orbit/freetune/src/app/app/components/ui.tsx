"use client";
import React from "react";

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`card p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between mb-3">
          {title && (
            <h3 className="text-[13px] font-bold tracking-wide text-white/90">{title}</h3>
          )}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      {children}
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="card p-3">
      <div className="label">{label}</div>
      <div
        className="text-[22px] font-bold mono-num leading-none mt-1"
        style={{ color: accent || "#e7e9ee" }}
      >
        {value}
      </div>
      {sub && <div className="text-[11px] text-white/40 mt-1">{sub}</div>}
    </div>
  );
}

// Tiny dependency-free sparkline.
export function Spark({
  data,
  color = "#38bdf8",
  height = 44,
  max = 100,
}: {
  data: number[];
  color?: string;
  height?: number;
  max?: number;
}) {
  const w = 240;
  if (!data.length) return <svg width="100%" height={height} viewBox={`0 0 ${w} ${height}`} />;
  const n = data.length;
  const pts = data
    .map((v, i) => {
      const x = (i / Math.max(1, n - 1)) * w;
      const y = height - (Math.min(v, max) / max) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none">
      <polyline
        points={`0,${height} ${pts} ${w},${height}`}
        fill={color}
        opacity={0.1}
      />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

export function cpuBadge(cpu: string) {
  const map: Record<string, string> = {
    good: "#6ee7b7",
    slow: "#fcd34d",
    heavy: "#f87171",
  };
  const c = map[cpu] || "#9ca3af";
  return (
    <span className="pill" style={{ color: c, borderColor: c + "55" }}>
      cpu: {cpu}
    </span>
  );
}
