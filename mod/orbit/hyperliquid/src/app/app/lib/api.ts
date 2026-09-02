// Thin client over the Rust /hl/* proxy.

/** How much evidence sits behind a row's win rate. See `stats.rs`. */
export type Confidence = "none" | "low" | "measured";

export type TopTrader = {
  address: string;
  roi: number;            // window return on equity, percent (ranking metric)
  account_value: number;  // current account equity (USD)
  volume: number;
  pnl: number;            // net of fees
  /** Net-of-fee win rate, 0-100. `-1` means unmeasurable, not 0%. */
  win_rate: number;
  /** Every fill in the window, opens included. NOT the win-rate denominator. */
  trades: number;
  coins: string[];
  avg_trade_usd: number;
  sharpe: number;
  last_active: number;

  // ── the evidence behind the two ratios above ──
  // Rows served from a board cached by an older API arrive without these;
  // treat 0/undefined as "unknown" rather than as a real measurement.
  /** Fills that realised PnL — the actual denominator of `win_rate`. */
  closes: number;
  wins: number;
  losses: number;
  /** Wilson 95% lower bound on `win_rate`. Rank and filter on this. */
  win_rate_lo: number;
  confidence: Confidence;
  /** Days behind `sharpe`. Under 7 the ratio is noise — render "—". */
  sharpe_days: number;
  fees: number;
  /** Σ wins ÷ |Σ losses|. `-1` when there were no losses (undefined, not bad). */
  profit_factor: number;
  worst_close: number;
};

/** Closes below which a win rate is anecdote. Mirrors `stats::MIN_CLOSES`. */
export const MIN_CLOSES = 10;
/** Days below which a Sharpe is noise. Mirrors `stats::MIN_SHARPE_DAYS`. */
export const MIN_SHARPE_DAYS = 7;

/** The win rate a row has earned the right to claim, or `null` if none. */
export const defensibleWin = (t: Partial<TopTrader>): number | null => {
  if ((t.win_rate ?? -1) < 0) return null;
  const lo = t.win_rate_lo;
  return lo == null || lo < 0 ? (t.win_rate as number) : lo;
};

/** True when `sharpe` rests on enough days to be worth showing at all. */
export const sharpeMeasured = (t: Partial<TopTrader>): boolean =>
  (t.sharpe_days ?? 0) >= MIN_SHARPE_DAYS;

export type Follow = {
  id: string;
  follower: string;
  leader: string;
  size_pct: number;
  max_per_trade_usd: number;
  coins_allow: string[];
  coins_deny: string[];
  created_ms: number;
  last_seen_tid: number;
  paused: boolean;
  vault_address?: string | null;
};

export type IndexLeg = { address: string; weight: number };

export type Index = {
  id: string;
  name: string;
  owner: string;
  description: string;
  legs: IndexLeg[];
  days_window: number;
  created_ms: number;
  vault_address: string | null;
  max_leverage: number;
  notional_pct: number;
};

export type Signal = {
  id: string;
  follow_id: string;
  follower: string;
  leader: string;
  coin: string;
  side: string;
  leader_px: number;
  leader_sz: number;
  copy_sz: number;
  leader_tid: number;
  ts_ms: number;
  vault_address?: string | null;
  status: string;
};

// When the app is served behind a basePath (e.g. /hyperliquid via a reverse
// proxy), `/hl/*` won't reach Next. We have two rewrites — `/hl/*` for the
// no-basePath case, and `/api${basePath}/*` for the basePath case. Pick at
// runtime so the same client works in both deployments.
const BP = process.env.NEXT_PUBLIC_BASE_PATH || "";
const BASE = BP ? `/api${BP}` : "/hl";

// mod protocol-auth bearer token, persisted by WalletProvider (lib/wallet.tsx).
// Read per-call so a fresh sign-in takes effect without any plumbing.
function authToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return JSON.parse(localStorage.getItem("hl_wallet_v2") || "null")?.token ?? null;
  } catch { return null; }
}

