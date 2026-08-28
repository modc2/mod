"use client";

import Link from "next/link";
import { ThemeToggle } from "./ThemeToggle";

const LogoMark = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
    <defs>
      <linearGradient id="lg-brand-sub" x1="0" y1="0" x2="24" y2="24">
        <stop offset="0" stopColor="var(--grad-a)" />
        <stop offset="1" stopColor="var(--grad-b)" />
      </linearGradient>
    </defs>
    <path d="M12 2.6 20.6 7.4v9.2L12 21.4 3.4 16.6V7.4L12 2.6Z" stroke="url(#lg-brand-sub)" strokeWidth="1.5" strokeLinejoin="round" />
    <path d="M3.6 7.6 12 12.3l8.4-4.7M12 12.4v8.8" stroke="url(#lg-brand-sub)" strokeWidth="1.5" strokeLinejoin="round" />
  </svg>
);

/** Compact sticky header for sub-pages (object detail, docs): brand → home,
 *  a couple of nav links, and the theme toggle. */
export function SubHeader({ active }: { active?: "docs" }) {
  return (
    <header className="topbar">
      <div className="topbar-row">
        <div className="brand-block">
          <Link href="/" className="brand-row" title="Back to the store">
            <span className="brand-mark"><LogoMark /></span>
            <span className="brand">store</span>
          </Link>
        </div>
        <div className="identity">
          <Link href="/" className="navtab">Market</Link>
          <Link href="/docs" className={`navtab ${active === "docs" ? "active" : ""}`}>Docs</Link>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
