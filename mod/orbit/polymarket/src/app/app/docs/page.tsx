"use client";

import Link from "next/link";

// ── Reference data ───────────────────────────────────────────────

interface Endpoint {
  method: "GET" | "POST";
  path: string;
  description: string;
  params?: { name: string; type: string; desc: string; required?: boolean }[];
  body?: { name: string; type: string; desc: string }[];
  example?: string;
}

// The five hooks of the Strat class — src/app/app/lib/strats/strat.ts.
const STRAT_HOOKS = [
  {
    hook: "maxPerCycle()",
    returns: "number",
    desc: "Per-cycle BUY budget. Top-N sampling keeps only this many BUYs by score (SELLs always execute). The single most important fee-control knob.",
    def: "params.maxPerCycle ?? 3",
  },
  {
    hook: "shouldMirror(trade, history)",
    returns: "boolean",
    desc: "Pre-filter observed upstream trades before scoring/sizing. False = skip entirely.",
    def: "marketQuery + tradeFilters gates from params",
  },
  {
    hook: "scoreCandidate(trade, stats, history)",
    returns: "number ($ edge)",
    desc: "Rank BUY candidates by expected dollars of edge. The engine copies the top maxPerCycle; the same number drives EP-based capital rotation. ≤ 0 never executes.",
    def: "trader ROI × mirror$ (roi × notional × copyRatio)",
  },
  {
    hook: "sizeAndPrice(trade, constraints, history)",
    returns: "{ mirrorNotional, limitPrice, reason? }",
    desc: "Final USD size + limit price for a candidate that already won the rank race. mirrorNotional 0 = skip with reason.",
    def: "account-value-proportional mirror, clamped to user floor/ceiling + CLOB per-price min, refused past maxUpscale — see COPY SIZING",
  },
  {
    hook: "propose(history, constraints)",
    returns: "ProposedTrade[]",
    desc: "ORIGINATE trades from history alone — no upstream trade required. Runs once per cycle after the mirror pass with the complete watchlist history. This is how momentum / mean-reversion / market-making strats plug into the same engine.",
    def: "[] (pure copy strat)",
  },
];

// StratHistory — what every hook receives. src/app/app/lib/strats/strat.ts.
const HISTORY_FIELDS = [
  { name: "trades", type: "TraderTrade[]", desc: "Observed upstream trades across ALL watched traders inside the lookback window (backtestDays), newest-first. Each carries trader address, watchlist weight, proportional copyRatio, and $ notional." },
  { name: "traderStats", type: "Record<address, TraderRoiStats>", desc: "Per-trader window ROI / stdev / Sharpe / sample size — same stats that drive scoring." },
  { name: "positions", type: "PolymarketPosition[]", desc: "Open positions in the trading wallet. Fetched per-cycle only for strats that originate trades (proposes() true); empty otherwise (and in backtests)." },
  { name: "balance", type: "number | null", desc: "Usable USDC in the trading wallet. null = not read yet (backtests)." },
  { name: "capital", type: "number", desc: "The strat's allocated capital in USD." },
  { name: "watchlist", type: "IndexTrader[]", desc: "Enabled traders with their weights." },
  { name: "cycle / now", type: "number", desc: "Engine cycle counter (0 in backtest) and the history's assembly clock (ms epoch)." },
];

const PROPOSED_FIELDS = [
  { name: "conditionId", type: "string", desc: "Market condition id — the engine resolves outcome → CLOB token id." },
  { name: "outcome", type: "\"Yes\" | \"No\"", desc: "Defaults to Yes." },
  { name: "market", type: "string", desc: "Market title, for the execution log." },
  { name: "side", type: "\"BUY\" | \"SELL\"", desc: "Direction." },
  { name: "notional", type: "number", desc: "Order size in USD. Engine clamps up to the CLOB per-price floor (max($1, 5 × price))." },
  { name: "limitPrice", type: "number", desc: "0–1; engine tick-rounds to the 1¢ grid." },
  { name: "reason", type: "string?", desc: "Shown verbatim in the execution log next to the order." },
];

const STRAT_MODES = [
  {
    name: "mirror (default)",
    kind: "mirror",
    desc: "Mirrors watched traders' fills proportionally, ranked by ROI-weighted expected profit, limit prices slippage-widened toward the fillable side. This is `new Strat(params)` with no extras — every knob is a param.",
    params: "maxPerCycle · marketQuery · tradeFilters · slippageBps",
  },
  {
    name: "flow origination",
    kind: "history-driven",
    desc: "Set `flow: {…}` (and optionally `mirror: false`) to originate from history instead of per-trade mirroring: aggregates window flow across the watchlist and proposes entries where ≥ minTraders pile into the same side (net flow ≥ minFlowUsd), exits held positions when flow flips net-SELL.",
    params: "flow.lookbackMinutes · flow.minTraders · flow.minFlowUsd · flow.maxPositions · mirror",
  },
  {
    name: "price momentum origination",
    kind: "history-driven",
    desc: "Set `momentum: {…}` to trade the market's OWN odds — no watchlist needed. The engine feeds CLOB price history for markets matching `momentum.query` (default: marketQuery, else \"bitcoin\"), and a comma-separated query is SEARCHED PER GROUP and merged, so \"bitcoin, ethereum, solana\" covers three assets in one strat — always comma-separate a multi-asset query, since one string of coin names is ranked as a phrase and finds markets naming all of them instead of each coin's own markets. The strat BUYs the outcome that rose ≥ minRiseCents over the lookback (BTC-up going 50¢→60¢ = ride it) inside the price band, and SELLs a held outcome once it falls exitDropCents. `confirmMinutes` adds a second, shorter window an ENTRY must also be intact over — that's what separates a move still running from one that already peaked. Markets resolving within minMinutesToClose are skipped — sub-hour Up/Down markets are HFT-bot turf.",
    params: "momentum.query · momentum.lookbackMinutes · momentum.minRiseCents · momentum.confirmMinutes · momentum.exitDropCents · momentum.minPrice/maxPrice · momentum.maxPositions · momentum.maxMarkets · momentum.minMinutesToClose",
  },
];

const CUSTOM_STRAT_EXAMPLE = `// A strategy IS one class: new Strat(params). Configure, don't subclass:
import { Strat } from "./strat";

const mirror = new Strat({
  name: "CONSERVATIVE FAVORITES",
  maxPerCycle: 1,
  tradeFilters: { sides: "buy", minPrice: 0.65, maxPrice: 0.95 },
  slippageBps: 300,
});

const momentum = new Strat({
  name: "FLOW MOMENTUM",
  mirror: false,                       // never mirror per-trade
  flow: { minTraders: 2, minFlowUsd: 50, lookbackMinutes: 90 },
});

// Behavior no param expresses? Override a hook and hand the instance
// to the engine: new CopyEngine(config, myStrat).
class MyStrat extends Strat {
  scoreCandidate(trade, stats, h) {
    const flowInMarket = h.trades
      .filter((t) => t.conditionId === trade.conditionId && t.side === "BUY")
      .reduce((s, t) => s + t.notional, 0);
    const base = stats ? stats.roi * trade.notional * trade.copyRatio : 0;
    return base * (1 + Math.log1p(flowInMarket) / 10);
  }
}`;