// ── Errors that read like sentences ───────────────────────────────────────
//
// The old client threw `new Error("/indexes 401 unauthorized")` and pages
// printed it verbatim. That is a status code wearing a trench coat: it tells
// the person who just spent two minutes picking traders nothing about what
// went wrong or what to do. The API now answers refusals with a `message`
// written for a human plus a stable `reason`; this carries both, and keeps
// the route and status on the object for the console.
export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  /** Stable code from the API gate: no_token | expired_token | not_owner | … */
  readonly reason: string | null;
  /** The gate believes a fresh signature would fix this. */
  readonly signIn: boolean;
  readonly body: unknown;

  constructor(o: { status: number; path: string; message: string; reason?: string | null;
                   signIn?: boolean; body?: unknown }) {
    super(o.message);
    this.name = "ApiError";
    this.status = o.status;
    this.path = o.path;
    this.reason = o.reason ?? null;
    this.signIn = !!o.signIn;
    this.body = o.body;
  }

  /** What the console logs — the human line plus where it came from. */
  get detail(): string { return `${this.message} (${this.status} ${this.path})`; }
}

// Not every error body is ours: a proxy timing out in front of the API answers
// with its own HTML page, and dumping that verbatim into the UI is how a 524
// ends up rendered as a wall of markup. Prefer the API's human `message`, then
// its `error`/`detail`, then a short readable line of whatever came back.
function explain(status: number, body: string): { message: string; reason: string | null; signIn: boolean; parsed: unknown } {
  let parsed: any = null;
  try { parsed = JSON.parse(body); } catch { /* not ours */ }

  if (parsed && typeof parsed === "object") {
    const message = [parsed.message, parsed.error, parsed.detail]
      .find((v: unknown) => typeof v === "string" && v.trim());
    if (message) {
      return {
        message: String(message),
        reason: typeof parsed.reason === "string" ? parsed.reason : null,
        signIn: parsed.sign_in === true,
        parsed,
      };
    }
  }
  const text = body.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  return {
    // A bare status is still better than a wall of proxy markup.
    message: text ? (text.length > 160 ? `${text.slice(0, 160)}…` : text) : httpFallback(status),
    reason: null,
    signIn: status === 401,
    parsed,
  };
}

function httpFallback(status: number): string {
  if (status === 401) return "Sign in with your wallet to do that.";
  if (status === 403) return "That belongs to a different wallet.";
  if (status === 404) return "Not found.";
  if (status === 429) return "Too many requests right now — try again in a moment.";
  if (status >= 500) return "The Hyperliquid API is having trouble. Try again shortly.";
  return `Request failed (${status}).`;
}

// ── Session recovery ──────────────────────────────────────────────────────
//
// A mod-protocol token is a signature over a timestamp, so it expires — and
// the only way to renew one is to ask the wallet to sign again. lib/auth.tsx
// registers that ability here at mount, which lets a write that lands on an
// expired session recover itself: one MetaMask prompt, then the original
// request is replayed. It is the difference between "your session expired,
// here is a 401" and the save simply going through.
//
// Three rules keep that from becoming a nuisance:
//   1. Writes only. A background poll must never summon a signature prompt,
//      so GETs just report the 401 and let the UI offer sign-in.
//   2. One prompt. Concurrent 401s share a single in-flight refresh.
//   3. One try. A retry that fails again is a real failure, not a loop.
type Refresher = () => Promise<string | null>;
let refresher: Refresher | null = null;
let inFlight: Promise<string | null> | null = null;
let lastFailureMs = 0;
const REFRESH_COOLDOWN_MS = 10_000;

export function setTokenRefresher(fn: Refresher | null) { refresher = fn; }

async function refreshToken(): Promise<string | null> {
  if (!refresher) return null;
  if (Date.now() - lastFailureMs < REFRESH_COOLDOWN_MS) return null;
  if (!inFlight) {
    inFlight = refresher()
      .catch(() => null)
      .then((t) => { if (!t) lastFailureMs = Date.now(); return t; })
      .finally(() => { inFlight = null; });
  }
  return inFlight;
}

