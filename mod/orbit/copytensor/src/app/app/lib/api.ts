import type {
  AccountData,
  AccountWatch,
  CopyConfig,
  LeaderboardEntry,
  PnlData,
  SubnetInfo,
  Trade,
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

// ── subnets ──
export const fetchSubnets = () => j<SubnetInfo[]>("/subnets");

// ── accounts ──
export const fetchAccount = (ss58: string, days = 7) =>
  j<AccountData>(`/account/${ss58}?days=${days}`);

export const fetchPnl = (ss58: string, days = 7) =>
  j<PnlData>(`/account/${ss58}/pnl?days=${days}`);

// ── leaderboard ──
export const fetchLeaderboard = (days = 7, top = 50) =>
  j<LeaderboardEntry[]>(`/leaderboard?days=${days}&top=${top}`);

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
  target_ss58?: string;
  targets?: { ss58: string; weight: number }[];
  our_hotkey: string;
  label?: string;
  max_tao_per_tx?: number;
  daily_limit_tao?: number;
  rebalance_threshold_pct?: number;
}) => j<CopyConfig>("/copy", { method: "POST", body: JSON.stringify(body) });

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

// ── wallet ──
export const setWallet = (body: { mnemonic?: string; seed_hex?: string }) =>
  j<{ wallet_set: boolean; ss58: string }>("/wallet/set", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const walletBalance = () =>
  j<{ ss58: string; balance_tao: number }>("/wallet/balance");

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
