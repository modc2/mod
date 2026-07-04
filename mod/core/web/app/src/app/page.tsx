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
import { Nav, Footer } from "./components/Chrome";
import { ModuleCard } from "./components/ModuleCard";
import DepGraph from "./components/DepGraph";
import PoolWidget from "./components/PoolWidget";
import AddModule from "./components/AddModule";

type View = "grid" | "graph";

export default function Home() {
  const [info, setInfo] = useState<Info | null>(null);
  const [mods, setMods] = useState<Module[] | null>(null);
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [view, setView] = useState<View>("grid");
  const [q, setQ] = useState("");
  const [onchainOnly, setOnchainOnly] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Backend semantic search results for the active query (null = none yet).
  const [sem, setSem] = useState<SearchResult | null>(null);
  const [semBusy, setSemBusy] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" or ⌘K focuses search from anywhere; Escape clears and blurs it.
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
    return () => {
      alive = false;
    };
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
    // Prefer backend semantic ranking (already ordered by relevance); apply the
    // on-chain filter client-side on top of it.
    if (semantic && sem) {
      return sem.results.filter((m) => !onchainOnly || m.registered);
    }
    return mods.filter((m) => {
      if (onchainOnly && !m.registered) return false;
      if (!s) return true;
      return (
        m.name.toLowerCase().includes(s) ||
        m.description.toLowerCase().includes(s) ||
        m.fns.some((f) => f.toLowerCase().includes(s)) ||
        m.deps.some((d) => d.toLowerCase().includes(s))
      );
    });
  }, [mods, q, onchainOnly, semantic, sem]);

  const onchainCount = useMemo(
    () => (mods ? mods.filter((m) => m.registered).length : 0),
    [mods],
  );
  const chainUp = registry?.available ?? false;

  return (
    <>
      <Nav />

      <header className="search-hero wrap">
        <div className="search-hero-eyebrow eyebrow">the mod ecosystem</div>
        <div className="search-hero-bar">
          <span className="icon">⌕</span>
          <input
            ref={searchRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search modules by meaning — try “trade crypto”, “store files”, “on-chain identity”…"
            spellCheck={false}
            autoComplete="off"
            autoFocus
          />
          {semBusy ? (
            <span className="sem-spin" aria-label="searching" />
          ) : q.trim() ? (
            <span className="count">
              {mods ? `${filtered.length}/${mods.length}` : ""}
            </span>
          ) : (
            <span className="kbd" aria-hidden>
              /
            </span>
          )}
        </div>
        <div className="search-hero-meta">
          <span>
            <b>{mods?.length ?? 0}</b> modules
          </span>
          <span className="sep">·</span>
          <span>
            <b>{info?.stats.functions ?? 0}</b> functions
          </span>
          <span className="sep">·</span>
          <span
            className="oc"
            title={chainUp ? `chain · ${registry?.network}` : "chain module unreachable"}
          >
            ⛓ <b>{onchainCount}</b> on-chain
            <i className={`chain-dot ${chainUp ? "up" : "down"}`} />
          </span>
          {q.trim() && (
            <span className={`sem-badge ${semantic ? "on" : "off"}`}>
              {semBusy ? "ranking…" : semantic ? "✦ semantic" : "text match"}
            </span>
          )}
        </div>
      </header>

      <section className="wrap">
        <PoolWidget />
      </section>

      <section className="wrap" id="ecosystem">
        <div className="explorer-toolbar">
          <div className="toolbar-caption">
            {semantic
              ? "ranked by relevance"
              : q.trim()
                ? "text matches"
                : "all modules"}
          </div>

          <div className="toolbar-controls">
            <AddModule onAdded={() => api.mods().then(setMods).catch(() => {})} />
            <button
              className={`chip-toggle ${onchainOnly ? "active" : ""}`}
              onClick={() => setOnchainOnly((v) => !v)}
              title="Show only modules registered on-chain"
              disabled={!chainUp}
            >
              ⛓ on-chain only
            </button>
            <div className="view-toggle">
              <button
                className={view === "grid" ? "active" : ""}
                onClick={() => setView("grid")}
              >
                ▦ Grid
              </button>
              <button
                className={view === "graph" ? "active" : ""}
                onClick={() => setView("graph")}
              >
                ⌗ Graph
              </button>
            </div>
          </div>
        </div>

        {!chainUp && registry && (
          <div className="chain-note">
            chain module unreachable — on-chain registration status unavailable.
            Modules still listed from the on-disk catalog.
          </div>
        )}

        {err && <div className="empty">couldn’t reach mod-api · {err}</div>}

        {!mods && !err && (
          <div className="grid">
            {Array.from({ length: 9 }).map((_, i) => (
              <div className="skel" key={i} />
            ))}
          </div>
        )}

        {view === "grid" && mods && filtered.length === 0 && (
          <div className="empty">
            no modules match {onchainOnly ? "the on-chain filter" : `“${q}”`}
          </div>
        )}

        {view === "grid" && mods && filtered.length > 0 && (
          <div className="grid">
            {filtered.map((m, i) => (
              <ModuleCard m={m} index={i} key={m.name} />
            ))}
          </div>
        )}

        {view === "graph" && graph && <DepGraph graph={graph} />}
        {view === "graph" && !graph && !err && (
          <div className="skel" style={{ height: 560 }} />
        )}
      </section>

      <Footer version={info?.version} />
    </>
  );
}
