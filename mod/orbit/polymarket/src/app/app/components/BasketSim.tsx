"use client";

// THE BASKET — copy a SET of traders, a different amount against each, and
// replay the whole split over the last N days.
//
// The desk answers "is this leader working" one row at a time, and the profile
// answers "what would $N behind THIS trader have done" (CopySimPanel). Neither
// answers the question you actually have when you have money and a shortlist:
// six names, $2,000, how do I split it — and does the split I chose beat just
// dividing by six?
//
// That question is not the sum of six profile pages, because the amounts
// interact with the gates. $50 behind a whale copies nothing at all: the
// proportional mirror lands under the CLOB's order floor and every entry is
// refused, so an eighth of the bankroll sits in cash for the whole window while
// the desk shows a green "running" pill. This screen exists to make that
// visible BEFORE the money is committed, which is why:
//
//   • the roster table is the results table. The row you're typing a number
//     into is the row that tells you what that number did — you never read a
//     split in one place and its consequences in another.
//   • a leg that traded NOTHING is called out by name, with the smallest
//     amount that would have made it trade (FIND FLOORS). "$0.00 · 0 TXS" on
//     its own reads as break-even; it is not break-even, it is money that
//     never left cash.
//   • the split is scored against EQUAL. If your conviction weights don't beat
//     dividing evenly over the same window, the panel says so.
//
// Nothing here is committed until APPLY TO DESK, which writes each leg to the
// copy book through the same `/copy/allocations` route the MCP tools use. The
// draft roster (lib/basketDraft.ts) is browser-local on purpose — it's a
// shopping list, not a position.
//
// The replay itself is lib/basketSim.ts: one sleeve per leg, each on its own
// capital, summed — because that is what the deployment runs (one allocation =
// one live session with its own budget), not a pooled wallet that would make
// every leg's number depend on the order the others filled in.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import {
  BASKET_TOTALS, assemblePortfolio, basketOverlap, basketTotal, compareToEqualSplit,
  contributionConcentration, equalSplit, replaySleeve, runBasketSim, sleeveFloor,
  summarizeSleeve, weightedSplit,
  type BasketFeeds, type BasketLeg, type BasketPortfolio, type Sleeve, type SplitComparison,
} from "../lib/basketSim";
import {
  addToDraft, basketHref, clearDraft, isAddress, readDraft, removeFromDraft, seedFromQuery,
  writeDraft,
} from "../lib/basketDraft";
import { fetchCopyBook, upsertAllocation, type CopyBookRow } from "../lib/copyBook";
import { shortAddress } from "../lib/identityStrat";
import { HUB_WINDOWS, traderFeed, type TraderFeed } from "../lib/hubReplay";
import { fetchTraderBankrolls } from "../lib/liveSessions";
import { fetchResolvedLegs } from "../lib/hubCache";
import { getOwnerAddress } from "../lib/access";
import type { PolymarketPosition, PolymarketTrade } from "../lib/types";
import { useAuth } from "../context/AuthContext";
import EquityChart from "./EquityChart";

/** What a name added from another screen starts at, before you size it. */
const DEFAULT_LEG_USD = 100;

/** Amount edits re-run the whole basket, so they wait for you to stop typing.
    Long enough that "1000" is one run, short enough to feel live. */
const DEBOUNCE_MS = 450;

