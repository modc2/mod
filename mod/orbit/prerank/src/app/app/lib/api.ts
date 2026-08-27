/**
 * The client half of the market.
 *
 * The one thing worth reading here is `commitmentOf`: the commitment is
 * computed in this tab, from a salt generated in this tab, and the model
 * never leaves it until the reveal window opens. There is deliberately no
 * server endpoint that would do this for you — a market that hides your bet
 * from other bettors but not from the server is not a sealed market.
 */

const API = process.env.NEXT_PUBLIC_API_URL || "/prerank/_api";

export const MICRO = 1_000_000;

export type Phase = "open" | "reveal" | "sealed" | "settled" | "voided";

export interface Book {
  model: string;
  units: number | null;
  money: number | null;
  edge_units: number | null;
  holders: number | null;
  implied_odds: number | null;
}

export interface Round {
  id: string;
  phase: Phase;
  entrants: string[];
  spec_hash: string;
  opens_at: number;
  reveal_at: number;
  seal_at: number;
  settle_at: number;
  params: {
    fee_bps: number;
    quorum: number;
    earliness_k: number;
    edge_cap: number;
    edge_ttl_rounds: number;
    min_bet: number;
  };
  commitments: number;
  revealed: number;
  forfeited: number;
  pool: number | null;
  staked_visible: number;
  edge_money: number | null;
  books: Book[];
  merkle_root: string | null;
  attestations: {
    attestor: string;
    rank_hash: string;
    counted: boolean;
    note: string | null;
    at: number;
    ranking: string[] | null;
  }[];
  result: any;
  quorum: number;
}

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  const text = await res.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { error: text };
  }
  if (!res.ok) throw new Error(body?.error || `${res.status} ${res.statusText}`);
  return body as T;
}

export const post = <T = any>(path: string, body: any) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

/** sha256 hex, in the browser. */
async function sha256(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** The same field layout `types.rs` hashes. Change one, change both. */
export function commitmentOf(
  round: string,
  owner: string,
  model: string,
  amount: number,
  salt: string,
): Promise<string> {
  return sha256(["prerank:bet", round, owner.toLowerCase(), model, String(amount), salt].join("|"));
}

export function freshSalt(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export const credits = (micro: number | null | undefined) =>
  micro === null || micro === undefined ? "—" : (micro / MICRO).toFixed(2);

export const short = (addr: string) =>
  addr && addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr || "—";

export function countdown(to: number, now: number): string {
  let s = Math.max(0, to - now);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

// ── the wallet ───────────────────────────────────────────────────────

export interface Wallet {
  address: string;
  kind: "injected" | "dev";
  devIndex?: number;
}

/**
 * Sign with the browser wallet if there is one, otherwise ask the server's
 * development wallet. The second path only answers when the API is running in
 * open mode, which the INFO tab reports — so a console pointed at a real
 * deployment cannot quietly fall back to a key everybody has.
 */
export async function signMessage(wallet: Wallet, message: string): Promise<string> {
  if (wallet.kind === "injected") {
    const eth = (globalThis as any).ethereum;
    if (!eth) throw new Error("no browser wallet");
    return await eth.request({ method: "personal_sign", params: [message, wallet.address] });
  }
  const out = await post<{ signature: string }>("/dev/sign", {
    wallet: wallet.devIndex ?? 0,
    message,
  });
  return out.signature;
}

export async function connectInjected(): Promise<Wallet> {
  const eth = (globalThis as any).ethereum;
  if (!eth) throw new Error("no browser wallet found");
  const accounts: string[] = await eth.request({ method: "eth_requestAccounts" });
  if (!accounts?.length) throw new Error("no account");
  return { address: accounts[0].toLowerCase(), kind: "injected" };
}

export async function connectDev(index: number): Promise<Wallet> {
  const out = await post<{ address: string }>("/dev/sign", { wallet: index, message: "hello" });
  return { address: out.address.toLowerCase(), kind: "dev", devIndex: index };
}

// ── the bets this browser is holding salts for ───────────────────────

export interface LocalBet {
  round: string;
  owner: string;
  model: string;
  amount: number;
  salt: string;
  commitment: string;
  placedAt: number;
  revealed?: boolean;
}

const KEY = "prerank_bets_v1";

/**
 * Salts live here and only here. The server cannot open a bet for you, which
 * is the point — and also means losing this list before the reveal window
 * forfeits the stake. The BET tab says so.
 */
export function loadBets(): LocalBet[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(window.localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export function saveBet(bet: LocalBet) {
  const all = loadBets().filter((b) => b.commitment !== bet.commitment);
  all.push(bet);
  window.localStorage.setItem(KEY, JSON.stringify(all));
}

export function markRevealed(commitment: string) {
  const all = loadBets().map((b) =>
    b.commitment === commitment ? { ...b, revealed: true } : b,
  );
  window.localStorage.setItem(KEY, JSON.stringify(all));
}
