"use client";

import type { Audit, AuditSummary, Severity } from "../lib/types";

// Everything the console says about an agent audit lives here: the badge on a
// palette card, the verdict line in the inspector, and the full report in the
// source drawer. One colour scale, so "high" looks the same everywhere.

export const SEVERITY_COLOR: Record<string, string> = {
  critical: "#f87171",
  high: "#fb923c",
  medium: "#fbbf24",
  low: "#7dd3fc",
  info: "#7a8b9c",
  unknown: "#4d5b69",
};

const ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];

export function RiskPill({ summary, compact }: { summary?: AuditSummary | null; compact?: boolean }) {
  if (!summary) {
    return (
      <span className="pill" title="no agent audit yet" style={{ color: "var(--dim)" }}>
        unaudited
      </span>
    );
  }
  const color = SEVERITY_COLOR[summary.risk] ?? SEVERITY_COLOR.unknown;
  const tally = ORDER.filter((s) => (summary.counts?.[s] ?? 0) > 0)
    .map((s) => `${summary.counts[s]} ${s}`)
    .join(" · ");
  return (
    <span
      className="pill"
      style={{ color, borderColor: color + "55" }}
      title={`agent audit ${summary.audited_at ?? ""}: ${tally || "no findings"}${
        summary.worst ? ` — worst: ${summary.worst.title}` : ""
      }`}
    >
      <span className="dot" />
      {compact ? summary.risk : `${summary.risk} risk`}
    </span>
  );
}

export function CountsRow({ counts }: { counts: Partial<Record<Severity, number>> }) {
  return (
    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
      {ORDER.map((s) => {
        const n = counts?.[s] ?? 0;
        if (!n) return null;
        return (
          <span key={s} className="pill" style={{ color: SEVERITY_COLOR[s], borderColor: SEVERITY_COLOR[s] + "44" }}>
            {n} {s}
          </span>
        );
      })}
    </div>
  );
}

/** The full report, as rendered in the source drawer's AUDIT tab. */
export default function AuditView({ audit, loading }: { audit: Audit | null | undefined; loading?: boolean }) {
  if (loading && !audit) {
    return <div style={{ fontSize: 11, color: "var(--dim)" }}>loading audit…</div>;
  }
  if (!audit) {
    return (
      <div style={{ fontSize: 11, color: "var(--dim)", lineHeight: 1.6 }}>
        No agent audit for this block yet. Every audited block ships one at{" "}
        <span className="mono-small">/catalog/&lt;id&gt;/audit</span>; the palette badge says which do.
      </div>
    );
  }
  const color = SEVERITY_COLOR[audit.risk] ?? SEVERITY_COLOR.unknown;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span className="pill" style={{ color, borderColor: color + "55", fontSize: 11 }}>
          <span className="dot" /> {audit.risk} risk
        </span>
        <CountsRow counts={audit.counts} />
        <span className="mono-small" style={{ marginLeft: "auto" }}>
          {audit.auditor} · {audit.audited_at}
        </span>
      </div>

      <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.65 }}>{audit.summary}</div>

      <div className="issue warning" style={{ fontSize: 10.5 }}>
        <span>!</span>
        <span>
          An agent read {audit.file} and wrote this. It reduces the unknowns; it certifies nothing.
          Deploy to a testnet first and treat mainnet use as your own risk.
        </span>
      </div>

      {audit.findings.length > 0 && (
        <>
          <div className="label">Findings</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {audit.findings.map((f) => {
              const c = SEVERITY_COLOR[f.severity] ?? SEVERITY_COLOR.unknown;
              return (
                <details key={f.id} className="card" style={{ borderLeft: `2px solid ${c}` }}>
                  <summary style={{ cursor: "pointer", listStyle: "none", display: "flex", gap: 8, alignItems: "baseline" }}>
                    <span className="pill" style={{ color: c, borderColor: c + "44", flexShrink: 0 }}>
                      {f.severity}
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{f.title}</span>
                    <span className="mono-small" style={{ flexShrink: 0 }}>{f.id}</span>
                  </summary>
                  <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 7, fontSize: 11, lineHeight: 1.6 }}>
                    <div className="mono-small" style={{ color: "var(--muted)" }}>{f.where}</div>
                    <div style={{ color: "var(--text)" }}>{f.detail}</div>
                    <div>
                      <span className="label" style={{ display: "inline", marginRight: 6 }}>exploit</span>
                      <span style={{ color: "var(--muted)" }}>{f.exploit}</span>
                    </div>
                    <div>
                      <span className="label" style={{ display: "inline", marginRight: 6 }}>fix</span>
                      <span style={{ color: "var(--accent)" }}>{f.recommendation}</span>
                    </div>
                  </div>
                </details>
              );
            })}
          </div>
        </>
      )}

      {audit.safe_use?.length > 0 && (
        <>
          <div className="label">If you deploy it as-is</div>
          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11, color: "var(--muted)", lineHeight: 1.65 }}>
            {audit.safe_use.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
