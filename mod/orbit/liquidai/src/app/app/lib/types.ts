export type Runtime = "browser" | "server" | "cloud";
export type Kind = "text" | "vision" | "audio" | "embed";

// ── accounts ────────────────────────────────────────────────────────

export interface Session {
  signed_in: boolean;
  address: string;
  kind: string;                  // browser | evm | bittensor | cli
  owner: boolean;
  expires: number;
  label?: string;                // "MetaMask", "talisman", "this device"
  logins?: number;
}

export interface OwnerState {
  claimed: boolean;
  address: string | null;
  kind: string | null;
  pinned: boolean;
  open: boolean;                 // LIQUIDAI_OPEN — the gate is off
}

// ── arena ───────────────────────────────────────────────────────────

export type Check = "contains" | "equals" | "number" | "regex" | "lines" | "absent";

export interface GameRound {
  prompt: string;
  check: Check;
  expect: string;
}

export interface Game {
  id: string;
  name: string;
  blurb: string;
  system: string;
  max_tokens: number;
  rounds: GameRound[];
  builtin: boolean;
  author?: string;
}

export interface PlayedRound extends GameRound {
  answer: string;
  ok: boolean;
  error: string | null;
  elapsed_sec?: number;
}

export interface MatchResult {
  id: string;
  game: string;
  game_name: string;
  model: string;
  label: string;
  runtime?: string;
  passed: number;
  total: number;
  score: number;
  elapsed_sec: number;
  sec_per_round: number;
  at: number;
  rounds?: PlayedRound[];
}

export interface Leaderboard {
  count: number;
  runs: number;
  rows: MatchResult[];
  games: { id: string; name: string }[];
}

// ── embeddings ──────────────────────────────────────────────────────

export interface Embedding {
  runtime: string;
  repo: string;
  dim: number;
  count: number;
  vectors: number[][];
  similarity: number[][];
  elapsed_sec: number;
}

export interface Transcript {
  runtime: string;
  repo: string;
  text: string;
  seconds: number;
  elapsed_sec: number;
}

export interface VariantRepo {
  repo: string;
  quant: string | null;
  downloads: number;
  transformers_js: boolean;
  local?: boolean;
}

export interface Model {
  id: string;
  family: string;
  kind: Kind;
  role: string | null;
  params_b: number | null;
  active_b: number | null;
  downloads: number;
  likes: number;
  updated: string | null;
  languages: string[];
  license: string | null;
  runtimes: string[];          // browser | server | edge
  formats: string[];           // torch | gguf | onnx | mlx
  repo: string;
  onnx_repo: string | null;
  gguf_repo: string | null;
  torch_repo: string | null;
  variants: Record<string, { repos: VariantRepo[] }>;
}

export interface Catalog {
  count: number;
  total: number;
  source: string;
  fetched_at: number;
  families: string[];
  kinds: string[];
  roles: string[];
  models: Model[];
}

export interface Runtimes {
  browser: { ok: boolean; engine: string; note: string };
  server: {
    ok: boolean;
    torch?: string;
    transformers?: string;
    device?: string;
    gpu?: string;
    threads?: number;
    note?: string;
    error?: string;
    hint?: string;
    loaded?: { repo: string; load_sec: number } | null;
  };
  cloud: { ok: boolean; base: string; models?: string[]; count?: number; error?: string; hint?: string };
}

export interface LocalModel {
  repo: string;
  bytes: number;
  path: string;
  state: "ready" | "pulling";
  resident: boolean;
}

export interface Pull {
  repo: string;
  state: "running" | "done" | "error" | "idle";
  bytes?: number;
  total?: number | null;
  pct?: number | null;
  elapsed?: number;
  error?: string | null;
}

export interface KeyStatus {
  set: boolean;
  masked: string | null;
  source: string | null;
}

// A turn is text, or a list of parts when it carries an image. Both shapes go
// over the same /chat endpoint; the server flattens for text-only models.
export type ContentPart =
  | { type: "text"; text: string }
  | { type: "image"; image: string };        // data: URL

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string | ContentPart[];
}

export const messageText = (m: ChatMessage): string =>
  typeof m.content === "string"
    ? m.content
    : m.content.filter((p): p is { type: "text"; text: string } => p.type === "text")
        .map((p) => p.text).join("\n");

export const messageImages = (m: ChatMessage): string[] =>
  typeof m.content === "string"
    ? []
    : m.content.filter((p): p is { type: "image"; image: string } => p.type === "image")
        .map((p) => p.image);

// What a finished run cost, whichever runtime served it. `chunks_per_sec` is
// streamer chunks rather than tokens — the browser and the server chunk
// differently, so it's labelled as chunks everywhere it's shown.
export interface RunStats {
  runtime: Runtime;
  repo?: string;
  prompt_tokens?: number;
  chunks?: number;
  elapsed_sec?: number;
  ttft_sec?: number;
  chunks_per_sec?: number;
  usage?: Record<string, number>;
  device?: string;
}
