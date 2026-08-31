// Hub API client. In prod Caddy routes /api/mcp/* → mcp-api; in dev the Next
// rewrite in next.config.mjs does the same, so one base works everywhere.
const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/mcp";

export type Probe = {
  ok: boolean;
  protocolVersion: string;
  serverInfo: { name?: string; version?: string } | null;
  toolCount: number;
  latency_ms: number;
  checked_at: number;
  error: string;
};

export type Server = {
  id: string;
  name: string;
  url: string;
  source: "fleet" | "user" | string;
  note: string;
  enabled: boolean;
  added_at: number;
  auth_headers: string[];
  probe: Probe;
};

export type Tool = {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
};

export type WebProvider = { name: string; ready: boolean; needs_key: boolean; note?: string };

export type AuthConfig = {
  issuer: string;
  issuer_api: string;
  token_key: string;
  available: boolean;
  owners: number;
  gates: { writes: boolean; calls: boolean; local_calls_open: boolean; hub_secret: boolean };
};

export type Stats = {
  servers: number;
  up: number;
  down: number;
  tools: number;
  by_source: Record<string, number>;
  write_gate: boolean;
  swept_at: number;
  hubs?: { count: number; ready: number | null; checked_at: number };
  web: { provider: string | null; providers: WebProvider[] };
  auth: AuthConfig;
};

export type Me = {
  authenticated: boolean;
  address: string | null;
  role: string;
  key: string | null;
  can_write: boolean;
  can_call: boolean;
  is_owner: boolean;
  local: boolean;
};

export type Listing = {
  id: string;
  name: string;
  description?: string;
  url: string;
  /// featured | official | smithery | glama | pulsemcp | an index id | a peer hub id
  registry: "featured" | "official" | "smithery" | string;
  homepage?: string;
  verified?: boolean;
  uses?: number;
  needs_key?: boolean;
  note?: string;
  /// Set when `url` is another hub's MCP endpoint rather than the server
  /// itself — connect that hub once and this row's tools arrive nested.
  via?: string;
  /// live | auth | down | error | unknown — what an index last saw.
  status?: string;
  tools?: number;
};

/// Anything that lists MCP servers. kind: mod (a hub like this one — this
/// deployment is the one with self=true), index (an internet-wide crawl),
/// directory (a public registry).
export type Hub = {
  id: string;
  kind: "mod" | "index" | "directory" | string;
  name: string;
  description: string;
  url: string;
  homepage?: string;
  mcp?: string;
  registry?: string;
  source: "builtin" | "fleet" | "user" | string;
  self: boolean;
  needs_key: boolean;
  key_name?: string;
  ready: boolean;
  servers?: number;
  live?: number;
  tools?: number;
  version?: string;
  latency_ms: number;
  checked_at: number;
  error?: string;
  note?: string;
};

export type HubsView = {
  self: string | null;
  count: number;
  ready: number;
  kinds: Record<string, number>;
  hubs: Hub[];
  checked_at: number;
  kind_docs: Record<string, string>;
};

export type Hit = { title: string; url: string; snippet?: string; published?: string };

export type SearchResult = {
  query: string;
  /// Which provider answered — empty when none did.
  provider: string;
  results: Hit[];
  /// Every provider that was tried, and why it didn't answer.
  tried?: string[];
  /// Set when nobody answered — the search ran, it just came up empty.
  error?: string;
};

export type PageText = {
  url: string;
  title: string;
  text: string;
  chars: number;
  via: string;
  truncated?: boolean;
};

export type Candidate = {
  id: string;
  name: string;
  url: string;
  headers?: Record<string, string>;
  note?: string;
  kind: string;
};

export type ApiKey = {
  id: string;
  name: string;
  hint: string;
  created: number;
  created_by: string;
  last_used: number;
  calls: number;
};

const TOKEN_KEY = "mcp:token";
/// The identity issuer's session key. Same origin, so signing in at /build
/// signs you in here — the hub validates that token without minting its own.
const ISSUER_TOKEN_KEY = "build_jobs_token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || localStorage.getItem(ISSUER_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(t: string) {
  try {
    t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY);
  } catch {}
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(`${BASE}${path}`, { ...init, headers });
  const raw = await r.text();
  let body: unknown = {};
  try {
    body = JSON.parse(raw);
  } catch {
    // Not JSON — something between us and the hub wrote this. Cloudflare
    // replaces the body of any 5xx with its own "error code: 502", so carry
    // whatever text did arrive rather than reporting a bare status.
    if (!r.ok) throw new Error(`HTTP ${r.status}${raw.trim() ? ` — ${raw.trim().slice(0, 200)}` : ""}`);
  }
  if (!r.ok) throw new Error((body as { error?: string }).error || `HTTP ${r.status}`);
  return body as T;
}

