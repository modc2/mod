import { ownerHeader } from "./owner";
import type {
  AccountData,
  Backtest,
  ServerStrat,
  StratWrite,
  AccountWatch,
  AgentEvent,
  AgentStatus,
  CopyConfig,
  CurveData,
  LeaderboardEntry,
  MarketStats,
  PnlData,
  PortfolioPlan,
  PricePoint,
  SubnetDetail,
  SubnetInfo,
  Trade,
  TraderBoard,
  Universe,
} from "./types";

// All requests go through the Next.js rewrite at /api/copytensor → backend.
// Defined in next.config.js so the basePath ("/copytensor") doesn't get
// prepended to API calls.
const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/copytensor";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!r.ok)
    throw new Error(`${path} ${r.status} ${await r.text().catch(() => "")}`);
  return r.json() as Promise<T>;
}

// ── price ──
export const fetchTaoPrice = () =>
  j<{ usd: number; age_sec: number; stale: boolean } | { error: string }>("/tao_price");

// ── subnets ──
export const fetchSubnets = () => j<SubnetInfo[]>("/subnets");

export const fetchMarket = () => j<MarketStats>("/market");

export const fetchSubnetDetail = (netuid: number | string) =>
  j<SubnetDetail>(`/subnets/${netuid}`);

export const fetchSubnetHistory = (netuid: number | string, hours = 168) =>
  j<PricePoint[]>(`/subnets/${netuid}/history?hours=${hours}`);

// ── accounts ──
export const fetchAccount = (ss58: string, days = 7) =>
  j<AccountData>(`/account/${ss58}?days=${days}`);

export const fetchPnl = (ss58: string, days = 7) =>
  j<PnlData>(`/account/${ss58}/pnl?days=${days}`);

// Equity / PnL curve rebuilt from local snapshots, with inferred trades
// carrying the curve value at their timestamp (so markers sit on the line).
export const fetchCurve = (ss58: string, days = 7) =>
  j<CurveData>(`/account/${ss58}/curve?days=${days}`);

// ── leaderboard ──
export const fetchLeaderboard = (days = 7, top = 50) =>
  j<LeaderboardEntry[]>(`/leaderboard?days=${days}&top=${top}`);

// ── trader pool ──
// The leaderboard ranks the coldkeys we watch, so the pool size IS how many
// traders are visible. `known` is how many exist on-chain to choose from.
export const fetchUniverse = () => j<Universe>("/universe");

export const setPool = (size: number, refresh = false) =>
  j<Universe & { queued: boolean }>(
    `/pool?size=${size}&refresh=${refresh}`,
    { method: "POST" }
  );

// ── trader index (bt) ──
// Every tracked coldkey with live value, 24h/7d change and a spark — one
// request, answered from bt's local index. Cheap enough to hang a whole
// panel off, unlike /account/{ss58} which walks the chain per address.
export const fetchTraders = (sortBy = "total_tao") =>
  j<TraderBoard>(`/traders?sort_by=${sortBy}`);

// ── watchlist ──
export const fetchWatches = () =>
  j<{ accounts: AccountWatch[] }>("/watches");

export const watchAccount = (ss58: string, label?: string) =>
  j<{ watched: string; total: number }>("/watch", {
    method: "POST",
    body: JSON.stringify({ ss58, label }),
  });

export const unwatchAccount = (ss58: string) =>
  j<{ unwatched: string; total: number }>(`/watch/${ss58}`, {
    method: "DELETE",
  });

// ── copy trading ──
export const fetchCopies = () => j<CopyConfig[]>("/copies");

export const createCopy = (body: {
  target_ss58: string;
  our_hotkey: string;
  label?: string;
  /** The TAO behind this trader. Required — it is the size of the position. */
  alloc_tao: number;
  max_tao_per_tx?: number;
  daily_limit_tao?: number;
  rebalance_threshold_pct?: number;
  poll_interval_sec?: number;
}) => j<CopyConfig>("/copy", { method: "POST", body: JSON.stringify(body) });

/** Re-size a live copy. The blend picks it up on the next pass — no exit. */
export const updateCopy = (
  id: string,
  body: {
    alloc_tao?: number;
    label?: string;
    our_hotkey?: string;
    max_tao_per_tx?: number;
    rebalance_threshold_pct?: number;
    poll_interval_sec?: number;
  },
) => j<CopyConfig>(`/copy/${id}`, { method: "PUT", body: JSON.stringify(body) });

// ── portfolio ──
// Every active copy blended into one book. `portfolioPlan` is a pure read of
// exactly what `portfolioSync` would execute, so the preview cannot drift
// from the thing it previews.

export const portfolioPlan = () => j<PortfolioPlan>("/portfolio");

export const portfolioSync = (dryRun = false) =>
  j<PortfolioPlan>(`/portfolio/sync?dry_run=${dryRun}`, { method: "POST" });

export const pauseCopy = (id: string) =>
  j<{ id: string; status: string }>(`/copy/${id}/pause`, { method: "POST" });

export const resumeCopy = (id: string) =>
  j<{ id: string; status: string }>(`/copy/${id}/resume`, { method: "POST" });

