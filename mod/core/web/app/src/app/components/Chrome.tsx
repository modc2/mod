"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, gatewayUrl, type ScoredModule } from "@/lib/api";
import WalletButton from "./WalletButton";
import { Icon } from "./Icon";

function hue(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 62%)`;
}

// Global module search living in the nav. Types a query → dropdown of matching
// modules; clicking a name opens its page. The home page has its own big
// search box in the hero, so it hides this one.
function NavSearch() {
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
        setOpen(false);
        inputRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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
      <span className="icon">
        <Icon name="search" />
      </span>
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="Search apps…"
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
            <div className="nav-search-empty">Nothing matches “{q.trim()}”</div>
          )}
        </div>
      )}
    </div>
  );
}

// Top chrome. Plain text links (not icon-only tiles) so a first-time visitor
// can read where each one goes. `sub` docks an optional second row inside the
// sticky header. On phones the row wraps: brand + wallet on top, links below.
export function Nav({
  sub,
  hideSearch = false,
}: {
  sub?: ReactNode;
  hideSearch?: boolean;
}) {
  const path = usePathname() || "/";
  const is = (p: string) => (p === "/" ? path === "/" : path.startsWith(p));

  return (
    <nav className="nav">
      <div className="wrap nav-inner">
        <Link href="/" className="brand" title="mod — back to the explorer">
          <span className="glyph">m</span>
          <span>mod</span>
        </Link>
        <div className="nav-links" aria-label="main">
          <Link href="/" className={`nav-link${is("/") ? " on" : ""}`}>
            <Icon name="grid" />
            <span>Explore</span>
          </Link>
          <Link
            href="/workspace"
            className={`nav-link${is("/workspace") ? " on" : ""}`}
          >
            <Icon name="layout" />
            <span>Workspace</span>
          </Link>
          <Link href="/stake" className={`nav-link${is("/stake") ? " on" : ""}`}>
            <Icon name="clock" />
            <span>Stake</span>
          </Link>
          <a
            href={gatewayUrl("docs")}
            target="_blank"
            rel="noreferrer"
            className="nav-link"
          >
            <Icon name="book" />
            <span>Docs</span>
          </a>
          <a
            href="https://github.com/modc2/mod"
            target="_blank"
            rel="noreferrer"
            className="nav-link quiet"
            aria-label="source on GitHub"
            title="Source on GitHub"
          >
            <Icon name="github" />
          </a>
        </div>
        {!hideSearch && <NavSearch />}
        <WalletButton />
      </div>
      {sub && <div className="wrap nav-sub">{sub}</div>}
    </nav>
  );
}

export function Footer({
  version,
  extra,
}: {
  version?: string;
  extra?: ReactNode;
}) {
  return (
    <footer className="footer wrap">
      <span>
        © {new Date().getFullYear()} mod protocol · built modular, on Base
      </span>
      <span className="footer-right">
        {extra}
        <span className="mono">
          {version ? `mod-api v${version} · ` : ""}modc2.com/web
        </span>
      </span>
    </footer>
  );
}