function send(path: string, init: RequestInit | undefined, token: string | null) {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const isWrite = !!init?.method && init.method.toUpperCase() !== "GET";
  let r = await send(path, init, authToken());

  if (r.status === 401 && isWrite && typeof window !== "undefined") {
    const fresh = await refreshToken();
    if (fresh) r = await send(path, init, fresh);
  }

  if (r.status === 401 && typeof window !== "undefined") {
    // Still refused after (at most) one re-sign — the session really is dead.
    // Tell WalletProvider to drop the token so the header stops claiming to
    // be signed in and starts offering the way back.
    window.dispatchEvent(new CustomEvent("hl:unauthorized"));
  }

  if (!r.ok) {
    const raw = await r.text().catch(() => "");
    const e = explain(r.status, raw);
    throw new ApiError({ status: r.status, path, message: e.message,
                         reason: e.reason, signIn: e.signIn, body: e.parsed });
  }
  return r.json() as Promise<T>;
}

/** Who the API thinks you are. 401 here means the stored token is dead. */
export const authMe = () => j<{ ok: boolean; address: string }>(`/auth/me`);

// ── market ──
// All mid prices, coin → price string (perps by ticker; spot/dex/prediction
// markets carry "@"/":"/"#" prefixes — callers filter what they need).
export const fetchMids = () => j<Record<string, string>>(`/mids`);

// ── traders ──
// Live progress of the API's current top-traders scan (also ticks while the
// server-side prewarm loop is refreshing boards). Polled while `scan` runs.
export type ScanProgress = {
  scanned: number;      // addresses enriched so far
  total: number;        // addresses in the enrich slice
  days: number;
  hours_total: number;  // days * 24 — history depth being covered
  hours_scanned: number;
  started_ms: number;
  finished_ms: number;
  running: boolean;
};
export const fetchScanProgress = () => j<ScanProgress>(`/scan/progress`);

// `coins` is a requirement, not a display filter: the API walks the ranked
// leaderboard until it holds `pool` wallets that traded one of them, and
// reports how deep it went as `depth`.
export const fetchTopTraders = (days: number, minPerDay = 1, pool = 150, seed?: string[], coins?: string[]) =>
  j<{ traders: TopTrader[]; days: number; pool: number; updated_at?: number;
      coins?: string[]; depth?: number; candidates?: number }>(
    `/traders/top?days=${days}&min_per_day=${minPerDay}&pool=${pool}` +
    (seed?.length ? `&seed=${encodeURIComponent(seed.join(","))}` : "") +
    (coins?.length ? `&coins=${encodeURIComponent(coins.join(","))}` : "")
  );

// The whole-board fetch: `pool: "all"` = every gated wallet on the leaderboard
// (priced from the CDN, free); `enrich` = how many top rows by `rank` get fill
// stats (win%/sharpe/trades/coins — one throttled query each, so rationed).
// Score floors are applied client-side on the full list, so they're instant.
export type BoardMeta = {
  days: number; pool: number | "all"; all: boolean; enrich: number;
  rank: string; active: string; sort: string; coins: string[];
  depth: number; candidates: number | null; priced: number; matched: number; enriched: number;
  updated_at: number;
  // A cold board is walked in the background (it outlives any proxy timeout):
  // `scanning` means these rows are the last good board, not the answer yet.
  scanning?: boolean;
  progress?: ScanProgress | null;
};
export const fetchBoard = (o: {
  days: number; pool: number | "all"; rank?: string; enrich?: number; seed?: string[]; coins?: string[];
}) =>
  j<BoardMeta & { traders: TopTrader[] }>(
    `/traders/top?days=${o.days}&pool=${o.pool}` +
    (o.rank ? `&rank=${o.rank}` : "") +
    (o.enrich != null ? `&enrich=${o.enrich}` : "") +
    (o.seed?.length ? `&seed=${encodeURIComponent(o.seed.join(","))}` : "") +
    (o.coins?.length ? `&coins=${encodeURIComponent(o.coins.join(","))}` : "")
  );

