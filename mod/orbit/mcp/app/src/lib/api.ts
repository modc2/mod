const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/mcp";

function authHeaders(token: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Errors carry the status so callers can react (drop a dead session on 401,
// open the terms gate on 451) without string-matching the message.
export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function detailOf(text: string): string {
  try {
    const body = JSON.parse(text);
    const d = body?.detail ?? body?.error ?? body?.message;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => e?.msg ?? JSON.stringify(e)).join("; ");
    if (d) return JSON.stringify(d);
  } catch {
    /* not JSON — fall through to the raw text */
  }
  return text.trim();
}

function messageFor(status: number, detail: string): string {
  switch (status) {
    case 401:
      return "session expired — sign in again";
    case 403:
      return detail || "not authorized";
    case 404:
      return detail || "not found";
    case 429:
      return detail || "rate limited — try again shortly";
    case 502:
      return detail || "an upstream registry or the store mod is unreachable";
    default:
      if (status >= 500) return detail || "hub API error — the service may be restarting";
      return detail || `request failed (${status})`;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = detailOf(await res.text().catch(() => ""));
    throw new ApiError(res.status, detail, messageFor(res.status, detail));
  }
  return res.json() as Promise<T>;
}

export interface Source {
  id: string;
  label: string;
  about: string;
  ttl: number;
  weight: number;
  url: string;
  auth: string;
}

export interface Remote {
  type: string;
  url: string;
}

export interface Pkg {
  registry: string;
  identifier: string;
  version?: string | null;
}

export interface Server {
  id: string;
  ids?: string[];
  source: string;
  sources?: string[];
  name: string;
  title: string;
  description: string;
  repo: string;
  homepage: string;
  author: string;
  license: string | null;
  stars: number | null;
  downloads: number | null;
  tags: string[];
  transports: string[];
  remotes: Remote[];
  packages: Pkg[];
  install: Record<string, unknown>;
  updated: string | null;
  tools: number | null;
  cid: string | null;
  version: string | null;
  open_source: boolean;
  osi: boolean;
  categories: string[];
  score?: number;
  probe?: ProbeResult;
}

export interface SearchResult {
  q: string;
  count: number;
  sort: string;
  sources: string[];
  per_source: Record<string, number>;
  errors: Record<string, string>;
  servers: Server[];
}

export interface Tool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface ProbeResult {
  url: string;
  ok: boolean;
  tools: Tool[];
  tool_count?: number;
  protocol_version?: string;
  server_info?: { name?: string; version?: string };
  instructions?: string | null;
  latency_ms?: number;
  error?: string;
  tools_error?: string;
  needs_auth?: boolean;
  stdio_only?: boolean;
  cached?: boolean;
  probed_at?: number;
}

export interface ClientConfig {
  id: string;
  client: string;
  config: Record<string, unknown> | null;
  command: string | null;
  file?: string;
  note?: string;
  repo?: string;
}

export interface Submission {
  id: string;
  slug: string;
  name: string;
  title: string;
  description: string;
  repo: string;
  homepage: string;
  license: string | null;
  author: string;
  tags: string[];
  transports: string[];
  remotes: Remote[];
  packages: Pkg[];
  install: Record<string, unknown>;
  version: string | null;
  cid: string | null;
  pinned: boolean;
  pin_error?: string | null;
  created: number;
  updated: number;
}

export interface Terms {
  version: string;
  text: string;
  required: boolean;
  accepted?: boolean;
}

export interface Stats {
  providers: number;
  fleet_servers: number;
  fleet: string[];
  probes: number;
  submissions: number;
  publishers: number;
  pinned: number;
  unpinned: number;
  cache: { entries: number; bytes: number; dir: string };
  store: { url: string; up: boolean; via?: string; error?: string };
}

export interface SearchParams {
  q?: string;
  sources?: string;
  oss?: boolean;
  transport?: string;
  license?: string;
  tag?: string;
  category?: string;
  sort?: string;
  limit?: number;
}

export interface SubmitBody {
  name: string;
  description: string;
  repo?: string;
  homepage?: string;
  license?: string;
  version?: string;
  remote_url?: string;
  npm?: string;
  pypi?: string;
  tags?: string[];
  transports?: string[];
  slug?: string;
}

export const api = {
  async health() {
    return json<{ ok: boolean; service: string; version: string }>(await fetch(`${BASE}/health`));
  },
  async sources() {
    return json<{ sources: Source[] }>(await fetch(`${BASE}/sources`));
  },
  async stats() {
    return json<Stats>(await fetch(`${BASE}/stats`));
  },
  async search(params: SearchParams = {}) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v === undefined || v === "" || v === null) return;
      qs.append(k, typeof v === "boolean" ? String(v) : String(v));
    });
    return json<SearchResult>(await fetch(`${BASE}/search?${qs.toString()}`));
  },
  async server(id: string) {
    return json<Server>(await fetch(`${BASE}/server?id=${encodeURIComponent(id)}`));
  },
  async probe(body: { id?: string; url?: string; refresh?: boolean; token?: string }) {
    return json<ProbeResult>(
      await fetch(`${BASE}/probe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    );
  },
  async clientConfig(id: string, client = "claude") {
    return json<ClientConfig>(
      await fetch(`${BASE}/client_config?id=${encodeURIComponent(id)}&client=${client}`)
    );
  },
  async terms(token?: string | null) {
    return json<Terms>(await fetch(`${BASE}/store/terms`, { headers: authHeaders(token ?? null) }));
  },
  async acceptTerms(token: string) {
    return json<{ address: string; accepted: boolean; version: string }>(
      await fetch(`${BASE}/store/terms/accept`, { method: "POST", headers: authHeaders(token) })
    );
  },
  async submit(token: string, body: SubmitBody) {
    return json<Submission>(
      await fetch(`${BASE}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify(body),
      })
    );
  },
  async submissions(token?: string | null, mine = false) {
    return json<{ count: number; address: string | null; servers: Submission[] }>(
      await fetch(`${BASE}/submissions${mine ? "?mine=true" : ""}`, {
        headers: authHeaders(token ?? null),
      })
    );
  },
  async repin(token: string, id: string) {
    return json<Submission>(
      await fetch(`${BASE}/submissions/repin`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ id }),
      })
    );
  },
  async delist(token: string, id: string) {
    return json<{ removed: string; cid: string | null; note: string }>(
      await fetch(`${BASE}/submissions?id=${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: authHeaders(token),
      })
    );
  },
  // Manifests are store objects — link straight at the object page so a CID is
  // verifiable without leaving the fleet.
  storeObjectUrl(cid: string) {
    return `/store/o/${cid}`;
  },
};