// The COPY DESK's own surface. These are the routes the browser desk calls
// AND the routes the pm_copy_* MCP tools call — listed first because they are
// the ones you reach for.
const COPY_ENDPOINTS: Endpoint[] = [
  {
    method: "GET",
    path: "/copy/book",
    description:
      "The desk: every copied trader with their allocation, plus (when ?eoa is given) that " +
      "wallet's session per trader — running, TEST vs LIVE, orders placed, " +
      "realized P&L, last fill — and the roll-up totals.",
    params: [
      { name: "eoa", type: "0x…", desc: "Wallet whose sessions to report. Omit for the book alone." },
    ],
    example: "GET /api/polymarket/copy/book?eoa=0x89bc…",
  },
  {
    method: "POST",
    path: "/copy/allocations",
    description:
      "Copy a trader with a given number of dollars — or change the amount if they're already " +
      "on the desk. Idempotent by address: one leader, one allocation, one session. A running " +
      "session is reconfigured in place, keeping its execution mode. Nothing is placed.",
    body: [
      { name: "address", type: "0x…", desc: "The trader to copy." },
      { name: "allocationUsd", type: "number", desc: "Dollars behind them. This IS the position sizing — the engine budgets against it and the backtest replays with it." },
      { name: "label", type: "string?", desc: "Display name. Absent ⇒ a short address." },
      { name: "notes", type: "string?", desc: "Why you're copying them." },
      { name: "enabled", type: "boolean?", desc: "false pauses them without forgetting them." },
      { name: "params", type: "object?", desc: "Per-trader overrides on the identity template (minTrade, maxTrade, maxPerCycle, maxOpenPositions, pollMinutes, backtestDays, sizing, turnover, stopLoss, takeProfit, minMinutesToClose, maxTradeAgeSec, marketQuery, tradeFilters). A PATCH — omitted knobs keep their value. The GATE pair is marketQuery (which markets, by title — commas OR, spaces AND) + tradeFilters ({sides, minPrice, maxPrice, minNotional, maxNotional}: which trades inside them); the sentence box on /copy/trades compiles plain language into exactly that pair." },
    ],
    example: `POST /copy/allocations {"address":"0xab…","allocationUsd":250}`,
  },
  {
    method: "POST",
    path: "/copy/allocations/{address}  (DELETE)",
    description:
      "Stop copying a trader: ends their session (when ?eoa is given) and drops them from the " +
      "book. Their realized P&L survives in the engine ledger.",
    params: [{ name: "eoa", type: "0x…", desc: "Wallet whose session to stop." }],
  },
  {
    method: "POST",
    path: "/copy/rebalance",
    description:
      "Split a bankroll across every ENABLED trader. Running sessions pick up their new size " +
      "immediately; paused traders are left alone.",
    body: [
      { name: "bankroll", type: "number", desc: "Total dollars to split." },
      { name: "mode", type: "string", desc: "\"equal\" (default) — everyone the same. \"weighted\" — rescale the amounts you already set, so conviction survives a deposit." },
    ],
  },
  {
    method: "POST",
    path: "/copy/start",
    description:
      "Start copying — one trader with `address`, or every enabled trader without it. " +
      "DEFAULTS TO TEST: every mirror is computed and none is placed. The trading wallet is " +
      "derived from the EOA's backend signer unless you name one.",
    body: [
      { name: "eoa", type: "0x…", desc: "Wallet to run under." },
      { name: "address", type: "0x…?", desc: "One trader. Omit for the whole desk." },
      { name: "autoExecute", type: "boolean", desc: "true = LIVE — REAL orders with real money. Omitted ⇒ false ⇒ TEST. (The response still spells the mode \"DRY RUN\" for older clients.)" },
      { name: "proxyAddress", type: "0x…?", desc: "Override the derived trading wallet." },
    ],
  },
  {
    method: "POST",
    path: "/copy/stop",
    description: "Stop one trader's session, or the whole desk. The allocation and the ledger survive.",
    body: [
      { name: "eoa", type: "0x…", desc: "Wallet." },
      { name: "address", type: "0x…?", desc: "One trader. Omit for every session on the desk." },
    ],
  },
  {
    method: "POST",
    path: "/polymarket/api/basket",
    description:
      "THE BASKET — replay a SET of traders, each on its OWN capital, as one portfolio. Runs " +
      "on the Next app (not the Rust API), out of the background worker's feed store, so a " +
      "call costs CPU rather than a burst of upstream walks. It places nothing and writes " +
      "nothing: sizing a basket is not committing to it.",
    body: [
      { name: "legs", type: "array", desc: "[{address, allocationUsd, label?, params?}] — params is the same per-allocation patch /copy/allocations takes, so a leg can carry its own gates." },
      { name: "fromDesk", type: "boolean?", desc: "Replay the copy desk as it stands instead of naming legs." },
      { name: "days", type: "number", desc: "Window, 1–30 (default 7)." },
      { name: "total", type: "number?", desc: "Rescale the legs to this total, keeping their proportions." },
      { name: "split", type: "string?", desc: "\"equal\" divides `total` evenly instead of keeping proportions." },
      { name: "compare", type: "boolean?", desc: "Also replay the same total divided EVENLY and report the edge — did choosing different amounts pay?" },
      { name: "floors", type: "boolean?", desc: "Also find the smallest amount at which each leg trades at all. null ⇒ that leg never trades at any size on the ladder." },
      { name: "ladder", type: "number[]?", desc: "Also replay the whole split at these totals — copying is not linear in the money." },
    ],
    example: `POST /polymarket/api/basket {"legs":[{"address":"0xab…","allocationUsd":700},{"address":"0xcd…","allocationUsd":300}],"days":7,"compare":true}`,
  },
  {
    method: "GET",
    path: "/polymarket/api/copytrades",
    description:
      "RESULTS (my copy trades) — every trade the desk's leaders made, joined to every on-chain fill of " +
      "mine. Nothing upstream links them (a fill carries no leader tag), so the join is " +
      "inferred: same market, same side, my fill at or after theirs inside the match window, " +
      "nearest wins, one leader trade claimed once. Answers with coverage (what share of their " +
      "flow actually landed), median lag, signed slippage in cents, a per-leader roll-up, and " +
      "the rows. Fills with no leader behind them are reported as `unattributed` rather than " +
      "credited to somebody. Runs on the Next app, out of the worker's feed store.",
    params: [
      { name: "days", type: "number", desc: "Window, 1–30 (default 7)." },
      { name: "q", type: "string?", desc: "Plain-language filter — \"big buys on crypto under 30c\", \"missed longshots\", \"politics, not candles\". The answer echoes how it was read (`query.chips`) and the enforceable gate it compiles to (`query.gate`)." },
      { name: "matchMinutes", type: "number?", desc: "How long after a leader's trade a fill may still count as mirroring it (default 30)." },
    ],
    example: "GET /polymarket/api/copytrades?days=7&q=missed+longshots",
  },
  {
    method: "GET",
    path: "/copy/strats",
    description:
      "The book as identity strats — the exact objects the live engine runs. The background " +
      "backtest worker reads this every pass, which is why a leader added over MCP gets a " +
      "backtest card without a browser ever opening.",
  },
];