export const analyzeTrader = (addr: string, days: number) =>
  j<any>(`/trader/${addr}/analyze?days=${days}`);

// The shape behind a row's number: cumulative PnL across the window, rebased
// so the window opens at zero. Sourced from Hyperliquid's own portfolio
// series — the same definition the leaderboard prices `pnl` with — so the
// curve and the column agree. `available: false` carries a `note` instead of
// throwing: this is decoration on a row that already has its numbers, and a
// hover that errors looks like a broken row.
export type TraderCurve = {
  address: string;
  days: number;
  period: string;            // hyperliquid portfolio period backing the points
  points: [number, number][]; // [ms epoch, cumulative pnl]
  start_ms: number;
  end_ms: number;
  pnl: number;               // last point — what the drawn line ends at
  high: number;
  low: number;
  max_drawdown: number;      // deepest peak → trough fall, USD (>= 0)
  max_drawdown_pct: number;
  available: boolean;
  note?: string;
};
export const fetchTraderCurve = (addr: string, days: number) =>
  j<TraderCurve>(`/trader/${addr}/curve?days=${days}`);

// ── follows ──
export const listFollows = (follower?: string) =>
  j<{ follows: Follow[] }>(`/follows${follower ? `?follower=${follower}` : ""}`);

// `follower` is optional on purpose: the API takes it from the token that
// signed the request. A client that has to name itself can name itself wrong.
export const createFollow = (b: Partial<Follow> & { leader: string }) =>
  j<Follow>(`/follows`, { method: "POST", body: JSON.stringify(b) });

export const updateFollow = (id: string, b: Partial<Follow>) =>
  j<Follow>(`/follows/${id}`, { method: "PATCH", body: JSON.stringify(b) });

export const deleteFollow = (id: string) =>
  j<{ deleted: boolean }>(`/follows/${id}`, { method: "DELETE" });

export const pauseFollow = (id: string) =>
  j<Follow>(`/follows/${id}/pause`, { method: "POST" });
export const resumeFollow = (id: string) =>
  j<Follow>(`/follows/${id}/resume`, { method: "POST" });

export const listSignals = (follower?: string, limit = 100) =>
  j<{ signals: Signal[] }>(
    `/signals?limit=${limit}${follower ? `&follower=${follower}` : ""}`
  );

// ── indexes ──
export const listIndexes = () =>
  j<{ indexes: Index[] }>(`/indexes`);

export const getIndex = (id: string) => j<Index>(`/indexes/${id}`);

// `owner` likewise comes from the signed-in wallet — see createFollow.
export const createIndex = (b: Partial<Index> & { name: string; legs: IndexLeg[] }) =>
  j<Index>(`/indexes`, { method: "POST", body: JSON.stringify(b) });

export const updateIndex = (id: string, b: Partial<Index>) =>
  j<Index>(`/indexes/${id}`, { method: "PATCH", body: JSON.stringify(b) });

export const deleteIndex = (id: string) =>
  j<{ deleted: boolean }>(`/indexes/${id}`, { method: "DELETE" });

export const indexPerf = (id: string, days?: number) =>
  j<any>(`/indexes/${id}/perf${days ? `?days=${days}` : ""}`);

export const autoIndex = (b: { days?: number; top?: number; min_per_day?: number; pool?: number }) =>
  j<{ days: number; top: number; legs: IndexLeg[]; candidates: TopTrader[] }>(
    `/indexes/auto`, { method: "POST", body: JSON.stringify(b) }
  );

// ── vaults ──
export type Vault = {
  address: string;
  name: string;
  leader: string;
  apr: number;       // percent
  tvl: number;       // USD
  age_days: number;
};
export const listVaults = (pool = 300, minTvl?: number) =>
  j<{ vaults: Vault[] }>(`/vaults?pool=${pool}${minTvl != null ? `&min_tvl=${minTvl}` : ""}`);
export const vaultDetails = (addr: string, user?: string) =>
  j<any>(`/vaults/${addr}${user ? `?user=${user}` : ""}`);
