"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type Graph,
  type Info,
  type Module,
  type Registry,
  type SearchResult,
} from "@/lib/api";
import { backing, type BookTotals } from "@/lib/backing";
import { Nav, Footer } from "./components/Chrome";
import { ModuleCard } from "./components/ModuleCard";
import { Icon } from "./components/Icon";
import DepGraph from "./components/DepGraph";
import AddModule from "./components/AddModule";

type View = "grid" | "graph";
type Filter = "all" | "live" | "onchain";

export default function Home() {
  const [info, setInfo] = useState<Info | null>(null);
  const [mods, setMods] = useState<Module[] | null>(null);
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [stakes, setStakes] = useState<BookTotals | null>(null);
  const [view, setView] = useState<View>("grid");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [err, setErr] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Backend semantic search results for the active query (null = none yet).
  const [sem, setSem] = useState<SearchResult | null>(null);
  const [semBusy, setSemBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    // Load each piece independently: a hiccup in one call (especially the
    // chain-backed registry/graph, or a transient API restart during an
    // activator wake) must not blank the whole catalog. The module grid only
    // needs `mods`; info/registry/graph are enrichment, so only a failed
    // `mods` surfaces an error.
    api.mods().then((m) => alive && setMods(m)).catch((e) => alive && setErr(String(e)));
    api.info().then((i) => alive && setInfo(i)).catch(() => {});
    api.registry().then((r) => alive && setRegistry(r)).catch(() => {});
    api.graph().then((g) => alive && setGraph(g)).catch(() => {});
    // Merged backing book: the chain module's key-signed stakes plus the
    // wallet-signed ones this API owns.
    backing.totals().then((s) => alive && setStakes(s)).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // "/" or ⌘K focuses the hero search from anywhere; Escape clears it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      const typing =
        el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
      if ((e.key === "/" && !typing) || (e.key === "k" && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "Escape" && el === searchRef.current) {
        setQ("");
        searchRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Debounced semantic search. The backend ranks by meaning when an embeddings
  // provider is configured and silently reports `semantic: false` otherwise, in
  // which case we fall back to the local substring filter below.
  useEffect(() => {
    const s = q.trim();
    if (!s) {
      setSem(null);
      setSemBusy(false);
      return;
    }
    let alive = true;
    setSemBusy(true);
    const t = setTimeout(() => {
      api
        .search(s)
        .then((r) => alive && setSem(r))
        .catch(() => alive && setSem(null))
        .finally(() => alive && setSemBusy(false));
    }, 220);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q]);

  // True when the backend semantically ranked results for exactly this query.
  const semantic = !!sem?.semantic && sem.query.trim() === q.trim();

  const filtered = useMemo(() => {
    if (!mods) return [];
    const s = q.trim().toLowerCase();
    const pass = (m: Module) =>
      filter === "all" ||
      (filter === "live" && m.on_web) ||
      (filter === "onchain" && !!m.registered);
    // Prefer backend semantic ranking (already ordered by relevance); apply the
    // filter client-side on top of it.
    if (semantic && sem) {
      return sem.results.filter(pass);
    }
    // Keyword filter: every word of the query must appear somewhere in the
    // module — "store files" matches a module described as "file storage".
    // A term also matches on its singular stem ("files" → "file"); when no
    // module matches every word, partial matches surface instead, ranked by
    // how many words hit (name hits first).
    const terms = s.split(/\s+/).filter(Boolean);
    const pool = mods.filter(pass);
    // No query: apps you can open right now come first, then BLOC-staked
    // modules, then the backend's order (stable within ties).
    if (terms.length === 0) {
      const w = (m: Module) => stakes?.mods[m.name]?.total ?? 0;
      const open = (m: Module) => (m.on_web && m.has_app ? 1 : 0);
      return [...pool].sort((a, b) => open(b) - open(a) || w(b) - w(a));
    }
    const hits = (hay: string, t: string) =>
      hay.includes(t) ||
      (t.length > 3 && t.endsWith("s") && hay.includes(t.slice(0, -1)));
    const scored = pool
      .map((m) => {
        const name = m.name.toLowerCase();
        const hay = `${name} ${m.description} ${m.fns.join(" ")} ${m.deps.join(
          " ",
        )}`.toLowerCase();
        const matched = terms.filter((t) => hits(hay, t)).length;
        const nameHits = terms.filter((t) => hits(name, t)).length;
        return { m, matched, nameHits };
      })
      .filter((r) => r.matched > 0);
    const anyFull = scored.some((r) => r.matched === terms.length);
    return scored
      .filter((r) => !anyFull || r.matched === terms.length)
      .sort(
        (a, b) =>
          b.matched - a.matched ||
          b.nameHits - a.nameHits ||
          Number(b.m.on_web) - Number(a.m.on_web) ||
          a.m.name.localeCompare(b.m.name),
      )
      .map((r) => r.m);
  }, [mods, q, filter, semantic, sem, stakes]);

  const onchainCount = useMemo(
    () => (mods ? mods.filter((m) => m.registered).length : 0),
    [mods],
  );
  const onWebCount = useMemo(
    () => (mods ? mods.filter((m) => m.on_web).length : 0),
    [mods],
  );
  const chainUp = registry?.available ?? false;
  const searching = q.trim().length > 0;

  const emptyText = (() => {
    if (searching) return `Nothing matches “${q.trim()}”. Try a different word.`;
    if (filter === "live") return "No live apps right now.";
    if (filter === "onchain")
      return chainUp
        ? "No modules are registered on-chain yet."
        : "On-chain status is unavailable right now, so this filter is empty.";
    return "No modules found.";
  })();

  return (
    <>
      <Nav hideSearch />

      <section className="wrap home-hero">
        <h1>Apps and services, running right now.</h1>
        <p className="lede">
          mod is a catalog of small apps and services called modules. Open any
          one in your browser, read how it works, or back it on-chain.
        </p>

        <label className="hero-search">
          <span className="icon">
            <Icon name="search" />
          </span>
          <input
            ref={searchRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="What are you looking for? Try “wallet”, “trading”, “storage”…"
            spellCheck={false}
            autoComplete="off"
            aria-label="search modules"
          />
          {semBusy ? (
            <span className="sem-spin" aria-label="searching" />
          ) : q ? (
            <button
              type="button"
              className="hero-search-clear"
              onClick={() => setQ("")}
              aria-label="clear search"
            >
              <Icon name="x" />
            </button>
          ) : (
            <span className="kbd" aria-hidden>
              /
            </span>
          )}
        </label>

        <div className="hero-stats" aria-label="catalog size">
          <span>
            <b>{mods?.length ?? "…"}</b> modules
          </span>
          <span className="live">
            <i className="live-dot" />
            <b>{onWebCount}</b> live apps
          </span>
          <span>
            <b>{info?.stats.functions ?? "…"}</b> functions
          </span>
          <span title={chainUp ? `chain · ${registry?.network}` : "chain module unreachable"}>
            <Icon name="chain" />
            <b>{onchainCount}</b> on-chain
          </span>
        </div>
      </section>

      {!searching && (
        <section className="wrap steps" aria-label="how it works">
          <div className="step">
            <span className="step-n">1</span>
            <div>
              <b>Find</b>
              <span>Search above, or scroll the grid. Green “Live” means it is running now.</span>
            </div>
          </div>
          <div className="step">
            <span className="step-n">2</span>
            <div>
              <b>Open</b>
              <span>Click a card. The app loads right there, or open it in its own tab.</span>
            </div>
          </div>
          <div className="step">
            <span className="step-n">3</span>
            <div>
              <b>Go deeper</b>
              <span>Read the source, see what it depends on, or back it with BLOC.</span>
            </div>
          </div>
        </section>
      )}

      <section className="wrap" id="ecosystem">
        <div className="filters">
          <div className="seg" role="tablist" aria-label="filter">
            {(
              [
                ["all", "All"],
                ["live", "Live apps"],
                ["onchain", "On-chain"],
              ] as [Filter, string][]
            ).map(([k, label]) => (
              <button
                key={k}
                role="tab"
                aria-selected={filter === k}
                className={filter === k ? "on" : ""}
                onClick={() => setFilter(k)}
              >
                {k === "live" && <i className="live-dot" />}
                {k === "onchain" && <Icon name="chain" />}
                {label}
              </button>
            ))}
          </div>

          <div className="filters-meta">
            {searching ? (
              <>
                <b>{filtered.length}</b> result{filtered.length === 1 ? "" : "s"}
                {semantic && (
                  <span className="sem-badge on" title="Ranked by meaning, not just matching words">
                    by meaning
                  </span>
                )}
              </>
            ) : (
              <>
                <b>{filtered.length}</b> shown
              </>
            )}
          </div>

          <div className="seg view" role="tablist" aria-label="view">
            <button
              role="tab"
              aria-selected={view === "grid"}
              className={view === "grid" ? "on" : ""}
              onClick={() => setView("grid")}
              title="Cards"
            >
              <Icon name="grid" />
              <span>Cards</span>
            </button>
            <button
              role="tab"
              aria-selected={view === "graph"}
              className={view === "graph" ? "on" : ""}
              onClick={() => setView("graph")}
              title="Map of which modules use which"
            >
              <Icon name="map" />
              <span>Map</span>
            </button>
          </div>
        </div>

        {err && (
          <div className="empty">
            The catalog is not reachable right now. Refresh in a moment.
          </div>
        )}

        {!mods && !err && (
          <div className="grid">
            {Array.from({ length: 9 }).map((_, i) => (
              <div className="skel" key={i} />
            ))}
          </div>
        )}

        {view === "grid" && mods && filtered.length === 0 && (
          <div className="empty">{emptyText}</div>
        )}

        {view === "grid" && mods && filtered.length > 0 && (
          <div className="grid">
            {filtered.map((m, i) => (
              <ModuleCard
                m={m}
                index={i}
                staked={stakes?.mods[m.name]?.total ?? 0}
                key={m.name}
              />
            ))}
          </div>
        )}

        {view === "graph" && (
          <p className="view-hint">
            Each dot is a module. A line means one module uses another. Click a
            dot to open it.
          </p>
        )}
        {view === "graph" && graph && <DepGraph graph={graph} />}
        {view === "graph" && !graph && !err && (
          <div className="skel" style={{ height: 560 }} />
        )}
      </section>

      <Footer
        version={info?.version}
        extra={<AddModule onAdded={() => api.mods().then(setMods).catch(() => {})} />}
      />
    </>
  );
}
