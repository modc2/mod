"use client";

import { useEffect, useState } from "react";
import { deleteScan, listScans, type Scan } from "../lib/api";

export function ScanList({
  selectedId,
  onSelect,
  refreshKey,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
  refreshKey: number;
}) {
  const [scans, setScans] = useState<Scan[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const r = await listScans();
      setScans(r.scans);
    } catch {
      // ignore — backend may not be up yet
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [refreshKey]);

  async function remove(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    await deleteScan(id);
    load();
  }

  if (loading) {
    return (
      <div className="text-sm text-muted px-3 py-4">loading scans…</div>
    );
  }
  if (scans.length === 0) {
    return (
      <div className="text-sm text-muted px-3 py-4">
        No scans yet. Start one above.
      </div>
    );
  }

  return (
    <ul className="divide-y divide-border">
      {scans.map((s) => {
        const sel = s.scan_id === selectedId;
        const findings = s.stats?.total ?? 0;
        const crit = s.stats?.by_severity?.critical ?? 0;
        const high = s.stats?.by_severity?.high ?? 0;
        return (
          <li
            key={s.scan_id}
            onClick={() => onSelect(s.scan_id)}
            className={`cursor-pointer px-3 py-2.5 hover:bg-panel2 transition ${
              sel ? "bg-panel2" : ""
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="font-mono text-xs truncate text-text">
                  {prettyRepo(s.repo)}
                </div>
                <div className="flex items-center gap-2 mt-1 text-[10px] uppercase tracking-wider">
                  <StatusBadge status={s.status} />
                  {findings > 0 && (
                    <span className="text-muted">{findings} findings</span>
                  )}
                  {crit > 0 && <span className="text-critical">{crit} crit</span>}
                  {high > 0 && <span className="text-high">{high} high</span>}
                </div>
              </div>
              <button
                onClick={(e) => remove(e, s.scan_id)}
                className="text-muted hover:text-critical text-xs px-1"
                aria-label="delete scan"
              >
                ✕
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function prettyRepo(url: string) {
  return url.replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "");
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued: "text-muted",
    cloning: "text-info",
    scanning: "text-medium",
    done: "text-low",
    error: "text-critical",
  };
  const isWorking = status === "queued" || status === "cloning" || status === "scanning";
  return (
    <span className={`flex items-center gap-1 ${map[status] || "text-muted"}`}>
      {isWorking && <span className="spinner" />}
      {status}
    </span>
  );
}
