"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { useFilters, useFilterParams } from "../context/FiltersContext";
import { getAccessToken } from "../lib/access";
import NavMenu from "./NavMenu";
import UserSidebar from "./UserSidebar";
import WalletChip from "./WalletChip";
import ThemePicker from "./ThemePicker";

// Lower-cased 40-hex-char Ethereum address pattern — what Polymarket's trader
// URLs accept. Matching here lets the search box double as a "jump to trader"
// teleport: type any 0x address + Enter and we route to the profile page.
const ADDR_RE = /^0x[a-fA-F0-9]{40}$/;
// Top bar owns everything global: nav dropdown (top-left), search (center),
// sign-in (top-right). The wallet chip's dot conveys CLOB / trading readiness.
//
// The search row is also THE AGENT: type what you want in words ("consistent
// 30-day winners with real volume") and Enter hands the ask to a trader scout
// that answers through this module's own MCP server (pm_top_traders /
// pm_trader — /api/trader-agent), so every address it returns came out of the
// live leaderboard, not the model's memory. Typing still live-filters the
// board underneath, and an 0x address still teleports — three behaviors, one
// box, disambiguated by what the text is.

const SCOUT_API = "/polymarket/api/trader-agent";

interface ScoutTrader {
  address: string;
  label: string;
  stat: string;
  why: string;
}

interface ScoutResult {
  ask: string;
  reply: string;
  traders: ScoutTrader[];
}

interface TopBarProps {
  showSearch?: boolean;
  searchPlaceholder?: string;
}

