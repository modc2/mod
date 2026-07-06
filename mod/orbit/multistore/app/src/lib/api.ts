const BASE = "/api/multistore";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text.slice(0, 240)}`);
  }
  return res.json() as Promise<T>;
}

export interface BackendInfo {
  available: boolean;
  module: string;
}

export interface BackendsResp {
  [name: string]: BackendInfo;
}

export interface Status {
  name: string;
  total_objects: number;
  by_backend: Record<string, number>;
  alive_backends: string[];
  store: string;
}

export interface PutResp {
  ok: boolean;
  owner: string | null;
  targets: string[];
  cids: string[];
  results: Record<string, { ok: boolean; cid?: string; error?: string; size?: number }>;
}

export interface ObjectRow {
  cid: string;
  backend: string;
  owner: string | null;
  key: string | null;
  size: number | null;
  timestamp: number;
  meta: string | null;
}

export interface ListResp {
  total: number;
  offset: number;
  limit: number;
  count: number;
  query: string | null;
  objects: ObjectRow[];
}

export const api = {
  status: () => fetch(`${BASE}/status`).then((r) => json<Status>(r)),
  backends: () => fetch(`${BASE}/backends`).then((r) => json<BackendsResp>(r)),
  health: () => fetch(`${BASE}/health`).then((r) => json<Record<string, unknown>>(r)),
  list: (q?: { owner?: string; backend?: string; limit?: number; offset?: number; search?: string }) => {
    const p = new URLSearchParams();
    if (q?.owner) p.set("owner", q.owner);
    if (q?.backend) p.set("backend", q.backend);
    if (q?.limit) p.set("limit", String(q.limit));
    if (q?.offset) p.set("offset", String(q.offset));
    if (q?.search) p.set("search", q.search);
    const qs = p.toString();
    return fetch(`${BASE}/list${qs ? `?${qs}` : ""}`).then((r) => json<ListResp>(r));
  },
  // POST /put — the mod auto-server takes URL-encoded form params for non-file bodies.
  put: (path: string, backend: string, owner?: string) => {
    const body = new URLSearchParams();
    body.set("path", path);
    body.set("backend", backend);
    if (owner) body.set("owner", owner);
    return fetch(`${BASE}/put`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    }).then((r) => json<PutResp>(r));
  },
  pin: (cid: string, backend: string, owner?: string) => {
    const body = new URLSearchParams();
    body.set("cid", cid);
    body.set("backend", backend);
    if (owner) body.set("owner", owner);
    return fetch(`${BASE}/pin`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    }).then((r) => json<Record<string, unknown>>(r));
  },
  rm: (cid: string, backend = "all") =>
    fetch(`${BASE}/rm?cid=${encodeURIComponent(cid)}&backend=${backend}`, {
      method: "DELETE",
    }).then((r) => json<Record<string, unknown>>(r)),
  replicate: (cid: string, from: string, to: string) => {
    const body = new URLSearchParams();
    body.set("cid", cid);
    body.set("from_", from);
    body.set("to", to);
    return fetch(`${BASE}/replicate`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    }).then((r) => json<Record<string, unknown>>(r));
  },
};

export function shortCid(cid: string): string {
  if (!cid || cid.length <= 18) return cid;
  return `${cid.slice(0, 10)}…${cid.slice(-6)}`;
}

export function relTime(ts: number): string {
  const ms = Date.now() - ts * 1000;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)} KB`;
  return `${n} B`;
}

export const BACKEND_COLORS: Record<string, string> = {
  ipfs:     "cyan",
  filecoin: "violet",
  hippius:  "green",
  arweave:  "amber",
  localfs:  "dim",
};