export const vaultPerf = (addr: string) => j<any>(`/vaults/${addr}/perf`);
export const vaultIntent = (id: string, initial_usd: number) =>
  j<any>(`/indexes/${id}/vault/intent`, {
    method: "POST",
    body: JSON.stringify({ initial_usd }),
  });

// ── backend agent signer ──
export const signerAddress = (eoa: string) =>
  j<{ eoa: string; agentAddress: string }>(`/signer/address`, {
    method: "POST", body: JSON.stringify({ eoa }),
  });

export const approveAgentIntent = (eoa: string, agent_name?: string) =>
  j<{ action: any; nonce: number; agentAddress: string; digest: string; typedData: any; exchange_url: string }>(
    `/signer/approve_agent`,
    { method: "POST", body: JSON.stringify({ eoa, ...(agent_name ? { agent_name } : {}) }) }
  );

export const agentStatus = (eoa: string) =>
  j<{ eoa: string; agentAddress: string; approved: boolean; agents: any }>(
    `/agent/status?eoa=${encodeURIComponent(eoa)}`
  );

// ── master-wallet (MetaMask) signing: intents + relay ──
export type SignedIntent = { action: any; nonce: number; digest?: string; typedData: any };

export const intentWithdraw = (destination: string, amount: string) =>
  j<SignedIntent>(`/intent/withdraw`, {
    method: "POST", body: JSON.stringify({ destination, amount }),
  });

export const intentUsdClassTransfer = (amount: string, to_perp: boolean) =>
  j<SignedIntent>(`/intent/usd_class_transfer`, {
    method: "POST", body: JSON.stringify({ amount, to_perp }),
  });

export const exchangeRelay = (b: { action: any; nonce: number; signature: { r: string; s: string; v: number }; vaultAddress?: string }) =>
  j<any>(`/exchange/relay`, { method: "POST", body: JSON.stringify(b) });

export type WalletNetConfig = {
  testnet: boolean;
  chainId: number;
  chainIdHex: string;
  chainName: string;
  usdcAddress: string;
  bridgeAddress: string;
  rpcUrl: string;
  explorerUrl: string;
  minDepositUsd: number;
  withdrawalFeeUsd: number;
};
export const walletConfig = () => j<WalletNetConfig>(`/wallet/config`);

// ── cross-chain deposit: 7 chains → Hyperliquid, in one transaction ──
export type DepositToken = {
  symbol: string; address: string; decimals: number; native: boolean;
};
export type DepositChain = {
  key: string; name: string; chainId: number; chainIdHex: string;
  rpcUrl: string; explorerUrl: string; usdcAddress: string;
  nativeSymbol: string; gasReserve: number; direct: boolean;
  tokens: DepositToken[];
};
export type DepositChains = {
  testnet: boolean;
  /** Where deposits land — Hyperliquid Core itself (LI.FI chain id 1337). */
  toChainId: number; toUsdc: string;
  /** Withdrawals still exit via Arbitrum. */
  arbitrumChainId: number; arbitrumUsdc: string;
  minDepositUsd: number; chains: DepositChain[];
};
/** One spendable (chain, token) pair the wallet actually holds. */
export type DepositSourceRow = {
  chainKey: string; chainName: string; chainId: number;
  symbol: string; address: string; decimals: number; native: boolean;
  balance: number; max: number;
  /** null when no price is available — the balance is still depositable. */
  priceUsd: number | null; usd: number | null;
  gasReserve: number;
  /** USDC on Arbitrum: goes to Hyperliquid's own bridge, no router. */
  direct: boolean;
};
export type DepositSource = DepositSourceRow & { chain: DepositChain };
export type DepositBalance = {
  key: string; chainId: number; name: string; ok: boolean;
  native: { symbol: string; balance: number; usd: number; priceUsd: number; gasReserve: number };
  usdc: { balance: number; usd: number };
};
export type DepositQuote = {
  tool: string | null;
  fromChainId: number; fromChainName: string;
  fromToken: string; fromSymbol: string; fromAmountUnits: string;
  toChainId: number; toChainName: string;
  /** true when the route ends inside the Hyperliquid account — no follow-up tx. */
  landsOnHyperliquid: boolean;
  toUsdc: number; toUsdcMin: number;
  gasUsd: number; feeUsd: number; durationSec: number;
  approvalAddress: string | null;
  transactionRequest: { to: string; data: string; value?: string; gasLimit?: string; gasPrice?: string } | null;
};
export type DepositStatus = {
  status: "NOT_FOUND" | "INVALID" | "PENDING" | "DONE" | "FAILED";
  substatus?: string | null; substatusMessage?: string | null;
  receivedUsdc?: number | null; receivingTxHash?: string | null;
};

