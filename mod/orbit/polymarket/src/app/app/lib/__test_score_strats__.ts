// SCORE STRATS — every score function is a strat that copies its top N.
//
//   cd src/app && npx tsx app/lib/__test_score_strats__.ts
//
// Pins the pure half of lib/scoreStrats.ts: ranking honors a function's
// filter contract (null hides), a re-rank keeps the user's edits, the
// out-of-sample board is never AFTER the window it grades, and every builtin
// shelf listing actually compiles into a roster.

import {
  activeAt, isScoreStratId, pickScan, rankByScore, sameRoster, scoreStratId, scoreStratIndex,
  sourceHash, SCORE_STRAT_BOARD_DAYS,
} from "./scoreStrats";
import { compileScore, detectScoreLang } from "./scoreFormula";
import { SCORE_FN_LIBRARY, type ScoreFnListing } from "./scoreMarket";
import { WARMED_CANDIDATE_POOL, type ScanMeta, type TopTrader } from "./polymarket";
import { traderIndexTemplate } from "./defaultStrats";

let failed = 0;
function check(label: string, ok: boolean, detail?: string) {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}${detail ? " — " + detail : ""}`);
  if (!ok) failed++;
}

function trader(address: string, o: Partial<TopTrader> = {}): TopTrader {
  return {
    address, volume: 1000, buyVolume: 600, sellVolume: 400, pnl: 100, winRate: 60, resolveRate: 50,
    decidedPositions: 20, sharpe: 1, exitEntry: 1.1, positions: 20, marketTitles: ["a", "b", "c"],
    recentTrades: 5, pnlCurve: [0, 10, 20, 30, 40, 50, 60, 70], lastTradeTs: 1_000_000, ...o,
  } as TopTrader;
}

console.log("rankByScore");
{
  const rows = [
    trader("0xB", { pnl: 300 }),
    trader("0xa", { pnl: 300 }),
    trader("0xc", { pnl: 50 }),
    trader("0xd", { pnl: 900, volume: 10 }), // the function hides it
    trader("0xA", { pnl: 999 }), // duplicate address, different case → first wins
  ];
  const fn = compileScore("if (volume < 500) return null;\nreturn pnl;").fn!;
  const r = rankByScore(rows, fn, 2);
  check("null-filtered rows never rank", !r.some((x) => x.address === "0xd"));
  check("keeps N", r.length === 2);
  check("ties break by address (stable roster)", r[0].address === "0xa" && r[1].address === "0xB", JSON.stringify(r));
  const sink = compileScore("pnl / 0 * 0").fn!; // NaN → -Infinity sink
  check("non-finite scores are dropped, not ranked last", rankByScore(rows, sink, 5).length === 0);
  check("N=0 → empty", rankByScore(rows, fn, 0).length === 0);
}

console.log("activeAt");
{
  const ref = 1_790_000_000;
  const rows = [trader("0x1", { lastTradeTs: ref - 60 }), trader("0x2", { lastTradeTs: ref - 7 * 3600 }), trader("0x3", { lastTradeTs: undefined })];
  const kept = activeAt(rows, ref, 6).map((t) => t.address);
  check("6h floor judged against the scan's own clock; unknown = inactive", kept.join() === "0x1", kept.join());
}

console.log("pickScan");
{
  const key = `${SCORE_STRAT_BOARD_DAYS}:0:${WARMED_CANDIDATE_POOL}`;
  const scan = (id: number, w: Partial<ScanMeta["windows"][number]> = {}): ScanMeta => ({
    id, startedAt: id, finishedAt: id + 60, trigger: "hourly",
    windows: [{ key, days: SCORE_STRAT_BOARD_DAYS, minPerDay: 0, pool: WARMED_CANDIDATE_POOL, count: 100, syncedAt: id, bytes: 1, ...w }],
  });
  const target = 100_000;
  const scans = [scan(target + 3600), scan(target - 3600), scan(target - 7200), scan(target - 60, { skipped: true, count: 0 })];
  check("never a board AFTER the window starts", pickScan(scans, target)?.id === target - 3600);
  check("skipped windows carry no rows → passed over", pickScan(scans, target)?.id !== target - 60);
  check("too far back → null, not a wrong day", pickScan([scan(target - 13 * 3600)], target) === null);
  check("other windows don't count", pickScan([{ ...scan(target - 60), windows: [{ ...scan(0).windows[0], key: "30:0:2000" }] }], target) === null);
}

console.log("scoreStratIndex");
{
  const l: ScoreFnListing = { id: "steady-roi", name: "STEADY ROI", description: "", tags: [], source: "return pnl;", builtin: true };
  const ranked = [{ address: "0xAA", score: 2.5 }, { address: "0xbb", score: 1 }];
  const fresh = scoreStratIndex(l, ranked, 5, undefined, 42);
  check("stable id per listing", fresh.id === "scorefn-steady-roi" && isScoreStratId(fresh.id));
  check("community ids are namespaced", scoreStratId({ id: "steady-roi", builtin: false }) === "scorefn-c-steady-roi");
  check("generated name says the rule", fresh.name === "TOP 5 · STEADY ROI", fresh.name);
  check("TRADER INDEX sizing (the default strat)", fresh.sizing === traderIndexTemplate().params.sizing);
  check("starts stopped", fresh.liveEnabled === false);
  check("roster = the ranking, equal weight", fresh.traders.length === 2 && Math.abs(fresh.traders.reduce((s, t) => s + t.weight, 0) - 1) < 1e-9);
  check("scores stamped lowercase", fresh.scoreFn?.scores["0xaa"] === 2.5);
  check("source hash stamped", fresh.scoreFn?.sourceHash === sourceHash(l.source));

  const edited = { ...fresh, name: "my name", capital: 5000, stopLoss: 0.5, scoreFn: { ...fresh.scoreFn!, oos: { scanId: 1, testDays: 3, picks: 2, pnl: 1, roi: 0.1, trades: 3, capital: 1000, curve: [], at: 1 } } };
  const rer = scoreStratIndex(l, [{ address: "0xcc", score: 9 }], 5, edited, 99);
  check("re-rank keeps user edits", rer.name === "my name" && rer.capital === 5000 && rer.stopLoss === 0.5 && rer.id === fresh.id);
  check("re-rank replaces the roster", rer.traders.length === 1 && rer.traders[0].address === "0xcc");
  check("re-rank keeps the last grade", rer.scoreFn?.oos?.scanId === 1);
  check("sameRoster ignores order + case", sameRoster(
    [{ address: "0xA", weight: 0.5 }, { address: "0xb", weight: 0.5 }],
    [{ address: "0xB", weight: 0.5 }, { address: "0xa", weight: 0.5 }],
  ));
  check("sameRoster sees a swap", !sameRoster([{ address: "0xa", weight: 1 }], [{ address: "0xc", weight: 1 }]));
}

console.log("builtin shelf → strats");
{
  const board = [
    trader("0x01"),
    trader("0x02", { pnl: 500, volume: 2000, decidedPositions: 40, winRate: 70, resolveRate: 65 }),
    trader("0x03", { pnl: -50, pnlCurve: [0, -5, -10, -20, -30, -40, -45, -50] }),
  ];
  for (const l of SCORE_FN_LIBRARY) {
    if (detectScoreLang(l.source) === "py") {
      check(`${l.name}: python (Pyodide, browser-only — not run here)`, true);
      continue;
    }
    const c = compileScore(l.source);
    const r = c.fn ? rankByScore(board, c.fn, 5) : [];
    check(`${l.name}: compiles and ranks someone`, !!c.fn && r.length > 0, c.error ?? `${r.length} ranked`);
  }
}

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
