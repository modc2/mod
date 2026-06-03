"use client";

// Upload + manage user-written strats (mod.py / mod.rs).
//
// The Polymarket engine ships a Python `Strat` base class in
// `src/strats/base.py` plus a reference `copytrader.py`. This panel
// surfaces the *file* side of that: pick a `.py` (or `.rs`) from disk,
// give it a short id, click UPLOAD. Listed strats can be re-downloaded
// (for editing) or deleted. The actual runtime that loads + executes
// uploaded code is a follow-up — for now this gives users a place to
// stage strats so the engine can wire them up in a later release.

import { useCallback, useEffect, useRef, useState } from "react";

type StratKind = "py" | "rs";

interface UserStratEntry {
  id: string;
  kind: StratKind;
  size: number;
  updatedAt: number;
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

export default function UserStratsPanel() {
  const [strats, setStrats] = useState<UserStratEntry[]>([]);
  const [id, setId] = useState("");
  const [kind, setKind] = useState<StratKind>("py");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/polymarket/user-strats", { cache: "no-store" });
      if (r.ok) {
        const j = (await r.json()) as { strats?: UserStratEntry[] };
        setStrats(j.strats ?? []);
      }
    } catch {}
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleFile = useCallback(async (file: File) => {
    setError(null);
    const text = await file.text();
    setContent(text);
    // Auto-fill ID from filename if user hasn't typed one yet.
    if (!id) {
      const base = file.name.replace(/\.(py|rs)$/, "").replace(/[^a-zA-Z0-9_-]/g, "_");
      setId(base.slice(0, 64));
    }
    // Auto-detect kind from file extension.
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
        body: JSON.stringify({ id: id.trim(), kind, content }),
      });
      const text = await r.text();
      if (!r.ok) {
        let detail = text.slice(0, 200);
        try {
          const j = JSON.parse(text) as { error?: string };
          if (j.error) detail = j.error.slice(0, 200);
        } catch {}
        throw new Error(detail);
      }
      setStatus(`Uploaded ${id}.${kind} ✓`);
      setContent("");
      setId("");
      if (fileRef.current) fileRef.current.value = "";
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [id, kind, content, refresh]);

  const handleDelete = useCallback(async (s: UserStratEntry) => {
    if (!confirm(`Delete strat "${s.id}"? This removes the file from disk.`)) return;
    try {
      const r = await fetch(
        `/api/polymarket/user-strats/${encodeURIComponent(s.id)}/${s.kind}`,
        { method: "DELETE" },
      );
      if (r.ok) await refresh();
    } catch {}
  }, [refresh]);

  const handleDownload = useCallback(async (s: UserStratEntry) => {
    try {
      const r = await fetch(
        `/api/polymarket/user-strats/${encodeURIComponent(s.id)}/${s.kind}`,
      );
      if (!r.ok) return;
      const j = (await r.json()) as { content?: string };
      if (!j.content) return;
      const blob = new Blob([j.content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${s.id}.${s.kind}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {}
  }, []);

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs uppercase tracking-wide text-pixel-muted">
          Custom Strats
        </span>
        <span className="text-[10px] text-pixel-muted">
          Upload your own mod.py / mod.rs against the Strat interface.
        </span>
      </div>

      {/* Existing strats */}
      {strats.length > 0 && (
        <div className="space-y-1 text-xs">
          {strats.map((s) => (
            <div
              key={`${s.id}-${s.kind}`}
              className="flex items-center justify-between gap-2 border border-pixel-border/40 rounded px-2 py-1"
            >
              <span className="font-mono truncate flex-1">
                {s.id}<span className="text-pixel-muted">.{s.kind}</span>
              </span>
              <span className="text-[10px] text-pixel-muted whitespace-nowrap">
                {fmtSize(s.size)} · {fmtTime(s.updatedAt)}
              </span>
              <button
                onClick={() => handleDownload(s)}
                className="text-[10px] px-1.5 py-0.5 border border-pixel-border rounded hover:bg-pixel-border-light"
              >
                ⬇
              </button>
              <button
                onClick={() => handleDelete(s)}
                className="text-[10px] px-1.5 py-0.5 border border-red-400/40 text-red-400 rounded hover:bg-red-400/10"
              >
                DEL
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Upload form */}
      <div className="border border-pixel-border rounded p-2 space-y-2">
        <div className="flex gap-2 flex-wrap">
          <input
            type="text"
            placeholder="strat id (a-z, 0-9, _-)"
            value={id}
            onChange={(e) => setId(e.target.value.replace(/[^a-zA-Z0-9_-]/g, ""))}
            className="bg-pixel-bg border border-pixel-border rounded px-2 py-1 flex-1 font-mono text-xs outline-none"
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
      </div>

      {status && <div className="text-xs text-green-400 font-mono">{status}</div>}
      {error && <div className="text-xs text-red-400 font-mono break-all">{error}</div>}

      {/* Forkable templates — download a working example to edit. */}
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
                  const blob = new Blob([j.content], { type: "text/plain" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `${t.name}.py`;
                  a.click();
                  URL.revokeObjectURL(url);
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
            Defaults for{" "}
            <code className="font-mono">setup / sync / execute / teardown</code>{" "}
            are inherited from the base class. The
            <b> EV-gated copy</b> template above is the recommended starting
            point — it shows the full skeleton plus a working{" "}
            <code className="font-mono">_ev_per_trade()</code> hook that
            you can replace with your own edge formula.
          </p>
          <p>
            Upload your edited <code>mod.py</code> here for storage. Engine
            runtime hookup (loading + executing your code in the live tick
            loop) lands next — uploads persist on the data volume so they
            survive container restarts.
          </p>
        </div>
      </details>
    </div>
  );
}