export const depositChains = () => j<DepositChains>(`/deposit/chains`);
export const depositBalances = (eoa: string) =>
  j<{ eoa: string; chains: DepositBalance[]; sources: DepositSourceRow[] }>(
    `/deposit/balances?eoa=${encodeURIComponent(eoa)}`);
export const depositQuote = (b: {
  from_chain_id: number;
  /** "usdc", "native", a symbol, or a token address. */
  token: string;
  amount: string; eoa: string;
  to_chain_id?: number; to_address?: string; // withdrawals bridge Arbitrum → elsewhere
}) =>
  j<DepositQuote>(`/deposit/quote`, { method: "POST", body: JSON.stringify(b) });
export const depositStatus = (txHash: string, fromChainId: number, toChainId?: number) =>
  j<DepositStatus>(`/deposit/status?tx_hash=${encodeURIComponent(txHash)}&from_chain_id=${fromChainId}` +
    (toChainId ? `&to_chain_id=${toChainId}` : ""));

export const userState = (addr: string) => j<any>(`/user/${addr}/state`);

// ── trade actions ──
export const trade = (b: {
  eoa: string; coin: string; is_buy: boolean; size: number;
  price?: number; tif?: "Gtc" | "Ioc" | "Alo"; reduce_only?: boolean;
  slippage_bps?: number; cloid?: string; vault_address?: string;
}) => j<any>(`/trade`, { method: "POST", body: JSON.stringify(b) });

export const cancelOrders = (b: { eoa: string; cancels: { coin: string; oid: number }[]; vault_address?: string }) =>
  j<any>(`/cancel`, { method: "POST", body: JSON.stringify(b) });

export const modifyOrder = (b: {
  eoa: string; oid: number; coin: string; is_buy: boolean;
  price: number; size: number; reduce_only?: boolean; tif?: string; vault_address?: string;
}) => j<any>(`/modify`, { method: "POST", body: JSON.stringify(b) });

export const setLeverage = (b: { eoa: string; coin: string; leverage: number; is_cross?: boolean; vault_address?: string }) =>
  j<any>(`/leverage`, { method: "POST", body: JSON.stringify(b) });

export const usdClassTransfer = (b: { eoa: string; amount: string; to_perp: boolean }) =>
  j<any>(`/usd_class_transfer`, { method: "POST", body: JSON.stringify(b) });

export const vaultTransfer = (b: { eoa: string; vault: string; is_deposit: boolean; amount_usd: number }) =>
  j<any>(`/vault_transfer`, { method: "POST", body: JSON.stringify(b) });

export const withdraw = (b: { eoa: string; destination: string; amount: string }) =>
  j<any>(`/withdraw`, { method: "POST", body: JSON.stringify(b) });

export const usdSend = (b: { eoa: string; destination: string; amount: string }) =>
  j<any>(`/usd_send`, { method: "POST", body: JSON.stringify(b) });

// ── live engine ──
export type LiveTrader = { address: string; weight?: number; enabled?: boolean };
export type LiveStartReq = {
  eoa: string; traders: LiveTrader[]; interval_ms?: number; size_pct?: number;
  max_per_trade_usd?: number; min_order_size_usd?: number; max_slippage_bps?: number;
  coins_allow?: string[]; coins_deny?: string[]; vault_address?: string; capital?: number;
  strategy_id?: string;
};
export const liveStart = (b: LiveStartReq) =>
  j<any>(`/live/start`, { method: "POST", body: JSON.stringify(b) });
