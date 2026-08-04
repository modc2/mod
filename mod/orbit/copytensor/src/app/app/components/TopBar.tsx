"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import SkinPicker from "./SkinPicker";
import RpcPoolChip from "./RpcPoolChip";
import CurrencyToggle from "./CurrencyToggle";
import { useSidebar, type SidebarPanel } from "../context/SidebarContext";
import { useFilters } from "../context/FiltersContext";

const DRAWER: { id: SidebarPanel; label: string; title: string }[] = [
  { id: "watch", label: "WATCH", title: "Watchlist drawer" },
  { id: "strat", label: "STRAT", title: "Strat maker — build an index of traders" },
];

const NAV = [
  { href: "/leaderboard", label: "LEADERBOARD" },
  { href: "/subnets", label: "SUBNETS" },
  { href: "/traders", label: "TRADERS" },
  { href: "/strats", label: "STRATS" },
  { href: "/portfolio", label: "PORTFOLIO" },
];

export default function TopBar() {
  const path = usePathname() || "";
  const router = useRouter();
  const { docked, panel, setDocked, setPanel } = useSidebar();
  const { search, setSearch } = useFilters();
  const [q, setQ] = useState("");

  useEffect(() => { setQ(search); }, [search]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const val = q.trim();
    setSearch(val);
    // If it looks like an SS58 (starts with 5, ~48 chars), go straight to the
    // trader page; otherwise leave the value in global filters for whichever
    // table is on screen.
    if (val.startsWith("5") && val.length >= 40) {
      router.push(`/traders/${val}`);
    }
  };

  return (
    <header className="border-b-2 border-pixel-border bg-pixel-black sticky top-0 z-30">
      {/* Two rows at every width: marquee + status on top, menu + coin slot
          under it. It used to collapse onto one line on wide screens, but
          logo + 5 tabs + search + 4 controls only ever fitted by shrinking
          the tabs and the search field into each other — at 1800px the
          PORTFOLIO tab was still being clipped. Two honest rows are wider
          apart and never truncate. */}
      <div className="max-w-[1600px] mx-auto px-3 py-2 flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Link
            href="/leaderboard"
            className="arcade-title text-pixel-white no-underline whitespace-nowrap sprite-coin"
          >
            <span className="text-green-400">COPY</span>TENSOR
          </Link>

          {/* Status cluster hugs the right. Every control rides the same
              34px rail (`.topbar-ctl`) — they used to arrive at four
              different heights, each padded by hand. */}
          <div className="flex items-center gap-2 ml-auto">
            <RpcPoolChip />
            <CurrencyToggle />
            <SkinPicker />
            {/* Two doors into the one drawer: the list you watch, and the
                basket you build out of it. Clicking the tab you're already
                on closes the drawer. Spelled out, not "⌘" — Silkscreen has
                no glyph for it, so it fell back to a symbol font and
                rendered as a blot. */}
            {DRAWER.map((d) => {
              const on = docked && panel === d.id;
              return (
                <button
                  key={d.id}
                  onClick={() => {
                    if (on) return setDocked(false);
                    setPanel(d.id);
                    setDocked(true);
                  }}
                  className={`pixel-btn topbar-ctl px-3 ${
                    on ? "border-green-400 text-green-400" : ""
                  }`}
                  title={d.title}
                  aria-pressed={on}
                >
                  {d.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Under lg the search field takes its own line rather than eating
            into the tabs — five tabs need the full width of a tablet. */}
        <div className="flex flex-wrap items-center gap-2 lg:gap-3 min-w-0">
          <nav className="flex items-center gap-1.5 shrink min-w-0 overflow-x-auto no-scrollbar">
            {NAV.map((n) => {
              const active =
                path === n.href ||
                (n.href !== "/" && path.startsWith(n.href));
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  className={`pixel-btn topbar-ctl no-underline shrink-0 ${
                    active ? "nav-active" : "text-pixel-gray-light hover:text-pixel-white"
                  }`}
                >
                  {/* The selected item carries a cursor, the way a cabinet
                      menu marks where you are. */}
                  {active && <span className="mr-1.5" aria-hidden>▸</span>}
                  {n.label}
                </Link>
              );
            })}
          </nav>

          <form onSubmit={onSubmit} className="w-full lg:w-auto lg:flex-1 lg:min-w-[160px] lg:max-w-md">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="search SS58 or label…"
              className="pixel-input-sm topbar-ctl w-full font-mono"
              aria-label="search"
            />
          </form>
        </div>
      </div>
    </header>
  );
}