function fmtUsd(v: number, digits = 2): string {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const s = a >= 10_000
    ? Math.round(a).toLocaleString("en-US")
    : a.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${v < 0 ? "−" : ""}$${s}`;
}

function fmtPct(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}

/** Mirror size against the leader's own trade. Above 1 your capital dwarfs
    what they deployed, and "7575%" reads as a bug — say ×75.8. */
function fmtRatio(r: number): string {
  if (!r) return "—";
  return r >= 1 ? `×${r.toFixed(1)}` : `${(r * 100).toFixed(2)}%`;
}

function tone(v: number): string {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-pixel-gray-light";
}

export default function BasketSim() {
  const { auth } = useAuth();
  const eoa = getOwnerAddress() ?? auth.address ?? null;
  const router = useRouter();
  const search = useSearchParams();

  // ── The roster ──
  const [legs, setLegs] = useState<BasketLeg[]>([]);
  const [days, setDays] = useState(7);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Draft + `?add=` seed, once. The URL form is how every other screen hands a
  // set of traders over ("+ BASKET" on the leaderboard, on a profile) without
  // knowing where the draft is stored.
  useEffect(() => {
    const seeded = seedFromQuery(search.get("add"), DEFAULT_LEG_USD);
    setLegs(readDraft());
    if (seeded.length > 0) router.replace("/copy/basket");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /** Every mutation goes through here so the draft on disk and the roster on
      screen can never disagree — there is no second source of truth to merge. */
  const commitLegs = useCallback((next: BasketLeg[]) => {
    setLegs(writeDraft(next));
  }, []);

  // The desk, for IMPORT DESK and for showing which legs are already copied.
  const [book, setBook] = useState<CopyBookRow[] | null>(null);
  useEffect(() => {
    let live = true;
    void fetchCopyBook(eoa)
      .then((b) => { if (live) setBook(b.allocations); })
      .catch(() => { if (live) setBook([]); });
    return () => { live = false; };
  }, [eoa]);
  const onDesk = useMemo(
    () => new Map((book ?? []).map((r) => [r.address.toLowerCase(), r])),
    [book],
  );

  // ── Feeds ──
  // One fetch per address for the whole session, shared with every re-run: the
  // amounts change constantly while you're splitting, and the leader's 30-day
  // history does not.
  const feedCache = useRef(new Map<string, Promise<TraderFeed>>());
  const [feeds, setFeeds] = useState<BasketFeeds>({
    trades: new Map(), positions: new Map(), bankrolls: new Map(),
  });
  const [feedsLoading, setFeedsLoading] = useState(false);
  const [missing, setMissing] = useState<string[]>([]);

  const addresses = useMemo(
    () => legs.filter((l) => l.enabled !== false).map((l) => l.address).sort().join(","),
    [legs],
  );

  useEffect(() => {
    const list = addresses ? addresses.split(",") : [];
    if (list.length === 0) {
      setFeeds({ trades: new Map(), positions: new Map(), bankrolls: new Map() });
      setMissing([]);
      return;
    }
    let live = true;
    setFeedsLoading(true);
    void (async () => {
      const tradeMap = new Map<string, PolymarketTrade[]>();
      const posMap = new Map<string, PolymarketPosition[]>();
      const empty: string[] = [];
      // Sequential-ish in small batches: a ten-name basket firing ten
      // paginated /activity walks at once is how this console earns a 429.
      for (let i = 0; i < list.length; i += 3) {
        const slice = list.slice(i, i + 3);
        const got = await Promise.all(slice.map((a) => traderFeed(a, feedCache.current)));
        if (!live) return;
        slice.forEach((addr, j) => {
          tradeMap.set(addr, got[j].trades);
          posMap.set(addr, got[j].positions);
          if (got[j].trades.length === 0) empty.push(addr);
        });
        // Paint what has landed so far — a basket assembles progressively
        // instead of showing nothing until the slowest leader answers.
        setFeeds((prev) => ({ ...prev, trades: new Map(tradeMap), positions: new Map(posMap) }));
      }
      const bankrolls = await fetchTraderBankrolls(list).catch(() => new Map<string, number>());
      if (!live) return;
      // How the window's markets actually paid out. Without this the replay
      // values dead inventory at the last price a leader printed — which books
      // every winner and quietly forgives every loser.
      const cutoff = Date.now() - 30 * 86400_000;
      const ids = new Set<string>();
      for (const feed of tradeMap.values()) {
        for (const trade of feed) {
          if (trade.timestamp >= cutoff && trade.conditionId) ids.add(trade.conditionId);
        }
      }
      const resolved = await fetchResolvedLegs([...ids]).catch(() => new Map<string, number>());
      if (!live) return;
      setFeeds({ trades: tradeMap, positions: posMap, bankrolls, resolved });
      setMissing(empty);
      setFeedsLoading(false);
    })();
    return () => { live = false; };
  }, [addresses]);

  // ── The run ──
  // Debounced, and leg-at-a-time so a big basket paints as it goes rather than
  // locking the tab for several seconds behind one useMemo.
  const [debounced, setDebounced] = useState<{ legs: BasketLeg[]; days: number }>({ legs: [], days });
  useEffect(() => {
    const t = setTimeout(() => setDebounced({ legs, days }), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [legs, days]);

  const [sleeves, setSleeves] = useState<Sleeve[]>([]);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);

  const opts = useMemo(() => ({ days: debounced.days }), [debounced.days]);

  useEffect(() => {
    const active = debounced.legs.filter((l) => l.enabled !== false && l.allocationUsd > 0);
    if (active.length === 0 || feeds.trades.size === 0) {
      setSleeves([]);
      setRunning(false);
      return;
    }
    let live = true;
    setRunning(true);
    setProgress(0);
    const total = active.reduce((s, l) => s + l.allocationUsd, 0);
    void (async () => {
      const out: Sleeve[] = [];
      for (const leg of active) {
        // Yield to the browser between legs — the replay is CPU-bound and a
        // ten-leg basket would otherwise freeze every input on the page.
        await new Promise((r) => setTimeout(r, 0));
        if (!live) return;
        out.push(summarizeSleeve(leg, replaySleeve(leg, feeds, opts), total));
        setSleeves([...out]);
        setProgress(out.length / active.length);
      }
      if (live) setRunning(false);
    })();
    return () => { live = false; };
  }, [debounced.legs, opts, feeds]);

  const portfolio: BasketPortfolio | null = useMemo(
    () => (sleeves.length > 0 ? assemblePortfolio(sleeves) : null),
    [sleeves],
  );
  const overlap = useMemo(() => basketOverlap(sleeves), [sleeves]);
  const concentration = useMemo(() => contributionConcentration(sleeves), [sleeves]);
  const sleeveByAddr = useMemo(
    () => new Map(sleeves.map((s) => [s.address.toLowerCase(), s])),
    [sleeves],
  );

  const total = basketTotal(legs);
  const enabledCount = legs.filter((l) => l.enabled !== false).length;

  // ── The three deeper questions, each paid for on demand ──
  // Every one of these is N more full replays, so none of them runs just
  // because you opened the page.
  const [floors, setFloors] = useState<Map<string, number | null>>(new Map());
  const [floorBusy, setFloorBusy] = useState(false);
  const findFloors = useCallback(async () => {
    setFloorBusy(true);
    const out = new Map<string, number | null>();
    for (const leg of legs.filter((l) => l.enabled !== false)) {
      await new Promise((r) => setTimeout(r, 0));
      out.set(leg.address, sleeveFloor(leg, feeds, opts));
      setFloors(new Map(out));
    }
    setFloorBusy(false);
  }, [legs, feeds, opts]);

  const [comparison, setComparison] = useState<SplitComparison | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);
  const testSplit = useCallback(async () => {
    if (!portfolio) return;
    setCompareBusy(true);
    await new Promise((r) => setTimeout(r, 0));
    setComparison(compareToEqualSplit(legs, feeds, opts, portfolio.net));
    setCompareBusy(false);
  }, [legs, feeds, opts, portfolio]);

  const [ladder, setLadder] = useState<{ total: number; net: number; pct: number; legs: number; executed: number }[]>([]);
  const [ladderBusy, setLadderBusy] = useState(false);
  const sizeBasket = useCallback(async () => {
    setLadderBusy(true);
    const rows: typeof ladder = [];
    const sizes = [...new Set([...BASKET_TOTALS, ...(total > 0 ? [Math.round(total)] : [])])]
      .sort((a, b) => a - b);
    for (const size of sizes) {
      await new Promise((r) => setTimeout(r, 0));
      const run = runBasketSim(weightedSplit(legs, size), feeds, opts);
      rows.push({
        total: size,
        net: run.portfolio.net,
        pct: run.portfolio.pct,
        legs: run.portfolio.legsTrading,
        executed: run.portfolio.executed,
      });
      setLadder([...rows]);
    }
    setLadderBusy(false);
  }, [legs, feeds, opts, total]);

  // Every deeper answer is about the CURRENT split — drop them when it moves,
  // rather than leaving a stale "beats equal by $12" under a roster you just
  // re-weighted.
  useEffect(() => {
    setComparison(null);
    setLadder([]);
    setFloors(new Map());
  }, [addresses, days]);

  // ── Writes ──
  const [applying, setApplying] = useState(false);
  const applyToDesk = useCallback(async () => {
    const active = legs.filter((l) => l.enabled !== false && l.allocationUsd > 0);
    if (active.length === 0) return;
    setApplying(true);
    setError(null);
    try {
      for (const leg of active) {
        await upsertAllocation({
          address: leg.address,
          allocationUsd: leg.allocationUsd,
          ...(leg.label ? { label: leg.label } : {}),
          ...(leg.params ? { params: leg.params } : {}),
        }, eoa);
      }
      const b = await fetchCopyBook(eoa);
      setBook(b.allocations);
      setNotice(
        `${active.length} allocation${active.length === 1 ? "" : "s"} written to the copy desk — ` +
        "nothing is running yet; start them from the desk.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  }, [legs, eoa]);

  const importDesk = useCallback(() => {
    const rows = book ?? [];
    if (rows.length === 0) {
      setNotice("the copy desk is empty — add traders from the leaderboard or paste an address");
      return;
    }
    for (const r of rows) {
      addToDraft({
        address: r.address,
        allocationUsd: r.allocationUsd,
        label: r.label ?? null,
        enabled: r.enabled,
        ...(r.params ? { params: r.params } : {}),
      });
    }
    setLegs(readDraft());
  }, [book]);

  return (
    <div className="space-y-4">
      <Header
        legs={legs}
        total={total}
        enabledCount={enabledCount}
        days={days}
        onDays={setDays}
        running={running || feedsLoading}
        progress={progress}
        onEqual={() => commitLegs(equalSplit(legs, total || DEFAULT_LEG_USD * enabledCount))}
        onRescale={(t) => commitLegs(weightedSplit(legs, t))}
        onImportDesk={importDesk}
        onClear={() => { clearDraft(); setLegs([]); setSleeves([]); }}
      />

      {error && (
        <div className="pixel-panel-red p-3 font-mono text-[12px] text-red-300">{error}</div>
      )}
      {notice && (
        <div className="pixel-panel p-3 font-mono text-[12px] text-pixel-gray-light flex items-start gap-2">
          <span className="flex-1">{notice}</span>
          <button className="pixel-btn text-[10px] px-2" onClick={() => setNotice(null)}>✕</button>
        </div>
      )}

      <AddLeg
        existing={new Set(legs.map((l) => l.address))}
        onAdd={(address, label) => {
          addToDraft({ address, allocationUsd: DEFAULT_LEG_USD, label, enabled: true });
          setLegs(readDraft());
        }}
      />

      {legs.length === 0 ? (
        <EmptyBasket />
      ) : (
        <Roster
          legs={legs}
          sleeves={sleeveByAddr}
          floors={floors}
          onDesk={onDesk}
          total={total}
          days={days}
          missing={new Set(missing)}
          running={running}
          onAmount={(address, usd) =>
            commitLegs(legs.map((l) => (l.address === address ? { ...l, allocationUsd: usd } : l)))
          }
          onToggle={(address) =>
            commitLegs(legs.map((l) =>
              l.address === address ? { ...l, enabled: l.enabled === false } : l))
          }
          onRemove={(address) => { removeFromDraft(address); setLegs(readDraft()); }}
        />
      )}

      {portfolio && (
        <>
          <Result portfolio={portfolio} days={days} running={running} />
          <Diagnostics
            portfolio={portfolio}
            sleeves={sleeves}
            overlap={overlap}
            concentration={concentration}
            floors={floors}
            floorBusy={floorBusy}
            onFindFloors={() => void findFloors()}
            onDropIdle={() =>
              commitLegs(legs.map((l) => {
                const s = sleeveByAddr.get(l.address);
                return s && s.trades === 0 ? { ...l, enabled: false } : l;
              }))
            }
          />
          <SplitTest
            comparison={comparison}
            busy={compareBusy}
            onRun={() => void testSplit()}
            onUseEqual={() => commitLegs(equalSplit(legs, total))}
          />
          <SizeLadder
            rows={ladder}
            busy={ladderBusy}
            current={total}
            onRun={() => void sizeBasket()}
            onUse={(t) => commitLegs(weightedSplit(legs, t))}
          />
        </>
      )}

      {legs.length > 0 && (
        <Commit
          legs={legs}
          onDesk={onDesk}
          eoa={eoa}
          applying={applying}
          onApply={() => void applyToDesk()}
        />
      )}
    </div>
  );
}

// ── Header: the total, the window, and the splits ──────────────────────────

function Header({
  legs, total, enabledCount, days, onDays, running, progress,
  onEqual, onRescale, onImportDesk, onClear,
}: {
  legs: BasketLeg[];
  total: number;
  enabledCount: number;
  days: number;
  onDays: (d: number) => void;
  running: boolean;
  progress: number;
  onEqual: () => void;
  onRescale: (total: number) => void;
  onImportDesk: () => void;
  onClear: () => void;
}) {
  const [draft, setDraft] = useState("");
  useEffect(() => { setDraft(total ? String(Math.round(total)) : ""); }, [total]);

  return (
    <div className="pixel-panel p-4 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="font-mono text-[15px] tracking-[0.18em] text-pixel-green">BASKET</h1>
        <span className="font-mono text-[11px] text-pixel-gray">
          copy a set of traders with a different amount against each — replayed over the last {days}D
        </span>
        <span className="flex-1" />
        <Link href="/copy" className="pixel-btn text-[10px] px-2">← DESK</Link>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="TRADERS" value={String(enabledCount)} sub={`${legs.length} in the basket`} />
        <Stat label="TOTAL" value={fmtUsd(total, 0)} sub="across the enabled legs" />
        <Stat
          label="LARGEST LEG"
          value={fmtUsd(Math.max(0, ...legs.filter((l) => l.enabled !== false).map((l) => l.allocationUsd)), 0)}
          sub={total > 0
            ? `${Math.round((Math.max(0, ...legs.filter((l) => l.enabled !== false).map((l) => l.allocationUsd)) / total) * 100)}% of the basket`
            : "—"}
        />
        <Stat label="WINDOW" value={`${days}D`} sub={running ? `replaying… ${Math.round(progress * 100)}%` : "replayed"} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="font-mono text-[11px] text-pixel-gray tracking-[0.1em]">TOTAL $</label>
        <input
          className="pixel-input-sm w-28 font-mono text-[12px]"
          value={draft}
          inputMode="decimal"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            const v = Number(draft);
            if (Number.isFinite(v) && v > 0 && Math.abs(v - total) > 0.01) onRescale(v);
          }}
          title="Rescale every leg to this total, keeping the proportions you chose"
        />
        <button
          className="pixel-btn text-[11px]"
          onClick={onEqual}
          disabled={enabledCount === 0}
          title="Give every enabled leg the same dollars"
        >
          SPLIT EQUAL
        </button>
        <button
          className="pixel-btn text-[11px]"
          onClick={() => onRescale(total)}
          disabled={total <= 0}
          title="Round the legs back onto the current total, proportions kept"
        >
          RESCALE
        </button>

        <span className="flex-1" />

        <button className="pixel-btn text-[11px]" onClick={onImportDesk} title="Load every trader already on the copy desk, at their current amounts">
          IMPORT DESK
        </button>
        <button className="pixel-btn text-[11px] border-red-500 text-red-400" onClick={onClear} disabled={legs.length === 0}>
          CLEAR
        </button>

        <div className="flex items-center gap-1">
          {HUB_WINDOWS.map((d) => (
            <button
              key={d}
              onClick={() => onDays(d)}
              className={`pixel-btn text-[10px] px-2 ${d === days ? "border-pixel-green text-pixel-green" : ""}`}
              title={`Replay the basket over the last ${d} day(s)`}
            >
              {d}D
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, tone: t }: {
  label: string; value: string; sub?: string; tone?: "warn" | "good" | "bad";
}) {
  const color = t === "warn" ? "text-amber-400"
    : t === "good" ? "text-green-400"
    : t === "bad" ? "text-red-400"
    : "text-pixel-gray-light";
  return (
    <div className="border border-pixel-gray/25 rounded-[4px] px-2.5 py-1.5">
      <div className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">{label}</div>
      <div className={`font-mono text-[15px] ${color}`}>{value}</div>
      {sub && <div className="font-mono text-[9px] text-pixel-gray truncate" title={sub}>{sub}</div>}
    </div>
  );
}

// ── Adding a leg ───────────────────────────────────────────────────────────

function AddLeg({ onAdd, existing }: {
  onAdd: (address: string, label?: string) => void;
  existing: Set<string>;
}) {
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const addr = address.trim().toLowerCase();
  const valid = isAddress(addr);
  const dupe = valid && existing.has(addr);

  return (
    <div className="pixel-panel p-3 flex flex-wrap items-center gap-2">
      <span className="font-mono text-[11px] text-pixel-gray tracking-[0.14em]">ADD A TRADER</span>
      <input
        className="pixel-input-sm flex-1 min-w-[280px] font-mono text-[12px]"
        placeholder="0x…"
        value={address}
        onChange={(e) => setAddress(e.target.value)}
      />
      <input
        className="pixel-input-sm w-40 font-mono text-[12px]"
        placeholder="LABEL (optional)"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
      />
      <button
        className="pixel-btn text-[11px]"
        disabled={!valid || dupe}
        onClick={() => { onAdd(addr, label.trim() || undefined); setAddress(""); setLabel(""); }}
      >
        {dupe ? "ALREADY IN" : "+ BASKET"}
      </button>
      <span className="font-mono text-[10px] text-pixel-gray">
        or add names from the desk&apos;s FIND TRADERS board, or any trader&apos;s profile
      </span>
    </div>
  );
}

function EmptyBasket() {
  return (
    <div className="pixel-panel p-8 text-center space-y-2">
      <div className="font-mono text-[14px] text-pixel-gray-light tracking-wider">THE BASKET IS EMPTY</div>
      <div className="font-mono text-[12px] text-pixel-gray max-w-xl mx-auto">
        Paste an address above, press IMPORT DESK to load everyone you already copy, or use
        <Link href="/copy" className="text-pixel-green"> FIND TRADERS </Link>
        on the desk and hit + BASKET on the names you like. Then give each of them a different
        amount and replay the whole split.
      </div>
    </div>
  );
}

// ── The roster IS the results table ────────────────────────────────────────

function Roster({
  legs, sleeves, floors, onDesk, total, days, missing, running,
  onAmount, onToggle, onRemove,
}: {
  legs: BasketLeg[];
  sleeves: Map<string, Sleeve>;
  floors: Map<string, number | null>;
  onDesk: Map<string, CopyBookRow>;
  total: number;
  days: number;
  missing: Set<string>;
  running: boolean;
  onAmount: (address: string, usd: number) => void;
  onToggle: (address: string) => void;
  onRemove: (address: string) => void;
}) {
  return (
    <div className="pixel-panel overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2.5 border-b-2 border-pixel-border">
        <span className="font-mono text-[13px] text-pixel-white tracking-[0.14em]">THE SPLIT</span>
        <span className="font-mono text-[11px] text-pixel-gray">
          the amount you type is the amount the row was replayed with — and the amount the desk
          would budget
        </span>
        {running && <span className="font-mono text-[10px] text-amber-400 ml-auto">REPLAYING…</span>}
      </div>
      {/* `table-layout: fixed` (globals.css) divides the width evenly unless the
          headers say otherwise, and a fixed cell clips with an ellipsis — so
          every column is sized here, and the one piece of prose (why a leg
          traded nothing) sits UNDER the name where it has room to wrap
          instead of being cut to "all 1255 e…". */}
      <div className="overflow-x-auto">
        <table className="pixel-table w-full text-[12px] min-w-[880px]">
          <thead>
            <tr>
              <th className="w-10"></th>
              <th>TRADER</th>
              <th className="num text-right w-[120px]">AMOUNT $</th>
              <th className="num text-right w-[64px]">SHARE</th>
              <th className="num text-right w-[96px]">{days}D NET</th>
              <th className="num text-right w-[80px]">RETURN</th>
              <th className="num text-right w-[56px]">TXS</th>
              <th className="num text-right w-[104px]">COPIED</th>
              <th className="num text-right w-[88px]">SIZED AT</th>
              <th className="w-12"></th>
            </tr>
          </thead>
          <tbody>
            {legs.map((leg) => (
              <LegRow
                key={leg.address}
                leg={leg}
                sleeve={sleeves.get(leg.address)}
                floor={floors.get(leg.address)}
                desk={onDesk.get(leg.address)}
                total={total}
                noFeed={missing.has(leg.address)}
                onAmount={onAmount}
                onToggle={onToggle}
                onRemove={onRemove}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function LegRow({
  leg, sleeve, floor, desk, total, noFeed, onAmount, onToggle, onRemove,
}: {
  leg: BasketLeg;
  sleeve?: Sleeve;
  floor?: number | null;
  desk?: CopyBookRow;
  total: number;
  noFeed: boolean;
  onAmount: (address: string, usd: number) => void;
  onToggle: (address: string) => void;
  onRemove: (address: string) => void;
}) {
  const [draft, setDraft] = useState(String(leg.allocationUsd || ""));
  useEffect(() => { setDraft(String(leg.allocationUsd || "")); }, [leg.allocationUsd]);

  const off = leg.enabled === false;
  const share = total > 0 && !off ? (leg.allocationUsd / total) * 100 : 0;
  const dead = sleeve && sleeve.trades === 0;

  return (
    <tr className={off ? "opacity-40" : ""}>
      <td>
        <button
          onClick={() => onToggle(leg.address)}
          className={`pixel-btn text-[10px] px-1.5 py-0 ${off ? "" : "border-pixel-green text-pixel-green"}`}
          title={off ? "Include this leg in the basket" : "Park this leg — it stays in the roster with no money"}
        >
          {off ? "○" : "●"}
        </button>
      </td>
      {/* Name, and directly under it the one thing that decides whether the
          amount to its right meant anything. */}
      <td style={{ whiteSpace: "normal", overflow: "visible", textOverflow: "clip" }}>
        <div className="flex items-center gap-2 flex-wrap">
          <Link href={`/traders/${leg.address}`} className="font-mono text-pixel-white hover:text-pixel-green">
            {(leg.label ?? "").trim() || shortAddress(leg.address)}
          </Link>
          {desk && (
            <span className="pixel-badge border-pixel-gray text-pixel-gray" title={`already on the desk at ${fmtUsd(desk.allocationUsd, 0)}`}>
              DESK {fmtUsd(desk.allocationUsd, 0)}
            </span>
          )}
        </div>
        <div className="font-mono text-[10px] leading-snug mt-0.5">
          {noFeed ? (
            <span className="text-amber-400">no history fetched for this leader yet</span>
          ) : dead ? (
            <span className="text-amber-400">
              {sleeve?.note ?? "copied nothing"}
              {floor !== undefined && (
                <span className="text-pixel-gray">
                  {" · "}{floor === null ? "never trades at any size on the ladder" : `needs ~${fmtUsd(floor, 0)}`}
                </span>
              )}
            </span>
          ) : sleeve ? (
            <span className="text-pixel-gray">
              {shortAddress(leg.address)}
              {sleeve.confidence >= 0.999 ? " · fully settled" : ` · ${Math.round(sleeve.confidence * 100)}% settled`}
            </span>
          ) : (
            <span className="text-pixel-gray">{shortAddress(leg.address)}</span>
          )}
        </div>
      </td>
      <td className="num text-right">
        <input
          className="pixel-input-sm w-[86px] font-mono text-[12px] text-right"
          value={draft}
          inputMode="decimal"
          disabled={off}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            const v = Number(draft);
            onAmount(leg.address, Number.isFinite(v) && v > 0 ? v : 0);
          }}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
        />
      </td>
      <td className="num text-right font-mono text-pixel-gray">{off ? "—" : `${share.toFixed(0)}%`}</td>
      <td className={`num text-right font-mono ${sleeve ? tone(sleeve.net) : "text-pixel-gray"}`}>
        {sleeve ? fmtUsd(sleeve.net) : "—"}
      </td>
      <td className={`num text-right font-mono ${sleeve ? tone(sleeve.net) : "text-pixel-gray"}`}>
        {sleeve ? fmtPct(sleeve.pct) : "—"}
      </td>
      <td className="num text-right font-mono text-pixel-gray-light">{sleeve ? sleeve.trades : "—"}</td>
      <td className="num text-right font-mono text-pixel-gray-light" title="leader BUYs this leg mirrored, out of the ones its gate saw">
        {sleeve ? `${sleeve.executed}/${sleeve.observed}` : "—"}
      </td>
      <td className="num text-right font-mono text-pixel-gray-light" title="mirror size against the leader's own trade — ×N means your capital dwarfs theirs and every fill rides the MAX TRADE cap">
        {sleeve ? fmtRatio(sleeve.ratio) : "—"}
      </td>
      <td>
        <button
          className="pixel-btn text-[10px] px-1.5 py-0 border-red-500 text-red-400"
          onClick={() => onRemove(leg.address)}
          title="Take this trader out of the basket"
        >
          ✕
        </button>
      </td>
    </tr>
  );
}

// ── The portfolio ──────────────────────────────────────────────────────────

function Result({ portfolio, days, running }: {
  portfolio: BasketPortfolio; days: number; running: boolean;
}) {
  const idlePct = portfolio.capital > 0 ? (portfolio.idleUsd / portfolio.capital) * 100 : 0;
  return (
    <div className="pixel-panel">
      <div className="flex items-center gap-2 px-3 py-2.5 border-b-2 border-pixel-border">
        <span className="font-mono text-[13px] text-pixel-white tracking-[0.14em]">THE BASKET, REPLAYED</span>
        <span className="font-mono text-[11px] text-pixel-gray">
          every leg on its own capital, summed — which is how the desk runs them
        </span>
        {running && <span className="font-mono text-[10px] text-amber-400 ml-auto">PARTIAL</span>}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-px bg-pixel-border">
        <Cell label={`${days}D NET`} value={fmtUsd(portfolio.net)} tone={portfolio.net} hint="Final simulated equity across every leg, minus what you put in" />
        <Cell label="RETURN" value={fmtPct(portfolio.pct)} tone={portfolio.net} hint={`On ${fmtUsd(portfolio.capital, 0)} of capital`} />
        <Cell label="ENDS WITH" value={fmtUsd(portfolio.endEquity)} tone={portfolio.net} hint="Cash plus whatever the basket still holds" />
        <Cell
          label="LEGS TRADING"
          value={`${portfolio.legsTrading}/${portfolio.legs}`}
          tone={portfolio.legsTrading === portfolio.legs ? 1 : -1}
          hint="Legs that placed at least one order. The rest held cash for the whole window."
        />
        <Cell
          label="IDLE CAPITAL"
          value={fmtUsd(portfolio.idleUsd, 0)}
          tone={portfolio.idleUsd > 0 ? -1 : 0}
          hint={`${idlePct.toFixed(0)}% of the basket never left cash`}
        />
        <Cell
          label="DRAWDOWN"
          value={fmtPct(portfolio.drawdown)}
          tone={-1}
          hint="Deepest peak-to-trough of the combined curve"
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-pixel-border border-t-2 border-pixel-border">
        <Cell label="COPIED" value={`${portfolio.executed}/${portfolio.observed}`} hint="Leader BUYs mirrored, out of those the gates saw" />
        <Cell label="TXS" value={String(portfolio.trades)} hint="Simulated orders, both sides" />
        <Cell label="VOLUME" value={fmtUsd(portfolio.volume, 0)} hint="Executed notional" />
        <Cell
          label="SETTLED"
          value={`${Math.round(portfolio.confidence * 100)}%`}
          tone={portfolio.confidence >= 0.7 ? 0 : -1}
          hint="How much of the result is a real market resolution rather than a last-observed mark. Leaders ride winners out and let losers expire, so a mostly-MARKED number is biased upward."
        />
      </div>

      {portfolio.executed === 0 ? (
        <div className="p-6 text-center space-y-1">
          <div className="font-mono text-[13px] text-amber-400 tracking-wider">THIS BASKET COPIED NOTHING</div>
          <div className="font-mono text-[11px] text-pixel-gray">
            every leg&apos;s mirrors were refused — the chart is suppressed rather than drawing a
            flat line at your starting capital, which reads as &ldquo;went nowhere&rdquo; when the truth is
            &ldquo;never traded&rdquo;. Press FIND FLOORS below for the smallest amount that would.
          </div>
        </div>
      ) : (
        <div className="p-2">
          <EquityChart
            history={portfolio.equity}
            markers={portfolio.markers}
            emptyHint="no simulated trades in this window"
          />
        </div>
      )}
    </div>
  );
}

function Cell({ label, value, tone: t, hint }: {
  label: string; value: string; tone?: number; hint?: string;
}) {
  const color = t === undefined ? "text-pixel-white"
    : t > 0 ? "text-green-400" : t < 0 ? "text-red-400" : "text-pixel-white";
  return (
    <div className="bg-pixel-bg px-3 py-2" title={hint}>
      <div className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">{label}</div>
      <div className={`font-mono text-[15px] ${color}`}>{value}</div>
    </div>
  );
}

// ── What the split is actually doing ───────────────────────────────────────

function Diagnostics({
  portfolio, sleeves, overlap, concentration, floors, floorBusy, onFindFloors, onDropIdle,
}: {
  portfolio: BasketPortfolio;
  sleeves: Sleeve[];
  overlap: ReturnType<typeof basketOverlap>;
  concentration: number;
  floors: Map<string, number | null>;
  floorBusy: boolean;
  onFindFloors: () => void;
  onDropIdle: () => void;
}) {
  const idle = sleeves.filter((s) => s.trades === 0);
  const best = [...sleeves].sort((a, b) => b.net - a.net)[0];
  const worst = [...sleeves].sort((a, b) => a.net - b.net)[0];
  const capped = sleeves.filter((s) => s.ratio >= 1 && s.trades > 0);

  return (
    <div className="pixel-panel">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5 border-b-2 border-pixel-border">
        <span className="font-mono text-[13px] text-pixel-white tracking-[0.14em]">WHAT THE SPLIT DID</span>
        <span className="flex-1" />
        <button className="pixel-btn text-[11px]" onClick={onFindFloors} disabled={floorBusy}>
          {floorBusy ? "SEARCHING…" : "FIND FLOORS"}
        </button>
        {idle.length > 0 && (
          <button className="pixel-btn text-[11px] border-amber-500 text-amber-400" onClick={onDropIdle}>
            PARK THE {idle.length} IDLE LEG{idle.length === 1 ? "" : "S"}
          </button>
        )}
      </div>

      <div className="p-3 space-y-2 font-mono text-[12px]">
        {idle.length > 0 ? (
          <div className="text-amber-400">
            {fmtUsd(portfolio.idleUsd, 0)} across {idle.length} leg{idle.length === 1 ? "" : "s"} never
            traded: {idle.map((s) => s.label).join(", ")}.
            <span className="text-pixel-gray">
              {" "}A leg that copied nothing is not a small position — it is no position, and its
              money sat in cash for the whole window.
              {/* WHY they're idle decides whether money can fix it, so the
                  reasons are named rather than assumed. A sizing gate has a
                  floor; a time-to-close or topic gate does not, and FIND
                  FLOORS says so by finding none. */}
              {floors.size === 0
                ? " FIND FLOORS says whether a bigger amount would have changed that."
                : (() => {
                    const named = idle
                      .map((s) => ({ s, f: floors.get(s.address) }))
                      .filter((x) => x.f !== undefined);
                    if (named.length === 0) return null;
                    const fixable = named.filter((x) => x.f !== null);
                    const never = named.filter((x) => x.f === null);
                    return (
                      <>
                        {fixable.length > 0 && ` ${fixable.map((x) =>
                          `${x.s.label} would trade from ~${fmtUsd(x.f as number, 0)}`).join("; ")}.`}
                        {never.length > 0 && (
                          <span className="text-amber-400">
                            {" "}{never.map((x) => x.s.label).join(", ")} never trade
                            {never.length === 1 ? "s" : ""} at any size on the ladder — that is a gate,
                            not a budget, and no amount of money fixes it.
                          </span>
                        )}
                      </>
                    );
                  })()}
            </span>
          </div>
        ) : (
          <div className="text-pixel-gray">
            Every funded leg placed at least one order — the whole basket was put to work.
          </div>
        )}

        {best && worst && best.address !== worst.address && (
          <div className="text-pixel-gray">
            <span className="text-pixel-gray-light">{best.label}</span> carried the basket at{" "}
            <span className={tone(best.net)}>{fmtUsd(best.net)}</span>;{" "}
            <span className="text-pixel-gray-light">{worst.label}</span> cost it{" "}
            <span className={tone(worst.net)}>{fmtUsd(worst.net)}</span>.
            {concentration > 0.5 && (
              <span className="text-amber-400">
                {" "}One name accounts for most of the movement (concentration {concentration.toFixed(2)}) —
                this is not a diversified result, it is one bet with company.
              </span>
            )}
          </div>
        )}

        {capped.length > 0 && (
          <div className="text-pixel-gray">
            {capped.map((s) => s.label).join(", ")} {capped.length === 1 ? "is" : "are"} sized at or
            above the leader&apos;s own trade — every mirror rides the MAX TRADE cap, so more money
            behind {capped.length === 1 ? "that name" : "those names"} buys no more exposure.
          </div>
        )}

        {overlap.markets > 0 && (
          <div className="text-pixel-gray">
            {overlap.legsPaired} legs traded the same {overlap.markets} market
            {overlap.markets === 1 ? "" : "s"}
            {overlap.worst && ` (worst: “${overlap.worst.market}”, ${overlap.worst.legs.length} legs)`} —
            the desk would place both orders, so the basket is less diversified than its name count.
          </div>
        )}

        {portfolio.confidence < 0.7 && (
          <div className="text-amber-400">
            Only {Math.round(portfolio.confidence * 100)}% of this result is a settled market. The rest
            values inventory at the last price a leader printed, which books winners and forgives
            losers — read the number as an upper bound.
          </div>
        )}
      </div>
    </div>
  );
}

// ── Was the split worth choosing? ──────────────────────────────────────────

function SplitTest({ comparison, busy, onRun, onUseEqual }: {
  comparison: SplitComparison | null;
  busy: boolean;
  onRun: () => void;
  onUseEqual: () => void;
}) {
  return (
    <div className="pixel-panel">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5 border-b-2 border-pixel-border">
        <span className="font-mono text-[13px] text-pixel-white tracking-[0.14em]">DID THE SPLIT PAY?</span>
        <span className="font-mono text-[11px] text-pixel-gray">
          same names, same total, same window — divided evenly instead
        </span>
        <span className="flex-1" />
        <button className="pixel-btn text-[11px]" onClick={onRun} disabled={busy}>
          {busy ? "REPLAYING…" : comparison ? "RE-TEST" : "TEST THE SPLIT"}
        </button>
      </div>
      {comparison && (
        <div className="p-3 font-mono text-[12px] space-y-1">
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <span className="text-pixel-gray">YOUR SPLIT <span className={tone(comparison.chosen)}>{fmtUsd(comparison.chosen)}</span></span>
            <span className="text-pixel-gray">EQUAL SPLIT <span className={tone(comparison.equal)}>{fmtUsd(comparison.equal)}</span></span>
            <span className="text-pixel-gray">EDGE <span className={tone(comparison.edge)}>{fmtUsd(comparison.edge)}</span></span>
          </div>
          <div className="text-pixel-gray">
            {comparison.differs === 0
              ? "your amounts are already an even split — there is nothing to compare"
              : comparison.edge > 0
                ? `sizing ${comparison.differs} leg${comparison.differs === 1 ? "" : "s"} away from even was worth ${fmtUsd(comparison.edge)} over this window. One window is not a result — re-test on a different N before believing it.`
                : `dividing evenly would have done ${fmtUsd(-comparison.edge)} better. Your conviction weights did not pay for themselves here.`}
          </div>
          {comparison.edge < 0 && (
            <button className="pixel-btn text-[11px]" onClick={onUseEqual}>USE THE EQUAL SPLIT</button>
          )}
        </div>
      )}
    </div>
  );
}

// ── How big does this basket need to be? ───────────────────────────────────

function SizeLadder({ rows, busy, current, onRun, onUse }: {
  rows: { total: number; net: number; pct: number; legs: number; executed: number }[];
  busy: boolean;
  current: number;
  onRun: () => void;
  onUse: (total: number) => void;
}) {
  return (
    <div className="pixel-panel">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5 border-b-2 border-pixel-border">
        <span className="font-mono text-[13px] text-pixel-white tracking-[0.14em]">SIZE THE BASKET</span>
        <span className="font-mono text-[11px] text-pixel-gray">
          the same split at different totals — copying is not linear in the money
        </span>
        <span className="flex-1" />
        <button className="pixel-btn text-[11px]" onClick={onRun} disabled={busy}>
          {busy ? "REPLAYING…" : rows.length ? "RE-RUN" : "RUN THE LADDER"}
        </button>
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="pixel-table w-full text-[12px]">
            <thead>
              <tr>
                <th className="num text-right">TOTAL</th>
                <th className="num text-right">NET</th>
                <th className="num text-right">RETURN</th>
                <th className="num text-right">LEGS TRADING</th>
                <th className="num text-right">COPIED</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.total} className={Math.abs(r.total - current) < 1 ? "text-pixel-green" : ""}>
                  <td className="num text-right font-mono">{fmtUsd(r.total, 0)}</td>
                  <td className={`num text-right font-mono ${tone(r.net)}`}>{fmtUsd(r.net)}</td>
                  <td className={`num text-right font-mono ${tone(r.net)}`}>{fmtPct(r.pct)}</td>
                  <td className="num text-right font-mono text-pixel-gray-light">{r.legs}</td>
                  <td className="num text-right font-mono text-pixel-gray-light">{r.executed}</td>
                  <td className="text-right">
                    {Math.abs(r.total - current) >= 1 && (
                      <button className="pixel-btn text-[10px] px-2" onClick={() => onUse(r.total)}>USE</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Commit ─────────────────────────────────────────────────────────────────

function Commit({ legs, onDesk, eoa, applying, onApply }: {
  legs: BasketLeg[];
  onDesk: Map<string, CopyBookRow>;
  eoa: string | null;
  applying: boolean;
  onApply: () => void;
}) {
  const active = legs.filter((l) => l.enabled !== false && l.allocationUsd > 0);
  const changed = active.filter((l) => {
    const row = onDesk.get(l.address);
    return !row || Math.abs(row.allocationUsd - l.allocationUsd) > 0.01;
  });
  const total = active.reduce((s, l) => s + l.allocationUsd, 0);

  return (
    <div className="pixel-panel p-3 flex flex-wrap items-center gap-3">
      <div className="flex-1 min-w-[260px] font-mono text-[11px] text-pixel-gray">
        APPLY writes {active.length} allocation{active.length === 1 ? "" : "s"} totalling{" "}
        <span className="text-pixel-gray-light">{fmtUsd(total, 0)}</span> to the copy desk —{" "}
        {changed.length === 0
          ? "the desk already matches this basket"
          : `${changed.length} of them differ from what the desk holds`}
        . It places nothing and starts nothing: every session begins stopped, and going LIVE is
        still a separate act on the desk.
      </div>
      <Link href="/copy" className="pixel-btn text-[11px]">OPEN THE DESK</Link>
      <button
        className="pixel-btn text-[12px] border-pixel-green text-pixel-green"
        disabled={applying || active.length === 0 || !eoa}
        onClick={onApply}
        title={eoa ? "Write every leg to the copy book" : "no wallet — the basket replays, but nothing can be written"}
      >
        {applying ? "WRITING…" : "APPLY TO DESK"}
      </button>
    </div>
  );
}

/** The href other screens use to throw a set of names at the basket. */
export { basketHref };
