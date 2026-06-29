// Typed client for the Rust mod-api. All requests go through NEXT_PUBLIC_API_URL
// (`/api/web`), which the Caddy gateway (prod) or a Next rewrite (dev) proxies
// to the Rust service.

export const API = process.env.NEXT_PUBLIC_API_URL || "/api/web";

export type Module = {
  name: string;
  description: string;
  version: string;
  icon: string | null;
  color: string | null;
  port: number | null;
  app_port: number | null;
  fns: string[];
  fn_count: number;
  has_rust: boolean;
  has_app: boolean;
  mount: string;
  schema: string | null;
  config: Record<string, unknown>;
};

export type Stats = {
  modules: number;
  functions: number;
  rust_apis: number;
  apps: number;
};

export type Info = {
  name: string;
  protocol: string;
  version: string;
  tagline: string;
  description: string;
  stats: Stats;
};

export type TreeNode = {
  name: string;
  path: string;
  type: "file" | "dir";
  children?: TreeNode[];
};

export type FileContent = {
  path: string;
  content: string;
  lines: number;
  bytes: number;
  truncated: boolean;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  info: () => get<Info>("/info"),
  mods: () => get<Module[]>("/mods"),
  mod: (name: string) => get<Module>(`/mods/${encodeURIComponent(name)}`),
  stats: () => get<Stats>("/stats"),
  tree: (name: string) =>
    get<{ name: string; tree: TreeNode[] }>(
      `/mods/${encodeURIComponent(name)}/tree`,
    ),
  file: (name: string, path: string) =>
    get<FileContent>(
      `/mods/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`,
    ),
};

// Public gateway URL for a module's live app. Behind the modc2.com proxy the
// app sits at `<origin>/<name>`; running locally it's on the :3000 gateway.
export function gatewayUrl(name: string): string {
  if (typeof window === "undefined") return `/${name}`;
  const loc = window.location;
  const behindProxy =
    loc.protocol === "https:" ||
    loc.port === "" ||
    loc.port === "80" ||
    loc.port === "443";
  return behindProxy
    ? `${loc.origin}/${name}`
    : `http://${loc.hostname}:3000/${name}`;
}
