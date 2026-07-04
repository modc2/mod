"use client";

import Link from "next/link";

interface Endpoint {
  method: "GET" | "POST";
  path: string;
  description: string;
  params?: { name: string; type: string; desc: string; required?: boolean }[];
  body?: { name: string; type: string; desc: string }[];
  example?: string;
}

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
                API Documentation
              </span>
              <span className="text-pixel-gray text-[10px] tracking-widest leading-tight">
                POLYMARKET
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-pixel-gray">
            <div className="w-1.5 h-1.5 bg-pixel-white animate-pulse" />
            <span className="text-pixel-white">v1.0</span>
            <span className="text-pixel-border">|</span>
            <span>POLYGON CLOB</span>
          </div>
        </div>
      </header>

      <div className="p-4 space-y-6">
        {/* Overview */}
        <div className="pixel-panel p-4 space-y-3">
          <div className="text-[12px] text-pixel-white tracking-wider">OVERVIEW</div>
          <div className="text-[11px] text-pixel-gray-light leading-relaxed space-y-2">
            <p>
              The Polymarket API proxy provides read access to the Gamma API (market data, events,
              search) and the Data API (positions, trades, leaderboards), plus authenticated
              write access to the CLOB API (order placement, cancellation).
            </p>
            <p>
              All endpoints are proxied through Next.js API routes to avoid CORS issues.
              The app uses two proxy routes:
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="pixel-panel p-3">
              <div className="text-[11px] text-pixel-white mb-1">/api/polymarket</div>
              <div className="text-[7px] text-pixel-gray leading-relaxed">
                Proxies to Gamma API (gamma-api.polymarket.com) for market data, search, events.
                Also proxies to Data API (data-api.polymarket.com) for positions, trades, leaderboards.
              </div>
            </div>
            <div className="pixel-panel p-3">
              <div className="text-[11px] text-pixel-white mb-1">/api/clob</div>
              <div className="text-[7px] text-pixel-gray leading-relaxed">
                Proxies to CLOB API (clob.polymarket.com) for order book data, price queries,
                and authenticated trading operations (order placement, cancellation).
              </div>
            </div>
          </div>
        </div>

        {/* Endpoints */}
        <div className="space-y-4">
          <div className="flex items-center gap-2 px-1">
            <span className="text-[12px] text-pixel-white tracking-widest">ENDPOINTS</span>
            <span className="text-[11px] text-pixel-gray">{ENDPOINTS.length} TOTAL</span>
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

        {/* CLI Commands */}
        <div className="pixel-panel p-4 space-y-3">
          <div className="text-[12px] text-pixel-white tracking-wider">CLI COMMANDS</div>
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

        {/* Data Types */}
        <div className="pixel-panel p-4 space-y-3">
          <div className="text-[12px] text-pixel-white tracking-wider">DATA TYPES</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="pixel-panel p-3">
              <div className="text-[11px] text-pixel-white mb-2">PolymarketMarket</div>
              <div className="text-[7px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                <div>id: string</div>
                <div>conditionId: string</div>
                <div>question: string</div>
                <div>category: string</div>
                <div>endDate: string (ISO)</div>
                <div>volume: number</div>
                <div>liquidity: number</div>
                <div>outcomePrices: number[]</div>
                <div>outcomes: string[]</div>
                <div>active: boolean</div>
              </div>
            </div>
            <div className="pixel-panel p-3">
              <div className="text-[11px] text-pixel-white mb-2">PolymarketPosition</div>
              <div className="text-[7px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                <div>conditionId: string</div>
                <div>market: string</div>
                <div>outcome: string</div>
                <div>size: number</div>
                <div>avgPrice: number</div>
                <div>currentPrice: number</div>
                <div>value: number</div>
                <div>pnlUsd: number</div>
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
              </div>
            </div>
            <div className="pixel-panel p-3">
              <div className="text-[11px] text-pixel-white mb-2">AuthState</div>
              <div className="text-[7px] text-pixel-gray font-mono space-y-0.5 leading-relaxed">
                <div>connected: boolean</div>
                <div>address: string | null</div>
                <div>chainId: number | null</div>
                <div>authenticated: boolean</div>
                <div>clobCreds: ClobCredentials | null</div>
              </div>
            </div>
          </div>
        </div>

        {/* Auth */}
        <div className="pixel-panel p-4 space-y-3">
          <div className="text-[12px] text-pixel-white tracking-wider">AUTHENTICATION</div>
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

      {/* Footer */}
      <footer className="border-t border-pixel-border mx-4 mt-8 pt-4 pb-8">
        <div className="flex items-center justify-between text-[11px] text-pixel-gray">
          <div className="flex items-center gap-3">
            <Link href="/" className="text-pixel-white hover:underline">
              Polymarket
            </Link>
            <span className="text-pixel-border">|</span>
            <span>API Docs</span>
          </div>
          <span>Powered by mod</span>
        </div>
      </footer>
    </div>
  );
}
