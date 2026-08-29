"use client";

import type { Catalog, Graph, Plan, Prompt, Protocol, Report } from "./types";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "/defi";
const CONFIGURED = process.env.NEXT_PUBLIC_API_URL || "";
const OVERRIDE_KEY = "defi_api_url";
const TOKEN_KEY = "defi_token";
const ADDRESS_KEY = "defi_address";

/// Where the API lives depends on how you got here: straight to :50501 in dev,
/// or through the fleet gateway in production. Rather than guess, probe the
/// candidates once and remember the one that answers.
let resolved: string | null = null;
let resolving: Promise<string> | null = null;

function candidates(): string[] {
  const list: string[] = [];
  const stored = typeof window !== "undefined" ? localStorage.getItem(OVERRIDE_KEY) : null;
  if (stored) list.push(stored);
  if (CONFIGURED) list.push(CONFIGURED);
  if (typeof window !== "undefined") {
    const { origin, hostname, protocol } = window.location;
    // The fleet gateway's rule is /{mod} → app and /api/{mod} → API, so that is
    // the first thing to try from anywhere the app is actually served.
    list.push(`${origin}/api${BASE_PATH}`);
    list.push(`${origin}${BASE_PATH}/api`);
    if (hostname === "localhost" || hostname === "127.0.0.1") {
      list.push("http://localhost:50500");
    } else if (protocol === "http:") {
      // Served straight off the app port with no gateway in front: the API is
      // the next port down on the same host.
      list.push(`${protocol}//${hostname}:50500`);
    }
  }
  return Array.from(new Set(list));
}

export async function apiBase(): Promise<string> {
  if (resolved) return resolved;
  if (resolving) return resolving;
  resolving = (async () => {
    for (const base of candidates()) {
      try {
        const r = await fetch(`${base}/health`, { cache: "no-store" });
        if (r.ok) {
          const body = await r.json();
          if (body?.module === "defi") {
            resolved = base;
            return base;
          }
        }
      } catch {
        /* try the next one */
      }
    }
    // Nothing answered. Fall back so the error surfaces at the call site with a
    // real URL in it rather than "undefined" — but remember nothing, so the next
    // attempt probes again instead of being stuck on a guess.
    return candidates()[0] ?? "http://localhost:50500";
  })();
  return resolving;
}

export function setApiOverride(url: string) {
  localStorage.setItem(OVERRIDE_KEY, url.replace(/\/$/, ""));
  resolved = null;
  resolving = null;
}

export function getToken(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
}

export function getAddress(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(ADDRESS_KEY);
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ADDRESS_KEY);
}

async function call<T>(
  route: string,
  init: RequestInit & { auth?: boolean } = {}
): Promise<T> {
  const base = await apiBase();
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  const token = getToken();
  if (token) headers.authorization = `Bearer ${token}`;

  const response = await fetch(`${base}${route}`, { ...init, headers, cache: "no-store" });
  const text = await response.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    // A page instead of JSON means this URL is not the API — usually the app
    // itself, answering its own 404. Pasting the document helps nobody.
    body = /^\s*<(!doctype|html)/i.test(text)
      ? { error: `${base}${route} answered with a web page, not the API — the API URL is wrong` }
      : { error: text.slice(0, 300) };
  }
  if (!response.ok) {
    if (response.status === 401) clearSession();
    throw new Error(body?.error || `${route} failed (${response.status})`);
  }
  return body as T;
}

// ── reads ──────────────────────────────────────────────────────────────────

export const getCatalog = () => call<Catalog>("/catalog");
export const getBlock = (id: string) => call<{ block: any; artifact: any }>(`/catalog/${id}`);
export const getCompileStatus = () => call<any>("/compile/status");
export const getProtocols = () => call<{ protocols: Protocol[] }>("/protocols");
export const getProtocol = (id: string) => call<{ protocol: Protocol }>(`/protocols/${id}`);
export const getAgentStatus = () => call<any>("/agent/status");
export const getPrompts = () => call<{ prompts: Prompt[]; source: string }>("/agent/prompts");
export const getPrompt = (id: string) => call<{ prompt: Prompt }>(`/agent/prompts/${id}`);

export const validateGraph = (graph: Graph) =>
  call<Report>("/validate", { method: "POST", body: JSON.stringify({ graph }) });

export const planGraph = (graph: Graph) =>
  call<{ ok: boolean; report: Report; plan?: Plan; error?: string }>("/plan", {
    method: "POST",
    body: JSON.stringify({ graph }),
  });

export const compose = (prompt: string, promptId?: string, graph?: Graph) =>
  call<{ graph: Graph; report: Report }>("/agent/compose", {
    method: "POST",
    body: JSON.stringify({ prompt, promptId, graph }),
  });

// ── the trading desk ───────────────────────────────────────────────────────
// Reads are open; a trade carries `auth`, the bearer of whichever chain module
// is going to sign, which this module forwards and never stores.

export const getVenues = () => call<any>("/dex/venues?check=1");

