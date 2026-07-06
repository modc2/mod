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
  bloctime: boolean;
  via: "config" | "bloctime" | "open" | null;
  quota: Quota;
}

export interface PutResponse {
  owner: string;
  backend: string;
  results: Record<string, { cid?: string; error?: string; size?: number; backend?: string }>;
  refs?: string[];
}

export interface StoredObject {
  cid: string;
  backend: string;
  owner: string | null;
  key: string | null;
  size: number | null;
  timestamp: number;
  meta: string | null;
  visibility?: "public" | "private";
  scheme?: string;
  url?: string | null;
  shared_via?: string;
  semhash?: string | null;
  distance?: number | null;
  similarity?: number | null;
}

export interface Ticket {
  code: string;
  cid: string;
  backend: string | null;
  expires: number;
  expires_in: number;
}

export interface PinInfo {
  cid: string;
  backend: string;
  owner: string;
  created: number;
  size?: number | null;
  key?: string | null;
  visibility?: string;
}

export interface Preview {
  cid: string;
  size?: number;
  truncated?: boolean;
  preview_bytes?: number;
  is_text?: boolean;
  text?: string | null;
  external?: boolean;
  url?: string | null;
}

export interface CidLink {
  cid: string;
  key: string | null;
}

export interface ObjectInfo {
  cid: string;
  owner: string | null;
  stored_at: number | null;
  key: string | null;
  size: number | null;
  backends: string[];
  scheme: string;
  visibility: string;
  semhash: string | null;
  external_url: string | null;
  pinned: boolean;
  is_owner: boolean;
  grants: Grant[];
  pools: { id: string; name: string; owner: string; members: { address: string; role: string; expires_in: number | null; expired: boolean }[] }[];
  you_can_read?: boolean;
  links: { out: CidLink[]; in: CidLink[] };
}

export interface Grant {
  id: string;
  grantor: string;
  grantee: string;
  cid: string;
  scope: "read" | "write";
  created: number;
  expires: number | null;
  expired: boolean;
  expires_in: number | null;
}

export interface PoolSummary {
  id: string;
  name: string;
  owner: string;
  description: string | null;
  created: number;
  member_count: number;
  object_count: number;
  role: string | null;
}

export interface PoolMember {
  pool_id: string;
  address: string;
  role: string;
  added: number;
  expires: number | null;
  expired: boolean;
  expires_in: number | null;
}

export interface PoolObject {
  pool_id: string;
  cid: string;
  backend: string | null;
  scheme: string | null;
  key: string | null;
  added_by: string;
  added: number;
  size?: number | null;
  owner?: string | null;
}

export interface PoolDetail extends PoolSummary {
  members: PoolMember[];
  objects: PoolObject[];
}

export interface Handoff {
  code: string;
  expires: number;
  expires_in: number;
  address: string;
}