export const liveStop = (eoa: string) =>
  j<any>(`/live/stop`, { method: "POST", body: JSON.stringify({ eoa }) });
export const liveStatus = (eoa: string) =>
  j<{ eoa: string; config: any; state: any }>(`/live/status?eoa=${encodeURIComponent(eoa)}`);

// ── MCP (`/mcp/schema`) ──
// The tool surface this module exposes to agents, and the mod-protocol fn +
// REST route behind each tool. Public — the connect page renders it signed-out.
export type McpTool = {
  name: string;
  fn: string;
  method: string;
  path: string;
  public: boolean;
  description: string;
  inputSchema: { properties?: Record<string, any>; required?: string[] };
};
export type McpTransport = {
  type: string;
  endpoint?: string;
  messages?: string;
  command?: string;
  note?: string;
};
export type McpSchema = {
  name: string;
  version: string;
  testnet: boolean;
  mcp: {
    endpoint: string;
    protocolVersion: string;
    supportedVersions: string[];
    auth: string;
    instructions: string;
    transports: McpTransport[];
  };
  tools: McpTool[];
};
export const fetchMcpSchema = () => j<McpSchema>(`/mcp/schema`);

/** Absolute URL of an API path — what an external agent must be pointed at. */
export const apiUrl = (path: string) =>
  typeof window === "undefined" ? path : new URL(`${BASE}${path}`, window.location.origin).toString();

// ── agent (`/ask`) ──
// The agent answers only out of MCP tool calls against this same API, so it
// sees exactly what the signed-in wallet sees. `act` unlocks the write tools.
export type AskStatus = {
  ready: boolean;
  auth?: string | null;
  hint?: string | null;
  model?: string;
  max_turns?: number;
  read_tools?: number;
  write_tools?: number;
};
export const askStatus = () => j<AskStatus>(`/ask/status`);

export type AskEvent =
  | { type: "ready"; tools: number; act: boolean; signed_in: boolean }
  | { type: "start"; model: string; tools: number }
  | { type: "text"; text: string }
  | { type: "tool"; name: string; args: Record<string, any> }
  | { type: "tool_done"; error: boolean }
  | { type: "done"; answer: string; turns?: number; ms?: number; cost_usd?: number }
  | { type: "error"; error: string };

/** POST /ask and dispatch its SSE events as they arrive. */
export async function askStream(
  body: { question: string; act?: boolean },
  onEvent: (ev: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = authToken();
  const r = await fetch(`${BASE}/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (r.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("hl:unauthorized"));
  }
  if (!r.ok || !r.body) {
    const detail = await r.text().catch(() => "");
    throw new Error(`ask ${r.status} ${detail}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; each carries one JSON event.
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim())
        .join("");
      if (!data) continue;
      try { onEvent(JSON.parse(data) as AskEvent); } catch { /* keep-alive */ }
    }
  }
}

// ── formatting helpers ──
export const fmtUsd = (n: number) => {
  const a = Math.abs(n);
  const s = a >= 1e9 ? `${(a / 1e9).toFixed(2)}B`
    : a >= 1e6 ? `${(a / 1e6).toFixed(2)}M`
    : a >= 1e3 ? `${(a / 1e3).toFixed(2)}K`
    : a.toFixed(2);
  return `${n < 0 ? "-" : ""}$${s}`;
};

export const fmtPnl = (n: number) => `${n >= 0 ? "+" : "-"}${fmtUsd(Math.abs(n))}`;

export const fmtPct = (n: number, digits = 1) => `${(n).toFixed(digits)}%`;

export const shortAddr = (a: string) =>
  a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;

export const ago = (ms: number) => {
  if (!ms) return "—";
  const d = Date.now() - ms;
  const m = Math.floor(d / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
};

// The investment book's client lives in `lib/invest.ts` — it is a feature of
// its own and does not need to grow this file. It borrows the transport, so
// auth, the 401 → re-sign event and the basePath rewrite stay in one place.
export const apiCall = j;
