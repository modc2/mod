"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import AccountChip from "./AccountChip";
import RuntimeChip from "./RuntimeChip";
import SkinPicker from "./SkinPicker";

// Four tabs. Three are about models — pick one, talk to one, make several
// compete — and the fourth is about the machinery underneath: which provider
// serves a run, and every call the module has answered. What the box holds on
// disk and which cloud key it carries stay in the CHAT rail, next to the switch
// that raises the question.
const NAV = [
  { href: "/", label: "MODELS" },
  { href: "/chat", label: "CHAT" },
  { href: "/arena", label: "ARENA" },
  { href: "/backend", label: "BACKEND" },
];

export default function TopBar() {
  const path = usePathname() || "/";
  const navRef = useRef<HTMLElement>(null);

  // The rail scrolls sideways on a phone, so the tab you're ON can be parked
  // off-screen. Bring it into view whenever the route changes.
  useEffect(() => {
    navRef.current?.querySelector<HTMLElement>("[data-active='1']")
      ?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [path]);

  return (
    <header className="border-b-2 border-pixel-border bg-pixel-black sticky top-0 z-30">
      {/* Three tabs, three caps and a marquee. Past that the row wraps rather
          than truncating the logo — a clipped wordmark reads as a bug, and
          "LIQUID…" is what the old single-line rule produced on a phone. */}
      <div className="w-full max-w-[1800px] mx-auto px-2 sm:px-3 py-2 flex flex-wrap items-center gap-2 sm:gap-3">
        <Link href="/" className="arcade-title !text-[16px] sm:!text-[20px] text-pixel-white no-underline whitespace-nowrap shrink-0">
          <span className="text-cyan-400">LIQUID</span>AI
        </Link>

        <nav ref={navRef} className="rail no-scrollbar min-w-0 order-3 w-full sm:order-none sm:w-auto">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                data-active={active ? "1" : "0"}
                className={`pixel-btn topbar-ctl no-underline ${
                  active ? "nav-active" : "text-pixel-gray-light hover:text-pixel-white"
                }`}
              >
                {active && <span className="mr-1.5" aria-hidden>▸</span>}
                {n.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-2 ml-auto shrink-0">
          <RuntimeChip />
          <AccountChip />
          <SkinPicker />
        </div>
      </div>
    </header>
  );
}
