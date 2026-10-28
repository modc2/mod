// Thin client for the starktime Rust API. The app is served under
// /starktime by the same binary, so the API is always same-origin.
const BASE = process.env.NEXT_PUBLIC_BASE ?? '/starktime';
export const API = `${BASE}/_api`;

export async function get<T = any>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: 'no-store' });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || j?.error) throw new Error(j?.error || `HTTP ${r.status}`);
  return j as T;
}

export async function post<T = any>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok || j?.error) throw new Error(j?.error || `HTTP ${r.status}`);
  return j as T;
}

export const DAY = 86400;

export function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (a >= 1e4) return (n / 1e3).toFixed(1) + 'k';
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function pct(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return (n * 100).toFixed(digits) + '%';
}

export function span(sec: number): string {
  if (sec <= 0) return 'unlocked';
  const d = Math.floor(sec / DAY);
  if (d >= 365) return `${(d / 365).toFixed(d % 365 ? 1 : 0)}y`;
  if (d >= 1) return `${d}d ${Math.floor((sec % DAY) / 3600)}h`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m ${sec % 60}s`;
}

export function lockLabel(sec: number): string {
  if (sec <= 0) return 'flexible';
  const d = Math.round(sec / DAY);
  if (d >= 365 && d % 365 === 0) return `${d / 365}y`;
  return `${d}d`;
}

export const short = (a?: string | null) => (a ? (a.length > 14 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a) : '—');

// ── Starknet wallets (Argent X / Braavos inject window.starknet_*) ──

type InjectedWallet = {
  id?: string;
  name?: string;
  enable?: (o?: unknown) => Promise<string[]>;
  request?: (o: { type: string; params?: unknown }) => Promise<any>;
  selectedAddress?: string;
  account?: { address?: string };
};

export function injectedWallets(): { key: string; name: string; w: InjectedWallet }[] {
  if (typeof window === 'undefined') return [];
  const out: { key: string; name: string; w: InjectedWallet }[] = [];
  const seen = new Set<InjectedWallet>();
  for (const key of Object.keys(window)) {
    if (!key.startsWith('starknet')) continue;
    const w = (window as any)[key] as InjectedWallet;
    if (!w || typeof w !== 'object' || seen.has(w)) continue;
    seen.add(w);
    out.push({ key, name: w.name || w.id || key.replace('starknet_', ''), w });
  }
  return out;
}

export async function connectWallet(w: InjectedWallet): Promise<string> {
  let addrs: string[] | undefined;
  if (w.request) {
    try {
      addrs = await w.request({ type: 'wallet_requestAccounts' });
    } catch {
      /* older wallets: fall through to enable() */
    }
  }
  if (!addrs?.length && w.enable) addrs = await w.enable({ starknetVersion: 'v5' });
  const a = addrs?.[0] || w.selectedAddress || w.account?.address;
  if (!a) throw new Error('wallet returned no address');
  return a.toLowerCase();
}

export function randomPaperAddress(): string {
  const b = new Uint8Array(31);
  crypto.getRandomValues(b);
  return '0x' + Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}
