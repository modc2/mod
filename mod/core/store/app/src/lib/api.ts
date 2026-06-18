const BASE = "/api/store";

function authHeaders(token: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface Quota {
  address: string;
  admin: boolean;
  used_bytes: number;
  limit_bytes: number | null;
  unlimited: boolean;
  remaining_bytes: number | null;
}

export interface MeResponse {
  address: string;
  authorized: boolean;
  admin: boolean;
  quota: Quota;
}

export interface PutResponse {
  owner: string;
  backend: string;
  results: Record<string, { cid?: string; error?: string; size?: number; backend?: string }>;
}

export interface StoredObject {
  cid: string;
  backend: string;
  owner: string | null;
  key: string | null;
  size: number | null;
  timestamp: number;
  meta: string | null;
}

export const api = {
  async health() {
    return json<{ ok: boolean; service: string }>(await fetch(`${BASE}/health`));
  },
  async status() {
    return json<Record<string, unknown>>(await fetch(`${BASE}/status`));
  },
  async backends() {
    return json<{ backends: string[] }>(await fetch(`${BASE}/backends`));
  },
  async me(token: string) {
    return json<MeResponse>(
      await fetch(`${BASE}/me`, { headers: authHeaders(token) })
    );
  },
  async quota(token: string) {
    return json<Quota>(await fetch(`${BASE}/quota`, { headers: authHeaders(token) }));
  },
  async put(token: string, file: File, backend: string, key?: string) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("backend", backend);
    if (key) fd.append("key", key);
    return json<PutResponse>(
      await fetch(`${BASE}/put`, {
        method: "POST",
        headers: authHeaders(token),
        body: fd,
      })
    );
  },
  async list(token: string, backend?: string) {
    const q = backend ? `?backend=${backend}` : "";
    return json<{ owner: string; objects: StoredObject[] }>(
      await fetch(`${BASE}/list${q}`, { headers: authHeaders(token) })
    );
  },
  async pin(token: string, cid: string, backend: string) {
    return json<Record<string, unknown>>(
      await fetch(`${BASE}/pin`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ cid, backend }),
      })
    );
  },
  getUrl(cid: string, backend?: string) {
    const q = backend ? `&backend=${backend}` : "";
    return `${BASE}/get?cid=${cid}${q}`;
  },
  // Absolute URL (origin-qualified) so a scanned QR resolves off-device.
  absoluteUrl(cid: string, backend?: string) {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return `${origin}${api.getUrl(cid, backend)}`;
  },
};
