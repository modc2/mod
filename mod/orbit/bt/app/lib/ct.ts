/* copytensor client + the copy sidebar's signal.
 *
 * Every call goes to {BASE}/ct/*, bt.server's proxy onto the submod's API
 * (:50150). Writes are operator-only: on localhost that is automatic,
 * anywhere else the operator key (~/.mod/bt/write_token) pasted once rides
 * along as a bearer token. Strats are owned by copytensor's per-browser owner
 * key — same origin as the copy studio, so the strats you built there show
 * up here as "yours". */
import { useEffect, useState } from 'react';
import { BASE } from './api';

export interface CtStatus { running: boolean; reads: string; wallet_set: boolean; active_copies: number;
  allocated_tao: number; tracked_accounts: number; block_height?: number }
export interface Copy { id: string; target_ss58: string; label?: string | null; status: string;
  alloc_tao: number; last_sync_block?: number | null;
  target_info?: { total_stake_tao: number; num_subnets: number; pnl_pct: number } | null }
export interface PlanRow { netuid: number; subnet_name: string; action: string; desired_tao: number;
  current_tao: number; amount_tao: number; reason: string }
export interface Plan { our_ss58?: string | null; staked_tao: number; free_tao: number; requested_tao: number;
  deployable_tao: number; scale: number; rows: PlanRow[]; trades: number; blocked?: string | null;
  notes: string[]; executed: boolean; results: { action: string; netuid: number; amount_tao: number; status: string; error?: string }[] }
export interface Trade { id: string; timestamp: string; action: string; netuid: number; amount_tao: number;
  status: string; error?: string | null; tx_hash?: string | null }
export interface StratTrader { ss58: string; weight: number; enabled?: boolean; label?: string | null; alloc_tao?: number | null }
export interface Strat { id: string; name: string; visibility: string; mine: boolean; owner_fingerprint: string | null;
  traders: StratTrader[]; thesis?: string | null; live_copy_ids?: string[]; updated_at: number }
export interface Backtest { ok: boolean; note?: string; points: number; thin: boolean;
  stats: { total_return_pct?: number; max_drawdown_pct?: number } }

export const TOKEN_KEY = 'bt.ct.token';
const OWNER_KEY = 'copytensor:owner_key:v1';   /* copytensor's own key — read, never minted here */

export async function ct<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let tok = '', owner = '';
  try { tok = JSON.parse(localStorage.getItem(TOKEN_KEY) || '""'); } catch { /* */ }
  try { owner = localStorage.getItem(OWNER_KEY) || ''; } catch { /* */ }
  const r = await fetch(`${BASE}/ct/${path.replace(/^\//, '')}`, {
    method, cache: 'no-store',
    headers: { 'content-type': 'application/json', ...(tok ? { authorization: `Bearer ${tok}` } : {}),
               ...(owner ? { 'x-owner-key': owner } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j: any = null;
  try { j = await r.json(); } catch { /* */ }
  if (r.status === 403) throw new Error('403 operator only');
  if (!r.ok) throw new Error(j?.detail || j?.error || `HTTP ${r.status}`);
  if (j && j.ok === false && !('points' in j)) throw new Error(j.error || 'copytensor unreachable');
  return j as T;
}

export const denied = (m: string) => m.startsWith('403');

/* ------------------------------------------------------------ the sidebar
 * Anything can open/toggle the copy sidebar without importing it (same
 * pattern as lib/dock for the chat). `target` pre-fills "Copy a trader". */
export type CopyTab = 'copy' | 'strats';
export interface CopyDockRequest { open?: boolean | 'toggle'; tab?: CopyTab; target?: string }
export const COPYDOCK_EVENT = 'bt:copydock';

export function openCopy(req: CopyDockRequest = { open: true }) {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent<CopyDockRequest>(COPYDOCK_EVENT, { detail: req }));
}
export const toggleCopy = () => openCopy({ open: 'toggle' });

/* whether the sidebar is showing — CopyDock broadcasts it */
export function useCopyOpen() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const f = (e: Event) => setOn(!!(e as CustomEvent<boolean>).detail);
    addEventListener('bt:copydock-state', f);
    return () => removeEventListener('bt:copydock-state', f);
  }, []);
  return on;
}
