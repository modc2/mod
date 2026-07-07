"use client";

import { useRouter } from "next/navigation";
import { useFilters, useFilterParams } from "../context/FiltersContext";
import NavMenu from "./NavMenu";
import HeaderStratPicker from "./HeaderStratPicker";
import WalletChip from "./WalletChip";

// Lower-cased 40-hex-char Ethereum address pattern — what Polymarket's trader
// URLs accept. Matching here lets the search box double as a "jump to trader"
// teleport: type any 0x address + Enter and we route to the profile page.
const ADDR_RE = /^0x[a-fA-F0-9]{40}$/;
// Top bar owns everything global: nav dropdown (top-left), search (center),
// sign-in (top-right). The wallet chip's dot conveys CLOB / trading readiness.

interface TopBarProps {
  showSearch?: boolean;
  searchPlaceholder?: string;
}

export default function TopBar({
  showSearch = true,
  searchPlaceholder = "SEARCH...",
}: TopBarProps) {
  const router = useRouter();
  const { search, setSearch } = useFilters();
  const filterQs = useFilterParams();
  const isAddrSearch = ADDR_RE.test(search.trim());

  return (
    <header
      className="sticky top-0 z-40 backdrop-blur-md bg-[rgb(var(--pixel-black-rgb)/0.75)]"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      {/* Balanced 3-column grid so the search box sits dead-center in the
          viewport regardless of the wallet chip's width. */}
      <div className="px-4 h-12 grid grid-cols-[1fr_minmax(0,620px)_1fr] items-center gap-3">
        {/* ── Nav dropdown + strat picker — top-left corner. The strat
            picker is a one-line dropdown of saved strats plus a [+] to
            create a new one; selection is global (indexStore). ── */}
        <div className="justify-self-start flex items-center gap-1 min-w-0">
          <NavMenu />
          <HeaderStratPicker />
        </div>
        {/* The search box (also a "jump to trader" teleport: paste any 0x
            address + Enter) sits centered, and sign-in (the wallet chip)
            sits in the top-right corner. */}
        {showSearch ? (
          <div className="min-w-0">
            <div className="relative w-full">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[13px] text-pixel-gray pointer-events-none">/</span>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && isAddrSearch) {
                    const addr = search.trim().toLowerCase();
                    setSearch("");
                    router.push(`/traders/${addr}${filterQs ? `?${filterQs}` : ""}`);
                  }
                }}
                placeholder={isAddrSearch ? "press ENTER to view this trader →" : searchPlaceholder}
                className={`pixel-input-sm w-full pl-6 pr-20 font-mono text-[14px] ${
                  isAddrSearch ? "border-green-400 text-green-400" : ""
                }`}
              />
              {isAddrSearch && (
                <button
                  onClick={() => {
                    const addr = search.trim().toLowerCase();
                    setSearch("");
                    router.push(`/traders/${addr}${filterQs ? `?${filterQs}` : ""}`);
                  }}
                  title="Open trader profile"
                  className="absolute right-7 top-1/2 -translate-y-1/2 text-[11px] text-green-400 font-mono px-1.5 py-0.5 border border-green-400 rounded-[4px] hover:bg-green-400/10"
                >
                  ↵ GO
                </button>
              )}
              {search && (
                <button
                  onClick={() => setSearch("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[13px] text-pixel-gray hover:text-pixel-white"
                >
                  x
                </button>
              )}
            </div>
          </div>
        ) : (
          <div />
        )}
        {/* ── Sign in — top-right corner ── */}
        <div className="justify-self-end">
          <WalletChip />
        </div>
      </div>
    </header>
  );
}
