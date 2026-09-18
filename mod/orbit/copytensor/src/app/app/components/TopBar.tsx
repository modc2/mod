"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import SkinPicker from "./SkinPicker";
import RpcPoolChip from "./RpcPoolChip";
import CurrencyToggle from "./CurrencyToggle";
import { useSidebar, type SidebarPanel } from "../context/SidebarContext";
import { useFilters } from "../context/FiltersContext";

// Four doors on the rail. Everything a first-timer doesn't need — baskets,
// the agent, the drawer, skins, the RPC readout — lives behind MORE.
const NAV = [
  { href: "/", label: "HOME" },
  { href: "/traders", label: "TRADERS" },
  { href: "/subnets", label: "SUBNETS" },
  { href: "/portfolio", label: "MY COPIES" },
];

const MORE_LINKS = [
  { href: "/strats", label: "STRATS", hint: "baskets of traders" },
  { href: "/agent", label: "AGENT", hint: "ask for a strat" },
];

const DRAWER: { id: SidebarPanel; label: string }[] = [
  { id: "watch", label: "WATCHLIST" },
  { id: "strat", label: "STRAT MAKER" },
];

export default function TopBar() {
  const path = usePathname() || "";
  const router = useRouter();
  const { docked, panel, setDocked, setPanel } = useSidebar();
  const { search, setSearch } = useFilters();
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(false);   // the phone ☰ sheet
  const [more, setMore] = useState(false);   // the desktop MORE drop
  const navRef = useRef<HTMLElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setQ(search); }, [search]);
  useEffect(() => { setMenu(false); setMore(false); }, [path]);

  useEffect(() => {
    const el = navRef.current?.querySelector<HTMLElement>("[data-active='1']");
    el?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [path]);

  // Click-away / Escape close the MORE drop — same contract as the skin menu.
  useEffect(() => {
    if (!more) return;
    const onDown = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setMore(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMore(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [more]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const val = q.trim();
    setSearch(val);
    setMenu(false);
    if (val.startsWith("5") && val.length >= 40) router.push(`/traders/${val}`);
    else if (val && !path.startsWith("/traders")) router.push("/traders");
  };

  const openDrawer = (id: SidebarPanel) => {
    setMenu(false);
    setMore(false);
    if (docked && panel === id) return setDocked(false);
    setPanel(id);
    setDocked(true);
  };

  const isActive = (href: string) =>
    href === "/" ? path === "/" : path === href || path.startsWith(href + "/");

  const tabs = (
    <nav ref={navRef} className="rail no-scrollbar -mx-3 px-3 lg:mx-0 lg:px-0 shrink min-w-0">
      {NAV.map((n) => {
        const active = isActive(n.href);
        return (
          <Link
            key={n.href}
            href={n.href}
            data-active={active ? "1" : "0"}
            className={`pixel-btn topbar-ctl no-underline ${
              active ? "nav-active" : "text-pixel-gray-light hover:text-pixel-white"
            }`}
          >
            {n.label}
          </Link>
        );
      })}
    </nav>
  );

  const searchBox = (
    <form onSubmit={onSubmit} className="min-w-0">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="find a trader…"
        className="pixel-input-sm topbar-ctl w-full font-mono"
        aria-label="search"
      />
    </form>
  );

  return (
    <header className="border-b-2 border-pixel-border bg-pixel-black sticky top-0 z-30">
      <div className="max-w-[1600px] mx-auto px-3 py-2 flex flex-col gap-2 min-w-0">
        <div className="flex items-center gap-3 min-w-0">
          <Link href="/" className="arcade-title text-pixel-white no-underline whitespace-nowrap sprite-coin shrink-0">
            <span className="text-green-400">COPY</span>TENSOR
          </Link>

          {/* Desktop: tabs, search, currency, MORE — one row. */}
          <div className="hidden lg:flex items-center gap-3 flex-1 min-w-0">
            {tabs}
            <div className="flex-1 min-w-[140px] max-w-sm">{searchBox}</div>
            <div className="flex items-center gap-2 ml-auto shrink-0">
              <CurrencyToggle />
              <div ref={moreRef} className="relative">
                <button
                  onClick={() => setMore((m) => !m)}
                  aria-haspopup="menu"
                  aria-expanded={more}
                  className={`pixel-btn topbar-ctl px-3 ${more || docked ? "nav-active" : "text-pixel-gray-light"}`}
                >
                  MORE ▾
                </button>
                {more && (
                  <div className="more-menu" role="menu" aria-label="More">
                    {MORE_LINKS.map((l) => (
                      <Link key={l.href} href={l.href} role="menuitem" className="more-menu__item no-underline">
                        <span>{l.label}</span>
                        <span className="more-menu__hint">{l.hint}</span>
                      </Link>
                    ))}
                    <div className="more-menu__sep" />
                    {DRAWER.map((d) => (
                      <button
                        key={d.id}
                        role="menuitemcheckbox"
                        aria-checked={docked && panel === d.id}
                        onClick={() => openDrawer(d.id)}
                        className="more-menu__item"
                      >
                        <span>{d.label}</span>
                        <span className="more-menu__hint">{docked && panel === d.id ? "close" : "open"}</span>
                      </button>
                    ))}
                    <div className="more-menu__sep" />
                    <div className="more-menu__row"><SkinPicker /><RpcPoolChip /></div>
                  </div>
                )}
              </div>
            </div>
          </div>

          <button
            onClick={() => setMenu((m) => !m)}
            aria-expanded={menu}
            aria-label="Menu"
            className={`pixel-btn topbar-ctl px-3 ml-auto lg:hidden ${menu ? "nav-active" : ""}`}
          >
            {menu ? "✕" : "☰"}
          </button>
        </div>

        {/* Phone: the tab rail scrolls sideways under the logo. */}
        <div className="lg:hidden min-w-0">{tabs}</div>

        {menu && (
          <div className="lg:hidden flex flex-col gap-2 pt-2 border-t-2 border-pixel-border">
            {searchBox}
            <div className="grid grid-cols-2 gap-2">
              {MORE_LINKS.map((l) => (
                <Link key={l.href} href={l.href} className="pixel-btn topbar-ctl no-underline">{l.label}</Link>
              ))}
              {DRAWER.map((d) => (
                <button
                  key={d.id}
                  onClick={() => openDrawer(d.id)}
                  className={`pixel-btn topbar-ctl ${docked && panel === d.id ? "nav-active" : ""}`}
                >
                  {d.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <CurrencyToggle />
              <SkinPicker />
              <div className="min-w-0 flex-1 flex justify-end"><RpcPoolChip /></div>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
