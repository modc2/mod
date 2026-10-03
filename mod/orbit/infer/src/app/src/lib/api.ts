// The python server owns the REST API; next.config.mjs proxies /infer/_api/*
// to it, so the same path works through the gateway and bare on the app port.
export const API = '/infer/_api';

export async function api(path: string, opts?: RequestInit): Promise<any> {
  const r = await fetch(API + path, opts);
  const t = await r.text();
  let j: any;
  try { j = JSON.parse(t); } catch { throw new Error(t.slice(0, 300)); }
  if (!r.ok || j.error) throw new Error(j.error || String(r.status));
  return j;
}

export const fmtB = (b: number) =>
  b >= 1e6 ? (b / 1e6).toFixed(2) + ' MB' : (b / 1e3).toFixed(1) + ' kB';

export const num = (n: any) =>
  n === null || n === undefined ? '—' : (+n).toLocaleString();
