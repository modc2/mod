"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ThemeToggle from "./ThemeToggle";
import RpcPoolChip from "./RpcPoolChip";
import CurrencyToggle from "./CurrencyToggle";
import { useSidebar } from "../context/SidebarContext";
import { useFilters } from "../context/FiltersContext";

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
  const { docked, toggleDocked } = useSidebar();
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
    <header className="border-b border-pixel-border bg-pixel-black/80 backdrop-blur-sm sticky top-0 z-30">
      <div className="max-w-[1600px] mx-auto px-3 flex items-center gap-4 h-12">
        <Link
          href="/leaderboard"
          className="font-display font-bold tracking-tight text-pixel-white no-underline whitespace-nowrap"
        >
          <span className="text-green-400">copy</span>tensor
        </Link>

        <nav className="flex items-center gap-1 ml-2">
          {NAV.map((n) => {
            const active =
              path === n.href ||
              (n.href !== "/" && path.startsWith(n.href));
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`pixel-btn text-[11px] px-3 py-1.5 no-underline ${
                  active
                    ? "border-green-400 text-green-400"
                    : "text-pixel-gray-light hover:text-pixel-white"
                }`}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>

        <form onSubmit={onSubmit} className="flex-1 max-w-md">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="search SS58 or label…"
            className="pixel-input-sm w-full font-mono"
            aria-label="search"
          />
        </form>

        <div className="flex items-center gap-2">
          <RpcPoolChip />
          <CurrencyToggle />
          <ThemeToggle />
          <button
            onClick={toggleDocked}
            className={`pixel-btn text-[11px] px-2 py-1.5 ${
              docked ? "border-green-400 text-green-400" : ""
            }`}
            title="Toggle watchlist drawer"
            aria-pressed={docked}
          >
            ⌘
          </button>
        </div>
      </div>
    </header>
  );
}
