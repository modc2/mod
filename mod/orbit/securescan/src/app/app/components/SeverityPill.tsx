import type { Severity } from "../lib/api";

const COLORS: Record<string, string> = {
  critical: "text-critical",
  high: "text-high",
  medium: "text-medium",
  low: "text-low",
  info: "text-info",
};

export function SeverityPill({ severity }: { severity: Severity }) {
  const cls = COLORS[severity] || "text-muted";
  return <span className={`sev-pill ${cls}`}>{severity}</span>;
}
