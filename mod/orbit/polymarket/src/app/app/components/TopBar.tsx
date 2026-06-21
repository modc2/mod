"use client";

import { useRouter } from "next/navigation";
import { useFilters, useFilterParams } from "../context/FiltersContext";
import ProfileMenu from "./ProfileMenu";
import WalletChip from "./WalletChip";

// Lower-cased 40-hex-char Ethereum address pattern — what Polymarket's trader
// URLs accept. Matching here lets the search box double as a "jump to trader"
// teleport: type any 0x address + Enter and we route to the profile page.
const ADDR_RE = /^0x[a-fA-F0-9]{40}$/;
// Top bar intentionally minimal — primary nav moved to the global LeftNav
// rail; the wallet chip's dot conveys CLOB / trading readiness, and
// ProfileMenu (the right-side panel toggle) holds split-screen + token gen.

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
    <header className="border-b-2 border-pixel-border bg-pixel-black/90 sticky top-0 z-40">
      <div className="px-4 h-12 flex items-center gap-3">
        {/* Primary nav lives in the global left rail (LeftNav). The top bar
            now carries only search + wallet so it stays compact. */}

        {/* ── CENTER: search ──
            Doubles as a "jump to trader" teleport — paste any 0x address +
            press Enter (or click ↵) and we route straight to the trader page
            instead of filtering the current list. */}
        {showSearch ? (
          <div className="flex-1 flex justify-center">
            <div className="relative w-full max-w-[480px]">
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
          <div className="flex-1" />
        )}

        {/* ── RIGHT: wallet (with trading-ready dot) + profile panel toggle ── */}
        <div className="shrink-0 flex items-center gap-2">
          <WalletChip />
          <ProfileMenu />
        </div>
      </div>
    </header>
  );
}
