// Shared client helpers: the API base, owner sign-in, and job shapes.

export const BASE = process.env.NEXT_PUBLIC_BASE ?? '/cairo';
// Self-calls go to /cairo/_api/* — the gateway KEEPS this prefix (routed to the
// app), and src/api/server.py maps _api onto the same handlers as /cairo/api.
export const API = `${BASE}/_api`;

export type BuildState = 'fresh' | 'stale' | 'unbuilt';
export type Contract = { name: string; built: boolean; state?: BuildState; template?: string | null; description?: string | null };
export type Template = { id: string; title: string; blurb: string };
export type Job = {
  id: string;
  fn: string;
  name: string | null;
  state: 'queued' | 'running' | 'done' | 'error';
  created: number;
  started: number | null;
  finished: number | null;
  result: any;
  error: string | null;
  steps?: AgentStep[];
};

// One agent step as the job table keeps it (vibe runs via orbit/claudecode).
export type AgentStep = { tool: string; what: string; error: boolean };

// Writes are owner-only through the gateway (src/api/server.py). One
// personal_sign makes a mod-protocol token; starknet's studio token is the
// same scheme on the same origin, so it works here too.
const TOKEN_KEY = 'cairo.token';
const token = () => {
  try {
    return localStorage.getItem(TOKEN_KEY) || localStorage.getItem('starknet.studio.token');
  } catch {
    return null;
  }
};

export const authHeaders = (): Record<string, string> => {
  const t = token();
  return t ? { token: t } : {};
};

function b64url(s: string): string {
  let bin = '';
  new TextEncoder().encode(s).forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export async function signIn(): Promise<void> {
  const eth = (window as any).ethereum;
  if (!eth) throw new Error('No browser wallet found. Install MetaMask or Rabby to sign in.');
  const [address] = await eth.request({ method: 'eth_requestAccounts' });
  const data = 'cairo'; // a string: verifies in Python and Rust alike
  const time = Math.floor(Date.now() / 1000).toString();
  const signature = await eth.request({ method: 'personal_sign', params: [JSON.stringify({ data, time }), address] });
  const t = b64url(JSON.stringify({ data, time, key: String(address).toLowerCase(), signature }));
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    // shared-origin quota full: the token just won't survive a reload
  }
}

export async function call(fn: string, body?: Record<string, unknown>, query?: Record<string, string>): Promise<any> {
  const qs = query ? `?${new URLSearchParams(query)}` : '';
  const t = token();
  const res = await fetch(`${API}/${fn}${qs}`, {
    method: body ? 'POST' : 'GET',
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(t ? { token: t } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

export const live = (j: Job) => j.state === 'queued' || j.state === 'running';

export function jobOk(j: Job): boolean | null {
  if (j.state !== 'done') return j.state === 'error' ? false : null;
  const r = j.result || {};
  if (r.error) return false;
  if (j.fn === 'vibe') return !!r.ok && (r.build?.ok ?? true) && (r.test?.ok ?? true);
  return r.ok ?? true;
}

export function secs(j: Job, now: number): string {
  if (!j.started) return '';
  return `${Math.round((j.finished ?? now) - j.started)}s`;
}

export const STATE_TIP: Record<BuildState, string> = {
  fresh: 'built, up to date',
  stale: 'sources changed since the last build',
  unbuilt: 'not built yet',
};
