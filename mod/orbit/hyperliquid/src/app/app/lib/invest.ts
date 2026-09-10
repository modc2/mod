// The investment book, client side.
//
// One noun (a position) and one verb (invest), whether the money goes into a
// Hyperliquid vault or into a trader. The console never has to branch on which
// kind it is until it renders the words.

import { apiCall } from "./api";

export type InvestKind = "vault" | "trader";
export type InvestStatus = "active" | "paused" | "closing" | "closed";
export type InvestMode = "live" | "paper";

export type Risk = {
  max_leverage: number;
  max_slippage_bps: number;
  min_order_usd: number;
  coins_allow: string[];
  coins_deny: string[];
  stop_loss_pct: number;
};

export type Valuation = {
  equity: number;
  pnl: number;
  roi_pct: number;
  realized: number;
  unrealized: number;
  exposure: number;
  leverage: number;
  basis_note: string;
  authoritative: boolean;
};

export type PositionLeg = {
  coin: string;
  size: number;
  avg_px: number;
  mark: number;
  notional: number;
  unrealized: number;
};

export type Position = {
  id: string;
  investor: string;
  kind: InvestKind;
  target: string;
  name: string;
  status: InvestStatus;
  mode: InvestMode;
  group_id: string | null;
  group_name: string | null;
  group_weight: number;
  contributed_usd: number;
  withdrawn_usd: number;
  net_contributed: number;
  basis: number;
  risk: Risk;
  value: Valuation;
  legs: PositionLeg[];
  created_ms: number;
  updated_ms: number;
  last_sync_ms: number;
  last_error: string | null;
  next_attempt_ms: number;
};

export type SleeveFill = {
  ts_ms: number; coin: string; side: string; size: number; price: number;
  notional: number; realized: number; reason: string; live: boolean;
};
export type Flow = { ts_ms: number; dir: string; amount_usd: number; note: string };
export type PositionEvent = { ts_ms: number; kind: string; text: string };

export type PositionDetail = Position & {
  fills: SleeveFill[];
  flows: Flow[];
  events: PositionEvent[];
  realized_pnl: number;
  leader?: {
    address: string;
    equity: number;
    positions: number;
    scale: number;
    deleverage: number;
    targets: { coin: string; size: number; mark: number; notional: number }[];
  };
};

export type Portfolio = {
  investor: string;
  positions: Position[];
  totals: {
    count: number; invested: number; equity: number;
    pnl: number; roi_pct: number; exposure: number;
  };
  capacity: {
    account_value: number; withdrawable: number; committed: number; free: number;
  };
  engine: {
    dry_run: boolean;
    stats: {
      cycles: number; last_cycle_ms: number; positions_tracked: number;
      orders_placed: number; orders_failed: number; paper_fills: number;
      volume_usd: number; last_error: string | null;
    };
  };
};

export type PreviewRow = {
  coin: string; size: number; mark: number; notional: number; side: "long" | "short";
};

export type Preview = {
  trader: string;
  amount: number;
  leader_equity: number;
  leader_positions: number;
  scale: number;
  deleverage: number;
  gross: number;
  leverage: number;
  positions: PreviewRow[];
  too_small: PreviewRow[];
  covered_pct: number;
  min_order_usd: number;
  note: string;
};

const qs = (params: Record<string, string | number | boolean | undefined>) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");

// ── reads ──

/** What `amount` would buy in `trader`. Public — no wallet required. */
export const previewInvest = (
  trader: string,
  amount: number,
  opts: { max_leverage?: number; min_order_usd?: number; coins_allow?: string } = {},
) => apiCall<Preview>(`/invest/preview?${qs({ trader, amount, ...opts })}`);

export const portfolio = (investor: string, include_closed = false) =>
  apiCall<Portfolio>(`/invest?${qs({ investor, include_closed })}`);

export const position = (id: string) => apiCall<PositionDetail>(`/invest/${id}`);

// ── writes ──

export type InvestReq = {
  investor: string;
  kind: "vault" | "trader" | "strat";
  target?: string;
  index_id?: string;
  amount_usd: number;
  name?: string;
  mode?: InvestMode;
  risk?: Partial<Risk>;
};

export const invest = (req: InvestReq) =>
  apiCall<{ ok: boolean; position?: Position; positions?: Position[]; group_id?: string }>(
    `/invest`, { method: "POST", body: JSON.stringify(req) });

export const addToPosition = (id: string, amount_usd: number) =>
  apiCall<{ ok: boolean; position: Position }>(
    `/invest/${id}/add`, { method: "POST", body: JSON.stringify({ amount_usd }) });

export const withdrawFromPosition = (id: string, amount_usd?: number, all = false) =>
  apiCall<{ ok: boolean; position: Position }>(
    `/invest/${id}/withdraw`, { method: "POST", body: JSON.stringify({ amount_usd, all }) });

export const pausePosition = (id: string) =>
  apiCall<{ ok: boolean; position: Position }>(`/invest/${id}/pause`, { method: "POST", body: "{}" });

export const resumePosition = (id: string) =>
  apiCall<{ ok: boolean; position: Position }>(`/invest/${id}/resume`, { method: "POST", body: "{}" });

export const closePosition = (id: string) =>
  apiCall<{ ok: boolean; position: Position }>(`/invest/${id}/close`, { method: "POST", body: "{}" });

export const updatePosition = (
  id: string,
  patch: { name?: string; mode?: InvestMode; risk?: Partial<Risk> },
) => apiCall<{ ok: boolean; position: Position }>(
  `/invest/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

export const forgetPosition = (id: string) =>
  apiCall<{ ok: boolean }>(`/invest/${id}`, { method: "DELETE" });

// ── words ──
//
// Every label the feature uses lives here, so "invest" never becomes "deposit"
// on one screen and "allocate" on the next.

export const kindLabel = (p: { kind: InvestKind; mode: InvestMode }) =>
  p.mode === "paper" ? "paper" : p.kind === "vault" ? "vault" : "trader";

export const statusLabel = (p: Position): string => {
  if (p.status === "closing") return "closing out";
  if (p.status === "paused") return "paused";
  if (p.status === "closed") return "closed";
  if (p.last_error) return "needs attention";
  return p.kind === "vault" ? "invested" : "tracking";
};

export const statusTone = (p: Position): "win" | "warn" | "loss" | "muted" => {
  if (p.status === "closed") return "muted";
  if (p.last_error) return "loss";
  if (p.status === "paused" || p.status === "closing") return "warn";
  return "win";
};

/** Plain-language explanation of what this position is doing right now. */
export function explain(p: Position): string {
  if (p.kind === "vault") {
    if (p.status === "closed") return "Closed — the money is back in your Hyperliquid balance.";
    return "Your USDC sits in this vault; the vault's leader trades it. Value comes straight from Hyperliquid.";
  }
  if (p.status === "closed") return "Closed — every position was sold back and the money is free again.";
  if (p.status === "closing") return "Selling out of every position this sleeve holds. Usually done within a minute.";
  if (p.status === "paused") return "Paused — what you hold stays as it is, and nothing new is opened.";
  if (p.mode === "paper") return "Paper: the same trades are tracked and priced live, but no orders are sent and no money moves.";
  return "Holding what this trader holds, scaled to your money. It re-checks every few seconds and trades only the difference.";
}
