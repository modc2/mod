"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, SearchResult, Source, Stats } from "@/lib/api";
import { clearSession, isExpired, loadSession, Session } from "@/lib/session";
import { shortAddress } from "@/lib/wallet";
import ServerCard from "@/components/ServerCard";
import ServerPanel from "@/components/ServerPanel";
import SignIn from "@/components/SignIn";
import Publish from "@/components/Publish";

const CATEGORIES = ["dev", "data", "web", "cloud", "files", "comms", "ai", "finance", "security"];
const SORTS = ["relevance", "stars", "downloads", "new", "name"];

export default function Home() {
  const [sources, setSources] = useState<Source[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);

  const [q, setQ] = useState("");
  const [active, setActive] = useState<Set<string>>(new Set());
  const [oss, setOss] = useState(true);
  const [transport, setTransport] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("relevance");

  const [result, setResult] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [selected, setSelected] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  // A search is a fan-out to eight registries; only the newest one may win.
  const seq = useRef(0);

  const run = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    setError("");
    try {
      const r = await api.search({
        q,
        sources: [...active].join(","),
        oss,
        transport,
        category,
        sort,
        limit: 60,
      });
      if (mine === seq.current) setResult(r);
    } catch (e) {
      if (isExpired(e)) {
        clearSession();
        setSession(null);
      }
      if (mine === seq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [q, active, oss, transport, category, sort]);

  useEffect(() => {
    api.sources().then((r) => setSources(r.sources)).catch(() => setSources([]));
    api.stats().then(setStats).catch(() => setStats(null));
    setSession(loadSession());
    const t = (localStorage.getItem("mcp:theme") as "dark" | "light") || "dark";
    setTheme(t);
    // Deep link: /mcp?id=npm:@foo/bar opens straight to a card.
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) setSelected(id);
  }, []);

  // Typing debounces; filter changes fire immediately.
  useEffect(() => {
    const t = setTimeout(run, q ? 320 : 0);
    return () => clearTimeout(t);
  }, [run, q]);

  function toggleSource(id: string) {
    setActive((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function flipTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("mcp:theme", next);
    } catch {
      /* private mode — the theme just won't stick */
    }
  }

  function signOut() {
    clearSession();
    setSession(null);
  }

  const servers = result?.servers ?? [];

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="glyph">◈</span>
            <span>MCP Hub</span>
            <span className="sub">
              {stats ? `${stats.providers} directories · ${stats.submissions} published here` : ""}
            </span>
          </div>
          <div className="spacer" />
          <button className="ghost sm" onClick={flipTheme} title="theme">
            {theme === "dark" ? "☾" : "☀"}
          </button>
          {session ? (
            <div className="row">
              <button className="primary sm" onClick={() => setPublishing(true)}>
                Publish
              </button>
              <button className="ghost sm" onClick={signOut} title={session.address}>
                {shortAddress(session.address)}
              </button>
            </div>
          ) : (
            <button className="sm" onClick={() => setSigningIn(true)}>
              Sign in to publish
            </button>
          )}
        </div>
      </header>

      <main className="page">
        <div className="hero">
          <h1>Every MCP server, one directory.</h1>
          <p>
            The hub scans the official registry, GitHub, npm, Glama, Smithery, the community
            awesome-lists and this fleet&apos;s own servers, then merges what they each know into one
            card per project. Open source first — and anything you publish here is pinned by CID to
            the store mod under your own wallet.
          </p>
        </div>

        <div className="searchbar">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="what should the server do?  postgres · browser automation · kubernetes"
            autoFocus
          />
          <select value={sort} onChange={(e) => setSort(e.target.value)} title="sort">
            {SORTS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select value={transport} onChange={(e) => setTransport(e.target.value)} title="transport">
            <option value="">any transport</option>
            <option value="stdio">stdio</option>
            <option value="streamable-http">streamable-http</option>
            <option value="sse">sse</option>
          </select>
        </div>

        <div className="filters">
          <div className="chips">
            {sources.map((s) => (
              <span
                key={s.id}
                className="chip"
                data-on={active.has(s.id)}
                style={{ ["--hue" as string]: `var(--src-${s.id}, var(--accent))` }}
                onClick={() => toggleSource(s.id)}
                title={s.about}
              >
                <span className="dot" />
                {s.label}
                {result?.per_source?.[s.id] !== undefined && (
                  <span className="n">{result.per_source[s.id]}</span>
                )}
              </span>
            ))}
            {active.size > 0 && (
              <span className="chip" onClick={() => setActive(new Set())}>
                clear
              </span>
            )}
          </div>
          <div className="spacer" />
          <label className="toggle" title="only servers with public source">
            <input type="checkbox" checked={oss} onChange={(e) => setOss(e.target.checked)} />
            open source only
          </label>
        </div>

        <div className="filters">
          <div className="chips">
            {CATEGORIES.map((c) => (
              <span
                key={c}
                className="chip"
                data-on={category === c}
                onClick={() => setCategory(category === c ? "" : c)}
              >
                {c}
              </span>
            ))}
          </div>
        </div>

        {error && <div className="note bad" style={{ marginTop: 14 }}>{error}</div>}

        {result && Object.keys(result.errors).length > 0 && (
          <div className="note warn" style={{ marginTop: 14 }}>
            partial results —{" "}
            {Object.entries(result.errors)
              .map(([s, e]) => `${s}: ${e}`)
              .join(" · ")}
          </div>
        )}

        {loading && !result && (
          <div className="grid">
            {Array.from({ length: 6 }).map((_, i) => (
              <div className="skel" key={i} />
            ))}
          </div>
        )}

        {result && (
          <>
            <div className="row small muted" style={{ marginTop: 16 }}>
              <span>
                {result.count} server{result.count === 1 ? "" : "s"}
                {loading ? " · refreshing…" : ""}
              </span>
            </div>
            <div className="grid">
              {servers.map((s) => (
                <ServerCard key={s.id} server={s} onOpen={() => setSelected(s.id)} />
              ))}
            </div>
            {servers.length === 0 && !loading && (
              <div className="empty">
                nothing matched.{" "}
                {oss && <button className="ghost sm" onClick={() => setOss(false)}>include servers with no public source</button>}
              </div>
            )}
          </>
        )}
      </main>

      {selected && <ServerPanel id={selected} onClose={() => setSelected(null)} />}
      {signingIn && (
        <SignIn
          onClose={() => setSigningIn(false)}
          onDone={(s) => {
            setSession(s);
            setSigningIn(false);
            setPublishing(true);
          }}
        />
      )}
      {publishing && session && (
        <Publish session={session} onClose={() => setPublishing(false)} onPublished={run} />
      )}
    </>
  );
}