const ENDPOINTS: Endpoint[] = [
  {
    method: "GET",
    path: "/api/polymarket?endpoint=markets",
    description: "List active prediction markets sorted by volume, liquidity, or end date.",
    params: [
      { name: "endpoint", type: "string", desc: "markets", required: true },
      { name: "_limit", type: "number", desc: "Max results (default 100)" },
      { name: "active", type: "boolean", desc: "Active markets only (default true)" },
      { name: "order", type: "string", desc: "Sort: volume | liquidity | end_date_min" },
      { name: "ascending", type: "boolean", desc: "Sort direction (default false)" },
      { name: "end_date_min", type: "ISO date", desc: "Filter by minimum end date" },
      { name: "end_date_max", type: "ISO date", desc: "Filter by maximum end date" },
    ],
    example: "/api/polymarket?endpoint=markets&_limit=20&order=volume&active=true",
  },
  {
    method: "GET",
    path: "/api/polymarket?endpoint=markets/{id}",
    description: "Get a single market by condition ID.",
    params: [
      { name: "endpoint", type: "string", desc: "markets/{condition_id}", required: true },
    ],
    example: "/api/polymarket?endpoint=markets/0x1234...",
  },
  {
    method: "GET",
    path: "/api/polymarket?endpoint=public-search",
    description: "Search markets by keyword. Returns events with embedded markets.",
    params: [
      { name: "endpoint", type: "string", desc: "public-search", required: true },
      { name: "q", type: "string", desc: "Search query", required: true },
      { name: "_limit", type: "number", desc: "Max results (default 40)" },
    ],
    example: "/api/polymarket?endpoint=public-search&q=election&_limit=20",
  },
  {
    method: "GET",
    path: "/api/polymarket?endpoint=events",
    description: "List events, optionally filtered by tag/category.",
    params: [
      { name: "endpoint", type: "string", desc: "events", required: true },
      { name: "tag_slug", type: "string", desc: "Category: politics | sports | crypto | pop-culture | business | science | tech | ai" },
      { name: "_limit", type: "number", desc: "Max results (default 50)" },
      { name: "_offset", type: "number", desc: "Pagination offset" },
      { name: "active", type: "boolean", desc: "Active events only (default true)" },
    ],
    example: "/api/polymarket?endpoint=events&tag_slug=crypto&_limit=20",
  },
  {
    method: "GET",
    path: "/api/polymarket?endpoint=trending",
    description: "Get trending markets ranked by volume.",
    params: [
      { name: "endpoint", type: "string", desc: "trending", required: true },
      { name: "_limit", type: "number", desc: "Max results (default 20)" },
    ],
    example: "/api/polymarket?endpoint=trending&_limit=10",
  },
  {
    method: "GET",
    path: "/api/polymarket?endpoint=positions",
    description: "Get positions for a wallet address.",
    params: [
      { name: "endpoint", type: "string", desc: "positions", required: true },
      { name: "user", type: "address", desc: "Wallet address", required: true },
      { name: "sizeThreshold", type: "number", desc: "Min position size (default 0.1)" },
      { name: "limit", type: "number", desc: "Max results (default 100)" },
    ],
    example: "/api/polymarket?endpoint=positions&user=0x1234...&sizeThreshold=.1",
  },
  {
    method: "GET",
    path: "/api/polymarket?endpoint=activity",
    description: "Get trade activity for a wallet address.",
    params: [
      { name: "endpoint", type: "string", desc: "activity", required: true },
      { name: "user", type: "address", desc: "Wallet address", required: true },
      { name: "limit", type: "number", desc: "Max results (default 200)" },
    ],
    example: "/api/polymarket?endpoint=activity&user=0x1234...&limit=50",
  },
  {
    method: "GET",
    path: "/api/polymarket?endpoint=v1/leaderboard",
    description: "Get the trader leaderboard ranked by PNL or volume.",
    params: [
      { name: "endpoint", type: "string", desc: "v1/leaderboard", required: true },
      { name: "timePeriod", type: "string", desc: "MONTH | WEEK | ALL" },
      { name: "orderBy", type: "string", desc: "PNL | VOL" },
      { name: "limit", type: "number", desc: "Max results (default 30)" },
    ],
    example: "/api/polymarket?endpoint=v1/leaderboard&timePeriod=MONTH&orderBy=PNL&limit=10",
  },
  {
    method: "GET",
    path: "/api/clob?path=order-book",
    description: "Get the full order book for a token.",
    params: [
      { name: "path", type: "string", desc: "order-book", required: true },
      { name: "token_id", type: "string", desc: "Token ID", required: true },
    ],
    example: "/api/clob?path=order-book&token_id=0x1234...",
  },
  {
    method: "GET",
    path: "/api/clob?path=midpoint-price",
    description: "Get the midpoint price for a token.",
    params: [
      { name: "path", type: "string", desc: "midpoint-price", required: true },
      { name: "token_id", type: "string", desc: "Token ID", required: true },
    ],
  },
  {
    method: "POST",
    path: "/api/clob?path=order",
    description: "Place a limit order on the CLOB. Requires POLY_API_KEY, POLY_PASSPHRASE, POLY_TIMESTAMP, POLY_SIGNATURE headers.",
    body: [
      { name: "tokenID", type: "string", desc: "Token to trade" },
      { name: "price", type: "number", desc: "Limit price (0-1)" },
      { name: "size", type: "number", desc: "Order size in shares" },
      { name: "side", type: "string", desc: "BUY or SELL" },
      { name: "type", type: "string", desc: "GTC | GTD | FOK" },
    ],
  },
  {
    method: "POST",
    path: "/api/clob?path=market-order",
    description: "Place a market order. Requires auth headers.",
    body: [
      { name: "tokenID", type: "string", desc: "Token to trade" },
      { name: "size", type: "number", desc: "Order size in shares" },
      { name: "side", type: "string", desc: "BUY or SELL" },
    ],
  },
  {
    method: "GET",
    path: "/api/polymarket/sync/status",
    description:
      "Background sync schedule: the server re-pulls the 1/7/14/30-day trader leaderboards on this cadence (every 5 minutes by default) whether or not the console is open. Returns cadence, last run + duration, next run, and the last error.",
  },
  {
    method: "POST",
    path: "/api/polymarket/sync/config",
    description:
      "Owner sets the sync cadence. Applies immediately (the sleeping scheduler is re-timed) and persists to ~/.mod/polymarket/sync.json. Range 5 minutes – 7 days.",
    body: [
      { name: "enabled", type: "boolean", desc: "Pause / resume auto-sync" },
      { name: "intervalSecs", type: "number", desc: "Cadence in seconds (300–604800)" },
      { name: "intervalMinutes", type: "number", desc: "Cadence in minutes" },
      { name: "intervalHours", type: "number", desc: "Cadence in hours" },
    ],
    example: '{"intervalHours": 6}',
  },
  {
    method: "POST",
    path: "/api/polymarket/sync/run",
    description:
      "Run one background cycle now, bypassing the freshness skip. Queued for the scheduler, so a manual run can never overlap a scheduled one — poll /sync/status for progress.",
  },
];

const CLI_COMMANDS = [
  { cmd: "m polymarket/search query=election", desc: "Search markets by keyword" },
  { cmd: "m polymarket/markets limit=20", desc: "List top markets" },
  { cmd: "m polymarket/trending limit=10", desc: "Get trending markets" },
  { cmd: "m polymarket/by_liquidity limit=10", desc: "Markets by liquidity" },
  { cmd: "m polymarket/ending_soon limit=10", desc: "Markets ending soon" },
  { cmd: "m polymarket/orderbook token_id=0x...", desc: "Get order book" },
  { cmd: "m polymarket/buy token_id=0x... price=0.5 size=10", desc: "Buy shares" },
  { cmd: "m polymarket/sell token_id=0x... price=0.7 size=10", desc: "Sell shares" },
  { cmd: "m polymarket/positions", desc: "Get current positions" },
  { cmd: "m polymarket/backtest start=0 end=9999999999", desc: "Run backtest" },
  { cmd: "m polymarket/scrape interval=60", desc: "Start price scraper" },
  { cmd: "m polymarket/sync hours=6", desc: "Set the background sync cadence (no args = show it)" },
  { cmd: "m polymarket/serve", desc: "Start API + app" },
  { cmd: "m polymarket/kill", desc: "Stop all services" },
  { cmd: "m polymarket/status", desc: "Check service status" },
];

// ── Small building blocks ────────────────────────────────────────

function MethodBadge({ method }: { method: "GET" | "POST" }) {
  return (
    <span
      className={`pixel-badge text-[10px] ${
        method === "GET"
          ? "border-pixel-white text-pixel-white"
          : "border-pixel-gray-light text-pixel-gray-light"
      }`}
    >
      {method}
    </span>
  );
}

function SectionTitle({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <div id={id} className="flex items-center gap-2 px-1 scroll-mt-16">
      <span className="text-[12px] text-pixel-white tracking-widest">{children}</span>
    </div>
  );
}

