// Known contracts get names; everything else is a short hash.
export const TOKENS: Record<string, { symbol: string; decimals: number }> = {
  '0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7': { symbol: 'ETH', decimals: 18 },
  '0x4718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d': { symbol: 'STRK', decimals: 18 },
  '0x53c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8': { symbol: 'USDC', decimals: 6 },
  '0x33068f6539f8e6e6b131e6b2b814e6c34a5224bc66947c47dab9dfee93b35fb': { symbol: 'USDC', decimals: 6 },
};
export const POOL = '0x40337b1af3c663e86e333bab5a4b28da8d4652a15a69beee2b677776ffe812a';

export const ALIASES: Record<string, string> = {
  strk: '0x4718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d',
  eth: '0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7',
  usdc: '0x53c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8',
  strk20: POOL, pool: POOL,
};

/** Canonical lowercase 0x felt without leading zeros. */
export function norm(a: string | null | undefined): string {
  if (!a) return '';
  const s = String(a).trim().toLowerCase();
  if (ALIASES[s]) return ALIASES[s];
  if (!s.startsWith('0x')) return s;
  return '0x' + (s.slice(2).replace(/^0+/, '') || '0');
}

export function nameOf(a: string): string | null {
  const n = norm(a);
  if (n === POOL) return 'STRK20 privacy pool';
  return TOKENS[n] ? `${TOKENS[n].symbol} token` : null;
}

export function short(h: string | null | undefined, n = 6): string {
  if (!h) return '';
  const s = String(h);
  return s.length <= 2 * n + 4 ? s : `${s.slice(0, n + 2)}…${s.slice(-n)}`;
}

export function hexInt(h: string | number | null | undefined): number {
  if (h === null || h === undefined) return 0;
  if (typeof h === 'number') return h;
  return Number(BigInt(h));
}

export function num(v: number | null | undefined, max = 4): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { maximumFractionDigits: v >= 1000 ? 2 : max });
}

/** Raw integer (number, decimal or hex string, bigint) scaled by decimals.
 *  JSON numbers past 2^53 arrive as floats, so those are scaled as floats. */
export function units(raw: string | number | bigint, decimals: number): number {
  if (typeof raw === 'number') return raw / 10 ** decimals;
  let b: bigint;
  try { b = typeof raw === 'bigint' ? raw : BigInt(raw); }
  catch { const f = Number(raw); return Number.isNaN(f) ? NaN : f / 10 ** decimals; }
  const d = 10n ** BigInt(decimals);
  return Number(b / d) + Number(b % d) / Number(d);
}

export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return '—';
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function when(ts: number | null | undefined): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString();
}

export function fee(f: { amount: string; unit: string } | undefined): string {
  if (!f) return '—';
  const v = units(f.amount, 18);
  return `${num(v, 6)} ${f.unit === 'FRI' ? 'STRK' : 'ETH'}`;
}
