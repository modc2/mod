// Client-side API helper for the securescan Rust backend.
// Uses /api/proxy/* (rewritten by next.config.mjs) so the browser doesn't
// need to know the backend host or worry about CORS in dev.

const BASE = "/api/proxy";

export type Severity = "critical" | "high" | "medium" | "low" | "info" | string;

export interface Finding {
  severity: Severity;
  category?: string;
  title?: string;
  description?: string;
  file?: string;
  line?: number | null;
  recommendation?: string;
}

export interface Stats {
  total: number;
  by_severity?: Record<string, number>;
  by_category?: Record<string, number>;
}

export interface Scan {
  scan_id: string;
  repo: string;
  branch?: string | null;
  status: "queued" | "cloning" | "scanning" | "done" | "error" | string;
  started_at?: number;
  finished_at?: number;
  elapsed_seconds?: number;
  reviewer?: string;
  stats?: Stats;
  findings?: Finding[];
  error?: string;
}

export async function startScan(body: {
  repo: string;
  branch?: string;
  steps?: number;
  provider?: string;
  model?: string;
}): Promise<{ scan_id: string; repo: string; status: string }> {
  const r = await fetch(`${BASE}/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error || `scan failed (${r.status})`);
  }
  return r.json();
}

export async function getScan(id: string): Promise<Scan> {
  const r = await fetch(`${BASE}/scans/${id}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`failed to load scan (${r.status})`);
  return r.json();
}

export async function listScans(): Promise<{ scans: Scan[]; total: number }> {
  const r = await fetch(`${BASE}/scans`, { cache: "no-store" });
  if (!r.ok) throw new Error(`failed to list scans (${r.status})`);
  return r.json();
}

export async function deleteScan(id: string): Promise<void> {
  await fetch(`${BASE}/scans/${id}`, { method: "DELETE" });
}
