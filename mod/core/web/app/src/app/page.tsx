"use client";

import { useEffect, useMemo, useState } from "react";
import {
  api,
  type Graph,
  type Info,
  type Module,
  type Registry,
  type SearchResult,
} from "@/lib/api";
import { bloc, chain, type ModStakesAll } from "@/lib/chain";
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
  const [stakes, setStakes] = useState<ModStakesAll | null>(null);
  const [view, setView] = useState<View>("grid");
  const [q, setQ] = useState("");
  const [onchainOnly, setOnchainOnly] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
    chain.modStakes().then((s) => alive && setStakes(s)).catch(() => {});
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
    // Keyword filter: every word of the query must appear somewhere in the
    // module — "store files" matches a module described as "file storage".
    // A term also matches on its singular stem ("files" → "file"); when no
    // module matches every word, partial matches surface instead, ranked by
    // how many words hit (name hits first).
    const terms = s.split(/\s+/).filter(Boolean);
    const pool = mods.filter((m) => !onchainOnly || m.registered);
    // No query: BLOC-staked modules float to the top (stable within ties, so
    // the backend's live-first ordering is preserved among the unstaked).
    if (terms.length === 0) {
      const w = (m: Module) => stakes?.mods[m.name]?.total ?? 0;
      return [...pool].sort((a, b) => w(b) - w(a));
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
  }, [mods, q, onchainOnly, semantic, sem, stakes]);

  const onchainCount = useMemo(
    () => (mods ? mods.filter((m) => m.registered).length : 0),
    [mods],
  );
  const onWebCount = useMemo(
    () => (mods ? mods.filter((m) => m.on_web).length : 0),
    [mods],
  );
  const chainUp = registry?.available ?? false;

  return (
    <>
      <Nav
        onQuery={setQ}
        sub={
          <>
            <div className="nav-sub-meta">
              {q.trim() ? (
                <>
                  <span>
                    <b>{filtered.length}</b>/{mods?.length ?? 0}{" "}
                    {semantic ? "ranked by relevance" : "keyword matches"} for
                    “{q.trim()}”
                  </span>
                  <span className={`sem-badge ${semantic ? "on" : "off"}`}>
                    {semBusy ? "ranking…" : semantic ? "✦ semantic" : "text match"}
                  </span>
                </>
              ) : (
                <>
                  <span>
                    <b>{mods?.length ?? 0}</b> modules
                  </span>
                  <span className="sep">·</span>
                  <span>
                    <b>{info?.stats.functions ?? 0}</b> functions
                  </span>
                  <span className="sep">·</span>
                  <span className="onweb-count" title="Modules the gateway serves live on the web">
                    ● <b>{onWebCount}</b> live
                  </span>
                  {(stakes?.total ?? 0) > 0 && (
                    <>
                      <span className="sep">·</span>
                      <span
                        className="bloc-count"
                        title="BlocTime staked to modules across the catalog"
                      >
                        ⧗ <b>{bloc(stakes?.total)}</b> BLOC staked
                      </span>
                    </>
                  )}
                  <span className="sep">·</span>
                  <span
                    className="oc"
                    title={
                      chainUp
                        ? `chain · ${registry?.network}`
                        : "chain module unreachable"
                    }
                  >
                    ⛓ <b>{onchainCount}</b> on-chain
                    <i className={`chain-dot ${chainUp ? "up" : "down"}`} />
                  </span>
                </>
              )}
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
          </>
        }
      />

      <section className="wrap">
        <PoolWidget />
      </section>

      <section className="wrap" id="ecosystem">
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
              <ModuleCard
                m={m}
                index={i}
                staked={stakes?.mods[m.name]?.total ?? 0}
                key={m.name}
              />
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
