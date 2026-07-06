"use client";

import { useEffect, useState } from "react";
import { getScan, type Finding, type Scan } from "../lib/api";
import { SeverityPill } from "./SeverityPill";

const SEV_ORDER = ["critical", "high", "medium", "low", "info", "unknown"];

export function ScanReport({ id }: { id: string }) {
  const [scan, setScan] = useState<Scan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    setScan(null);
    setError(null);
    async function tick() {
      try {
        const s = await getScan(id);
        if (cancel) return;
        setScan(s);
        if (s.status !== "done" && s.status !== "error") {
          setTimeout(tick, 2000);
        }
      } catch (e: any) {
        if (cancel) return;
        setError(e?.message || "failed to load");
        setTimeout(tick, 4000);
      }
    }
    tick();
    return () => {
      cancel = true;
    };
  }, [id]);

  if (error) return <div className="text-critical p-6">{error}</div>;
  if (!scan) return <div className="text-muted p-6">loading scan…</div>;

  const findings = (scan.findings ?? []).slice().sort((a, b) => {
    return SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity);
  });

  const stats = scan.stats?.by_severity || {};

  return (
    <div className="space-y-5">
      <header className="bg-panel border border-border rounded-lg p-5">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wider text-muted mb-1">
              repository
            </div>
            <a
              href={prettyRepoUrl(scan.repo)}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-base text-accent hover:underline break-all"
            >
              {prettyRepo(scan.repo)}
            </a>
            {scan.branch && (
              <span className="ml-2 text-muted text-sm">@ {scan.branch}</span>
            )}
          </div>
          <StatusLine scan={scan} />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-4">
          {SEV_ORDER.slice(0, 5).map((sev) => {
            const colorMap: Record<string, string> = {
              critical: "text-critical",
              high: "text-high",
              medium: "text-medium",
              low: "text-low",
              info: "text-info",
            };
            return (
              <div
                key={sev}
                className="bg-panel2 border border-border rounded px-3 py-2"
              >
                <div className="text-[10px] uppercase tracking-wider text-muted">
                  {sev}
                </div>
                <div className={`text-xl font-mono mt-0.5 ${colorMap[sev] || "text-text"}`}>
                  {stats[sev] || 0}
                </div>
              </div>
            );
          })}
        </div>
      </header>

      {scan.status === "error" && (
        <div className="border border-critical/40 bg-critical/10 text-critical p-4 rounded">
          {scan.error || "scan failed"}
        </div>
      )}

      {findings.length > 0 ? (
        <ul className="space-y-3">
          {findings.map((f, i) => (
            <FindingCard key={i} f={f} />
          ))}
        </ul>
      ) : scan.status === "done" ? (
        <div className="bg-panel border border-border rounded-lg p-6 text-muted text-center">
          No vulnerabilities detected.
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg p-6 text-muted text-center">
          Waiting for results…
        </div>
      )}
    </div>
  );
}

function StatusLine({ scan }: { scan: Scan }) {
  const isWorking = ["queued", "cloning", "scanning"].includes(scan.status);
  const elapsed = scan.elapsed_seconds
    ? ` · ${scan.elapsed_seconds}s`
    : "";
  return (
    <div className="flex items-center gap-2 text-sm">
      {isWorking && <span className="spinner" />}
      <span
        className={`uppercase tracking-wider text-xs ${
          scan.status === "done"
            ? "text-low"
            : scan.status === "error"
            ? "text-critical"
            : "text-medium"
        }`}
      >
        {scan.status}
        {elapsed}
      </span>
    </div>
  );
}

function FindingCard({ f }: { f: Finding }) {
  return (
    <li className="bg-panel border border-border rounded-lg p-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <SeverityPill severity={f.severity} />
          {f.category && (
            <span className="text-xs text-muted uppercase tracking-wider">
              {f.category}
            </span>
          )}
        </div>
        {f.file && (
          <code className="text-xs text-accent break-all">
            {f.file}
            {f.line ? `:${f.line}` : ""}
          </code>
        )}
      </div>
      <div className="mt-2 font-medium">{f.title || "Untitled finding"}</div>
      {f.description && (
        <p className="text-sm text-muted mt-1 whitespace-pre-wrap">
          {f.description}
        </p>
      )}
      {f.recommendation && (
        <div className="mt-3 border-l-2 border-accent/60 pl-3 text-sm">
          <span className="text-accent uppercase tracking-wider text-[10px] mr-2">
            fix
          </span>
          <span className="text-text/90">{f.recommendation}</span>
        </div>
      )}
    </li>
  );
}

function prettyRepo(url: string) {
  return url.replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "");
}
function prettyRepoUrl(url: string) {
  return url.replace(/\.git$/, "");
}
