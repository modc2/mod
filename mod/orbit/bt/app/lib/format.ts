/* Number and time formatting — one place, so every table reads the same. */

export const fmt = (x: unknown, d = 4): string =>
  typeof x === 'number' && isFinite(x) ? x.toLocaleString(undefined, { maximumFractionDigits: d }) : '—';

const COMPACT = typeof Intl !== 'undefined'
  ? new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 }) : null;

export const compact = (x: unknown): string =>
  typeof x === 'number' && isFinite(x) ? (COMPACT ? COMPACT.format(x) : String(x)) : '—';

export const fmtPrice = (p: unknown): string => {
  if (typeof p !== 'number' || !isFinite(p)) return '—';
  if (p >= 1) return p.toLocaleString(undefined, { maximumFractionDigits: 3 });
  if (p >= 0.0001) return p.toFixed(5);
  return p.toPrecision(3);
};

export const short = (a?: string | null): string => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—');

/* ---- τ / $ display. Every TAO amount goes through money(): when the person
 * flips the console to USD and the free-ticker rate is known, it converts;
 * otherwise it stays honest in τ. */
export type Ccy = 'tao' | 'usd';

export const ccySign = (ccy: Ccy, rate?: number | null) =>
  ccy === 'usd' && rate != null ? '$' : 'τ';

export function money(tao: unknown, ccy: Ccy, rate?: number | null,
                      style: 'compact' | 'price' | 'fixed' = 'compact', d = 3): string {
  if (typeof tao !== 'number' || !isFinite(tao)) return '—';
  const usd = ccy === 'usd' && rate != null;
  const v = usd ? tao * rate : tao;
  const s = usd ? '$' : 'τ ';
  if (style === 'price') return s + fmtPrice(v);
  if (style === 'fixed') return s + fmt(v, d);
  return s + compact(v);
}

export const now = () => Math.floor(Date.now() / 1000);

export function agoText(sec: number): string {
  if (sec < 5) return 'just now';
  if (sec < 90) return `${Math.round(sec)}s ago`;
  if (sec < 5400) return `${Math.round(sec / 60)}m ago`;
  if (sec < 172800) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

export const ago = (ts?: number | null) => (ts ? agoText(Math.max(0, now() - ts)) : '—');

export const when = (ts: number) => new Date(ts * 1000).toLocaleString(undefined,
  { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

/* a plausible ss58: base58, 46-48 chars, generic-substrate prefix "5" */
export const looksSS58 = (s: string) => /^5[1-9A-HJ-NP-Za-km-z]{45,47}$/.test(s.trim());

export const RANGES = [
  { label: '1D', hours: 24 }, { label: '7D', hours: 168 },
  { label: '30D', hours: 720 }, { label: 'ALL', hours: 24 * 365 },
] as const;
