"use client";

import { useCallback, useEffect, useState } from "react";
import { Bento, BentoGrid, CidChip, GlassButton } from "./Bento";
import { VersionGraph } from "./VersionGraph";

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

// Raw hex (not CSS vars) so we can derive alpha glows per dot/tag.
const ACTION_GLYPH: Record<string, { color: string; label: string }> = {
  snapshot:        { color: "#a78bfa", label: "snapshot" },
  restore:         { color: "#fbbf24", label: "rollback" },
  "auto-snapshot": { color: "#8a8aa2", label: "auto"     },
  fork:            { color: "#818cf8", label: "fork"     },
};

type Props = {
  apiBase: string;
  module: string;
  authHeader?: Record<string, string>;
  onForked?: (newModule: string) => void;
};

// Older records minted from image-attached jobs stored the raw
// "[Attached images: /tmp/…]" preamble (often truncated) as the message —
// hide the artifact paths and fall back to a generic label.
function displayMessage(m: string): string {
  if (!m.startsWith("[Attached images:")) return m;
  const end = m.indexOf("]");
  return (end >= 0 ? m.slice(end + 1).trim() : "") || "(task with attached image)";
}

function timeAgo(ts: number): string {
  const now = Math.floor(Date.now() / 1000);
  const d = now - ts;
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function clockTime(ts: number): string {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function dayLabel(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (diff <= 0) return "today";
  if (diff === 1) return "yesterday";
  const label = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return now.getFullYear() === d.getFullYear() ? label : `${label} ${d.getFullYear()}`;
}

export function VersionsPanel({ apiBase, module, authHeader, onForked }: Props) {
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [snapMsg, setSnapMsg] = useState("");
  const [snapBusy, setSnapBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Two readings of the same history: the timeline (what happened, when) and
  // the graph (how the versions relate, and what each one changed).
  const [view, setView] = useState<"timeline" | "graph">("timeline");

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
    <div className="snap-bar">
      <div className="vgr-toggle">
        {(["timeline", "graph"] as const).map((v) => (
          <button
            key={v}
            className={view === v ? "on" : undefined}
            onClick={() => setView(v)}
            title={v === "timeline" ? "Every version in order, newest first" : "Version lineage as a branch graph, plus the file tree each version changed"}
          >
            {v === "timeline" ? "≡ timeline" : "◆ graph"}
          </button>
        ))}
      </div>
      <input
        type="text"
        className="snap-input"
        value={snapMsg}
        onChange={(e) => setSnapMsg(e.target.value)}
        placeholder="describe this version…"
        onKeyDown={(e) => {
          if (e.key === "Enter" && !snapBusy) snapshot();
        }}
      />
      <button className="snap-btn" onClick={snapshot} disabled={snapBusy}>
        {snapBusy ? "…" : "✦ snap"}
      </button>
    </div>
  );

  // Newest first, grouped by calendar day for the timeline headers.
  const groups: { label: string; items: VersionRecord[] }[] = [];
  for (const v of [...versions].reverse()) {
    const label = dayLabel(v.timestamp);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(v);
    else groups.push({ label, items: [v] });
  }

  return (
    <BentoGrid>
      <Bento title={`versions · ${versions.length}`} span={3} action={snapAction}>
        {status && <div className="vtl-note ok">✓ {status}</div>}
        {error && <div className="vtl-note err">✗ {error}</div>}
        {loading && (
          <div className="vtl-skels">
            <div className="vtl-skel" style={{ width: "62%" }} />
            <div className="vtl-skel" style={{ width: "78%" }} />
            <div className="vtl-skel" style={{ width: "54%" }} />
          </div>
        )}
        {!loading && versions.length === 0 && (
          <div className="vtl-empty">
            <span className="vtl-empty-dot" />
            no versions yet — describe &amp; snap above to start a history
          </div>
        )}
        {!loading && versions.length > 0 && view === "graph" && (
          <VersionGraph apiBase={apiBase} module={module} versions={versions} />
        )}
        <div className="vtl fade-in" hidden={view !== "timeline"}>
          {groups.map((g, gi) => (
            <section key={g.label} className="vtl-group">
              <div className="vtl-day">
                <span className="lab">{g.label}</span>
                <span className="rule" />
              </div>
              <div className="vtl-items">
                {g.items.map((v, i) => {
                  const glyph = ACTION_GLYPH[v.action || "snapshot"] || ACTION_GLYPH.snapshot;
                  const isHead = gi === 0 && i === 0;
                  const isAuto = v.action === "auto-snapshot";
                  return (
                    <div
                      key={v.cid + v.timestamp}
                      className={`vtl-row${isHead ? " head" : ""}${isAuto ? " auto" : ""}`}
                    >
                      <span className="vtl-rail">
                        <span
                          className="vtl-dot"
                          title={glyph.label}
                          style={{ background: glyph.color, boxShadow: `0 0 10px ${glyph.color}59` }}
                        />
                      </span>
                      <div className="vtl-main">
                        <span className="vtl-msg" title={displayMessage(v.message)}>
                          {displayMessage(v.message) || "(no message)"}
                        </span>
                        {isHead && <span className="vtl-tag head-tag">latest</span>}
                        {v.action && v.action !== "snapshot" && (
                          <span
                            className="vtl-tag"
                            style={{
                              color: glyph.color,
                              borderColor: `${glyph.color}45`,
                              background: `${glyph.color}14`,
                            }}
                          >
                            {glyph.label}
                          </span>
                        )}
                      </div>
                      <div className="vtl-side">
                        <span className="vtl-acts">
                          <CidChip cid={v.cid} />
                          <GlassButton variant="ghost" onClick={() => fork(v.cid)} title="Fork this version into your portal">
                            fork
                          </GlassButton>
                          <GlassButton variant="ghost" onClick={() => restore(v.cid)} title="Rollback to this version (auto-snapshots current state first)">
                            ↺
                          </GlassButton>
                        </span>
                        <span className="vtl-time">
                          {g.label === "today" ? timeAgo(v.timestamp) : clockTime(v.timestamp)}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </Bento>
    </BentoGrid>
  );
}
