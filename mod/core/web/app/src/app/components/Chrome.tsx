"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, gatewayUrl, type ScoredModule } from "@/lib/api";

function hue(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 62%)`;
}

// Global module search living in the nav. Types a query → dropdown of matching
// modules; clicking a name opens its page. On the home page `onQuery` also
// feeds the grid filter so results narrow live below.
function NavSearch({ onQuery }: { onQuery?: (q: string) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ScoredModule[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // "/" or ⌘K focuses search from anywhere; Escape clears and blurs it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      const typing =
        el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
      if ((e.key === "/" && !typing) || (e.key === "k" && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        inputRef.current?.focus();
      } else if (e.key === "Escape" && el === inputRef.current) {
        setQ("");
        onQuery?.("");
        setOpen(false);
        inputRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Close the dropdown on any pointer-down outside the search root.
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, []);

  // Debounced backend search (semantic when available, keyword fallback).
  useEffect(() => {
    const s = q.trim();
    if (!s) {
      setResults([]);
      setBusy(false);
      return;
    }
    let alive = true;
    setBusy(true);
    const t = setTimeout(() => {
      api
        .search(s, 8)
        .then((r) => alive && setResults(r.results.slice(0, 8)))
        .catch(() => alive && setResults([]))
        .finally(() => alive && setBusy(false));
    }, 200);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q]);

  const showPop = open && q.trim().length > 0;

  return (
    <div className="nav-search" ref={rootRef}>
      <span className="icon" aria-hidden>
        ⌕
      </span>
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          onQuery?.(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="search modules…"
        spellCheck={false}
        autoComplete="off"
        aria-label="search modules"
      />
      {busy ? (
        <span className="sem-spin" aria-label="searching" />
      ) : (
        <span className="kbd" aria-hidden>
          /
        </span>
      )}
      {showPop && (
        <div className="nav-search-pop">
          {results.map((m) => (
            <Link
              key={m.name}
              href={`/mods/${m.name}`}
              className="nav-search-row"
              onClick={() => setOpen(false)}
            >
              <span
                className="nav-search-ic"
                style={{ background: m.color || hue(m.name) }}
              >
                {(m.icon || m.name[0]).slice(0, 1)}
              </span>
              <span className="nav-search-name">{m.name}</span>
              <span className="nav-search-desc">{m.description}</span>
            </Link>
          ))}
          {!busy && results.length === 0 && (
            <div className="nav-search-empty">no modules match “{q.trim()}”</div>
          )}
        </div>
      )}
    </div>
  );
}

// `sub` docks an optional second row inside the sticky header — the home page
// uses it for catalog stats + controls so everything lives in the top chrome.
export function Nav({
  onQuery,
  sub,
}: {
  onQuery?: (q: string) => void;
  sub?: ReactNode;
}) {
  return (
    <nav className="nav">
      <div className="wrap nav-inner">
        <Link href="/" className="brand" title="mod — back to the explorer">
          <span className="glyph">m</span>
          <span>mod</span>
        </Link>
        <div className="nav-links">
          <Link
            href="/workspace"
            className="nav-ic"
            data-tip="workspace"
            aria-label="workspace"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
              <rect x="3.5" y="3.5" width="7" height="7" rx="2.2" />
              <rect x="13.5" y="3.5" width="7" height="7" rx="2.2" />
              <rect x="3.5" y="13.5" width="7" height="7" rx="2.2" />
              <rect x="13.5" y="13.5" width="7" height="7" rx="2.2" />
            </svg>
          </Link>
          <a
            href={gatewayUrl("docs")}
            target="_blank"
            rel="noreferrer"
            className="nav-ic t2"
            data-tip="docs"
            aria-label="docs"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
          </a>
          <a
            href="https://github.com/modc2/mod"
            target="_blank"
            rel="noreferrer"
            className="nav-ic t3"
            data-tip="source"
            aria-label="source on GitHub"
          >
            <svg viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
        </div>
        <NavSearch onQuery={onQuery} />
      </div>
      {sub && <div className="wrap nav-sub">{sub}</div>}
    </nav>
  );
}

export function Footer({ version }: { version?: string }) {
  return (
    <footer className="footer wrap">
      <span>
        © {new Date().getFullYear()} mod protocol · built modular, on Base
      </span>
      <span className="mono">
        {version ? `mod-api v${version} · ` : ""}modc2.com/web
      </span>
    </footer>
  );
}
