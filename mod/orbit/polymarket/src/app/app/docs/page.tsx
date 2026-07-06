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

// The five hooks of the Strat class — src/app/app/lib/strats/base.ts.
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
    def: "proportional mirror clamped to user floor/ceiling + CLOB per-price min",
  },
  {
    hook: "propose(history, constraints)",
    returns: "ProposedTrade[]",
    desc: "ORIGINATE trades from history alone — no upstream trade required. Runs once per cycle after the mirror pass with the complete watchlist history. This is how momentum / mean-reversion / market-making strats plug into the same engine.",
    def: "[] (pure copy strat)",
  },
];

// StratHistory — what every hook receives. src/app/app/lib/strats/base.ts.
const HISTORY_FIELDS = [
  { name: "trades", type: "TraderTrade[]", desc: "Observed upstream trades across ALL watched traders inside the lookback window (backtestDays), newest-first. Each carries trader address, watchlist weight, proportional copyRatio, and $ notional." },
  { name: "traderStats", type: "Record<address, TraderRoiStats>", desc: "Per-trader window ROI / stdev / Sharpe / sample size — same stats that drive scoring." },
  { name: "positions", type: "PolymarketPosition[]", desc: "Open positions in the trading wallet. Fetched per-cycle only for strats that override propose; empty otherwise (and in backtests)." },
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

const BUILTIN_STRATS = [
  {
    name: "copytrader",
    kind: "mirror",
    desc: "The reference subclass. Mirrors watched traders' fills proportionally, ranked by ROI-weighted expected profit. Overrides ONE hook (adjustPrice: slippage-widened limit pricing) — everything else is inherited base behavior.",
    params: "maxPerCycle · marketQuery · tradeFilters · slippageBps · minSampleSize",
  },
  {
    name: "flowmomentum",
    kind: "history-driven",
    desc: "The reference propose() strat. Never mirrors per-trade — aggregates window flow across the watchlist and originates entries where ≥ minTraders pile into the same side (net flow ≥ minFlowUsd), exits held positions when flow flips net-SELL.",
    params: "lookbackMinutes · minTraders · minFlowUsd · maxPositions · marketQuery",
  },
];

const CUSTOM_STRAT_EXAMPLE = `// my_strat.ts — drop next to base.ts, register in registry.ts
import { Strat, StratParams, StratHistory, ProposedTrade,
         SizeConstraints, tickRoundPrice } from "./base";

export interface MyOpts extends StratParams {
  minEdge?: number;          // your tunables are ordinary class params
}

export class MyStrat extends Strat<MyOpts> {
  readonly name = "my_strat";

  // Take ANY history of the data and propose trades…
  propose(h: StratHistory, c: SizeConstraints): ProposedTrade[] {
    const out: ProposedTrade[] = [];
    // …aggregate h.trades / h.traderStats / h.positions however you like…
    return out;
  }

  // …or re-score the mirror candidates against that history.
  scoreCandidate(trade, stats, h: StratHistory): number {
    const flowInMarket = h.trades
      .filter((t) => t.conditionId === trade.conditionId && t.side === "BUY")
      .reduce((s, t) => s + t.notional, 0);
    const base = stats ? stats.roi * trade.notional * trade.copyRatio : 0;
    return base * (1 + Math.log1p(flowInMarket) / 10);
  }
}

// registry.ts:  my_strat: MyStrat as StratClass,`;

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
  { cmd: "m polymarket/serve", desc: "Start API + app" },
  { cmd: "m polymarket/kill", desc: "Stop all services" },
  { cmd: "m polymarket/status", desc: "Check service status" },
];

// ── Small building blocks ────────────────────────────────────────

