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
        <div className="nav-links">
          <Link href="/" className="hide-sm">
            Ecosystem
          </Link>
          <Link href="/workspace" className="nav-cta-soft">
            ▦ Workspace
          </Link>
          <a
            href={gatewayUrl("docs")}
            target="_blank"
            rel="noreferrer"
            className="hide-sm"
          >
            Docs
          </a>
          <a
            href="https://github.com/modc2/mod"
            target="_blank"
            rel="noreferrer"
            className="nav-cta"
          >
            GitHub ↗
          </a>
        </div>
        <div className="nav-right">
          <NavSearch onQuery={onQuery} />
          <Link href="/" className="brand" title="mod — back to the explorer">
            <span className="glyph">m</span>
            <span>mod</span>
          </Link>
        </div>
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
