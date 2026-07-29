import { ApiError } from "./api";

// Shared display helpers for the sub-pages (the main page keeps its own copies
// co-located with its components).

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "∞";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

export function fmtDate(secs: number | null | undefined): string {
  if (!secs) return "—";
  return new Date(secs * 1000).toLocaleString();
}

export function fmtDuration(secs: number | null): string {
  if (secs === null) return "no expiry";
  if (secs <= 0) return "expired";
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${secs}s`;
}

export function fmtAgo(secs: number): string {
  const d = Math.floor(Date.now() / 1000) - secs;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  if (d < 86400 * 30) return `${Math.floor(d / 86400)}d ago`;
  return `${Math.floor(d / (86400 * 30))}mo ago`;
}

export function shortCid(cid: string): string {
  return cid.length > 18 ? `${cid.slice(0, 8)}…${cid.slice(-6)}` : cid;
}

/* deterministic gradient avatar for a 0x address */
export function identiconStyle(addr: string): React.CSSProperties {
  let h = 0;
  for (let i = 2; i < addr.length; i++) h = (h * 31 + addr.charCodeAt(i)) >>> 0;
  const h1 = h % 360;
  const h2 = (h1 + 80 + ((h >> 8) % 140)) % 360;
  const ang = (h >> 16) % 360;
  return {
    background: `linear-gradient(${ang}deg, hsl(${h1} 72% 58%), hsl(${h2} 68% 42%))`,
  };
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  const msg = ((e as { message?: string })?.message || String(e)).trim();
  if (/failed to fetch|networkerror|load failed/i.test(msg)) return "can't reach the store API — check your connection";
  return msg || "something went wrong";
}
