"use client";

// Upload + manage user-written strats (mod.py / mod.rs) AND share them.
//
// The Polymarket engine ships a Python `Strat` base class in
// `src/strats/base.py` plus a reference `copytrader.py`. This panel has
// three sections:
//   1. MY STRATS   — your uploads. Make each public/private, download,
//                    delete, view source. Public ones appear in every
//                    trader's community gallery.
//   2. UPLOAD      — stage a new strat (file or paste) with a title +
//                    description, optionally publishing immediately.
//   3. COMMUNITY   — public strats shared by other traders. Fork any of
//                    them into your own list (lineage tracked) and tweak.
//
// Ownership is keyed on the connected wallet (`eoa`). The actual runtime
// that loads + executes uploaded code is a follow-up — for now this gives
// traders a place to publish, discover, and fork strategies.

import { useCallback, useEffect, useRef, useState } from "react";

type StratKind = "py" | "rs";

interface UserStratEntry {
  id: string;
  kind: StratKind;
  size: number;
  updatedAt: number;
  owner: string;
  title: string;
  description: string;
  public: boolean;
  forkedFrom?: string;
  createdAt: number;
  forks: number;
  mine: boolean;
}

function fmtTime(secs: number): string {
  if (!secs) return "—";
  const d = new Date(secs * 1000);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(2)} MB`;
}

function shortAddr(a: string): string {
  if (!a) return "anon";
  if (a.length <= 12) return a;
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

async function readError(r: Response): Promise<string> {
  const text = await r.text();
  let detail = text.slice(0, 200);
  try {
    const j = JSON.parse(text) as { error?: string };
    if (j.error) detail = j.error.slice(0, 200);
  } catch {}
  return detail;
}

function downloadSource(id: string, kind: StratKind, content: string) {
  const blob = new Blob([content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${id}.${kind}`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function UserStratsPanel({ eoa }: { eoa?: string }) {
  const owner = (eoa || "").toLowerCase();

  const [mine, setMine] = useState<UserStratEntry[]>([]);
  const [community, setCommunity] = useState<UserStratEntry[]>([]);
  // Toggle between managing your own strats (upload/publish/fork) and the
  // community hub (observe/fork other traders' public strats).
  const [hubTab, setHubTab] = useState<"mine" | "hub">("mine");

  // Upload form
  const [id, setId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<StratKind>("py");
  const [content, setContent] = useState("");
  const [makePublic, setMakePublic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ id: string; kind: StratKind; src: string } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const mineUrl = owner
        ? `/api/polymarket/user-strats?owner=${encodeURIComponent(owner)}`
        : "/api/polymarket/user-strats";
      const pubUrl = owner
        ? `/api/polymarket/user-strats/public?owner=${encodeURIComponent(owner)}`
        : "/api/polymarket/user-strats/public";
      const [rm, rp] = await Promise.all([
        fetch(mineUrl, { cache: "no-store" }),
        fetch(pubUrl, { cache: "no-store" }),
      ]);
      if (rm.ok) {
        const j = (await rm.json()) as { strats?: UserStratEntry[] };
        setMine(j.strats ?? []);
      }
      if (rp.ok) {
        const j = (await rp.json()) as { strats?: UserStratEntry[] };
        // Hub = every public strat. Keep your own published ones in the list
        // too (marked YOURS) so you can always confirm what's live and toggle
        // it back to private — others' strats sort first for discovery.
        const all = j.strats ?? [];
        all.sort((a, b) => Number(a.mine) - Number(b.mine) || b.forks - a.forks);
        setCommunity(all);
      }
    } catch {}
  }, [owner]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleFile = useCallback(async (file: File) => {
    setError(null);
    const text = await file.text();
    setContent(text);
    if (!id) {
      const base = file.name.replace(/\.(py|rs)$/, "").replace(/[^a-zA-Z0-9_-]/g, "_");
      setId(base.slice(0, 64));
    }
    if (file.name.endsWith(".rs")) setKind("rs");
    else if (file.name.endsWith(".py")) setKind("py");
  }, [id]);

  const handleUpload = useCallback(async () => {
    setError(null);
    setStatus(null);
    if (!id.trim()) {
      setError("Pick a short ID (a-z, 0-9, -, _).");
      return;
    }
    if (!content.trim()) {
      setError("Pick a file or paste source code first.");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/polymarket/user-strats", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: id.trim(),
          kind,
          content,
          owner,
          title: title.trim() || id.trim(),
          description: description.trim(),
          public: makePublic,
        }),
      });
      if (!r.ok) throw new Error(await readError(r));
      setStatus(`Uploaded ${id}.${kind}${makePublic ? " · published ✓" : " ✓"}`);
      setContent("");
      setId("");
      setTitle("");
      setDescription("");
      setMakePublic(false);
      if (fileRef.current) fileRef.current.value = "";
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [id, kind, content, owner, title, description, makePublic, refresh]);

  const handleDelete = useCallback(async (s: UserStratEntry) => {
    if (!confirm(`Delete strat "${s.id}"? This removes the file from disk.`)) return;
    setError(null);
    try {
      const r = await fetch(
        `/api/polymarket/user-strats/${encodeURIComponent(s.id)}/${s.kind}?owner=${encodeURIComponent(owner)}`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(await readError(r));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [refresh, owner]);

  const handleTogglePublic = useCallback(async (s: UserStratEntry) => {
    setError(null);
    setStatus(null);
    try {
      const r = await fetch(
        `/api/polymarket/user-strats/${encodeURIComponent(s.id)}/publish`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ owner, public: !s.public }),
        },
      );
      if (!r.ok) throw new Error(await readError(r));
      setStatus(s.public ? `"${s.title}" is now private` : `"${s.title}" is now public ✓`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [refresh, owner]);

  const fetchSource = useCallback(async (s: UserStratEntry): Promise<string | null> => {
    try {
      const r = await fetch(
        `/api/polymarket/user-strats/${encodeURIComponent(s.id)}/${s.kind}`,
      );
      if (!r.ok) return null;
      const j = (await r.json()) as { content?: string };
      return j.content ?? null;
    } catch {
      return null;
    }
  }, []);

  const handleDownload = useCallback(async (s: UserStratEntry) => {
    const src = await fetchSource(s);
    if (src != null) downloadSource(s.id, s.kind, src);
  }, [fetchSource]);

  const handleView = useCallback(async (s: UserStratEntry) => {
    const src = await fetchSource(s);
    if (src != null) setViewing({ id: s.id, kind: s.kind, src });
  }, [fetchSource]);

  const handleFork = useCallback(async (s: UserStratEntry) => {
    if (!owner) {
      setError("Connect a wallet to fork strats.");
      return;
    }
    const suggested = `${s.id}-fork`.slice(0, 64).replace(/[^a-zA-Z0-9_-]/g, "-");
    const newId = prompt(`Fork "${s.title}" — pick an ID for your copy:`, suggested);
    if (!newId) return;
    const cleaned = newId.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64);
    if (!cleaned) {
      setError("Fork ID must be a-z, 0-9, -, _.");
      return;
    }
    setError(null);
    setStatus(null);
    try {
      const r = await fetch(
        `/api/polymarket/user-strats/${encodeURIComponent(s.id)}/fork`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ newId: cleaned, owner }),
        },
      );
      if (!r.ok) throw new Error(await readError(r));
      setStatus(`Forked → "${cleaned}" (private). Edit + publish when ready.`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [owner, refresh]);

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs uppercase tracking-wide text-pixel-muted">
          Strategy Hub
        </span>
        <span className="text-[10px] text-pixel-muted">
          Upload, publish, and fork copy-trading strats.
        </span>
      </div>

      {/* ── Tabs: MY STRATS (upload/manage) vs HUB (community/fork) ── */}
      <div className="flex border-b border-pixel-border/60">
        {([
          ["mine", `My Strats${mine.length ? ` (${mine.length})` : ""}`],
          ["hub", `Hub${community.length ? ` (${community.length})` : ""}`],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setHubTab(id)}
            className={`px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide border-b-2 -mb-px transition-all duration-150 ${
              hubTab === id
                ? "border-green-400 text-green-400 glow-green"
                : "border-transparent text-pixel-muted hover:text-pixel-white"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── MY STRATS ── */}
      {hubTab === "mine" && mine.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] uppercase tracking-wide text-pixel-muted">My strats</div>
          {mine.map((s) => (
            <div
              key={`${s.id}-${s.kind}`}
              className="border border-pixel-border/40 rounded-[var(--radius-sm)] px-2.5 py-2 space-y-1 transition-all duration-150 hover:border-green-400/30 hover:bg-pixel-white/[0.03]"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-bold truncate">{s.title}</div>
                  <div className="text-[10px] text-pixel-muted font-mono truncate">
                    {s.id}.{s.kind} · {fmtSize(s.size)} · {fmtTime(s.updatedAt)}
                    {s.forkedFrom && <> · forked from <span className="text-pixel-white">{s.forkedFrom}</span></>}
                  </div>
                </div>
                <span
                  className={`text-[9px] px-1.5 py-0.5 rounded whitespace-nowrap border ${
                    s.public
                      ? "border-green-400/50 text-green-400"
                      : "border-pixel-border text-pixel-muted"
                  }`}
                >
                  {s.public ? `PUBLIC · ${s.forks}⑂` : "PRIVATE"}
                </span>
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                <button
                  onClick={() => handleTogglePublic(s)}
                  className={`text-[10px] px-2 py-0.5 rounded border ${
                    s.public
                      ? "border-pixel-border hover:bg-pixel-border-light"
                      : "border-green-400/50 text-green-400 hover:bg-green-400/10"
                  }`}
                >
                  {s.public ? "MAKE PRIVATE" : "MAKE PUBLIC"}
                </button>
                <button
                  onClick={() => handleView(s)}
                  className="text-[10px] px-2 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
                >
                  VIEW
                </button>
                <button
                  onClick={() => handleDownload(s)}
                  className="text-[10px] px-2 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
                >
                  ⬇
                </button>
                <button
                  onClick={() => handleDelete(s)}
                  className="text-[10px] px-2 py-0.5 border border-red-400/40 text-red-400 rounded hover:bg-red-400/10"
                >
                  DEL
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── UPLOAD (My Strats tab) ── */}
      {hubTab === "mine" && (
      <div className="border border-pixel-border rounded p-2 space-y-2">
        <div className="text-[10px] uppercase tracking-wide text-pixel-muted">Upload a strat</div>
        <div className="flex gap-2 flex-wrap">
          <input
            type="text"
            placeholder="strat id (a-z, 0-9, _-)"
            value={id}
            onChange={(e) => setId(e.target.value.replace(/[^a-zA-Z0-9_-]/g, ""))}
            className="bg-pixel-bg border border-pixel-border rounded px-2 py-1 flex-1 min-w-[120px] font-mono text-xs outline-none"
            disabled={busy}
          />
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as StratKind)}
            className="bg-pixel-bg border border-pixel-border rounded px-2 py-1 font-mono text-xs outline-none"
            disabled={busy}
          >
            <option value="py">mod.py</option>
            <option value="rs">mod.rs</option>
          </select>
        </div>
        <input
          type="text"
          placeholder="title (shown in the community gallery)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={120}
          className="bg-pixel-bg border border-pixel-border rounded px-2 py-1 w-full text-xs outline-none"
          disabled={busy}
        />
        <textarea
          placeholder="description — what edge does this strat capture?"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={2000}
          rows={2}
          className="bg-pixel-bg border border-pixel-border rounded px-2 py-1 w-full text-xs outline-none resize-y"
          disabled={busy}
        />
        <div className="flex gap-2 items-center">
          <input
            ref={fileRef}
            type="file"
            accept=".py,.rs"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleFile(f);
            }}
            disabled={busy}
            className="text-xs flex-1"
          />
          <label className="flex items-center gap-1 text-[10px] text-pixel-muted cursor-pointer whitespace-nowrap">
            <input
              type="checkbox"
              checked={makePublic}
              onChange={(e) => setMakePublic(e.target.checked)}
              disabled={busy}
            />
            publish now
          </label>
          <button
            onClick={handleUpload}
            disabled={busy || !content || !id}
            className="px-3 py-1 bg-green-700 hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded"
          >
            UPLOAD
          </button>
        </div>
        {content && (
          <div className="text-[10px] text-pixel-muted font-mono">
            {content.length.toLocaleString()} chars loaded — ready to upload
          </div>
        )}
        {!owner && (
          <div className="text-[10px] text-yellow-400/80">
            Connect a wallet to claim ownership + publish.
          </div>
        )}
      </div>
      )}

      {status && <div className="text-xs text-green-400 font-mono break-all">{status}</div>}
      {error && <div className="text-xs text-red-400 font-mono break-all">{error}</div>}

      {/* ── COMMUNITY GALLERY (Hub tab) ── */}
      {hubTab === "hub" && (
      <div className="border border-pixel-border rounded p-2 space-y-1.5">
        <div className="flex items-center justify-between">
          <div className="text-[10px] uppercase tracking-wide text-pixel-muted">
            Community strats
          </div>
          <button
            onClick={refresh}
            className="text-[10px] px-1.5 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
          >
            ↻
          </button>
        </div>
        {community.length === 0 ? (
          <div className="text-[10px] text-pixel-muted">
            No public strats yet. Publish one from the <b>My Strats</b> tab to share it with other traders.
          </div>
        ) : (
          community.map((s) => (
            <div
              key={`${s.id}-${s.kind}`}
              className="border border-pixel-border/40 rounded-[var(--radius-sm)] px-2.5 py-2 space-y-1 transition-all duration-150 hover:border-green-400/30 hover:bg-pixel-white/[0.03]"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-bold truncate">{s.title}</div>
                  <div className="text-[10px] text-pixel-muted font-mono truncate">
                    by {s.mine ? "you" : shortAddr(s.owner)} · {s.id}.{s.kind} · {s.forks}⑂
                    {s.forkedFrom && <> · forked from {s.forkedFrom}</>}
                  </div>
                </div>
                {s.mine && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded border border-green-400/50 text-green-400 whitespace-nowrap">
                    YOURS
                  </span>
                )}
              </div>
              {s.description && (
                <div className="text-[10px] text-pixel-gray line-clamp-2">{s.description}</div>
              )}
              <div className="flex items-center gap-1.5 flex-wrap">
                {s.mine ? (
                  <button
                    onClick={() => handleTogglePublic(s)}
                    className="text-[10px] px-2 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
                    title="Unpublish — remove from the hub"
                  >
                    MAKE PRIVATE
                  </button>
                ) : (
                  <button
                    onClick={() => handleFork(s)}
                    className="text-[10px] px-2 py-0.5 border border-blue-400/50 text-blue-400 rounded hover:bg-blue-400/10"
                  >
                    ⑂ FORK
                  </button>
                )}
                <button
                  onClick={() => handleView(s)}
                  className="text-[10px] px-2 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
                >
                  VIEW
                </button>
                <button
                  onClick={() => handleDownload(s)}
                  className="text-[10px] px-2 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
                >
                  ⬇
                </button>
              </div>
            </div>
          ))
        )}
      </div>
      )}

      {/* ── SOURCE VIEWER ── */}
      {viewing && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
          onClick={() => setViewing(null)}
        >
          <div
            className="pixel-panel border-2 border-pixel-border max-w-3xl w-full max-h-[80vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-3 py-2 border-b border-pixel-border">
              <span className="text-xs font-mono">{viewing.id}.{viewing.kind}</span>
              <button
                onClick={() => setViewing(null)}
                className="text-xs px-2 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
              >
                CLOSE
              </button>
            </div>
            <pre className="overflow-auto p-3 text-[11px] font-mono whitespace-pre leading-snug">
              {viewing.src}
            </pre>
          </div>
        </div>
      )}

      {/* ── TEMPLATES (My Strats tab) ── */}
      {hubTab === "mine" && (
      <div className="border border-pixel-border rounded p-2 space-y-1.5">
        <div className="text-xs uppercase tracking-wide text-pixel-muted">
          Start from a template
        </div>
        <div className="flex gap-2 flex-wrap">
          {[
            { name: "example_ev_strat", label: "EV-gated copy (recommended)" },
            { name: "copytrader",       label: "Minimal copy" },
            { name: "base",             label: "Strat interface only" },
          ].map((t) => (
            <button
              key={t.name}
              onClick={async () => {
                try {
                  const r = await fetch(`/api/polymarket/user-strats/template/${t.name}`);
                  if (!r.ok) return;
                  const j = (await r.json()) as { content?: string };
                  if (!j.content) return;
                  downloadSource(t.name, "py", j.content);
                } catch {}
              }}
              className="text-[11px] px-2 py-1 border border-pixel-border rounded hover:bg-pixel-border-light font-mono"
              title={`Download ${t.name}.py to edit + re-upload`}
            >
              ⬇ {t.label}
            </button>
          ))}
        </div>
      </div>
      )}

      <details className="text-[10px] text-pixel-muted">
        <summary className="cursor-pointer">How does this work?</summary>
        <div className="mt-1 space-y-1.5">
          <p>
            Strats are Python files. Subclass{" "}
            <code className="font-mono">Strat</code> from{" "}
            <code className="font-mono">src/strats/base.py</code> and
            implement two methods:
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              <code className="font-mono">signal(sync) → list[Order]</code>{" "}
              — pure function. Given the latest sync snapshot (recent leader
              trades, your wallet balance, your open positions), return the
              orders you want fired this tick.
            </li>
            <li>
              <code className="font-mono">backtest(history) → BacktestResult</code>{" "}
              — replay your signal logic over a historical trade list and
              return the PnL curve + fees + ROI. Powers the BACKTEST tab.
            </li>
          </ul>
          <p>
            <b>Sharing:</b> flip a strat <b>public</b> and it shows up in every
            trader&apos;s Community list. Anyone can <b>fork</b> a public strat
            into their own (the copy starts private and records what it was
            forked from), tweak it, and re-publish. Your wallet address is the
            owner — only you can edit, delete, or change visibility of your own
            strats.
          </p>
          <p>
            Engine runtime hookup (loading + executing your code in the live
            tick loop) lands next — uploads persist on the data volume so they
            survive container restarts.
          </p>
        </div>
      </details>
    </div>
  );
}