function FieldTable({ rows, cols }: { rows: { name: string; type: string; desc: string }[]; cols: [string, string, string] }) {
  return (
    <table className="pixel-table wrap-prose">
      {/* `table-layout: fixed` splits three columns evenly, which left the
          prose column narrower than the names it explains. Give it half. */}
      <colgroup>
        <col style={{ width: "27%" }} />
        <col style={{ width: "23%" }} />
        <col style={{ width: "50%" }} />
      </colgroup>
      <thead>
        <tr>
          <th>{cols[0]}</th>
          <th>{cols[1]}</th>
          <th>{cols[2]}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td className="text-pixel-white font-mono whitespace-nowrap">{r.name}</td>
            <td className="text-pixel-gray font-mono">{r.type}</td>
            <td className="text-pixel-gray-light">{r.desc}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Page ─────────────────────────────────────────────────────────

// The pm_copy_* half of the MCP server — src/mcp.py. Documented here because
// "what can an agent do to my money" deserves an answer on the same page as
// what the buttons do.
const MCP_TOOLS = [
  {
    name: "pm_copy_book",
    writes: false,
    desc: "The desk: who is copied, with how much, running or not, TEST or LIVE, orders placed, realized P&L. The place to start.",
  },
  {
    name: "pm_copy_backtest",
    writes: false,
    desc: "Replay copying ONE trader over a window. Returns pnl/roi/trades, the walk-forward verdict (only \"held\" means it worked in the prior window AND this one) and the funnel (how many of their entries this desk could actually copy, and which gate blocked the rest).",
  },
  {
    name: "pm_copy_trades",
    writes: false,
    desc: "What the desk's leaders traded against what actually landed in my wallet: coverage (their trades I got), median lag, signed slippage, per-leader roll-up, and every missed trade. `q` filters it in plain language and returns the gate that sentence compiles to, ready for pm_copy_allocate params:{marketQuery, tradeFilters}.",
  },
  {
    name: "pm_copy_basket",
    writes: false,
    desc: "Size a SET of traders against each other: a different amount per name, replayed as one portfolio. Reports legsTrading/legs and idleUsd (how much of the money never traded), optionally the smallest amount each leg needs (floors) and how the split scores against dividing the total evenly (compare).",
  },
  {
    name: "pm_copy_allocate",
    writes: true,
    desc: "Copy a trader with N dollars, or change the amount. Adds intent — places nothing.",
  },
  { name: "pm_copy_remove", writes: true, desc: "Stop copying a trader and drop them from the book." },
  { name: "pm_copy_rebalance", writes: true, desc: "Split a bankroll across the enabled traders (equal | weighted)." },
  {
    name: "pm_copy_start",
    writes: true,
    desc: "Start copying. TEST by default. autoExecute=true means LIVE — real orders — and is REFUSED unless the deployment sets POLYMARKET_MCP_ALLOW_LIVE=1; otherwise a human flips the TEST|LIVE switch here in the browser.",
  },
  { name: "pm_copy_stop", writes: true, desc: "Stop a session, or the whole desk. Always permitted — it only reduces exposure." },
];

const NAV = [
  ["#index", "TRADER INDEX"],
  ["#copy", "COPY DESK"],
  ["#agents", "AGENTS / MCP"],
  ["#strategy", "STRATEGY CLASS"],
  ["#sizing", "COPY SIZING"],
  ["#engine", "LIVE ENGINE"],
  ["#api", "API"],
  ["#cli", "CLI"],
  ["#types", "TYPES"],
  ["#auth", "AUTH"],
] as const;

export default function DocsPage() {
  return (
    <div className="max-w-[1920px] mx-auto">
      {/* Header */}
      {/* Same frosted-glass recipe as TopBar — without backdrop-blur the
          90%-alpha bar let scrolled table rows read straight through it. */}
      <header className="border-b border-pixel-border backdrop-blur-md bg-[rgb(var(--pixel-black-rgb)/0.75)] sticky top-0 z-50">
        <div className="px-4 h-12 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="pixel-btn text-[11px] border-pixel-border text-pixel-gray hover:text-pixel-white"
            >
              BACK
            </Link>
            <div className="flex flex-col">
              <span className="text-pixel-white text-[13px] tracking-wider leading-tight">
                Documentation
              </span>
              <span className="text-pixel-gray text-[10px] tracking-widest leading-tight">
                POLYMARKET · TRADER INDEX
              </span>
            </div>
          </div>
          <nav className="hidden md:flex items-center gap-3 text-[10px] tracking-wider">
            {NAV.map(([href, label]) => (
              <a key={href} href={href} className="text-pixel-gray hover:text-green-400 transition-colors">
                {label}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <div className="docs-content p-4 space-y-6">
        {/* ── Overview ── */}
        <div className="pixel-panel p-4 space-y-3">
          <div className="text-[12px] text-pixel-white tracking-wider">OVERVIEW</div>
          <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
            <p>
              This module copies Polymarket traders, and its default unit is an{" "}
              <Link href="#index" className="text-green-400">INDEX</Link> of them:{" "}
              <span className="text-pixel-white">one bench, one pot of capital, no per-name dollar
              amounts</span>. Every trade they make is re-sized by the ratio between your capital
              and that trader&apos;s own net worth — they stake 2% of their book, you stake 2% of
              yours.
            </p>
            <p>
              The console is three tabs and they read as one sentence:{" "}
              <Link href="/traders" className="text-green-400">TRADERS</Link> (pick who to copy) →{" "}
              <Link href="/backtest" className="text-green-400">BACKTEST</Link> (test them against
              real past trades, on simulated money) →{" "}
              <Link href="/live" className="text-green-400">LIVE</Link> (run them for real). There
              is no fourth. Capital is set in SETTINGS above the charts, and money is not a tab:
              topping up and taking it out is a drawer in the side panel, open over whatever you
              were looking at.
            </p>
            <p>
              Underneath it the older per-trader{" "}
              <Link href="#copy" className="text-green-400">COPY DESK</Link> still runs — one
              leader, one dollar amount, one session each — and the two meet at the same object,
              because both become strats on one engine. The rest of this page documents that
              machinery — the strategy class, how a mirror is sized, what the live engine does per
              cycle — because when a leader isn&apos;t being copied, the answer is always in there.
            </p>
          </div>
        </div>

        {/* ── TRADER INDEX — the default unit ── */}
        <div className="space-y-4">
          <SectionTitle id="index">TRADER INDEX</SectionTitle>
          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                A trader index is a strat with a bench of traders on it and one number behind it:{" "}
                <span className="text-pixel-white">capital</span>. There is no amount per name.
              </p>
              <pre className="text-[10px] font-mono text-green-400 leading-relaxed">{`mirror$ = their$ × (yourCapital × weight) / theirBankroll`}</pre>
              <p>
                <span className="text-pixel-white">weight</span> is that trader&apos;s share of the
                bench (equal by default, redistributed when you disable someone).{" "}
                <span className="text-pixel-white">theirBankroll</span> is their live net worth on
                Polymarket — open positions at mark plus free collateral. So a leader&apos;s
                $50,000 conviction entry and their $200 punt land on your book 250× apart, which is
                the only thing worth copying about a whale. Turned on by{" "}
                <span className="font-mono">sizing: &quot;bankroll&quot;</span>, which is the
                default.
              </p>
              <p>
                <span className="text-pixel-white">What it costs, stated out loud.</span> A $1,000
                index against a $100,000 trader runs at 1%: their $5,000 entry becomes $50, and
                their $50 punt becomes 50¢ — under the exchange floor of max($1, 5 shares), so the
                engine refuses it as <span className="font-mono">SUB_SCALE</span>. It is not
                inflated to the minimum, because an index whose every order is the same $2.55 is
                not an index of anything. The <span className="text-pixel-white">YOUR SCALE</span>{" "}
                card on STRATS names the threshold (&ldquo;the smallest trade of theirs that
                reaches you is ~$125&rdquo;), names the capital that would clear it, and points at
                the alternative: <span className="font-mono">sizing: &quot;flow&quot;</span>,
                which divides by the capital they deployed <em>this window</em> instead of their
                whole balance sheet — more coverage, smaller ratio, no longer a risk mirror.
              </p>
              <p className="text-[10px] text-pixel-gray">
                The ratio is <span className="font-mono">copyRatioFor</span> in{" "}
                <span className="text-pixel-white">app/lib/strats/strat.ts</span>, pinned
                line-for-line to the Rust engine&apos;s <span className="font-mono">copy_ratio_for</span>.
                The layer that projects it onto a real trade is{" "}
                <span className="text-pixel-white">app/lib/traderIndex.ts</span>.
              </p>
            </div>
          </div>
        </div>

        {/* ── COPY DESK ── */}
        <div className="space-y-4">
          <SectionTitle id="copy">COPY DESK</SectionTitle>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                One row per trader. The number on the row is the whole position-sizing model:
                the live engine budgets against it and the backtest replays with it, so changing
                it changes both. Nothing about a row is stored in your browser — the book lives on
                the server, in the open, which is what lets an{" "}
                <Link href="#agents" className="text-green-400">agent</Link> read and change the
                same desk you&apos;re looking at.
              </p>
              <p>
                <span className="text-pixel-white">The loop:</span> pick a{" "}
                <span className="text-pixel-white">market type</span> (BITCOIN, ELECTIONS, or any
                topic you type) → read the traders it returns, ranked on{" "}
                <span className="text-pixel-white">that flow alone</span> → put an amount against
                one → run them in <span className="text-pixel-white">TEST</span> to confirm they
                produce entries this desk can actually copy → flip the switch to{" "}
                <span className="text-pixel-white">LIVE</span>. Pasting an address still works,
                and skips the first step.
              </p>
              <p>
                <span className="text-pixel-white">FIND TRADERS BY MARKET</span> is not a search
                box over a fixed leaderboard. The market type filters each trader&apos;s{" "}
                <span className="text-pixel-white">trades</span> first, and their P&amp;L, trade
                count, win rate and Sharpe are recomputed from{" "}
                <span className="text-pixel-white">only the trades that survive</span>; a trader
                with no trades in those markets isn&apos;t on the list at all. A $400k lifetime
                P&amp;L earned in someone&apos;s election book is not evidence about copying them
                in bitcoin, and this is the screen that refuses to present it as such. When the
                server can only answer from its disk cache — which carries no per-market
                breakdown — the panel says the numbers are lifetime instead of quietly showing
                them as if they were scoped.
              </p>
              <p>
                It also only offers you traders who are{" "}
                <span className="text-pixel-white">still trading</span>:{" "}
                <span className="text-pixel-white">ACTIVE 6H</span> is on by default, here and on
                the TRADERS board (<span className="text-pixel-white">LAST TRADE ≤ HRS</span> in
                FILTERS). A wallet that stopped yesterday keeps its excellent 7-day record —
                those trades already closed — and copying it fills nothing, so it ranks above
                people you can copy while contributing no fills. The filter runs on the server
                against the same cached leaderboard the rows come from, so it narrows the whole
                board and its count without costing a re-sync; turn it off to see the dormant
                names, and the panel tells you how many it hid.
              </p>
              <p>
                <span className="text-pixel-white">THE BASKET</span> (
                <Link href="/copy/basket" className="text-green-400">/copy/basket</Link>) is the
                desk&apos;s other half: several traders at once, with a{" "}
                <span className="text-pixel-white">different amount against each</span>, replayed
                over the same window as one portfolio. Each leg runs on its{" "}
                <span className="text-pixel-white">own capital</span> — which is exactly what the
                deployment does, one allocation being one session with its own budget — so a
                leg&apos;s number never depends on what the others did. What the basket adds over
                reading N per-trader backtests is the arithmetic of the{" "}
                <span className="text-pixel-white">split</span>: which legs never traded at all
                (an underfunded leg does not take a small position, it takes{" "}
                <span className="text-pixel-white">no</span> position, because its proportional
                mirror lands under the order floor), how many dollars are sitting idle in them,
                the smallest amount each of those legs would need, and whether your amounts beat
                simply dividing the total evenly. Nothing there is committed until{" "}
                <span className="text-pixel-white">APPLY TO DESK</span>.
              </p>
              <p>
                The query that found a trader becomes their{" "}
                <span className="text-pixel-white">gate</span>: the{" "}
                <span className="text-pixel-white">COPIES</span> line on their row. The live
                engine and the backtest both refuse their entries outside it, so the row goes on
                meaning what the search meant. Clear it and you copy everything they trade —
                which is what every row did before the gate existed.
              </p>
            </div>
          </div>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[12px] text-pixel-white tracking-wider">
              THE IDENTITY TEMPLATE
            </div>
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                An allocation isn&apos;t a special kind of thing. It is materialized into an{" "}
                <span className="text-pixel-white">IDENTITY STRAT</span> — an ordinary strategy
                whose watchlist is exactly that one trader at weight 1 — and from there it runs on
                the same engine, the same backtest and the same ledger as everything else on this
                page. Its id is derived from the address (<code className="text-pixel-white">copy-&lt;address&gt;</code>),
                so the session key, the ledger bucket and the backtest card agree without a lookup
                table, and adding a trader twice updates one allocation instead of starting two
                sessions.
              </p>
              <p>
                The template exists in two languages —{" "}
                <code className="text-pixel-white">api/src/copy.rs</code> for the live engine and{" "}
                <code className="text-pixel-white">app/lib/identityStrat.ts</code> for the browser
                and the worker — pinned against each other by{" "}
                <code className="text-pixel-white">identity.fixture.json</code>. Change a default
                on one side and the other side&apos;s tests go red. That is deliberate: a backtest
                card promising something the live session doesn&apos;t do is the bug class this
                whole arrangement exists to close.
              </p>
            </div>
            <table className="pixel-table wrap-prose">
              <thead>
                <tr>
                  <th>DEFAULT</th>
                  <th>VALUE</th>
                  <th>WHY</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="text-pixel-white font-mono">sizing</td>
                  <td className="text-pixel-gray">flow</td>
                  <td className="text-pixel-gray-light">
                    Copy the leader&apos;s CONVICTION — your allocation spread across the capital
                    they deployed — not their bankroll fraction. A small desk copying a whale
                    places real orders under <code>flow</code> and nothing at all under{" "}
                    <code>bankroll</code>: their 0.1%-of-net-worth bet is $2,000 of theirs and 25¢
                    of yours, below every floor there is.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">minMinutesToClose</td>
                  <td className="text-pixel-gray">60</td>
                  <td className="text-pixel-gray-light">
                    Refuse markets resolving sooner than an hour. Sub-hour Up/Down candles resolve
                    before a poller can react — copying them is a measured loss, not a strategy.
                    This is the gate that blocks most high-frequency leaders; the funnel says so
                    when it fires.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">maxTradeAgeSec</td>
                  <td className="text-pixel-gray">300</td>
                  <td className="text-pixel-gray-light">
                    Never mirror a stale fill. After a fetch outage the backlog would enter at
                    prices the leader never paid.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">stopLoss / takeProfit</td>
                  <td className="text-pixel-gray">0.75 / 0.99</td>
                  <td className="text-pixel-gray-light">
                    Sell at 75% of entry rather than riding a market to zero; liquidate anything
                    that runs to the top tick instead of leaving capital dead until resolution.
                    Explicit <code>0</code> turns either off — it is a value, not an absence.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">pollMinutes</td>
                  <td className="text-pixel-gray">0.5</td>
                  <td className="text-pixel-gray-light">
                    30 seconds. Fast enough to reach a fill while the price is near the
                    leader&apos;s, slow enough not to draw rate limits. The backtest aggregates at
                    the same cadence, so it can&apos;t promise fills the engine never sees.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">minTrade / maxTrade</td>
                  <td className="text-pixel-gray">$1 / $100</td>
                  <td className="text-pixel-gray-light">
                    Order floor and per-order ceiling. The CLOB&apos;s own hard floor is $1.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">marketQuery</td>
                  <td className="text-pixel-gray">unset</td>
                  <td className="text-pixel-gray-light">
                    The row&apos;s market gate — the <span className="text-pixel-white">COPIES</span>{" "}
                    line. Set from the market type that found the trader, editable afterwards, and
                    empty means every market they trade. Matching is OR across comma-separated
                    groups and AND within a group, against the market title:{" "}
                    <code className="text-pixel-white">bitcoin, btc</code> is either spelling,{" "}
                    <code className="text-pixel-white">price of bitcoin</code> is one phrase. It
                    gates <span className="text-pixel-white">entries only</span> — an exit is
                    never blocked, or changing the gate would strand an open position.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[12px] text-pixel-white tracking-wider">
              READING A ROW
            </div>
            <table className="pixel-table wrap-prose">
              <thead>
                <tr>
                  <th>WHAT</th>
                  <th>MEANS</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="text-pixel-white font-mono">TEST</td>
                  <td className="text-pixel-gray-light">
                    Running, computing every mirror, placing none. This is the default and it is
                    the answer to most &quot;why did nothing trade&quot; questions — check it
                    first, before any other theory.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">LIVE</td>
                  <td className="text-pixel-gray-light">Placing real orders with real money.</td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">PAUSED</td>
                  <td className="text-pixel-gray-light">
                    On the desk with its allocation and history intact, but not started and not
                    backtested. Pausing is not removing.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">REALIZED</td>
                  <td className="text-pixel-gray-light">
                    Booked P&amp;L only — sells and redemptions. Open positions are deliberately
                    excluded: a mark reads high, because leaders sell their winners and let losers
                    expire quietly.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">verdict</td>
                  <td className="text-pixel-gray-light">
                    The walk-forward result on the backtest chip. The same trader replayed over
                    the window BEFORE the card&apos;s, with no knowledge of what came after.{" "}
                    <span className="text-pixel-white">held</span> is the only pass — profitable
                    then, and profitable since. <span className="text-pixel-white">faded</span>{" "}
                    made money once. A row ranked on its P&amp;L alone is ranked on one window.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">unsettled</td>
                  <td className="text-pixel-gray-light">
                    How much of that P&amp;L was still open at the window&apos;s end, valued at
                    the last observed price rather than a real resolution. A large number makes
                    the result a hypothesis.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono">funnel</td>
                  <td className="text-pixel-gray-light">
                    Entries observed → entries copied, with the gate that blocked each of the
                    rest. &quot;Flat&quot; is usually &quot;blocked&quot;: a leader whose whole
                    flow is gated cannot be copied whatever their own P&amp;L says.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="space-y-3">
            {COPY_ENDPOINTS.map((ep, i) => (
              <div key={i} className="pixel-panel p-4 space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <MethodBadge method={ep.method} />
                  <code className="text-[11px] text-pixel-white font-mono break-all">{ep.path}</code>
                </div>
                <div className="text-[11px] text-pixel-gray-light leading-relaxed">
                  {ep.description}
                </div>
                {ep.params && ep.params.length > 0 && (
                  <table className="pixel-table wrap-prose">
                    <thead>
                      <tr><th>QUERY</th><th>TYPE</th><th>DESCRIPTION</th></tr>
                    </thead>
                    <tbody>
                      {ep.params.map((p, j) => (
                        <tr key={j}>
                          <td className="text-pixel-white font-mono">{p.name}</td>
                          <td className="text-pixel-gray">{p.type}</td>
                          <td className="text-pixel-gray-light">{p.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {ep.body && ep.body.length > 0 && (
                  <table className="pixel-table wrap-prose">
                    <thead>
                      <tr><th>BODY</th><th>TYPE</th><th>DESCRIPTION</th></tr>
                    </thead>
                    <tbody>
                      {ep.body.map((b, j) => (
                        <tr key={j}>
                          <td className="text-pixel-white font-mono">{b.name}</td>
                          <td className="text-pixel-gray">{b.type}</td>
                          <td className="text-pixel-gray-light">{b.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {ep.example && (
                  <code className="block text-[10px] text-pixel-gray-light font-mono bg-pixel-black/50 p-2 border border-pixel-border break-all">
                    {ep.example}
                  </code>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* ── Agents / MCP ── */}
        <div className="space-y-4">
          <SectionTitle id="agents">AGENTS / MCP</SectionTitle>
          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                The desk has an MCP server in front of it (<code className="text-pixel-white">src/mcp.py</code>,
                stdio or HTTP on <code className="text-pixel-white">:50092/mcp</code>), and it is
                not a mirror of the console — it calls the same{" "}
                <code className="text-pixel-white">/copy/*</code> routes the screen does. Ask an
                agent to &quot;put $50 on 0xab…&quot; and it appears here at the next poll; move a
                number here and that is what the agent reads next.
              </p>
              <p>
                <span className="text-pixel-white">There is no order-placing tool, and there
                won&apos;t be.</span> The one thing that can spend money is{" "}
                <code className="text-pixel-white">pm_copy_start</code> with{" "}
                <code className="text-pixel-white">autoExecute: true</code>, and it is refused
                unless the deployment sets{" "}
                <code className="text-pixel-white">POLYMARKET_MCP_ALLOW_LIVE=1</code>. Without it
                an agent can research, backtest, allocate and run in TEST — and a human flips
                the TEST|LIVE switch in this console. Stopping is always allowed: it only reduces exposure.
              </p>
            </div>
            <table className="pixel-table wrap-prose">
              <thead>
                <tr><th>TOOL</th><th>WRITES</th><th>WHAT IT DOES</th></tr>
              </thead>
              <tbody>
                {MCP_TOOLS.map((t) => (
                  <tr key={t.name}>
                    <td className="text-pixel-white font-mono">{t.name}</td>
                    <td className={t.writes ? "text-amber-400" : "text-pixel-gray"}>
                      {t.writes ? "yes" : "read"}
                    </td>
                    <td className="text-pixel-gray-light">{t.desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[11px] text-pixel-gray-light leading-relaxed">
              Alongside them: <code className="text-pixel-white">pm_top_traders</code> /{" "}
              <code className="text-pixel-white">pm_trader</code> to find and vet a leader,{" "}
              <code className="text-pixel-white">pm_live_sessions</code> /{" "}
              <code className="text-pixel-white">pm_live_gates</code> for why a running session
              isn&apos;t filling, and <code className="text-pixel-white">pm_markets</code>,{" "}
              <code className="text-pixel-white">pm_health</code>,{" "}
              <code className="text-pixel-white">pm_strats</code>,{" "}
              <code className="text-pixel-white">pm_backtests</code>.
            </div>
          </div>
        </div>

        {/* ── The machinery under the desk ── */}
        <div className="pixel-panel p-4 space-y-3">
          <div className="text-[12px] text-pixel-white tracking-wider">
            UNDER THE DESK — THE STRATEGY ENGINE
          </div>
          <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
            <p>
              A <span className="text-pixel-white">strategy is ONE class</span>:
              the engine constructs <code className="text-pixel-white">new Strat(params)</code> and drives it through five hooks.
              Every hook receives the full observed history of the data — trades across the watchlist, per-trader stats, open
              positions, balance — so a strategy can <span className="text-pixel-white">score</span> candidate trades or{" "}
              <span className="text-pixel-white">propose</span> its own from any aggregation of that history.
            </p>
            <p>
              The same class runs everywhere: the TEST preview, the top-N sampling shown in the feed, and the LIVE
              engine all construct the identical class with the identical params, so what you test is what trades.
            </p>
          </div>
        </div>

        {/* ── Strategy class ── */}
        <div className="space-y-4">
          <SectionTitle id="strategy">STRATEGY CLASS</SectionTitle>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                <code className="text-pixel-white">class Strat</code> —{" "}
                <span className="font-mono">src/app/app/lib/strats/strat.ts</span>. The one standard strategy class:{" "}
                <code className="text-pixel-white">StratParams</code> declares every tunable, the engine constructs the class with
                them, and <code className="text-pixel-white">this.params</code> carries them at runtime. All five hooks have working
                param-driven defaults — a strategy is a params value, not a new class.
              </p>
            </div>
            <FieldTable
              cols={["HOOK", "RETURNS", "WHAT IT DECIDES"]}
              rows={STRAT_HOOKS.map((h) => ({ name: h.hook, type: h.returns, desc: `${h.desc} Default: ${h.def}.` }))}
            />
          </div>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-white">StratHistory — what every hook receives</div>
            <div className="text-[11px] text-pixel-gray-light leading-relaxed">
              The &quot;any history of the data&quot; contract. Aggregate flow, detect momentum, compare against your own book —
              whatever the logic needs. Complete for the whole watchlist by sizing and propose time.
            </div>
            <FieldTable cols={["FIELD", "TYPE", "DESCRIPTION"]} rows={HISTORY_FIELDS} />
          </div>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-white">ProposedTrade — originating trades from history</div>
            <div className="text-[11px] text-pixel-gray-light leading-relaxed">
              Returned from <code className="text-pixel-white">propose(history, constraints)</code>. The engine resolves the
              token id, tick-rounds the price, clamps to the CLOB floor, caps to maxPerCycle, submits through the same order
              path mirrors use, and applies a 30-minute per-market cooldown so an unfilled GTC entry isn&apos;t re-stacked
              every cycle.
            </div>
            <FieldTable cols={["FIELD", "TYPE", "DESCRIPTION"]} rows={PROPOSED_FIELDS} />
          </div>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-white">Built-in modes — one class, param-selected</div>
            <table className="pixel-table wrap-prose">
              <thead>
                <tr>
                  <th>MODE</th>
                  <th>KIND</th>
                  <th>BEHAVIOR</th>
                  <th>PARAMS</th>
                </tr>
              </thead>
              <tbody>
                {STRAT_MODES.map((s, i) => (
                  <tr key={i}>
                    <td className="text-pixel-white font-mono whitespace-nowrap">{s.name}</td>
                    <td className="text-pixel-gray whitespace-nowrap">{s.kind}</td>
                    <td className="text-pixel-gray-light">{s.desc}</td>
                    <td className="text-pixel-gray font-mono">{s.params}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[10px] text-pixel-gray leading-relaxed">
              Sources are readable in-app: STRAT → SOURCE tab. Uploaded mod.py / mod.rs strats are editable there and
              shareable by CID from the HUB tab.
            </div>
          </div>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-white">Write your own</div>
            <div className="text-[11px] text-pixel-gray-light leading-relaxed">
              A strategy is <code className="text-pixel-white">new Strat(params)</code> — pick the params, done: backtest,
              top-N sampling, and the live engine all run it. For behavior no param expresses, subclassing still works —
              override any hook and pass the instance to the engine. Python authors: the same shape exists at{" "}
              <span className="font-mono">src/strats/base/mod.py</span> (sync → signal → execute).
            </div>
            <pre className="text-[10px] text-pixel-gray-light font-mono bg-pixel-black/50 p-3 border border-pixel-border overflow-x-auto leading-[1.5]">
              {CUSTOM_STRAT_EXAMPLE}
            </pre>
          </div>
        </div>

        {/* ── Copy sizing ── */}
        <div className="space-y-4">
          <SectionTitle id="sizing">COPY SIZING</SectionTitle>
          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                Copying is <span className="text-pixel-white">proportional to account value</span>: whatever fraction of their
                own net worth a leader risked, the strat risks that same fraction of yours.
              </p>
              <pre className="text-[10px] font-mono text-green-400 leading-relaxed">{`mirror$ = leader$ × (accountValue × weightFraction) / leaderBankroll`}</pre>
              <p>
                <span className="text-pixel-white">accountValue</span> = the strat&apos;s free cash + the mark value of the
                positions it holds, re-read every cycle — so sizes grow with a winning account and shrink with a drawdown.
                <span className="text-pixel-white"> leaderBankroll</span> = that trader&apos;s open-position value + free USDC.
                A $10,000 conviction entry therefore copies 100× larger than a $100 punt from the same wallet.
              </p>
              <p>
                When a leader&apos;s balance sheet can&apos;t be read, sizing falls back to the older volume model
                (<span className="font-mono">capitalAlloc / leaderVolume</span> over the lookback window).
              </p>
            </div>
            <table className="pixel-table wrap-prose">
              <tbody>
                <tr>
                  <td className="text-pixel-white font-mono whitespace-nowrap">maxUpscale</td>
                  <td className="text-pixel-gray-light">
                    Exchange floors are discrete: Polymarket refuses orders under max($1, 5 shares). When the proportional
                    size lands below that floor, the order can only be placed by making it BIGGER than intended. This caps
                    that distortion — default 2×. A mirror that would need more is skipped as
                    <span className="font-mono"> SUB_SCALE</span> rather than placed. Without it every sub-floor intent
                    becomes the same flat minimum, and a conviction bet and a throwaway punt copy identically.
                    Set it in STRATS → SIZING → <span className="font-mono">UPSCALE</span>; ∞ (0) turns the cap off so
                    every filtered trade is placed at the floor — the trade a small account makes to trade at all.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono whitespace-nowrap">exits</td>
                  <td className="text-pixel-gray-light">
                    Symmetric: a leader who sells 40% of their shares makes the strat sell 40% of its own; a leader who goes
                    flat takes the strat flat. Sizing exits off the leader&apos;s notional instead systematically under-sells
                    and leaves the book full of positions the leader has already left.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono whitespace-nowrap">minMinutesToClose</td>
                  <td className="text-pixel-gray-light">
                    Mirrors are refused in markets resolving sooner than this (default 60m). Sub-hour Up/Down candles are
                    decided before a polling engine can react to the fill — copying them is a structural loss, not a strategy.
                  </td>
                </tr>
                <tr>
                  <td className="text-pixel-white font-mono whitespace-nowrap">maxTradeAgeSec</td>
                  <td className="text-pixel-gray-light">
                    Mirrors are refused for leader trades older than this. <span className="text-pixel-white">Off unless you set it</span> —
                    as a default it refused most observed flow, because the history a session pulls on its first cycle is
                    old by definition. Set it when you care that a post-outage backlog would enter at prices the leader
                    never paid.
                  </td>
                </tr>
              </tbody>
            </table>
            <div className="text-[10px] text-pixel-gray leading-relaxed">
              The ratio, the clamps and these defaults are pinned across TypeScript and Rust by
              <span className="font-mono"> parity.fixture.json</span>, so the TEST tab previews the sizes the live engine
              will actually place.
            </div>
          </div>
        </div>

        {/* ── Live engine ── */}
        <div className="space-y-4">
          <SectionTitle id="engine">LIVE ENGINE</SectionTitle>
          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                The engine (<span className="font-mono">copyEngine.ts</span>) owns everything that is NOT strategy: the cycle
                loop, balance gate + EP-driven capital rotation, deposit-wallet resolution, CLOB submission, retries, logging,
                and ROI stats refresh. Each cycle:
              </p>
            </div>
            <table className="pixel-table wrap-prose">
              <tbody>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">0 · history</td><td className="text-pixel-gray-light">Assemble StratHistory: balance, stats, watchlist, positions (when the strat proposes).</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">1 · collect</td><td className="text-pixel-gray-light">Poll each watched trader, feed their lookback window into the history, pre-filter with shouldMirror.</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">2 · rank</td><td className="text-pixel-gray-light">Score every BUY candidate (scoreCandidate), keep the top maxPerCycle with positive expected profit. Every loser is logged with why.</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">3 · execute</td><td className="text-pixel-gray-light">SELLs first (free capital), then winning BUYs — sizeAndPrice decides final notional + limit; the engine places and logs.</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">4 · propose</td><td className="text-pixel-gray-light">History-driven strats originate trades (propose) — same submission path, per-market cooldown.</td></tr>
              </tbody>
            </table>
            <div className="text-[10px] text-pixel-gray leading-relaxed">
              A session runs in TEST until you flip the TEST|LIVE switch (desk row, or the TRADE
              tab&apos;s engine header). Resolved positions can&apos;t be sold (no order book): cash
              out via REDEEM.
            </div>
          </div>
        </div>

        {/* ── API endpoints ── */}
        <div className="space-y-4">
          <SectionTitle id="api">
            API <span className="text-[11px] text-pixel-gray ml-2">{ENDPOINTS.length} ENDPOINTS</span>
          </SectionTitle>
          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed">
              Read access to the Gamma API (market data, events, search) and Data API (positions, trades, leaderboards),
              plus authenticated write access to the CLOB API (orders) — all proxied to avoid CORS:{" "}
              <span className="font-mono text-pixel-white">/api/polymarket</span> (Gamma + Data),{" "}
              <span className="font-mono text-pixel-white">/api/clob</span> (order book, prices, trading).
            </div>
          </div>

          {ENDPOINTS.map((ep, i) => (
            <div key={i} className="pixel-panel p-4 space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <MethodBadge method={ep.method} />
                <code className="text-[11px] text-pixel-white font-mono break-all">
                  {ep.path}
                </code>
              </div>
              <div className="text-[11px] text-pixel-gray-light leading-relaxed">
                {ep.description}
              </div>

              {ep.params && ep.params.length > 0 && (
                <div>
                  <div className="text-[10px] text-pixel-gray tracking-wider mb-1.5">
                    QUERY PARAMETERS
                  </div>
                  <table className="pixel-table wrap-prose">
                    <thead>
                      <tr>
                        <th>NAME</th>
                        <th>TYPE</th>
                        <th>DESCRIPTION</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ep.params.map((p, j) => (
                        <tr key={j}>
                          <td className="text-pixel-white font-mono">
                            {p.name}
                            {p.required && (
                              <span className="text-pixel-gray-light ml-1">*</span>
                            )}
                          </td>
                          <td className="text-pixel-gray">{p.type}</td>
                          <td className="text-pixel-gray-light">{p.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {ep.body && ep.body.length > 0 && (
                <div>
                  <div className="text-[10px] text-pixel-gray tracking-wider mb-1.5">
                    REQUEST BODY (JSON)
                  </div>
                  <table className="pixel-table wrap-prose">
                    <thead>
                      <tr>
                        <th>FIELD</th>
                        <th>TYPE</th>
                        <th>DESCRIPTION</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ep.body.map((b, j) => (
                        <tr key={j}>
                          <td className="text-pixel-white font-mono">{b.name}</td>
                          <td className="text-pixel-gray">{b.type}</td>
                          <td className="text-pixel-gray-light">{b.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {ep.example && (
                <div>
                  <div className="text-[10px] text-pixel-gray tracking-wider mb-1">EXAMPLE</div>
                  <code className="block text-[10px] text-pixel-gray-light font-mono bg-pixel-black/50 p-2 border border-pixel-border break-all">
                    {ep.example}
                  </code>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* ── CLI ── */}
        <div className="space-y-4">
          <SectionTitle id="cli">CLI</SectionTitle>
          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed mb-2">
              All functions are accessible via the mod CLI. Requires the polymarket module.
            </div>
            <table className="pixel-table wrap-prose">
              <thead>
                <tr>
                  <th>COMMAND</th>
                  <th>DESCRIPTION</th>
                </tr>
              </thead>
              <tbody>
                {CLI_COMMANDS.map((c, i) => (
                  <tr key={i}>
                    <td className="text-pixel-white font-mono whitespace-nowrap">{c.cmd}</td>
                    <td className="text-pixel-gray-light">{c.desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── Data types ── */}
        <div className="space-y-4">
          <SectionTitle id="types">DATA TYPES</SectionTitle>
          <div className="pixel-panel p-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="pixel-panel p-3">
                <div className="text-[11px] text-pixel-white mb-2">TraderTrade <span className="text-pixel-gray">(strat hook input)</span></div>
                <div className="text-[10px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                  <div>…PolymarketTrade fields, plus:</div>
                  <div>trader: string (watched address)</div>
                  <div>weight: number (watchlist weight)</div>
                  <div>weightFraction: number</div>
                  <div>copyRatio: number (account-value proportional)</div>
                  <div>notional: number (price × size, USD)</div>
                </div>
              </div>
              <div className="pixel-panel p-3">
                <div className="text-[11px] text-pixel-white mb-2">TraderRoiStats</div>
                <div className="text-[10px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                  <div>address: string</div>
                  <div>windowDays: number</div>
                  <div>roi: number (0.12 = +12%)</div>
                  <div>stdev: number</div>
                  <div>sampleSize: number</div>
                  <div>sharpe: number</div>
                  <div>cashDeployed: number</div>
                </div>
              </div>
              <div className="pixel-panel p-3">
                <div className="text-[11px] text-pixel-white mb-2">PolymarketTrade</div>
                <div className="text-[10px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                  <div>id: string</div>
                  <div>market: string</div>
                  <div>conditionId: string</div>
                  <div>side: BUY | SELL</div>
                  <div>price: number</div>
                  <div>size: number</div>
                  <div>pnl: number</div>
                  <div>timestamp: number</div>
                  <div>outcome?: string</div>
                </div>
              </div>
              <div className="pixel-panel p-3">
                <div className="text-[11px] text-pixel-white mb-2">PolymarketPosition</div>
                <div className="text-[10px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                  <div>conditionId: string</div>
                  <div>tokenId: string</div>
                  <div>market: string</div>
                  <div>outcome: string</div>
                  <div>size / avgPrice / currentPrice</div>
                  <div>value: number</div>
                  <div>pnlUsd: number</div>
                  <div>negRisk: boolean</div>
                  <div>redeemable: boolean</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Auth ── */}
        <div className="space-y-4">
          <SectionTitle id="auth">AUTHENTICATION</SectionTitle>
          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                Read endpoints (markets, search, events, leaderboard) require no authentication.
              </p>
              <p>
                Trading endpoints (order, market-order, cancel) require CLOB API credentials
                derived by signing an EIP-712 message with your wallet. The app handles this
                automatically via the wallet connect flow.
              </p>
              <p>
                For CLOB POST requests, include these headers: POLY_API_KEY, POLY_PASSPHRASE,
                POLY_TIMESTAMP, POLY_SIGNATURE.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t border-pixel-border mx-4 mt-8 pt-4 pb-8">
        <div className="flex items-center justify-between text-[11px] text-pixel-gray">
          <div className="flex items-center gap-3">
            <Link href="/" className="text-pixel-white hover:underline">
              Polymarket
            </Link>
            <span className="text-pixel-border">|</span>
            <span>Strat Engine Docs</span>
          </div>
          <span>Powered by mod</span>
        </div>
      </footer>
    </div>
  );
}
