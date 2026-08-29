"use client";

import { useCallback, useEffect, useState } from "react";
import { Bento, BentoGrid, GlassButton } from "./Bento";

type VersionRecord = {
  cid: string;
  message: string;
  author: string;
  timestamp: number;
  parent: string | null;
  registry_cid?: string | null;
  registry_prev?: string | null;
  action?: "snapshot" | "restore" | "auto-snapshot" | "fork" | string;
  /// Is this version's blob still in the store? A version that isn't can be
  /// read about but not reverted to, so the row says so instead of offering a
  /// button that 400s.
  restorable?: boolean;
};

// Raw hex (not CSS vars) so we can derive alpha glows per dot/tag.
// `quiet` actions are the everyday ones — they'd stamp an identical pill on
// every row, so the rail dot carries them and only the notable actions
// (rollback / fork / auto) earn a tag.
const ACTION_GLYPH: Record<string, { color: string; label: string; quiet?: boolean }> = {
  edit:            { color: "#a78bfa", label: "edit",     quiet: true },
  snapshot:        { color: "#22d3ee", label: "snapshot", quiet: true },
  restore:         { color: "#fbbf24", label: "rollback" },
  "auto-snapshot": { color: "#8a8aa2", label: "auto"     },
  fork:            { color: "#818cf8", label: "fork"     },
};

// An unknown action gets a neutral dot and its own raw label — never
// mislabelled as something it isn't.
function glyphFor(action?: string) {
  return ACTION_GLYPH[action || "edit"] || { color: "#8a8aa2", label: action || "version" };
}

function shortCid(cid: string): string {
  return cid.length > 14 ? `${cid.slice(0, 8)}…${cid.slice(-4)}` : cid;
}