function MethodBadge({ method }: { method: "GET" | "POST" }) {
  return (
    <span
      className={`pixel-badge text-[7px] ${
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
    <table className="pixel-table">
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

const NAV = [
  ["#strategy", "STRATEGY CLASS"],
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
      <header className="border-b border-pixel-border bg-pixel-black/90 sticky top-0 z-50">
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
                POLYMARKET · STRAT ENGINE
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

      <div className="p-4 space-y-6">
        {/* ── Overview ── */}
        <div className="pixel-panel p-4 space-y-3">
          <div className="text-[12px] text-pixel-white tracking-wider">OVERVIEW</div>
          <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
            <p>
              This module is a strategy engine over Polymarket. A <span className="text-pixel-white">strategy is a class</span>:
              the engine is parameterized by it, constructs it with its params, and drives it through five hooks. Every hook
              receives the full observed history of the data — trades across the watchlist, per-trader stats, open positions,
              balance — so a strategy can <span className="text-pixel-white">score</span> candidate trades or{" "}
              <span className="text-pixel-white">propose</span> its own from any aggregation of that history.
            </p>
            <p>
              The same class runs everywhere: the BACKTEST preview, the top-N sampling shown in the feed, and the LIVE
              engine all construct the identical class from the registry, so what you backtest is what trades.
            </p>
          </div>
        </div>

        {/* ── Strategy class ── */}
        <div className="space-y-4">
          <SectionTitle id="strategy">STRATEGY CLASS</SectionTitle>

          <div className="pixel-panel p-4 space-y-3">
            <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
              <p>
                <code className="text-pixel-white">abstract class Strat&lt;P extends StratParams&gt;</code> —{" "}
                <span className="font-mono">src/app/app/lib/strats/base.ts</span>. The generic parameter{" "}
                <code className="text-pixel-white">P</code> declares the class&apos;s tunables; the registry constructs the class with
                them and <code className="text-pixel-white">this.params</code> carries them at runtime. All five hooks have working
                defaults — a custom strategy overrides only what it changes.
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
            <div className="text-[11px] text-pixel-white">Built-in classes</div>
            <table className="pixel-table">
              <thead>
                <tr>
                  <th>CLASS</th>
                  <th>KIND</th>
                  <th>BEHAVIOR</th>
                  <th>PARAMS</th>
                </tr>
              </thead>
              <tbody>
                {BUILTIN_STRATS.map((s, i) => (
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
              1 — extend <code className="text-pixel-white">Strat&lt;YourOpts&gt;</code> and override any subset of the hooks. 2 —
              add the class to <span className="font-mono">registry.ts</span>. 3 — done: backtest, top-N sampling, and the
              live engine all pick it up. Python authors: the same shape exists at{" "}
              <span className="font-mono">src/strats/base/mod.py</span> (sync → signal → execute).
            </div>
            <pre className="text-[10px] text-pixel-gray-light font-mono bg-pixel-black/50 p-3 border border-pixel-border overflow-x-auto leading-[1.5]">
              {CUSTOM_STRAT_EXAMPLE}
            </pre>
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
            <table className="pixel-table">
              <tbody>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">0 · history</td><td className="text-pixel-gray-light">Assemble StratHistory: balance, stats, watchlist, positions (when the strat proposes).</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">1 · collect</td><td className="text-pixel-gray-light">Poll each watched trader, feed their lookback window into the history, pre-filter with shouldMirror.</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">2 · rank</td><td className="text-pixel-gray-light">Score every BUY candidate (scoreCandidate), keep the top maxPerCycle with positive expected profit. Every loser is logged with why.</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">3 · execute</td><td className="text-pixel-gray-light">SELLs first (free capital), then winning BUYs — sizeAndPrice decides final notional + limit; the engine places and logs.</td></tr>
                <tr><td className="text-pixel-white font-mono whitespace-nowrap">4 · propose</td><td className="text-pixel-gray-light">History-driven strats originate trades (propose) — same submission path, per-market cooldown.</td></tr>
              </tbody>
            </table>
            <div className="text-[10px] text-pixel-gray leading-relaxed">
              Execution is DRY RUN until enabled — toggle live execution in the LIVE tab. Resolved positions can&apos;t be
              sold (no order book): cash out via REDEEM.
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
                  <div className="text-[7px] text-pixel-gray tracking-wider mb-1.5">
                    QUERY PARAMETERS
                  </div>
                  <table className="pixel-table">
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
                  <div className="text-[7px] text-pixel-gray tracking-wider mb-1.5">
                    REQUEST BODY (JSON)
                  </div>
                  <table className="pixel-table">
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
                  <div className="text-[7px] text-pixel-gray tracking-wider mb-1">EXAMPLE</div>
                  <code className="block text-[7px] text-pixel-gray-light font-mono bg-pixel-black/50 p-2 border border-pixel-border break-all">
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
            <table className="pixel-table">
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
                <div className="text-[7px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                  <div>…PolymarketTrade fields, plus:</div>
                  <div>trader: string (watched address)</div>
                  <div>weight: number (watchlist weight)</div>
                  <div>weightFraction: number</div>
                  <div>copyRatio: number (proportional sizing)</div>
                  <div>notional: number (price × size, USD)</div>
                </div>
              </div>
              <div className="pixel-panel p-3">
                <div className="text-[11px] text-pixel-white mb-2">TraderRoiStats</div>
                <div className="text-[7px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
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
                <div className="text-[7px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
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
                <div className="text-[7px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
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
