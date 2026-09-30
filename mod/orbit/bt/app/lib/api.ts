/* The one wire this console speaks: bt.server on the same origin.
 *
 * Every tool goes through POST {base}/api/call — the same registry the MCP
 * server publishes — so nothing here knows about the chain, a key, or a
 * third-party API. BASE is baked at build time (next.config.mjs) and matches
 * the gateway prefix; bt.server strips it, so the bare port works too. */

export const BASE: string = process.env.NEXT_PUBLIC_BASE ?? '/bt';

export const api = (p: string) => `${BASE}/api/${p.replace(/^\//, '')}`;

export interface CallReply<T = any> { ok: boolean; tool: string; ms: number; result: T; error?: string }

export async function call<T = any>(tool: string, args: Record<string, unknown> = {},
                                    signal?: AbortSignal): Promise<CallReply<T>> {
  const r = await fetch(api('call'), {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ tool, args }), signal,
  });
  let j: CallReply<T>;
  try { j = await r.json(); } catch { throw new Error(`HTTP ${r.status}`); }
  if (!j.ok) throw new Error(j.error || 'call failed');
  return j;
}

export async function getJSON<T = any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(api(path), init);
  if (!r.ok && r.status >= 500) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export const postJSON = <T = any>(path: string, body: unknown) =>
  getJSON<T>(path, { method: 'POST', headers: { 'content-type': 'application/json' },
                     body: JSON.stringify(body) });

/* tools that sign or touch key material — always confirmed in the browser */
export const WRITE_TOOLS = /^bt_(buy|sell|sell_all|swap|transfer|create_wallet)$/;

/* ------------------------------------------------------------ row shapes */

export interface SubnetRow {
  netuid: number; name?: string; symbol?: string; price?: number;
  market_cap?: number; tao_in?: number; alpha_in?: number; alpha_out?: number;
  vol_24h?: number | null; emission?: number; registered_at?: number;
  tempo?: number; owner?: string;
  change_1h?: number | null; change_24h?: number | null; change_7d?: number | null;
  spark?: number[]; logo?: string | null; github?: string | null; url?: string | null;
  discord?: string | null; description?: string | null;
}

export interface TraderRow {
  ss58: string; label?: string | null; total_tao?: number | null; free_tao?: number;
  staked_tao?: number; subnets?: number; change_24h?: number | null;
  change_7d?: number | null; flows_24h?: number; spark?: number[]; warming?: boolean;
}

export interface BoardRow {
  ss58: string; label?: string | null; pnl_tao: number; pnl_pct: number;
  market_pnl_tao: number; market_pct: number; flow_tao: number; num_subnets: number;
  top_subnet: number | null; top_subnet_name: string | null; baseline: boolean;
  window_days: number; total_stake_tao: number; free_tao: number; spark?: number[];
}

export interface Flow {
  ts: number; ss58?: string; label?: string | null; side: 'buy' | 'sell';
  netuid: number; name?: string; alpha: number; tao_value: number;
}

export interface Position {
  netuid: number; name?: string; hotkey?: string; alpha: number; price?: number;
  value_tao?: number | null; pct_of_total?: number;
}

export interface Stats {
  subnets?: number; total_market_cap_tao?: number; total_tao_in_pools?: number;
  volume_24h_tao?: number | null; updated_at?: number; block?: number; warming?: boolean;
}

export interface ToolSchema {
  name: string; description: string;
  inputSchema: { properties?: Record<string, { type: string; description?: string; default?: unknown }>;
                 required?: string[] };
}

/* what bt_view hands the console (see bt/tools.py _view) */
export interface ViewAction {
  view: string; netuid?: number; address?: string; search?: string; sort_by?: string;
}