type Props = {
  apiBase: string;
  module: string;
  authHeader?: Record<string, string>;
  onForked?: (newModule: string) => void;
  /// Does this session hold revert authority — the owner's OWN key? Editing
  /// and reverting are different powers: an editor (whitelisted, invited, even
  /// sudo-delegated) can change a module, and still cannot roll it back. When
  /// omitted, the versions response answers it (`revert.can_revert`), so the
  /// panel is never more permissive-looking than the API.
  canRevert?: boolean;
  /// apiBase-relative fetch that raises the Sudo sheet on 401 {sudo_required}
  /// and retries with the owner's signature. Without it a revert still works,
  /// it just surfaces the signature demand as an error.
  sudoFetch?: (path: string, init?: RequestInit) => Promise<Response>;
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

function fullStamp(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
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

export function VersionsPanel({ apiBase, module, authHeader, onForked, canRevert, sudoFetch }: Props) {
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  // What the API says about revert authority for THIS caller. The prop wins
  // when the console already knows (it fetched /auth/role), the server's own
  // answer is the fallback — and a `false` from either hides the control.
  const [serverCanRevert, setServerCanRevert] = useState<boolean | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const mayRevert = canRevert ?? serverCanRevert ?? false;
  const [loading, setLoading] = useState(false);
  const [snapMsg, setSnapMsg] = useState("");
  const [snapBusy, setSnapBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const copyCid = (cid: string) => {
    navigator.clipboard?.writeText(cid).catch(() => {});
    setCopied(cid);
    setTimeout(() => setCopied((c) => (c === cid ? null : c)), 1100);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${apiBase}/modules/${encodeURIComponent(module)}/versions`, {
        headers: { ...(authHeader || {}) },
      });
      const d = await r.json();
      setVersions(Array.isArray(d.versions) ? d.versions : []);
      setServerCanRevert(typeof d?.revert?.can_revert === "boolean" ? d.revert.can_revert : null);
    } catch (e) {
      setError(`load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // One request path for both reverts. `sudoFetch` (when the console passed
  // it) raises the Sudo sheet on the server's 401 {sudo_required} and retries
  // with the owner's fresh signature; without it we fall back to a plain
  // fetch and the signature demand simply surfaces as the error it is.
  // A bodyless 404 means the route isn't in the running API binary (it is
  // built from source and doesn't hot-reload), which otherwise surfaces as a
  // JSON parse error and reads like a bug in the panel.
  const readResult = async (r: Response, what: string) => {
    let d: any = {};
    try {
      d = await r.json();
    } catch {
      d = {};
    }
    if (!r.ok) {
      throw new Error(
        d.error ||
          (r.status === 404
            ? `this API build has no ${what} route yet — restart build-fork-api to pick it up`
            : `HTTP ${r.status}`),
      );
    }
    return d;
  };

  const postRevert = (path: string, body: unknown) =>
    sudoFetch
      ? sudoFetch(path, { method: "POST", body: JSON.stringify(body) })
      : fetch(`${apiBase}${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...(authHeader || {}) },
          body: JSON.stringify(body),
        });

  const restore = async (v: VersionRecord) => {
    const when = fullStamp(v.timestamp);
    if (
      !confirm(
        `Revert ${module} to this version?\n\n` +
          `${displayMessage(v.message) || "(no message)"}\n${when} · ${shortCid(v.cid)}\n\n` +
          `Every file goes back to that state. The current state is pinned as its own version first, so this is itself undoable.`,
      )
    )
      return;
    setError(null);
    setStatus(null);
    try {
      const r = await postRevert(`/modules/${encodeURIComponent(module)}/restore`, { cid: v.cid });
      const d = await readResult(r, "restore");
      setStatus(`reverted to ${shortCid(v.cid)} (${d.file_count} files written)`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // The owner's one-click undo: no CID to pick, the server walks the log back
  // one state (skipping repeats and versions whose blob is gone).
  const undoLast = async () => {
    if (
      !confirm(
        `Undo the last change to ${module}?\n\n` +
          `It goes back to the state before that change. The current state is pinned first, so you can walk forward again.`,
      )
    )
      return;
    setUndoBusy(true);
    setError(null);
    setStatus(null);
    try {
      const r = await postRevert(`/modules/${encodeURIComponent(module)}/undo`, { steps: 1 });
      const d = await readResult(r, "undo");
      const undone = d?.undo?.undone?.message;
      setStatus(
        d?.undo?.changed === false
          ? `already at that state — the history returns to it, nothing on disk moved`
          : undone
            ? `undid “${displayMessage(String(undone)).slice(0, 60)}” — ${d.file_count} files back`
            : `undone (${d.file_count} files written)`,
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUndoBusy(false);
    }
  };

  // The owner's undo sits OUTSIDE the snap pill: taking a version and undoing
  // one are opposite moves, and nesting the second inside the first's control
  // read as one compound widget.
  const snapAction = (
    <div className="snap-actions">
      {mayRevert && (
        <button
          className="undo-btn"
          onClick={undoLast}
          disabled={undoBusy || versions.length < 2}
          title={
            versions.length < 2
              ? "nothing to undo yet — there is only one state in this history"
              : "Undo the last change: back to the state before it (owner only)"
          }
        >
          {undoBusy ? "…" : "↺ undo last"}
        </button>
      )}
      <div className="snap-bar">
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
      <Bento
        title={`versions · ${versions.length}${mayRevert ? " · owner" : ""}`}
        span={3}
        action={snapAction}
      >
        {status && <div className="vtl-note ok">✓ {status}</div>}
        {error && <div className="vtl-note err">✗ {error}</div>}
        {/* Editing and reverting are different powers. Say which one this
            session holds, rather than showing everyone a button that 403s. */}
        {!mayRevert && versions.length > 0 && (
          <div className="vtl-note" style={{ color: "var(--text-tertiary)" }}>
            ⌾ history is read-only for you — anyone trusted to edit can change this module, but only
            its owner can revert it to an earlier version.
          </div>
        )}
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
        <div className="vtl fade-in">
          {groups.map((g, gi) => (
            <section key={g.label} className="vtl-group">
              <div className="vtl-day">
                <span className="lab">{g.label}</span>
                <span className="rule" />
                <span className="n">{g.items.length}</span>
              </div>
              <div className="vtl-items">
                {g.items.map((v, i) => {
                  const glyph = glyphFor(v.action);
                  const isHead = gi === 0 && i === 0;
                  const isAuto = v.action === "auto-snapshot";
                  const msg = displayMessage(v.message);
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
                        <div className="vtl-line">
                          <span className="vtl-msg" title={msg}>
                            {msg || "(no message)"}
                          </span>
                          {isHead && <span className="vtl-tag head-tag">latest</span>}
                          {!glyph.quiet && (
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
                        {/* The cid used to hide behind hover; at rest it is the
                            only thing that tells two rows apart, so it stays. */}
                        <button
                          className={`vtl-cid${copied === v.cid ? " copied" : ""}`}
                          title={`${v.cid} — click to copy`}
                          onClick={() => copyCid(v.cid)}
                        >
                          {copied === v.cid ? "copied" : shortCid(v.cid)}
                        </button>
                      </div>
                      <div className="vtl-side">
                        <span className="vtl-acts">
                          <GlassButton variant="ghost" onClick={() => fork(v.cid)} title="Fork this version into your portal">
                            fork
                          </GlassButton>
                          {/* The revert control exists only for the owner. A
                              version whose blob has left the store can be read
                              about but not reverted to, so it says so. */}
                          {mayRevert &&
                            (v.restorable === false ? (
                              <span
                                className="vtl-tag"
                                title="this version's blob is no longer in the store — nothing to revert to"
                                style={{ opacity: 0.5 }}
                              >
                                gone
                              </span>
                            ) : (
                              <GlassButton
                                variant="ghost"
                                onClick={() => restore(v)}
                                title={
                                  isHead
                                    ? "This is the current state"
                                    : "Revert to this version — the owner's undo (current state is pinned first)"
                                }
                              >
                                ↺
                              </GlassButton>
                            ))}
                        </span>
                        <span className="vtl-time" title={fullStamp(v.timestamp)}>
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