export const getDexTokens = (chain?: string) =>
  call<any>(`/dex/tokens${chain ? `?chain=${encodeURIComponent(chain)}` : ""}`);

export const dexQuote = (body: Record<string, any>) =>
  call<any>("/dex/quote", { method: "POST", body: JSON.stringify(body) });

export const dexSwap = (body: Record<string, any>) =>
  call<any>("/dex/swap", { method: "POST", body: JSON.stringify(body) });

// ── writes ─────────────────────────────────────────────────────────────────

export const saveProtocol = (graph: Graph, name?: string, id?: string) =>
  call<{ protocol: Protocol }>("/protocols", {
    method: "POST",
    body: JSON.stringify({ graph, name, id }),
  });

export const deleteProtocol = (id: string) =>
  call<{ ok: boolean }>(`/protocols/${id}`, { method: "DELETE" });

export const publishProtocol = (id: string) =>
  call<{ cid: string; share: string }>(`/protocols/${id}/publish`, { method: "POST" });

export const importProtocol = (cid: string) =>
  call<{ protocol: Protocol }>("/protocols/import", {
    method: "POST",
    body: JSON.stringify({ cid }),
  });

export const importPrompt = (cid: string) =>
  call<any>("/agent/prompts/import", { method: "POST", body: JSON.stringify({ cid }) });

export const recordDeployment = (
  id: string,
  payload: { chainId: number; network: string; addresses: Record<string, string>; txs: string[] }
) =>
  call<any>(`/protocols/${id}/deployments`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

// ── wallet sign-in ─────────────────────────────────────────────────────────

export async function signIn(): Promise<{ address: string; token: string }> {
  const ethereum = (window as any).ethereum;
  if (!ethereum) throw new Error("no browser wallet found — install MetaMask or Rabby");
  const { BrowserProvider } = await import("ethers");
  const provider = new BrowserProvider(ethereum);
  await provider.send("eth_requestAccounts", []);
  const signer = await provider.getSigner();
  const address = (await signer.getAddress()).toLowerCase();

  const base = await apiBase();
  const challenge = await fetch(`${base}/auth/challenge?address=${address}`, {
    cache: "no-store",
  }).then((r) => r.json());
  if (!challenge?.message) throw new Error(challenge?.error || "could not get a challenge");

  const signature = await signer.signMessage(challenge.message);
  const verified = await fetch(`${base}/auth/verify`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ address, signature }),
  }).then((r) => r.json());
  if (!verified?.token) throw new Error(verified?.error || "sign-in rejected");

  localStorage.setItem(TOKEN_KEY, verified.token);
  localStorage.setItem(ADDRESS_KEY, address);
  return { address, token: verified.token };
}

// ── the yields table ───────────────────────────────────────────────────────
// Open, like the rest of the reads here: an APR is a public fact about a public
// market, and a sign-in in front of it would only make the number harder to
// check.

function qs(params: Record<string, any>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const getYields = (params: Record<string, any> = {}) =>
  call<any>(`/yields${qs(params)}`);

export const getYieldProtocols = (params: Record<string, any> = {}) =>
  call<any>(`/yields/protocols${qs(params)}`);

export const getYieldFacets = () => call<any>("/yields/facets");

export const getYieldPool = (id: string) => call<any>(`/yields/pool/${encodeURIComponent(id)}`);

// ── the treasury ───────────────────────────────────────────────────────────

export const getTreasury = () => call<any>("/treasury");
export const getSchedule = (weeks = 8) => call<any>(`/treasury/schedule?weeks=${weeks}`);
export const getHolders = () => call<any>("/treasury/holders");
export const getPreview = () => call<any>("/treasury/preview");

export const chooseAllocation = (body: Record<string, any>) =>
  call<any>("/treasury/allocations", { method: "POST", body: JSON.stringify(body) });

export const dropAllocation = (id: string) =>
  call<any>(`/treasury/allocations/${encodeURIComponent(id)}`, { method: "DELETE" });

export const watchAddress = (address: string, remove = false) =>
  call<any>("/treasury/participants", {
    method: "POST",
    body: JSON.stringify({ address, remove }),
  });

export const bindTreasury = (body: Record<string, any>) =>
  call<any>("/treasury/bind", { method: "POST", body: JSON.stringify(body) });

/// The three that move money. Each carries the caller's eth-module bearer,
/// which this module forwards and never stores.
export const lockAllocation = (body: Record<string, any>) =>
  call<any>("/treasury/lock", { method: "POST", body: JSON.stringify(body) });

export const distributeWeek = (body: Record<string, any>) =>
  call<any>("/treasury/distribute", { method: "POST", body: JSON.stringify(body) });

export const claimPayout = (body: Record<string, any>) =>
  call<any>("/treasury/claim", { method: "POST", body: JSON.stringify(body) });

export const registerHolder = (body: Record<string, any>) =>
  call<any>("/treasury/register", { method: "POST", body: JSON.stringify(body) });
