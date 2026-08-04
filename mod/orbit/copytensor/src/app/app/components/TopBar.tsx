"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import SkinPicker from "./SkinPicker";
import RpcPoolChip from "./RpcPoolChip";
import CurrencyToggle from "./CurrencyToggle";
import { useSidebar, type SidebarPanel } from "../context/SidebarContext";
import { useFilters } from "../context/FiltersContext";

// `label` is the rail cap; `long` is the same door spelled out in the ☰
// sheet, where there's room for it.
const DRAWER: { id: SidebarPanel; label: string; long: string; title: string }[] = [
  { id: "watch", label: "WATCH", long: "WATCHLIST", title: "Watchlist drawer" },
  { id: "strat", label: "STRAT", long: "STRAT MAKER", title: "Strat maker — build an index of traders" },
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
  const [menu, setMenu] = useState(false);
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => { setQ(search); }, [search]);

  // Navigating is what you opened the menu to do — close it behind you.
  useEffect(() => { setMenu(false); }, [path]);

  // The tab rail scrolls sideways on a phone, so the tab you're ON can be
  // parked off-screen. Bring it into view whenever the route changes.
  useEffect(() => {
    const el = navRef.current?.querySelector<HTMLElement>("[data-active='1']");
    el?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [path]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const val = q.trim();
    setSearch(val);
    setMenu(false);
    // If it looks like an SS58 (starts with 5, ~48 chars), go straight to the
    // trader page; otherwise leave the value in global filters for whichever
    // table is on screen.
    if (val.startsWith("5") && val.length >= 40) {
      router.push(`/traders/${val}`);
    }
  };

  const drawerCap = (d: (typeof DRAWER)[number], wide = false) => {
    const on = docked && panel === d.id;
    return (
      <button
        key={d.id}
        onClick={() => {
          setMenu(false);
          if (on) return setDocked(false);
          setPanel(d.id);
          setDocked(true);
        }}
        className={`pixel-btn topbar-ctl px-3 ${wide ? "flex-1" : ""} ${
          on ? "border-green-400 text-green-400" : ""
        }`}
        title={d.title}
        aria-pressed={on}
      >
        {wide ? d.long : d.label}
      </button>
    );
  };

  return (
    <header className="border-b-2 border-pixel-border bg-pixel-black sticky top-0 z-30">
      {/* Two rows at every width: marquee + status on top, menu + coin slot
          under it. It used to collapse onto one line on wide screens, but
          logo + 5 tabs + search + 4 controls only ever fitted by shrinking
          the tabs and the search field into each other — at 1800px the
          PORTFOLIO tab was still being clipped. Two honest rows are wider
          apart and never truncate.

          On a phone the status cluster moves into the ☰ sheet. It is not a
          space-saving nicety: rpc + currency + skin + two drawer caps come
          to ~500px, and a control rail wider than the glass makes the phone
          zoom the ENTIRE board out to fit it. */}
      <div className="max-w-[1600px] mx-auto px-3 py-2 flex flex-col gap-2 min-w-0">
        <div className="flex items-center gap-x-3 gap-y-2 min-w-0">
          <Link
            href="/leaderboard"
            className="arcade-title text-pixel-white no-underline whitespace-nowrap sprite-coin min-w-0 truncate"
          >
            <span className="text-green-400">COPY</span>TENSOR
          </Link>

          {/* Status cluster hugs the right. Every control rides the same
              34px rail (`.topbar-ctl`) — they used to arrive at four
              different heights, each padded by hand. */}
          <div className="flex items-center gap-2 ml-auto shrink-0">
            <span className="hidden lg:flex items-center gap-2">
              <RpcPoolChip />
            </span>
            <CurrencyToggle />
            <span className="hidden lg:flex items-center gap-2">
              <SkinPicker />
              {/* Two doors into the one drawer: the list you watch, and the
                  basket you build out of it. Clicking the tab you're already
                  on closes the drawer. Spelled out, not "⌘" — Silkscreen has
                  no glyph for it, so it fell back to a symbol font and
                  rendered as a blot. */}
              {DRAWER.map((d) => drawerCap(d))}
            </span>

            <button
              onClick={() => setMenu((m) => !m)}
              aria-expanded={menu}
              aria-label="More controls"
              className={`pixel-btn topbar-ctl px-3 lg:hidden ${
                menu ? "nav-active" : ""
              }`}
            >
              {menu ? "✕" : "☰"}
            </button>
          </div>
        </div>

        {/* Five tabs never fit across a phone. Rather than wrap them into two
            ragged rows, the rail scrolls sideways and the active tab is
            scrolled into view — a cabinet menu you thumb along. */}
        <div className="flex flex-wrap items-center gap-2 lg:gap-3 min-w-0">
          <nav
            ref={navRef}
            className="rail no-scrollbar -mx-3 px-3 lg:mx-0 lg:px-0 lg:flex-wrap shrink min-w-0"
          >
            {NAV.map((n) => {
              const active =
                path === n.href ||
                (n.href !== "/" && path.startsWith(n.href));
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  data-active={active ? "1" : "0"}
                  className={`pixel-btn topbar-ctl no-underline ${
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

          <form onSubmit={onSubmit} className="hidden lg:block lg:flex-1 lg:min-w-[160px] lg:max-w-md">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="search SS58 or label…"
              className="pixel-input-sm topbar-ctl w-full font-mono"
              aria-label="search"
            />
          </form>
        </div>

        {/* The ☰ sheet: everything the rail can't hold on a phone, laid out
            as full-width rows instead of a squeezed strip. */}
        {menu && (
          <div className="lg:hidden flex flex-col gap-2 pt-2 border-t-2 border-pixel-border">
            <form onSubmit={onSubmit}>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="search SS58 or label…"
                className="pixel-input-sm topbar-ctl w-full font-mono"
                aria-label="search"
              />
            </form>
            <div className="flex items-center gap-2">
              {DRAWER.map((d) => drawerCap(d, true))}
            </div>
            <div className="flex items-center gap-2">
              <SkinPicker />
              <span className="min-w-0 flex-1 flex justify-end">
                <RpcPoolChip />
              </span>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
