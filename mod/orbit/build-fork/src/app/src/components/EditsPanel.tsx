"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bento, BentoGrid, CidChip, GlassButton } from "./Bento";

// EDIT tab: every past edit of the selected module, newest first, each paired
// with the localfs CID of the version it produced. Edit jobs are matched to
// version records by job_id (the API auto-snapshots the module into the
// localfs blob store when a job completes); manual snapshots / rollbacks /
// forks appear in the same timeline so the history reads as one thread.

type VersionRecord = {
  cid: string;
  message: string;
  author: string;
  timestamp: number;
  parent: string | null;
  registry_cid?: string | null;
  action?: string;
  job_id?: string | null;
};

type JobLite = {
  id: string;
  prompt: string;
  work_dir: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  output: string;
  created_at: number;
  updated_at: number;
  user_address?: string;
};

type FileEdit = { kind: "edit" | "write"; file: string; removed: string[]; added: string[]; lineCount: number };

type Row = {
  key: string;
  timestamp: number;
  job?: JobLite;
  version?: VersionRecord;
};

const ACTION_GLYPH: Record<string, { color: string; label: string }> = {
  edit:            { color: "var(--crt-blue)",      label: "edit"     },
  snapshot:        { color: "var(--accent-color)",  label: "snapshot" },
  restore:         { color: "var(--crt-amber)",     label: "rollback" },
  "auto-snapshot": { color: "var(--text-tertiary)", label: "auto"     },
  fork:            { color: "var(--crt-blue)",      label: "fork"     },
  import:          { color: "var(--crt-purple, #c084fc)", label: "import" },
};