export const api = {
  stats: () => req<Stats>("/stats"),
  servers: () => req<{ servers: Server[] }>("/servers").then((r) => r.servers),
  tools: (server?: string) =>
    req<{ count: number; tools: Tool[] }>(`/tools${server ? `?server=${server}` : ""}`),
  addServer: (body: {
    url: string;
    id?: string;
    name?: string;
    note?: string;
    headers?: Record<string, string>;
    force?: boolean;
  }) => req<{ added: Server; probe: Probe }>("/servers", { method: "POST", body: JSON.stringify(body) }),
  removeServer: (id: string) => req<{ removed: string }>(`/servers/${id}`, { method: "DELETE" }),
  toggle: (id: string, enabled: boolean) =>
    req<{ id: string; enabled: boolean }>(`/servers/${id}/toggle`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  refresh: (id?: string) =>
    req<Record<string, unknown>>(id ? `/servers/${id}/refresh` : "/refresh", {
      method: "POST",
      body: "{}",
    }),
  probe: (url: string, headers?: Record<string, string>) =>
    req<{ probe: Probe; tools: Tool[] }>("/probe", {
      method: "POST",
      body: JSON.stringify({ url, headers: headers || {} }),
    }),
  call: (tool: string, args: unknown) =>
    req<{ tool: string; result: unknown }>("/call", {
      method: "POST",
      body: JSON.stringify({ tool, args }),
    }),
  clientConfig: (client: string) =>
    req<{ url: string; config: unknown }>(`/client_config?client=${client}`),

  /// Knock on every fleet port and adopt whatever speaks MCP.
  discover: () => req<{ swept: number; servers: string[] }>("/discover", { method: "POST", body: "{}" }),

  search: (q: string, count = 8, provider?: string) =>
    req<SearchResult>(
      `/search?q=${encodeURIComponent(q)}&count=${count}${provider ? `&provider=${provider}` : ""}`
    ),
  readPage: (url: string, maxChars = 6000) =>
    req<PageText>(`/fetch?url=${encodeURIComponent(url)}&max_chars=${maxChars}`),

  catalog: (q: string, registry = "all", limit = 20) =>
    req<{ count: number; listings: Listing[]; errors?: string[]; sources: string[] }>(
      `/catalog?q=${encodeURIComponent(q)}&registry=${registry}&limit=${limit}`
    ),

  /// Every hub type this one can see, with what each holds.
  hubs: (refresh = false) => req<HubsView>(`/hubs${refresh ? "?refresh=1" : ""}`),
  manifest: () => req<Record<string, unknown>>("/hub"),
  addHub: (body: { url: string; id?: string; name?: string; headers?: Record<string, string>; note?: string }) =>
    req<{ added: Hub; kind: string }>("/hubs", { method: "POST", body: JSON.stringify(body) }),
  removeHub: (id: string) => req<{ removed: string }>(`/hubs/${id}`, { method: "DELETE" }),
  /// Register a mod/index hub's own MCP endpoint as one upstream server.
  connectHub: (id: string) =>
    req<{ added: Server; probe: Probe }>(`/hubs/${id}/connect`, { method: "POST", body: "{}" }),

  /// Parse a URL, CID, client config, CLI line or QR payload into candidates.
  intake: (text: string) =>
    req<{ kind: string; candidates: Candidate[]; warnings: string[]; source?: string }>("/intake", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  me: () => req<{ me: Me; auth: AuthConfig }>("/auth/me"),
  keys: () => req<{ keys: ApiKey[] }>("/keys"),
  createKey: (name: string) =>
    req<{ key: ApiKey; secret: string }>("/keys", { method: "POST", body: JSON.stringify({ name }) }),
  revokeKey: (id: string) => req<{ revoked: string }>(`/keys/${id}`, { method: "DELETE" }),
};
