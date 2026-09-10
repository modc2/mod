import type {
  CallPage,
  CallStats,
  Catalog,
  ChatMessage,
  Embedding,
  Game,
  KeyStatus,
  Leaderboard,
  LocalModel,
  MatchResult,
  McpDescriptor,
  Model,
  OwnerState,
  ProvidersTable,
  Pull,
  Runtimes,
  Session,
  Transcript,
} from "./types";

// Everything goes through the Next rewrite at /api/liquidai → backend, so the
// basePath ("/liquidai") never gets prepended to an API call.
const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/liquidai";

// The session token, held here rather than passed down through every caller:
// AuthProvider owns it and pushes it in, and each fetch picks it up. One place
// to look when a call comes back 403.
let TOKEN: string | null = null;
export const setAuthToken = (token: string | null) => { TOKEN = token; };

function headers(extra?: HeadersInit): HeadersInit {
  return {
    "Content-Type": "application/json",
    // The ledger groups calls by the door they came through, and a request
    // proxied by the Next server has lost every header that would have said
    // "a person pressed a button". So the console says so itself.
    "X-Liquidai-Via": "console",
    ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
    ...(extra || {}),
  };
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: headers(init?.headers),
    cache: "no-store",
  });
  if (!r.ok) {
    // FastAPI puts the useful sentence in `detail`; the raw JSON body is
    // noise on screen.
    const body = await r.text().catch(() => "");
    let detail = body;
    try { detail = JSON.parse(body).detail ?? body; } catch {}
    throw new Error(detail || `${path} ${r.status}`);
  }
  return r.json() as Promise<T>;
}

// ── catalog ─────────────────────────────────────────────────────────

export const fetchCatalog = (params: Record<string, string> = {}) => {
  const q = new URLSearchParams(params).toString();
  return j<Catalog>(`/models${q ? `?${q}` : ""}`);
};

export const fetchModel = (id: string) => j<Model>(`/models/${id}`);

export const fetchRuntimes = () => j<Runtimes>("/runtimes");

// ── accounts ────────────────────────────────────────────────────────

export const requestNonce = (address: string, kind: string) =>
  j<{ nonce: string; message: string; expires_in: number }>("/auth/nonce", {
    method: "POST",
    body: JSON.stringify({ address, kind }),
  });

export const verifySignature = (nonce: string, signature: string, pubkey?: string) =>
  j<{ token: string; account: { address: string; kind: string }; owner: boolean;
      expires_in: number }>("/auth/verify", {
    method: "POST",
    body: JSON.stringify({ nonce, signature, pubkey }),
  });

