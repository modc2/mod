// Thin client over the freetune Rust API. All calls go through /api/freetune,
// which Caddy (prod) or next rewrites (dev) proxy to the backend.
const API = process.env.NEXT_PUBLIC_API_URL || "/api/freetune";

// Set by AuthContext once the owner wallet has signed in. Gated endpoints
// (train, infer, bench, stop, delete, worker evict) attach it as the `token`
// header; the Rust API ignores it when no owner is configured.
let ownerToken: string | null = null;
export function setOwnerToken(token: string | null) {
  ownerToken = token;
}

async function j<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(ownerToken ? { token: ownerToken } : {}),
      ...(init?.headers || {}),
    },
  });
  const text = await res.text();
  let body: any = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { error: text };
  }
  if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
  return body as T;
}

export type Model = {
  id: string;
  label: string;
  params: string;
  cpu: string;
  note: string;
};

export const api = {
  info: () => j<{ auth: "open" | "owner-gated"; owner: string | null }>("/info"),
  models: () => j<{ models: Model[]; default: string }>("/models"),
  datasetPreview: (src: string) =>
    j("/dataset/preview", { method: "POST", body: JSON.stringify({ src }) }),
  train: (cfg: any) =>
    j<{ id: string }>("/train", { method: "POST", body: JSON.stringify(cfg) }),
  runs: () => j<{ runs: any[] }>("/runs"),
  run: (id: string) => j<any>(`/runs/${id}`),
  runLogs: (id: string, tail = 200) =>
    j<{ logs: string }>(`/runs/${id}/logs?tail=${tail}`),
  stop: (id: string) => j(`/runs/${id}/stop`, { method: "POST" }),
  del: (id: string) => j(`/runs/${id}`, { method: "DELETE" }),
  infer: (req: any) =>
    j<any>("/infer", { method: "POST", body: JSON.stringify(req) }),
  metrics: () => j<any>("/metrics"),
  workers: () => j<any>("/workers"),
  bench: (req: any) =>
    j<any>("/bench", { method: "POST", body: JSON.stringify(req) }),
};
