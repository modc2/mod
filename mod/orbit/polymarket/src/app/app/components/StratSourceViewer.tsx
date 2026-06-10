"use client";

// STRATS-tab source viewer.
//
// Two source surfaces in one panel:
//   - Built-in TS strats (base / copytrader / registry) — read-only.
//     Pulled in via the webpack `?raw` rule wired in next.config.mjs.
//   - User-uploaded mod.py / mod.rs strats — editable, persisted through
//     /api/polymarket/user-strats POST (same endpoint UserStratsPanel uses).
//
// Read-only vs editable is decided by `editable` on the entry, NOT by
// kind, so a future "share a built-in fork as editable" flow can drop in
// without changing the component.

import { useCallback, useEffect, useMemo, useState } from "react";

import baseSrc from "../lib/strats/base.ts?raw";
import copytraderSrc from "../lib/strats/copytrader.ts?raw";
import registrySrc from "../lib/strats/registry.ts?raw";

type StratKind = "py" | "rs" | "ts";

interface ViewerStrat {
  id: string;
  kind: StratKind;
  source: string;
  editable: boolean;
  label: string;
  updatedAt?: number;
}

// `?raw` should resolve to a string via the webpack rule in next.config.mjs,
// but if the rule doesn't apply (e.g. Turbopack, or extension-resolution
// hiccup), coerce to "" so the viewer degrades to an empty pane instead of
// crashing on `.length`.
const asSource = (v: unknown): string => (typeof v === "string" ? v : "");

const BUILTIN: ViewerStrat[] = [
  { id: "copytrader", kind: "ts", source: asSource(copytraderSrc), editable: false, label: "copytrader — reference impl" },
  { id: "base",       kind: "ts", source: asSource(baseSrc),       editable: false, label: "base — Strat interface" },
  { id: "registry",   kind: "ts", source: asSource(registrySrc),   editable: false, label: "registry — active binding" },
];

const keyOf = (s: { id: string; kind: StratKind }) => `${s.id}.${s.kind}`;

export default function StratSourceViewer() {
  const [userStrats, setUserStrats] = useState<ViewerStrat[]>([]);
  const [selected, setSelected] = useState<string>(keyOf(BUILTIN[0]));
  const [draft, setDraft] = useState<string>(BUILTIN[0].source);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/polymarket/user-strats", { cache: "no-store" });
      if (!r.ok) return;
      const j = (await r.json()) as {
        strats?: Array<{ id: string; kind: StratKind; updatedAt: number }>;
      };
      const entries = await Promise.all(
        (j.strats ?? []).map(async (s): Promise<ViewerStrat | null> => {
          try {
            const rr = await fetch(
              `/api/polymarket/user-strats/${encodeURIComponent(s.id)}/${s.kind}`,
              { cache: "no-store" },
            );
            if (!rr.ok) return null;
            const jj = (await rr.json()) as { content?: string };
            if (jj.content == null) return null;
            return {
              id: s.id,
              kind: s.kind,
              source: jj.content,
              editable: true,
              label: `${s.id}.${s.kind}`,
              updatedAt: s.updatedAt,
            };
          } catch {
            return null;
          }
        }),
      );
      setUserStrats(entries.filter((e): e is ViewerStrat => e !== null));
    } catch {}
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const all = useMemo(() => [...BUILTIN, ...userStrats], [userStrats]);
  const current = useMemo(
    () => all.find((s) => keyOf(s) === selected) ?? all[0],
    [all, selected],
  );

  // Re-seat the draft whenever the selected strat (or its persisted source) changes.
  useEffect(() => {
    setDraft(current.source);
    setStatus(null);
    setError(null);
  }, [current.id, current.kind, current.source]);

  const dirty = current.editable && draft !== current.source;

  const save = useCallback(async () => {
    if (!current.editable) return;
    setBusy(true);
    setStatus(null);
    setError(null);
    try {
      const r = await fetch("/api/polymarket/user-strats", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: current.id, kind: current.kind, content: draft }),
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
      setStatus(`Saved ${current.id}.${current.kind} ✓`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [current, draft, refresh]);

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs uppercase tracking-wide text-pixel-muted">
          Strat Source
        </span>
        <span className="text-[10px] text-pixel-muted">
          Built-in strats ship in the bundle (read-only). Strats you upload below are editable in place.
        </span>
      </div>

      {/* Strat picker — built-ins first, then user-owned. */}
      <div className="flex flex-wrap gap-1.5">
        {all.map((s) => {
          const k = keyOf(s);
          const active = k === selected;
          return (
            <button
              key={k}
              onClick={() => setSelected(k)}
              className={`text-[11px] px-2 py-1 border rounded font-mono whitespace-nowrap transition-colors ${
                active
                  ? "border-green-400 text-green-400 bg-green-400/10"
                  : "border-pixel-border text-pixel-gray hover:bg-pixel-border-light"
              }`}
              title={s.editable ? "Owned — editable" : "Built-in — read-only"}
            >
              {s.label}
              <span className="ml-1 text-pixel-muted">
                {s.editable ? "·edit" : "·ro"}
              </span>
            </button>
          );
        })}
      </div>

      {/* Source pane */}
      <div className="relative">
        <textarea
          value={current.editable ? draft : current.source}
          readOnly={!current.editable}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          className="w-full h-[460px] bg-pixel-bg border border-pixel-border rounded p-2 font-mono text-[11px] leading-[1.45] outline-none resize-y whitespace-pre overflow-auto"
        />
        <div className="absolute top-1.5 right-2.5 text-[10px] text-pixel-muted pointer-events-none font-mono">
          {current.id}.{current.kind} · {(current.source.length / 1024).toFixed(1)} KB
          {current.editable ? "" : " · readonly"}
        </div>
      </div>

      {/* Save row — only when the selected strat is owned. */}
      {current.editable && (
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={save}
            disabled={busy || !dirty}
            className="px-3 py-1 bg-green-700 hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded"
          >
            {busy ? "SAVING…" : "SAVE"}
          </button>
          <button
            onClick={() => setDraft(current.source)}
            disabled={busy || !dirty}
            className="px-3 py-1 border border-pixel-border text-pixel-gray rounded text-xs hover:bg-pixel-border-light disabled:opacity-50"
          >
            DISCARD
          </button>
          {dirty && <span className="text-[10px] text-yellow-400 font-mono">● unsaved</span>}
          {status && <span className="text-[10px] text-green-400 font-mono">{status}</span>}
          {error && <span className="text-[10px] text-red-400 font-mono break-all">{error}</span>}
        </div>
      )}

      {userStrats.length === 0 && (
        <div className="text-[10px] text-pixel-muted">
          No uploaded strats yet — upload a mod.py / mod.rs on the LIVE → PARAMS tab to see it appear here, editable.
        </div>
      )}
    </div>
  );
}