export const deleteCopy = (id: string) =>
  j<{ deleted: boolean }>(`/copy/${id}`, { method: "DELETE" });

export const syncCopy = (id: string) =>
  j<{ synced: boolean; trades: any[] }>(`/copy/${id}/sync`, { method: "POST" });

// ── trades ──
export const fetchTrades = (limit = 50, copyId?: string) =>
  j<Trade[]>(
    `/trades?limit=${limit}${copyId ? `&copy_id=${copyId}` : ""}`
  );

// ── strat agent ──
export const fetchAgentStatus = () => j<AgentStatus>("/agent");

/**
 * Talk to the strat agent. The reply is a live SSE stream, not a payload —
 * tool calls land as they happen and the basket arrives before the closing
 * summary, so `onEvent` is the whole interface.
 *
 * Pass `sessionId` (from the `start`/`done` events) to continue the same
 * conversation; omit it to begin a fresh one.
 */
export async function askAgent(
  question: string,
  sessionId: string | null,
  onEvent: (ev: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`${BASE}/agent/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, session_id: sessionId }),
    cache: "no-store",
    signal,
  });
  if (!r.ok || !r.body)
    throw new Error(`/agent/ask ${r.status} ${await r.text().catch(() => "")}`);

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // Frames are "data: {...}\n\n"; a chunk can split one anywhere, so keep
    // the tail until its blank line shows up.
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as AgentEvent);
      } catch {
        /* a truncated frame is not worth killing the stream over */
      }
    }
  }
}

// ── wallet ──
export const setWallet = (body: { mnemonic?: string; seed_hex?: string }) =>
  j<{ wallet_set: boolean; ss58: string }>("/wallet/set", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const walletBalance = () =>
  j<{ ss58: string; balance_tao: number }>("/wallet/balance");

// ── strats (server-side, owned by your owner key) ──
// Every strat call carries X-Owner-Key. The key lives in this browser only
// (see lib/owner.ts); the server stores its hash, so "mine" means "signed
// with the same key", and the fingerprint is what you hand someone who
// should be whitelisted.

export const fetchWhoami = () =>
  j<{ fingerprint: string | null; anonymous: boolean }>("/whoami", {
    headers: ownerHeader(),
  });

export const backtestBasket = (
  traders: Array<{
    ss58: string;
    weight: number;
    enabled?: boolean;
    label?: string | null;
    /** When set, the leg is weighted by this TAO rather than by `weight`. */
    alloc_tao?: number | null;
  }>,
  days = 7,
  capitalTao = 100,
) =>
  j<Backtest>("/strats/backtest", {
    method: "POST",
    body: JSON.stringify({ traders, days, capital_tao: capitalTao }),
  });

export const fetchStrats = () =>
  j<{ fingerprint: string | null; strats: ServerStrat[] }>("/strats", {
    headers: ownerHeader(),
  });

export const fetchHubStrats = () =>
  j<{ strats: ServerStrat[] }>("/strats/hub", { headers: ownerHeader() });

export const createStrat = (body: StratWrite) =>
  j<ServerStrat>("/strats", {
    method: "POST",
    headers: ownerHeader(),
    body: JSON.stringify(body),
  });

export const updateStrat = (id: string, body: StratWrite) =>
  j<ServerStrat>(`/strats/${id}`, {
    method: "PUT",
    headers: ownerHeader(),
    body: JSON.stringify(body),
  });

export const deleteStrat = (id: string) =>
  j<{ deleted: string }>(`/strats/${id}`, {
    method: "DELETE",
    headers: ownerHeader(),
  });

export const cloneStrat = (id: string) =>
  j<ServerStrat>(`/strats/${id}/clone`, {
    method: "POST",
    headers: ownerHeader(),
  });

// ── formatting helpers ──
export const fmtTao = (n: number) => {
  const a = Math.abs(n);
  const s =
    a >= 1e6
      ? `${(a / 1e6).toFixed(2)}M`
      : a >= 1e3
        ? `${(a / 1e3).toFixed(2)}K`
        : a.toFixed(4);
  return `${n < 0 ? "-" : ""}${s} TAO`;
};

export const fmtPnl = (n: number) =>
  `${n >= 0 ? "+" : ""}${fmtTao(n)}`;

export const fmtPct = (n: number, digits = 1) =>
  `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;

export const shortSs58 = (a: string) =>
  a.length > 14 ? `${a.slice(0, 8)}...${a.slice(-6)}` : a;

// Big numbers in a narrow cell: 5.44M / 214.7K / 1,234 / 0.0827
export const fmtCompact = (n: number | null | undefined, digits = 2) => {
  if (n == null || !isFinite(n)) return "—";
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(digits)}B`;
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(digits)}M`;
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(1)}K`;
  if (a >= 1) return `${sign}${a.toFixed(digits)}`;
  if (a === 0) return "0";
  return `${sign}${a.toPrecision(3)}`;
};

// A stable accent per subnet — logo-less subnets still get an identity.
export const netuidHue = (netuid: number) => (netuid * 47) % 360;

export const ago = (ts: string) => {
  if (!ts) return "-";
  const ms = Date.now() - new Date(ts).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
};