export interface PutOpts {
  key?: string;
  public?: boolean;
  pool?: string;
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
  async put(token: string, file: File, backend: string, opts: PutOpts = {}) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("backend", backend);
    if (opts.key) fd.append("key", opts.key);
    if (opts.public) fd.append("public", "true");
    if (opts.pool) fd.append("pool", opts.pool);
    return json<PutResponse>(
      await fetch(`${BASE}/put`, {
        method: "POST",
        headers: authHeaders(token),
        body: fd,
      })
    );
  },
  async registerExternal(
    token: string,
    body: { cid: string; scheme?: string; backend?: string; url?: string; key?: string; public?: boolean; pool?: string }
  ) {
    return json<Record<string, unknown>>(
      await fetch(`${BASE}/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify(body),
      })
    );
  },
  async publish(token: string, cid: string, isPublic: boolean) {
    return json<{ cid: string; visibility: string }>(
      await fetch(`${BASE}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ cid, public: isPublic }),
      })
    );
  },
  async list(token: string, backend?: string) {
    const q = backend ? `?backend=${backend}` : "";
    return json<{ owner: string; objects: StoredObject[] }>(
      await fetch(`${BASE}/list${q}`, { headers: authHeaders(token) })
    );
  },
  async shared(token: string) {
    return json<{ address: string; objects: StoredObject[]; wildcard_grantors: string[] }>(
      await fetch(`${BASE}/shared`, { headers: authHeaders(token) })
    );
  },
  async search(
    token: string,
    params: { q?: string; semantic_q?: string; backend?: string; scheme?: string; visibility?: string; scope?: string } = {}
  ) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v && qs.append(k, String(v)));
    return json<{ count: number; objects: StoredObject[] }>(
      await fetch(`${BASE}/search?${qs.toString()}`, { headers: authHeaders(token) })
    );
  },
  async preview(token: string, cid: string, maxBytes = 65536) {
    return json<Preview>(
      await fetch(`${BASE}/preview?cid=${encodeURIComponent(cid)}&max_bytes=${maxBytes}`, {
        headers: authHeaders(token),
      })
    );
  },
  async objectInfo(token: string, cid: string) {
    return json<ObjectInfo>(
      await fetch(`${BASE}/object?cid=${encodeURIComponent(cid)}`, { headers: authHeaders(token) })
    );
  },

  // ── pins ──
  async pins(token: string) {
    return json<{ owner: string; pins: PinInfo[] }>(
      await fetch(`${BASE}/pins`, { headers: authHeaders(token) })
    );
  },
  async unpin(token: string, cid: string, backend?: string) {
    const q = backend ? `&backend=${backend}` : "";
    return json<Record<string, unknown>>(
      await fetch(`${BASE}/pin?cid=${encodeURIComponent(cid)}${q}`, {
        method: "DELETE",
        headers: authHeaders(token),
      })
    );
  },

  // ── one-time access tickets ──
  async createTicket(token: string, cid: string, ttl_seconds = 10, backend?: string) {
    return json<Ticket>(
      await fetch(`${BASE}/tickets`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ cid, ttl_seconds, backend }),
      })
    );
  },
  ticketUrl(code: string) {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return `${origin}${BASE}/ticket/${code}`;
  },

  // ── grants ──
  async grants(token: string) {
    return json<{ address: string; granted: Grant[]; received: Grant[] }>(
      await fetch(`${BASE}/grants`, { headers: authHeaders(token) })
    );
  },
  async createGrant(
    token: string,
    body: { grantee: string; cid?: string; scope?: string; ttl_seconds?: number }
  ) {
    return json<Grant>(
      await fetch(`${BASE}/grants`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify(body),
      })
    );
  },
  async revokeGrant(token: string, id: string) {
    return json<{ revoked: string }>(
      await fetch(`${BASE}/grants/${id}`, { method: "DELETE", headers: authHeaders(token) })
    );
  },

  // ── pools ──
  async pools(token: string) {
    return json<{ address: string; pools: PoolSummary[] }>(
      await fetch(`${BASE}/pools`, { headers: authHeaders(token) })
    );
  },
  async createPool(token: string, body: { name?: string; description?: string }) {
    return json<PoolDetail>(
      await fetch(`${BASE}/pools`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify(body),
      })
    );
  },
  async pool(token: string, id: string) {
    return json<PoolDetail>(await fetch(`${BASE}/pools/${id}`, { headers: authHeaders(token) }));
  },
  async deletePool(token: string, id: string) {
    return json<{ deleted: string }>(
      await fetch(`${BASE}/pools/${id}`, { method: "DELETE", headers: authHeaders(token) })
    );
  },
  async addMember(
    token: string,
    id: string,
    body: { address: string; role?: string; ttl_seconds?: number }
  ) {
    return json<PoolDetail>(
      await fetch(`${BASE}/pools/${id}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify(body),
      })
    );
  },
  async removeMember(token: string, id: string, address: string) {
    return json<Record<string, unknown>>(
      await fetch(`${BASE}/pools/${id}/members?address=${address}`, {
        method: "DELETE",
        headers: authHeaders(token),
      })
    );
  },
  async addPoolObject(token: string, id: string, body: { cid: string; backend?: string; key?: string }) {
    return json<PoolDetail>(
      await fetch(`${BASE}/pools/${id}/objects`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify(body),
      })
    );
  },
  async removePoolObject(token: string, id: string, cid: string) {
    return json<Record<string, unknown>>(
      await fetch(`${BASE}/pools/${id}/objects?cid=${cid}`, {
        method: "DELETE",
        headers: authHeaders(token),
      })
    );
  },

  // ── QR auth handoff ──
  async createHandoff(token: string, ttl_seconds = 120) {
    return json<Handoff>(
      await fetch(`${BASE}/handoff`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ ttl_seconds }),
      })
    );
  },
  async claimHandoff(code: string) {
    return json<{ token: string; address: string }>(await fetch(`${BASE}/handoff/${code}`));
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
  getUrl(cid: string, backend?: string, token?: string | null) {
    const q = backend ? `&backend=${backend}` : "";
    // Token rides in the query so a plain <a> link / scanned QR can authenticate
    // a *private* object off-device (the API also accepts a Bearer header).
    const t = token ? `&token=${encodeURIComponent(token)}` : "";
    return `${BASE}/get?cid=${encodeURIComponent(cid)}${q}${t}`;
  },
  // Absolute URL (origin-qualified) so a scanned QR resolves off-device.
  // Private objects get the token appended; public ones stay clean/shareable.
  absoluteUrl(cid: string, backend?: string, token?: string | null) {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return `${origin}${api.getUrl(cid, backend, token)}`;
  },
};
