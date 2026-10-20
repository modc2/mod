"use client";

import { useEffect, useMemo, useState } from "react";
import ThemeToggle from "./ThemeToggle";
import type { Paper } from "./tex";

type RootInfo = { root: string | null; epoch: number | null; count?: number } | null;

const API = process.env.NEXT_PUBLIC_API_URL || "/api/whitepaper";

function short(h: string) {
  return h.length > 22 ? `${h.slice(0, 12)}…${h.slice(-8)}` : h;
}

export default function RootClient({ paper, initialRoot }: { paper: Paper; initialRoot: RootInfo }) {
  const [root, setRoot] = useState<RootInfo>(initialRoot);
  const [online, setOnline] = useState(initialRoot !== null);
  const [refreshing, setRefreshing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [active, setActive] = useState<string>("");
  const [progress, setProgress] = useState(0);
  const [tocOpen, setTocOpen] = useState(false);

  async function refresh() {
    setRefreshing(true);
    try {
      const r = await fetch(`${API}/tree/root`, { cache: "no-store" });
      setOnline(r.ok);
      if (r.ok) setRoot(await r.json());
    } catch {
      setOnline(false);
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, []);

  // Reading progress + scrollspy.
  useEffect(() => {
    const ids = paper.toc.map((t) => t.id);
    const onScroll = () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      setProgress(max > 0 ? Math.min(1, h.scrollTop / max) : 0);
      let cur = "";
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top < 120) cur = id;
        else if (el) break;
      }
      setActive(cur);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [paper.toc]);

  // Section of the active entry, so the TOC only unfolds the current section.
  const activeSection = useMemo(() => {
    let sec = "";
    for (const t of paper.toc) {
      if (t.level < 2) sec = t.id;
      if (t.id === active) return sec;
    }
    return "";
  }, [active, paper.toc]);

  const minutes = Math.max(1, Math.round(paper.words / 230));

  function copyRoot() {
    if (!root?.root) return;
    navigator.clipboard?.writeText(root.root).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }

  const toc = (
    <nav className="toc" aria-label="Contents">
      {paper.toc.map((t) => {
        let sec = "";
        for (const u of paper.toc) {
          if (u.level < 2) sec = u.id;
          if (u === t) break;
        }
        if (t.level === 2 && sec !== activeSection) return null;
        return (
          <a
            key={t.id}
            href={`#${t.id}`}
            onClick={() => setTocOpen(false)}
            className={`toc-l${t.level} ${active === t.id ? "toc-active" : ""}`}
          >
            {t.level === 0 ? (
              <>
                <span className="toc-part">{t.num}</span>
                {t.title}
              </>
            ) : (
              <>
                {t.num && <span className="toc-num">{t.num}</span>}
                {t.title}
              </>
            )}
          </a>
        );
      })}
    </nav>
  );

  return (
    <>
      <div className="progress" style={{ transform: `scaleX(${progress})` }} aria-hidden />

      <header className="topbar">
        <div className="topbar-in">
          <a href="#top" className="brand">
            <span className="brand-mark">MOD</span>
            <span className="brand-sep">/</span>
            <span>whitepaper</span>
          </a>
          <div className="flex items-center gap-2">
            {paper.version && <span className="chip">v{paper.version}</span>}
            <a className="btn" href={`${API}/paper.tex`} target="_blank" rel="noreferrer">
              .tex
            </a>
            <button className="btn lg:hidden" onClick={() => setTocOpen((o) => !o)} aria-expanded={tocOpen}>
              Contents
            </button>
            <ThemeToggle />
          </div>
        </div>
        {tocOpen && <div className="toc-drawer lg:hidden">{toc}</div>}
      </header>

      <div className="shell" id="top">
        <aside className="sidebar">{toc}</aside>

        <article className="prose-paper">
          <section className="hero">
            <p className="eyebrow">MOD Protocol · Whitepaper</p>
            <h1>{paper.title}</h1>
            {paper.subtitle && <p className="subtitle" dangerouslySetInnerHTML={{ __html: paper.subtitle }} />}
            <div className="meta">
              {paper.version && <span>Version {paper.version}</span>}
              {paper.date && <span>{paper.date}</span>}
              <span>{minutes} min read</span>
            </div>
          </section>

          <section className="registry" aria-label="Live registry">
            <div className="registry-head">
              <span className={`dot ${online ? "dot-on" : "dot-off"}`} />
              <span>{online ? "Reference registry · live" : "Reference registry · offline"}</span>
              <button onClick={refresh} disabled={refreshing} className="btn btn-sm ml-auto">
                {refreshing ? "…" : "refresh"}
              </button>
            </div>
            <div className="registry-grid">
              <div>
                <div className="k">Merkle root</div>
                <button className="v v-root" onClick={copyRoot} title={root?.root ?? ""} disabled={!root?.root}>
                  {root?.root ? (copied ? "copied" : short(root.root)) : "no tree built"}
                </button>
              </div>
              <div>
                <div className="k">Epoch</div>
                <div className="v">{root?.epoch ?? "—"}</div>
              </div>
              <div>
                <div className="k">Records</div>
                <div className="v">{root?.count ?? "—"}</div>
              </div>
            </div>
          </section>

          {paper.abstract && (
            <section className="abstract">
              <div className="abstract-k">Abstract</div>
              <div dangerouslySetInnerHTML={{ __html: paper.abstract }} />
            </section>
          )}

          {paper.html ? (
            <div dangerouslySetInnerHTML={{ __html: paper.html }} />
          ) : (
            <p className="text-muted">The paper source could not be loaded.</p>
          )}

          <footer className="foot">
            <span>MOD Protocol · whitepaper module{paper.version ? ` · v${paper.version}` : ""}</span>
            <span className="flex gap-4">
              <a href="#top">back to top</a>
              <a href={`${API}/paper.tex`} target="_blank" rel="noreferrer">
                download .tex
              </a>
            </span>
          </footer>
        </article>
      </div>
    </>
  );
}
