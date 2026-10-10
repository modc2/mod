// One way to reach the API. The app is served under /starknet; the server
// maps /starknet/_api/<route> to <route> (the gateway owns /starknet/api/*).
export const BASE = process.env.NEXT_PUBLIC_BASE || '/starknet';
export const API = `${BASE}/_api`;

export type Network = 'mainnet' | 'sepolia';

export class ApiError extends Error {
  body: any;
  constructor(msg: string, body: any) { super(msg); this.body = body; }
}

export async function get<T = any>(path: string, network?: Network): Promise<T> {
  const sep = path.includes('?') ? '&' : '?';
  const url = `${API}/${path.replace(/^\//, '')}${network ? `${sep}network=${network}` : ''}`;
  const r = await fetch(url, { headers: { Accept: 'application/json' } });
  return parse<T>(r);
}

export async function post<T = any>(path: string, body: any, network?: Network): Promise<T> {
  const r = await fetch(`${API}/${path.replace(/^\//, '')}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ ...body, ...(network ? { network } : {}) }),
  });
  return parse<T>(r);
}

async function parse<T>(r: Response): Promise<T> {
  let j: any;
  try { j = await r.json(); } catch { throw new ApiError(`HTTP ${r.status}`, null); }
  if (!r.ok) throw new ApiError(j?.error || `HTTP ${r.status}`, j);
  return j as T;
}

/** Absolute API base for copy-paste snippets (curl, MCP config). */
export function apiOrigin(): string {
  if (typeof window === 'undefined') return API;
  return `${window.location.origin}${API}`;
}
