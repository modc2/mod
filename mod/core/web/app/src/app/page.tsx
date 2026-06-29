"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type Graph, type Info, type Module, type Registry } from "@/lib/api";
import { Nav, Footer } from "./components/Chrome";
import { ModuleCard } from "./components/ModuleCard";
import DepGraph from "./components/DepGraph";
import PoolWidget from "./components/PoolWidget";

const LOGO = ` _____ _______ ______
|     |       |      \\
| | | |   -   |   -  |
|_|_|_|_______|______/`;

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

  useEffect(() => {
    let alive = true;
    Promise.all([api.info(), api.mods(), api.registry(), api.graph()])
      .then(([i, m, r, g]) => {
        if (!alive) return;
        setInfo(i);
        setMods(m);
        setRegistry(r);
        setGraph(g);
      })
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, []);

  const filtered = useMemo(() => {
    if (!mods) return [];
    const s = q.trim().toLowerCase();
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
  }, [mods, q, onchainOnly]);

  const onchainCount = useMemo(
    () => (mods ? mods.filter((m) => m.registered).length : 0),
    [mods],
  );
  const ready = !!info;
  const chainUp = registry?.available ?? false;

  return (
    <>
      <Nav />

      <header className="explorer-hero wrap">
        <pre className="hero-ascii">{LOGO}</pre>
        <div className="badge">
          <span className="dot" />
          {ready ? `${mods?.length ?? 0} modules in orbit` : "connecting to orbit…"}
        </div>
        <h1>
          The <span className="grad-text">module</span> explorer.
        </h1>
        <p className="lede">
          Every module in the mod protocol — searchable, with its on-chain
          registration status and the dependency links between them.
        </p>

        <div className="hero-chips">
          <span className="hero-chip">
            <b>{mods?.length ?? 0}</b> modules
          </span>
          <span className="hero-chip">
            <b>{info?.stats.functions ?? 0}</b> functions
          </span>
          <span className="hero-chip onchain-chip" title={chainUp ? `chain · ${registry?.network}` : "chain module unreachable"}>
            ⛓ <b>{onchainCount}</b> on-chain
            <i className={`chain-dot ${chainUp ? "up" : "down"}`} />
          </span>
        </div>
      </header>

      <section className="wrap">
        <PoolWidget />
      </section>

      <section className="wrap" id="ecosystem">
        <div className="explorer-toolbar">
          <div className="search">
            <span className="icon">⌕</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search modules, descriptions, functions, deps…"
              spellCheck={false}
              autoComplete="off"
            />
            <span className="count">
              {mods ? `${filtered.length}/${mods.length}` : ""}
            </span>
          </div>

          <div className="toolbar-controls">
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
