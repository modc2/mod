"use client";

import { useCallback, useEffect, useState } from "react";
import { Bento, BentoGrid, CidChip, GlassButton } from "./Bento";

type VersionRecord = {
  cid: string;
  message: string;
  author: string;
  timestamp: number;
  parent: string | null;
  registry_cid?: string | null;
  registry_prev?: string | null;
  action?: "snapshot" | "restore" | "auto-snapshot" | "fork" | string;
};

const ACTION_GLYPH: Record<string, { color: string; label: string }> = {
  snapshot:        { color: "var(--accent-color)",  label: "snapshot" },
  restore:         { color: "var(--crt-amber)",     label: "rollback" },
  "auto-snapshot": { color: "var(--text-tertiary)", label: "auto"     },
  fork:            { color: "var(--crt-blue)",      label: "fork"     },
};

type Props = {
  apiBase: string;
  module: string;
  authHeader?: Record<string, string>;
  onForked?: (newModule: string) => void;
};

function timeAgo(ts: number): string {
  const now = Math.floor(Date.now() / 1000);
  const d = now - ts;
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export function VersionsPanel({ apiBase, module, authHeader, onForked }: Props) {
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [snapMsg, setSnapMsg] = useState("");
  const [snapBusy, setSnapBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${apiBase}/modules/${encodeURIComponent(module)}/versions`);
      const d = await r.json();
      setVersions(Array.isArray(d.versions) ? d.versions : []);
    } catch (e) {
      setError(`load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [apiBase, module]);

  useEffect(() => {
    if (module) load();
  }, [module, load]);

  const snapshot = async () => {
    setSnapBusy(true);
    setError(null);
    setStatus(null);
    try {
      const r = await fetch(`${apiBase}/modules/${encodeURIComponent(module)}/snapshot`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(authHeader || {}) },
        body: JSON.stringify({ message: snapMsg || "snapshot" }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setStatus(`snapshot ${d.cid.slice(0, 10)}… (${d.file_count} files, ${d.store})`);
      setSnapMsg("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSnapBusy(false);
    }
  };

  const fork = async (cid: string) => {
    setError(null);
    setStatus(null);
    try {
      const r = await fetch(`${apiBase}/modules/${encodeURIComponent(module)}/fork`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(authHeader || {}) },
        body: JSON.stringify({ cid }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setStatus(`forked → ${d.target_module}`);
      onForked?.(d.target_module);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const restore = async (cid: string) => {
    if (!confirm(`Restore ${module} to ${cid.slice(0, 12)}…?\n\nCurrent state will be auto-snapshotted first.`)) return;
    setError(null);
    setStatus(null);
    try {
      const r = await fetch(`${apiBase}/modules/${encodeURIComponent(module)}/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(authHeader || {}) },
        body: JSON.stringify({ cid }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setStatus(`restored (${d.file_count} files written)`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const snapAction = (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <input
        type="text"
        value={snapMsg}
        onChange={(e) => setSnapMsg(e.target.value)}
        placeholder="describe…"
        style={{
          width: 180,
          background: "var(--glass-bg-strong)",
          border: "1px solid var(--glass-border)",
          borderRadius: 8,
          padding: "5px 9px",
          color: "var(--text-primary)",
          fontSize: 12.5,
          outline: "none",
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !snapBusy) snapshot();
        }}
      />
      <GlassButton variant="primary" onClick={snapshot} disabled={snapBusy}>
        {snapBusy ? "…" : "snap"}
      </GlassButton>
    </div>
  );

  return (
    <BentoGrid>
      <Bento title={`versions · ${versions.length}`} span={3} action={snapAction}>
        {status && (
          <div className="bento-sub" style={{ marginBottom: 6, color: "var(--crt-green)" }}>✓ {status}</div>
        )}
        {error && (
          <div className="bento-sub" style={{ marginBottom: 6, color: "var(--crt-red)" }}>✗ {error}</div>
        )}
        {loading && <div className="bento-sub">loading…</div>}
        {!loading && versions.length === 0 && (
          <div className="bento-sub">no versions yet — snap above to start a history</div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {[...versions].reverse().map((v) => {
            const glyph = ACTION_GLYPH[v.action || "snapshot"] || ACTION_GLYPH.snapshot;
            return (
              <div key={v.cid + v.timestamp} className="version-row">
                <span
                  className="dot"
                  title={glyph.label}
                  style={{ background: glyph.color, boxShadow: `0 0 0 2px ${glyph.color}1a` }}
                />
                <div style={{ minWidth: 0, display: "flex", alignItems: "baseline", gap: 8, overflow: "hidden" }}>
                  <span className="msg" style={{ flex: "0 1 auto", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {v.message || "(no message)"}
                  </span>
                  <span className="meta" style={{ flex: "0 0 auto" }}>
                    {glyph.label} · {timeAgo(v.timestamp)}
                    {v.registry_cid && <> · <span style={{ color: "var(--accent-color)" }}>{v.registry_cid.slice(0, 8)}…</span></>}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  <CidChip cid={v.cid} />
                  <GlassButton variant="ghost" onClick={() => fork(v.cid)} title="Fork this version into your portal">fork</GlassButton>
                  <GlassButton variant="ghost" onClick={() => restore(v.cid)} title="Rollback to this version (auto-snapshots current state first)">↺</GlassButton>
                </div>
              </div>
            );
          })}
        </div>
      </Bento>
    </BentoGrid>
  );
}
