/* The console's one door to the module: GET/POST <base>/_api/<fn>.
 *
 * Everything the UI knows about a chain comes from GET networks — a new
 * adapter under src/networks/<chain>/ shows up here with no UI change.
 */
export const BASE = process.env.NEXT_PUBLIC_BASE ?? '/ztao';
const API = `${BASE}/_api`;

export async function get<T = any>(fn: string, q: Record<string, any> = {}): Promise<T> {
  const qs = new URLSearchParams(
    Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => [k, String(v)]),
  ).toString();
  const r = await fetch(`${API}/${fn}${qs ? `?${qs}` : ''}`, { cache: 'no-store' });
  const d = await r.json().catch(() => ({ error: `${fn}: HTTP ${r.status}` }));
  if (!r.ok || (d && typeof d === 'object' && !Array.isArray(d) && d.error && Object.keys(d).length === 1))
    throw new Error(d?.error || `${fn}: HTTP ${r.status}`);
  return d as T;
}

export async function post<T = any>(fn: string, body: Record<string, any> = {}): Promise<T> {
  const r = await fetch(`${API}/${fn}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({ error: `${fn}: HTTP ${r.status}` }));
  if (!r.ok) throw new Error(d?.error || `${fn}: HTTP ${r.status}`);
  return d as T;
}

export type Chain = {
  chain: string;
  title: string;
  name?: string;
  nets: string[];
  net: string;
  max_ring: number;
  cost_fields: string[];
  local?: string;
  color?: string;
  verifier?: string;
  deploying?: string;
};

export type Point = { x: string; y: string };

export type Tag = {
  tag: string;
  score_sum: number;
  scores: number;
  claimed?: boolean;
  consensus?: number;
  rank?: number;
  settled?: boolean;
};

export type Tally = {
  epoch: number;
  total: number;
  validators?: number;
  rank_total?: number;
  settled?: number;
  tags: Tag[];
  open?: boolean;
};

export type Board = {
  network: string;
  chain: string;
  deployed: boolean;
  devnet_up: boolean;
  contract?: string;
  owner?: string;
  tempo?: number;
  min_ring?: number;
  emission?: number;
  open?: boolean;
  block?: number;
  epoch?: number;
  blocks_left?: number;
  miners?: Point[];
  validators?: Point[];
  epochs?: Tally[];
  [k: string]: any;
};

export type SimEvent = { kind: string; t: number; chain?: string; [k: string]: any };

/* Cost of one event in a chain's own units: the first cost_field it carries. */
export function costOf(e: Record<string, any>, fields: string[]): { field: string; v: number } | null {
  for (const f of fields) if (typeof e[f] === 'number') return { field: f, v: e[f] };
  return null;
}

export const short = (h?: string, a = 6, b = 4) =>
  !h ? '' : h.length <= a + b + 2 ? h : `${h.slice(0, a)}…${h.slice(-b)}`;

export const fmt = (n?: number | null, d = 0) =>
  n === undefined || n === null || Number.isNaN(n) ? '—'
    : n >= 1e9 ? `${(n / 1e9).toFixed(2)}B`
    : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M`
    : n >= 1e4 ? `${(n / 1e3).toFixed(1)}k`
    : n.toLocaleString(undefined, { maximumFractionDigits: d });

export const usd = (n?: number | null) =>
  n === undefined || n === null ? '—'
    : n < 0.01 ? `$${n.toPrecision(2)}`
    : n < 1 ? `$${n.toFixed(3)}`
    : `$${n.toFixed(2)}`;

/* Deterministic hue from a hex string: identicons for public keys and tags. */
export function hueOf(h: string): number {
  let x = 0;
  for (let i = 2; i < Math.min(h.length, 20); i++) x = (x * 31 + h.charCodeAt(i)) >>> 0;
  return x % 360;
}

/* "3 h ago" from a unix-seconds timestamp. */
export function ago(sec: number): string {
  const s = Math.max(0, Date.now() / 1000 - sec);
  return s < 90 ? 'just now' : s < 5400 ? `${Math.round(s / 60)} min ago`
    : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
}
