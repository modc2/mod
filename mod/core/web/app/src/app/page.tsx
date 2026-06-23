"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Info, type Module } from "@/lib/api";
import { Nav, Footer } from "./components/Chrome";
import { ModuleCard } from "./components/ModuleCard";

const LOGO = ` _____ _______ ______
|     |       |      \\
| | | |   -   |   -  |
|_|_|_|_______|______/`;

// Count-up animation that respects reduced-motion and only runs once visible.
function useCountUp(target: number, run: boolean, ms = 1100) {
  const [val, setVal] = useState(0);
  const started = useRef(false);
  useEffect(() => {
    if (!run || started.current) return;
    started.current = true;
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setVal(target);
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min((now - t0) / ms, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setVal(Math.round(target * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, run, ms]);
  return val;
}

function Stat({ value, label, run }: { value: number; label: string; run: boolean }) {
  const v = useCountUp(value, run);
  return (
    <div className="stat">
      <div className="num">{v.toLocaleString()}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export default function Home() {
  const [info, setInfo] = useState<Info | null>(null);
  const [mods, setMods] = useState<Module[] | null>(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([api.info(), api.mods()])
      .then(([i, m]) => {
        if (!alive) return;
        setInfo(i);
        setMods(m);
      })
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, []);

  const filtered = useMemo(() => {
    if (!mods) return [];
    const s = q.trim().toLowerCase();
    if (!s) return mods;
    return mods.filter(
      (m) =>
        m.name.toLowerCase().includes(s) ||
        m.description.toLowerCase().includes(s) ||
        m.fns.some((f) => f.toLowerCase().includes(s)),
    );
  }, [mods, q]);

  const stats = info?.stats ?? { modules: 0, functions: 0, rust_apis: 0, apps: 0 };
  const ready = !!info;

  return (
    <>
      <Nav />

      <header className="hero wrap">
        <pre className="hero-ascii">{LOGO}</pre>
        <div className="badge">
          <span className="dot" />
          {ready
            ? `${stats.modules} modules live in orbit`
            : "connecting to orbit…"}
        </div>
        <h1>
          A modular runtime for <span className="grad-text">on-chain</span>{" "}
          software.
        </h1>
        <p className="lede">
          Write a module. Register it on-chain. Set a price. Get paid every time
          someone calls it. One protocol, {stats.modules || "many"} modules,
          composable end to end.
        </p>
        <div className="cta-row">
          <a className="btn btn-primary" href="#ecosystem">
            Explore the ecosystem
          </a>
          <a
            className="btn btn-ghost"
            href="https://github.com/modc2/mod"
            target="_blank"
            rel="noreferrer"
          >
            Read the docs ↗
          </a>
        </div>

        <div className="flow">
          <span className="step">write code</span>
          <span className="arrow">→</span>
          <span className="step">register on-chain</span>
          <span className="arrow">→</span>
          <span className="step">users call it</span>
          <span className="arrow">→</span>
          <span className="step grad-text">you get paid</span>
        </div>
      </header>

      <section className="wrap">
        <div className="stats">
          <Stat value={stats.modules} label="Modules" run={ready} />
          <Stat value={stats.functions} label="Functions" run={ready} />
          <Stat value={stats.rust_apis} label="Rust APIs" run={ready} />
          <Stat value={stats.apps} label="Apps" run={ready} />
        </div>
      </section>

      <section className="wrap" id="ecosystem">
        <div className="section-head">
          <div>
            <div className="eyebrow">The orbit</div>
            <h2>Every module, live.</h2>
          </div>
          <p className="sub">
            Each card is a real directory in the monorepo — its config, its
            functions, its ports. Search across names, descriptions, and exposed
            functions.
          </p>
        </div>

        <div className="search">
          <span className="icon">⌕</span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search modules, descriptions, functions…"
            spellCheck={false}
            autoComplete="off"
          />
          <span className="count">
            {mods ? `${filtered.length}/${mods.length}` : ""}
          </span>
        </div>

        {err && (
          <div className="empty">
            couldn’t reach mod-api · {err}
          </div>
        )}

        {!mods && !err && (
          <div className="grid">
            {Array.from({ length: 9 }).map((_, i) => (
              <div className="skel" key={i} />
            ))}
          </div>
        )}

        {mods && filtered.length === 0 && (
          <div className="empty">no modules match “{q}”</div>
        )}

        {mods && filtered.length > 0 && (
          <div className="grid">
            {filtered.map((m, i) => (
              <ModuleCard m={m} index={i} key={m.name} />
            ))}
          </div>
        )}
      </section>

      <Footer version={info?.version} />
    </>
  );
}
