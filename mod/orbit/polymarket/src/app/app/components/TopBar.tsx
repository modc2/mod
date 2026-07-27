"use client";

import { useRouter } from "next/navigation";
import { useFilters, useFilterParams } from "../context/FiltersContext";
import NavMenu from "./NavMenu";
import HeaderStratPicker from "./HeaderStratPicker";
import WalletChip from "./WalletChip";
import ThemeToggle from "./ThemeToggle";

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

  const goToTrader = () => {
    const addr = search.trim().toLowerCase();
    setSearch("");
    router.push(`/traders/${addr}${filterQs ? `?${filterQs}` : ""}`);
  };

  const searchBox = (
    <div className="relative w-full">
      <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[13px] text-pixel-gray pointer-events-none">/</span>
      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && isAddrSearch) goToTrader();
        }}
        placeholder={isAddrSearch ? "press ENTER to view this trader →" : searchPlaceholder}
        className={`pixel-input-sm w-full pl-6 pr-20 font-mono text-[14px] ${
          isAddrSearch ? "border-green-400 text-green-400" : ""
        }`}
      />
      {isAddrSearch && (
        <button
          onClick={goToTrader}
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
  );

  return (
    <header
      className="sticky top-0 z-40 backdrop-blur-md bg-[rgb(var(--pixel-black-rgb)/0.75)]"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      {/* Nav cluster left, theme toggle + sign-in right; search lives on
          its own row below this bar. */}
      <div className="px-4 h-12 flex items-center justify-between gap-3">
        {/* ── Strat picker + page selector — top-left corner. The strat
            picker leads (its list expands into a left sidebar drawer showing
            your $ per strat) with the page-nav dropdown to its right;
            selection is global (indexStore). ── */}
        <div className="flex items-center gap-1">
          <HeaderStratPicker />
          <NavMenu />
        </div>
        {/* ── Theme toggle + sign in — top-right corner. The toggle yields
            on tiny screens so the wallet chip never gets shoved under the
            left cluster. ── */}
        <div className="flex items-center gap-2">
          <div className="hidden min-[480px]:block">
            <ThemeToggle />
          </div>
          <WalletChip />
        </div>
      </div>
      {/* The search box (also a "jump to trader" teleport: paste any 0x
          address + Enter) lives on its own full-width row below the header
          bar on every viewport. */}
      {showSearch && <div className="px-4 pb-2">{searchBox}</div>}
    </header>
  );
}