// Takes the token explicitly: this runs while restoring a stored session,
// before the module-level one has been set.
export const fetchMe = (token: string) =>
  fetch(`${BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
  }).then((r) => r.json() as Promise<Session>);

export const fetchOwner = () => j<OwnerState>("/auth/owner");

// ── local weights ───────────────────────────────────────────────────

export const fetchLocal = () => j<{ models: LocalModel[]; cache: string }>("/local/models");

export const pullRepo = (repo: string) =>
  j<Pull>("/local/pull", { method: "POST", body: JSON.stringify({ repo }) });

export const fetchPulls = () => j<{ pulls: Pull[] }>("/local/pulls");

export const loadRepo = (repo: string) =>
  j<{ repo: string; load_sec: number }>("/local/load", {
    method: "POST",
    body: JSON.stringify({ repo }),
  });

export const unloadRepo = () => j<{ unloaded: string | null }>("/local/unload", { method: "POST" });

export const fetchKeys = () => j<Record<string, KeyStatus> & { path: string }>("/keys");

export const setKey = (key: string, provider = "cloud") =>
  j<Record<string, KeyStatus>>("/keys", {
    method: "POST",
    body: JSON.stringify({ provider, key }),
  });

// ── the other modalities ────────────────────────────────────────────

export const embedTexts = (model: string, texts: string[]) =>
  j<Embedding>("/embed", { method: "POST", body: JSON.stringify({ model, texts }) });

// Multipart, so it can't go through `j` — the browser has to set its own
// boundary and a Content-Type header of ours would break the parse.
export async function transcribe(model: string, file: File, language?: string) {
  const form = new FormData();
  form.append("model", model);
  form.append("file", file);
  if (language) form.append("language", language);
  const r = await fetch(`${BASE}/transcribe`, {
    method: "POST",
    headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {},
    body: form,
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    let detail = body;
    try { detail = JSON.parse(body).detail ?? body; } catch {}
    throw new Error(detail || `transcribe ${r.status}`);
  }
  return r.json() as Promise<Transcript>;
}

// ── arena ───────────────────────────────────────────────────────────

export const fetchGames = () => j<{ games: Game[] }>("/arena/games");

export const saveGame = (game: Partial<Game>) =>
  j<Game>("/arena/games", { method: "POST", body: JSON.stringify(game) });

export const forkGame = (id: string) =>
  j<Game>(`/arena/games/${id}/fork`, { method: "POST" });

export const deleteGame = (id: string) =>
  j<{ deleted: string }>(`/arena/games/${id}`, { method: "DELETE" });

export const runMatch = (game: string, models: string[], runtime: string) =>
  j<{ game: string; runtime: string; results: MatchResult[] }>("/arena/match", {
    method: "POST",
    body: JSON.stringify({ game, models, runtime }),
  });

export const postBrowserResult = (body: {
  game: string; model: string; label?: string; answers: string[]; elapsed_sec: number;
}) => j<MatchResult>("/arena/result", { method: "POST", body: JSON.stringify(body) });

export const fetchLeaderboard = (game?: string) =>
  j<Leaderboard>(`/arena/leaderboard${game ? `?game=${encodeURIComponent(game)}` : ""}`);

// ── chat ────────────────────────────────────────────────────────────

// Server/cloud completions arrive as SSE. Yields each parsed event so the
// caller decides what a token, a done and an error look like on screen.
export async function* streamChat(body: {
  messages: ChatMessage[];
  model: string;
  runtime: "server" | "cloud";
  max_tokens?: number;
  temperature?: number;
}, signal?: AbortSignal): AsyncGenerator<any> {
  const r = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) {
    const detail = await r.text().catch(() => "");
    let why = detail;
    try { why = JSON.parse(detail).detail ?? detail; } catch {}
    throw new Error(why || `chat ${r.status}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE frames are blank-line separated; a chunk can split one in half.
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        yield JSON.parse(line.slice(5).trim());
      } catch {}
    }
  }
}

// ── the backend board ───────────────────────────────────────────────

export const fetchProviders = (windowHours = 24) =>
  j<ProvidersTable>(`/providers?window_hours=${windowHours}`);

export const fetchCalls = (params: Record<string, string> = {}) =>
  j<CallPage>(`/calls?${new URLSearchParams(params).toString()}`);

export const fetchCallStats = (windowHours = 24) =>
  j<CallStats>(`/calls/stats?window_hours=${windowHours}`);

export const fetchMcp = () => j<McpDescriptor>("/mcp");

export const clearCalls = () =>
  j<{ ok: boolean; dropped: number }>("/calls", { method: "DELETE" });

// A browser run happens where this server can't see it, so the tab is the only
// witness. Reporting is best-effort on purpose: a ledger line is never worth
// failing someone's chat over.
export function reportBrowserRun(body: {
  model: string;
  task?: string;
  ok?: boolean;
  error?: string;
  elapsed_sec?: number;
  ttft_sec?: number;
  tokens?: number;
  turns?: number;
  engine?: string;
}) {
  return j<{ ok: boolean; recorded: string }>("/calls/report", {
    method: "POST",
    body: JSON.stringify(body),
  }).catch(() => null);
}
