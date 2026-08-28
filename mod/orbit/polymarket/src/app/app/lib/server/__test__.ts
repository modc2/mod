// End-to-end check of the fetch/replay split. Run against a live deployment:
//
//   cd src/app && npx tsx app/lib/server/__test__.ts
//
// It fetches a couple of real traders into the feed store, then replays the
// published manifest OUT of that store with the network loader nowhere in
// sight, and asserts the second half spent no upstream requests.

import { readManifest } from "./hubWorker";
import { coverage, readFeed, readMeta } from "./feedStore";
import { TRADES_TTL_MS, feedSession, refreshRoster } from "./feedFetcher";
import { backtestOne, type TraderFeed } from "../hubReplay";
import { mintOwnerToken } from "./ownerToken";
import { setServerAuthToken } from "../polymarket";

function ok(cond: unknown, msg: string): void {
  if (!cond) throw new Error(`FAIL: ${msg}`);
  console.log(`  ok — ${msg}`);
}

async function main() {
  const token = mintOwnerToken();
  ok(token, "minted an owner token from server.secret");
  setServerAuthToken(token);

  const manifest = readManifest();
  const roster = [
    ...new Set(
      manifest.strats.flatMap((s) =>
        s.traders.filter((t) => t.enabled !== false).map((t) => t.address.toLowerCase()),
      ),
    ),
  ];
  console.log(`manifest: ${manifest.strats.length} strats, ${roster.length} traders, ${manifest.days}d`);
  ok(roster.length > 0, "the manifest names traders to warm");

  console.log("\n─ fetch loop (budget 3) ─");
  const t0 = Date.now();
  const stats = await refreshRoster(roster, { budget: 3 });
  console.log(`  ${JSON.stringify(stats)}  (${Date.now() - t0}ms)`);
  ok(stats.synced <= 3, "the per-cycle budget is respected");
  ok(stats.coverage.cached > 0, "the store holds at least one feed");

  const first = roster.find((a) => readMeta(a)?.tradesAt);
  const feed = readFeed(first!)!;
  ok(feed.trades.length === feed.meta.tradeCount, `${first!.slice(0, 10)}… cached ${feed.trades.length} trades, meta agrees`);
  ok(feed.meta.coveredFromMs > 0, "coverage window recorded");

  console.log("\n─ second cycle is a no-op (nothing is stale yet) ─");
  const again = await refreshRoster(roster.slice(0, 3), { budget: 3 });
  ok(again.synced === 0, "a fresh feed is not re-fetched");

  console.log("\n─ forced re-sync is incremental, not another 30-day walk ─");
  const cached = roster.filter((a) => readMeta(a)?.tradesAt).slice(0, 3);
  const before = cached.map((a) => readMeta(a)!.tradeCount);
  const t2 = Date.now();
  const inc = await refreshRoster(cached, { budget: 3, maxAgeMs: 0 });
  const elapsed = Date.now() - t2;
  const after = cached.map((a) => readMeta(a)!.tradeCount);
  console.log(`  ${before} → ${after} in ${elapsed}ms`);
  ok(inc.synced === cached.length && inc.cold === 0, "re-synced without any cold backfill");
  ok(after.every((n, i) => n >= before[i]), "an incremental sync never loses cached trades");
  // The cold walk above took ~800ms/trader over dozens of pages; one page each
  // is the whole point of keeping the store.
  ok(elapsed < 20_000, `incremental cycle stayed cheap (${elapsed}ms)`);

  console.log("\n─ replay out of the store, no network ─");
  const session = feedSession({ coldBudget: 0 });
  const cache = new Map<string, Promise<TraderFeed>>();
  const idx = manifest.strats.find((s) =>
    s.traders.some((t) => t.enabled !== false && readMeta(t.address)?.tradesAt),
  );
  ok(idx, "found a published strat with at least one cached trader");
  const t1 = Date.now();
  const bt = await backtestOne(idx!, manifest.days, cache, session.load);
  console.log(`  ${JSON.stringify({ ...bt, curve: `[${bt?.curve.length}]` })}  (${Date.now() - t1}ms)`);
  ok(bt, "the replay produced a result");
  ok(session.stats.cold === 0, "no cold fetch (coldBudget 0)");
  ok(session.stats.hits + session.stats.stale > 0, "traders were served from the store");
  console.log(`  session: ${JSON.stringify(session.stats)}, pending ${session.pending.size}`);

  console.log(`\ncoverage: ${JSON.stringify(coverage(roster, TRADES_TTL_MS))}`);
  console.log("\nPASS");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
