"use client";

// MODELS — every LFM Liquid publishes, one row per model rather than per
// repo, with the question the whole module is about answered in the second
// column: where can this thing run.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { KindBadge, RoleBadge, RuntimeBadges, fmtCount, params } from "./components/badges";
import { fetchCatalog } from "./lib/api";
import type { Catalog, Model } from "./lib/types";

const RUNTIME_FILTERS = [
  { id: "", label: "ALL" },
  { id: "browser", label: "BROWSER" },
  { id: "server", label: "SERVER" },
  { id: "edge", label: "EDGE" },
];

type SortKey = "downloads" | "id" | "params_b" | "updated";

const SORTS: { id: SortKey; label: string; align: string }[] = [
  { id: "id", label: "MODEL", align: "text-left" },
  { id: "params_b", label: "PARAMS", align: "text-right" },
  { id: "downloads", label: "PULLS", align: "text-right" },
];

export default function CatalogPage() {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [runtime, setRuntime] = useState("");
  const [kind, setKind] = useState("");
  const [family, setFamily] = useState("");
  const [q, setQ] = useState("");
  // Downloads first: "what is everyone actually using" is the question a
  // stranger brings to a list of fifty models they've never heard of.
  const [sort, setSort] = useState<SortKey>("downloads");
  const [asc, setAsc] = useState(false);

  useEffect(() => {
    fetchCatalog({ limit: "500" }).then(setCat).catch((e) => setErr(String(e)));
  }, []);

  // Filtering is local: 51 rows is nothing, and a round trip per chip press
  // would make the board feel like a form instead of a console.
  const rows = useMemo(() => {
    if (!cat) return [];
    const needle = q.trim().toLowerCase();
    const kept = cat.models.filter((m) =>
      (!runtime || m.runtimes.includes(runtime)) &&
      (!kind || m.kind === kind) &&
      (!family || m.family === family) &&
      (!needle || m.id.toLowerCase().includes(needle) || (m.role ?? "").includes(needle)),
    );
    const dir = asc ? 1 : -1;
    return [...kept].sort((a, b) => {
      if (sort === "id") return dir * a.id.localeCompare(b.id);
      if (sort === "updated") return dir * String(a.updated ?? "").localeCompare(String(b.updated ?? ""));
      return dir * ((a[sort] ?? 0) - (b[sort] ?? 0));
    });
  }, [cat, runtime, kind, family, q, sort, asc]);

  // The headline facts about the list you're looking at, not about the whole
  // catalog: they move when you press a filter, which is what makes them
  // worth the strip they sit on.
  const tiles = useMemo(() => {
    const top = rows.reduce<Model | null>(
      (best, m) => (!best || m.downloads > best.downloads ? m : best), null);
    const browser = rows.filter((m) => m.runtimes.includes("browser")).length;
    const smallest = rows
      .filter((m) => m.params_b)
      .reduce<Model | null>((best, m) => (!best || m.params_b! < best.params_b! ? m : best), null);
    return { top, browser, smallest };
  }, [rows]);

  const press = (key: SortKey) => {
    if (sort === key) setAsc((a) => !a);
    else { setSort(key); setAsc(key === "id"); }
  };

  return (
    <div className="flex flex-col gap-2 min-h-0">
      {/* The marquee doubles as the filter rail — a separate filter panel
          under it would be a second band of chrome saying nothing new. */}
      <div className="page-head">
        <div className="page-head-band !py-2 !px-3">
          <h1 className="font-display text-sm sm:text-base whitespace-nowrap">
            MODELS
          </h1>
          <span className="font-mono text-sm text-pixel-gray-light">
            {rows.length}/{cat?.total ?? "—"} models · {cat?.source ?? "…"}
          </span>

          <div className="flex flex-wrap items-center gap-1.5 ml-auto">
            {RUNTIME_FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setRuntime(f.id)}
                aria-pressed={runtime === f.id}
                className={`pixel-btn topbar-ctl px-2.5 ${runtime === f.id ? "nav-active" : ""}`}
              >
                {f.label}
              </button>
            ))}
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              className="pixel-input-sm topbar-ctl"
              aria-label="task"
            >
              <option value="">TASK</option>
              {(cat?.kinds ?? []).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <select
              value={family}
              onChange={(e) => setFamily(e.target.value)}
              className="pixel-input-sm topbar-ctl"
              aria-label="family"
            >
              <option value="">GEN</option>
              {(cat?.families ?? []).map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="filter…"
              aria-label="filter"
              className="pixel-input-sm topbar-ctl font-mono w-[110px] sm:w-[150px]"
            />
          </div>
        </div>
      </div>

      {err && (
        <div className="pixel-panel pixel-panel-red p-3 font-mono text-sm text-red-400">
          catalog unreachable — {err}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <button
          onClick={() => { setSort("downloads"); setAsc(false); }}
          className="stat-tile stat-tile-accent text-left"
          title="sort the board by pulls"
        >
          <span className="stat-tile-label">MOST PULLED</span>
          <div className="stat-tile-value !text-[22px]">{tiles.top?.id ?? "—"}</div>
          <span className="stat-tile-sub">
            {tiles.top ? `${fmtCount(tiles.top.downloads)} downloads from HuggingFace` : "…"}
          </span>
        </button>
        <div className="stat-tile stat-tile-up">
          <span className="stat-tile-label">RUNS IN A TAB</span>
          <div className="stat-tile-value">{tiles.browser}</div>
          <span className="stat-tile-sub">of {rows.length} shown · ONNX + WebGPU</span>
        </div>
        <div className="stat-tile">
          <span className="stat-tile-label">SMALLEST</span>
          <div className="stat-tile-value !text-[22px]">{tiles.smallest?.id ?? "—"}</div>
          <span className="stat-tile-sub">
            {tiles.smallest ? `${params(tiles.smallest)} parameters` : "…"}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-tile-label">GENERATIONS</span>
          <div className="stat-tile-value">{cat?.families.length ?? "—"}</div>
          <span className="stat-tile-sub">
            {(cat?.families ?? []).join(" · ") || "…"}
          </span>
        </div>
      </div>

      <div className="pixel-panel overflow-x-auto">
        <table className="pixel-table pixel-table-auto w-full">
          <thead>
            {/* `pixel-table-auto` because the default fixed layout splits the
                width evenly and clips the model names, which are the one
                column here that varies in length. */}
            <tr>
              {SORTS.map((s) => (
                <th
                  key={s.id}
                  onClick={() => press(s.id)}
                  className={`${s.align} sortable ${sort === s.id ? "sorted" : ""} ${
                    s.id === "downloads" ? "hidden sm:table-cell" : ""}`}
                  title={`sort by ${s.label.toLowerCase()}`}
                >
                  {s.label}{sort === s.id ? (asc ? " ▲" : " ▼") : ""}
                </th>
              ))}
              <th className="text-left">RUNS ON</th>
              <th className="text-left">TASK</th>
              <th className="text-left hidden md:table-cell">FORMATS</th>
              <th className="text-left hidden lg:table-cell">LANGS</th>
              <th className="text-right">CHAT</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => <Row key={m.id} m={m} top={m.id === tiles.top?.id} />)}
            {!rows.length && (
              <tr>
                <td colSpan={8} className="text-center text-pixel-gray py-6 font-mono">
                  {cat ? "nothing matches those switches" : "loading catalog…"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({ m, top }: { m: Model; top: boolean }) {
  // Browser first when it's available: a run that costs the operator nothing
  // and leaks nothing is the one to offer by default.
  const runtime = m.runtimes.includes("browser") ? "browser"
    : m.runtimes.includes("server") ? "server" : "cloud";
  // Every kind is runnable now — the CHAT board picks the surface (transcript,
  // image, audio, vectors) off the model's own task.
  const runnable = m.runtimes.length > 0;

  return (
    <tr>
      <td>
        <Link href={`/models/${m.id}`} className="no-underline text-pixel-white hover:text-cyan-400">
          <span className="font-mono">{m.id}</span>
        </Link>
        {top && <span className="pixel-badge ml-2 text-amber-400 border-amber-400">#1</span>}
      </td>
      <td className="text-right font-mono">{params(m)}</td>
      <td className="text-right font-mono hidden sm:table-cell">{fmtCount(m.downloads)}</td>
      <td><RuntimeBadges runtimes={m.runtimes} short /></td>
      <td className="whitespace-nowrap">
        <span className="inline-flex gap-1">
          <KindBadge kind={m.kind} />
          <RoleBadge role={m.role} />
        </span>
      </td>
      <td className="font-mono text-xs text-pixel-gray-light hidden md:table-cell">
        {m.formats.join(" ")}
      </td>
      <td className="font-mono text-xs text-pixel-gray-light hidden lg:table-cell">
        {m.languages.length ? m.languages.slice(0, 6).join(" ") : "—"}
      </td>
      <td className="text-right">
        {runnable ? (
          <Link
            href={`/chat?model=${encodeURIComponent(m.id)}&runtime=${runtime}`}
            className="pixel-btn topbar-ctl px-2.5 no-underline"
            title={`chat with ${m.id} on ${runtime}`}
          >
            ▶
          </Link>
        ) : (
          <span className="font-mono text-xs text-pixel-gray">—</span>
        )}
      </td>
    </tr>
  );
}