function timeAgo(ts: number): string {
  const now = Math.floor(Date.now() / 1000);
  const d = Math.max(0, now - ts);
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

// Strip the injected "Note: you are working on…" suffix / image preamble the
// API adds to edit-job prompts; keep the first meaningful line.
function humanPrompt(prompt: string): string {
  const base = prompt.split("\n\nNote: you are working on")[0];
  const line = base.split("\n").find((l) => l.trim().length > 0) || base;
  return line.trim();
}

function moduleOfWorkDir(workDir: string): string | null {
  const m = workDir.match(/\/mod\/(?:orbit|core)\/([^/]+)/);
  return m ? m[1] : null;
}

// Same structured markers the task EDITS view parses (see jobs.rs
// format_tool_input): "┌─ EDIT: path" diff blocks and "┌─ WRITE: path (N lines)".
function parseFileEdits(text: string): FileEdit[] {
  if (!text) return [];
  const EDIT = "┌─ EDIT: ", WRITE = "┌─ WRITE: ";
  const edits: FileEdit[] = [];
  let cur: FileEdit | null = null;
  let phase: "removed" | "added" = "removed";
  for (const line of text.split("\n")) {
    if (line.startsWith(EDIT)) {
      cur = { kind: "edit", file: line.slice(EDIT.length).trim(), removed: [], added: [], lineCount: 0 };
      phase = "removed";
      continue;
    }
    if (line.startsWith(WRITE)) {
      const rest = line.slice(WRITE.length).trim();
      const m = rest.match(/^(.*) \((\d+) lines\)$/);
      edits.push({ kind: "write", file: m ? m[1] : rest, removed: [], added: [], lineCount: m ? parseInt(m[2], 10) : 0 });
      cur = null;
      continue;
    }
    if (!cur) continue;
    if (line === "│───") { phase = "added"; continue; }
    if (line === "└─") { edits.push(cur); cur = null; continue; }
    if (phase === "removed" && line.startsWith("│- ")) cur.removed.push(line.slice(3));
    else if (phase === "added" && line.startsWith("│+ ")) cur.added.push(line.slice(3));
  }
  if (cur) edits.push(cur);
  return edits;
}

const STATUS_COLOR: Record<string, string> = {
  completed: "var(--crt-green)",
  running: "var(--crt-amber)",
  pending: "var(--crt-amber)",
  failed: "var(--crt-red)",
  cancelled: "var(--text-tertiary)",
};

type Props = {
  apiBase: string;
  module: string;
  jobs: JobLite[];
  authHeader?: Record<string, string>;
  onRestored?: () => void;
  /// Revert authority — the owner's own key, not merely edit rights. Same
  /// rule as the VERSIONS panel: an editor changes the module, only the owner
  /// rolls it back. Omitted → the versions response answers it.
  canRevert?: boolean;
  /// apiBase-relative fetch that raises the Sudo sheet on 401 {sudo_required}.
  sudoFetch?: (path: string, init?: RequestInit) => Promise<Response>;
};

export function EditsPanel({ apiBase, module, jobs, authHeader, onRestored, canRevert, sudoFetch }: Props) {
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [serverCanRevert, setServerCanRevert] = useState<boolean | null>(null);
  const mayRevert = canRevert ?? serverCanRevert ?? false;
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!module) return;
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
  }, [apiBase, module]);

  useEffect(() => {
    setLoading(true);
    setVersions([]);
    setExpanded(null);
    load();
  }, [module, load]);

  const moduleJobs = useMemo(
    () => jobs.filter((j) => j.work_dir && moduleOfWorkDir(j.work_dir) === module),
    [jobs, module],
  );

  // Refetch versions when a job for this module completes — the API appends
  // the version record right after flipping the status, so grab it now and
  // once more shortly after in case the snapshot write is still in flight.
  const completedIds = useMemo(
    () => moduleJobs.filter((j) => j.status === "completed").map((j) => j.id).join(","),
    [moduleJobs],
  );
  const firstSeen = useRef(true);
  useEffect(() => {
    if (firstSeen.current) { firstSeen.current = false; return; }
    load();
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [completedIds, load]);

  const rows = useMemo<Row[]>(() => {
    const byJob = new Map<string, VersionRecord>();
    for (const v of versions) if (v.job_id) byJob.set(v.job_id, v);
    const out: Row[] = moduleJobs.map((j) => ({
      key: `job:${j.id}`,
      timestamp: j.updated_at || j.created_at,
      job: j,
      version: byJob.get(j.id),
    }));
    const jobIds = new Set(moduleJobs.map((j) => j.id));
    for (const v of versions) {
      if (v.job_id && jobIds.has(v.job_id)) continue; // already shown on its job row
      out.push({ key: `ver:${v.cid}:${v.timestamp}`, timestamp: v.timestamp, version: v });
    }
    return out.sort((a, b) => b.timestamp - a.timestamp);
  }, [moduleJobs, versions]);

  const restore = async (cid: string) => {
    if (!confirm(`Restore ${module} to ${cid.slice(0, 12)}…?\n\nCurrent state will be auto-snapshotted first.`)) return;
    setError(null);
    setStatus(null);
    try {
      const path = `/modules/${encodeURIComponent(module)}/restore`;
      const r = sudoFetch
        ? await sudoFetch(path, { method: "POST", body: JSON.stringify({ cid }) })
        : await fetch(`${apiBase}${path}`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...(authHeader || {}) },
            body: JSON.stringify({ cid }),
          });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setStatus(`restored to ${cid.slice(0, 10)}… (${d.file_count} files)`);
      await load();
      onRestored?.();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const editCount = rows.filter((r) => r.job).length;

  return (
    <BentoGrid>
      <Bento
        title={`edits · ${module} · ${editCount} edit${editCount !== 1 ? "s" : ""} · ${versions.length} version${versions.length !== 1 ? "s" : ""}`}
        span={3}
        action={
          <span className="bento-sub" style={{ opacity: 0.7 }}>
            each edit → localfs snapshot → cid
          </span>
        }
      >
        {status && <div className="bento-sub" style={{ marginBottom: 6, color: "var(--crt-green)" }}>✓ {status}</div>}
        {error && <div className="bento-sub" style={{ marginBottom: 6, color: "var(--crt-red)" }}>✗ {error}</div>}
        {loading && <div className="bento-sub">loading…</div>}
        {!loading && rows.length === 0 && (
          <div className="bento-sub">
            no edits yet — run an edit task against this module and its new version lands here with a CID
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          {rows.map((row) => {
            const j = row.job;
            const v = row.version;
            const glyph = ACTION_GLYPH[(v?.action || (j ? "edit" : "snapshot"))] || ACTION_GLYPH.snapshot;
            const dotColor = j ? STATUS_COLOR[j.status] || glyph.color : glyph.color;
            const fileEdits = j ? parseFileEdits(j.output) : [];
            const isOpen = expanded === row.key;
            const msg = j ? humanPrompt(j.prompt) : v?.message || "(no message)";
            const meta = j
              ? `${j.status} · ${timeAgo(row.timestamp)}${fileEdits.length ? ` · ${fileEdits.length} file${fileEdits.length !== 1 ? "s" : ""}` : ""}`
              : `${glyph.label} · ${timeAgo(row.timestamp)}`;
            return (
              <div key={row.key}>
                <div
                  className="version-row"
                  style={{ cursor: j && fileEdits.length ? "pointer" : "default" }}
                  onClick={() => {
                    if (j && fileEdits.length) setExpanded(isOpen ? null : row.key);
                  }}
                >
                  <span
                    className="dot"
                    title={j ? `${glyph.label} · ${j.status}` : glyph.label}
                    style={{ background: dotColor, boxShadow: `0 0 0 2px ${dotColor}1a` }}
                  />
                  <div style={{ minWidth: 0, display: "flex", alignItems: "baseline", gap: 8, overflow: "hidden" }}>
                    <span className="msg" style={{ flex: "0 1 auto", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {msg}
                    </span>
                    <span className="meta" style={{ flex: "0 0 auto" }}>
                      {meta}
                      {v?.registry_cid && <> · <span style={{ color: "var(--accent-color)" }}>{v.registry_cid.slice(0, 8)}…</span></>}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 4, alignItems: "center" }} onClick={(e) => e.stopPropagation()}>
                    {v ? (
                      <>
                        <CidChip cid={v.cid} />
                        {/* Owner only — an editor sees the history, not the undo. */}
                        {mayRevert && (
                          <GlassButton variant="ghost" onClick={() => restore(v.cid)} title="Revert the module to this version — the owner's undo (current state is pinned first)">↺</GlassButton>
                        )}
                      </>
                    ) : (
                      <span className="meta" style={{ opacity: 0.55 }}>
                        {j?.status === "completed" ? "no changes" : j?.status === "failed" ? "failed" : j?.status === "cancelled" ? "cancelled" : "in progress…"}
                      </span>
                    )}
                    {j && fileEdits.length > 0 && (
                      <span className="meta" style={{ opacity: 0.6, width: 14, textAlign: "center" }}>{isOpen ? "▾" : "▸"}</span>
                    )}
                  </div>
                </div>
                {isOpen && j && (
                  <div
                    style={{
                      margin: "2px 0 6px 22px",
                      padding: "8px 10px",
                      border: "1px solid var(--glass-border)",
                      borderRadius: 8,
                      background: "var(--glass-bg-strong, rgba(0,0,0,0.25))",
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                      maxHeight: 320,
                      overflowY: "auto",
                    }}
                  >
                    {fileEdits.map((e, i) => (
                      <div key={i} style={{ minWidth: 0 }}>
                        <div className="bento-sub" style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                          <span style={{ color: "var(--crt-amber)", textTransform: "uppercase", fontSize: 11 }}>{e.kind}</span>
                          <span style={{ color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.file}</span>
                          <span style={{ opacity: 0.6 }}>
                            {e.kind === "write"
                              ? `${e.lineCount} lines`
                              : `−${e.removed.length} +${e.added.length}`}
                          </span>
                        </div>
                        {(e.removed.length > 0 || e.added.length > 0) && (
                          <pre
                            style={{
                              margin: "4px 0 0",
                              fontSize: 12,
                              lineHeight: 1.45,
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                            }}
                          >
                            {e.removed.map((l, k) => (
                              <span key={`r${k}`} style={{ color: "var(--crt-red)" }}>- {l}{"\n"}</span>
                            ))}
                            {e.added.map((l, k) => (
                              <span key={`a${k}`} style={{ color: "var(--accent-color)" }}>+ {l}{"\n"}</span>
                            ))}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Bento>
    </BentoGrid>
  );
}