export default function TopBar({
  showSearch = true,
  searchPlaceholder = "SEARCH… or describe a trader + ENTER",
}: TopBarProps) {
  const router = useRouter();
  const { search, setSearch, daysAgo } = useFilters();
  // The teleport must not carry the typed ADDRESS along as ?q= — on the
  // profile it's a market-title keyword that matches nothing and empties the
  // page (and it's cleared from context below before the push anyway).
  const filterQs = useFilterParams({ excludeSearch: true });
  const isAddrSearch = ADDR_RE.test(search.trim());

  // ── The scout ──────────────────────────────────────────────────
  const [scouting, setScouting] = useState(false);
  const [scout, setScout] = useState<ScoutResult | null>(null);
  const [scoutError, setScoutError] = useState<string | null>(null);

  const closeScout = () => {
    setScout(null);
    setScoutError(null);
  };

  const runScout = useCallback(async () => {
    const ask = search.trim();
    if (!ask || scouting) return;
    setScouting(true);
    setScoutError(null);
    setScout(null);
    try {
      const token = getAccessToken();
      const res = await fetch(SCOUT_API, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ ask, days: Number(daysAgo) || 30 }),
      });
      const data = (await res.json()) as {
        reply?: string; traders?: ScoutTrader[]; error?: string;
      };
      if (!res.ok) {
        setScoutError(
          res.status === 401
            ? "sign in first — the scout runs on the owner's inference"
            : data.error || `the scout didn't answer (${res.status})`,
        );
        return;
      }
      setScout({ ask, reply: data.reply || "", traders: data.traders || [] });
    } catch (e) {
      setScoutError(e instanceof Error ? e.message : String(e));
    } finally {
      setScouting(false);
    }
  }, [search, scouting, daysAgo]);

  const goToTrader = () => {
    const addr = search.trim().toLowerCase();
    setSearch("");
    router.push(`/traders/${addr}${filterQs ? `?${filterQs}` : ""}`);
  };

  const openScoutTrader = (addr: string) => {
    closeScout();
    setSearch("");
    router.push(`/traders/${addr}`);
  };

  const showPanel = scouting || scout !== null || scoutError !== null;

  const searchBox = (
    <div className="relative w-full">
      {/* A magnifier, not the old bare `/` glyph — at mono 14px the slash sat
          one pixel off the placeholder and read as a rendering artifact
          rather than as "this is a search box". */}
      <svg
        aria-hidden
        className={`absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none transition-colors ${
          isAddrSearch ? "text-green-400" : "text-pixel-gray"
        }`}
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      >
        <circle cx="10.5" cy="10.5" r="6.5" />
        <path d="M15.5 15.5 21 21" />
      </svg>
      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") closeScout();
          if (e.key !== "Enter") return;
          if (isAddrSearch) goToTrader();
          else if (search.trim()) void runScout();
        }}
        placeholder={isAddrSearch ? "press ENTER to view this trader →" : searchPlaceholder}
        className={`pixel-input-sm w-full pr-24 font-mono text-[14px] ${
          isAddrSearch ? "border-green-400 text-green-400" : ""
        }`}
        // `.pixel-input-sm` sets the `padding` shorthand, which beats a
        // `pl-*` utility of equal specificity — the reason the old glyph and
        // the placeholder both started at 10px and collided.
        style={{ paddingLeft: 34 }}
      />
      {isAddrSearch ? (
        <button
          onClick={goToTrader}
          title="Open trader profile"
          className="absolute right-7 top-1/2 -translate-y-1/2 text-[11px] text-green-400 font-mono px-1.5 py-0.5 border border-green-400 rounded-[4px] hover:bg-green-400/10"
        >
          ↵ GO
        </button>
      ) : search.trim() ? (
        <button
          onClick={() => void runScout()}
          disabled={scouting}
          title="Ask the trader scout — an agent queries this console's own leaderboard tools and comes back with the traders that fit"
          className={`absolute right-7 top-1/2 -translate-y-1/2 text-[11px] font-mono px-1.5 py-0.5 border rounded-[4px] transition-colors ${
            scouting
              ? "border-amber-400/60 text-amber-400 animate-pulse"
              : "border-green-400/70 text-green-400 hover:bg-green-400/10"
          }`}
        >
          {scouting ? "SCOUTING…" : "✦ SCOUT"}
        </button>
      ) : null}
      {search && (
        <button
          onClick={() => setSearch("")}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-[13px] text-pixel-gray hover:text-pixel-white"
        >
          x
        </button>
      )}
      {/* ── Scout results — a dropdown under the box, every row a door to
          /traders/<address>. The agent proposes; the profile page (and its
          backtest) is still the judge. ── */}
      {showPanel && (
        <div
          className="absolute left-0 right-0 top-full mt-1 z-50 border border-pixel-border rounded-[4px] bg-[rgb(var(--pixel-black-rgb)/0.97)] backdrop-blur-md shadow-xl"
          style={{ borderColor: "var(--border)" }}
        >
          <div className="flex items-center justify-between px-3 py-1.5 border-b" style={{ borderColor: "var(--border)" }}>
            <span className="text-[11px] tracking-wider text-pixel-gray font-mono">
              <span className="text-green-400/80">✦</span> TRADER SCOUT
              {scout ? <span className="text-pixel-gray-light"> — {scout.ask}</span> : null}
            </span>
            <button
              onClick={closeScout}
              className="text-[13px] text-pixel-gray hover:text-pixel-white px-1"
            >
              x
            </button>
          </div>
          {scouting && (
            <div className="px-3 py-2.5 text-[12px] font-mono text-amber-400 animate-pulse">
              querying the leaderboard through the module&apos;s MCP tools — up to a few
              minutes on a cold window…
            </div>
          )}
          {scoutError && !scouting && (
            <div className="px-3 py-2.5 text-[12px] font-mono text-red-400 leading-snug">{scoutError}</div>
          )}
          {scout && !scouting && (
            <div className="max-h-[60vh] overflow-y-auto">
              {scout.reply && (
                <div className="px-3 py-2 text-[12px] font-mono text-pixel-gray-light leading-snug border-b" style={{ borderColor: "var(--border)" }}>
                  {scout.reply}
                </div>
              )}
              {scout.traders.map((t, i) => (
                <button
                  key={t.address}
                  onClick={() => openScoutTrader(t.address)}
                  title="Open this trader's profile"
                  className="w-full text-left px-3 py-2 font-mono hover:bg-green-400/5 border-b last:border-b-0 group"
                  style={{ borderColor: "var(--border)" }}
                >
                  <div className="flex items-baseline gap-2 min-w-0">
                    <span className="text-[11px] text-pixel-gray shrink-0">{i + 1}.</span>
                    <span className="text-[12px] text-pixel-white truncate">
                      {t.label || `${t.address.slice(0, 6)}…${t.address.slice(-4)}`}
                    </span>
                    <span className="text-[11px] text-pixel-gray truncate">
                      {t.address.slice(0, 6)}…{t.address.slice(-4)}
                    </span>
                    <span className="ml-auto text-[11px] text-green-400 opacity-0 group-hover:opacity-100 shrink-0">
                      VIEW →
                    </span>
                  </div>
                  {t.stat && <div className="text-[11px] text-green-400/80 mt-0.5">{t.stat}</div>}
                  {t.why && <div className="text-[11px] text-pixel-gray leading-snug mt-0.5">{t.why}</div>}
                </button>
              ))}
              {scout.traders.length === 0 && !scout.reply && (
                <div className="px-3 py-2.5 text-[12px] font-mono text-pixel-gray">
                  the scout came back empty.
                </div>
              )}
            </div>
          )}
        </div>
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
        {/* ── Page tabs — top-left corner, and nothing else. Every page is
            laid out inline; nothing here is a dropdown, the header is a row
            you read, not a menu. ── */}
        <div className="flex items-center gap-1 min-w-0">
          <NavMenu />
        </div>
        {/* ── Theme picker + the user column's handle + wallet chip —
            top-right corner. There is no strat readout beside them: the
            console copies one trader at a time, so "which strat am I on" is
            answered by the page you're on (/copy/<address>), not by a global
            picker whose selection could disagree with it. What IS there is the
            copy book itself (UserSidebar) — the leaders, their dollars and
            their backtests — opened from the same corner as the wallet that
            funds them, and from the wallet chip, which has always dispatched
            OPEN_ACCOUNTS_EVENT asking for exactly this column. The picker
            yields on tiny screens so the pair never gets shoved under the left
            cluster. ── */}
        <div className="flex items-center gap-2 min-w-0">
          <div className="hidden min-[480px]:block">
            <ThemePicker />
          </div>
          <UserSidebar />
          <WalletChip />
        </div>
      </div>
      {/* The search box (also a "jump to trader" teleport: paste any 0x
          address + Enter, and the trader-scout agent: describe one + Enter)
          lives on its own full-width row below the header bar. */}
      {showSearch && <div className="px-4 pb-2">{searchBox}</div>}
    </header>
  );
}
