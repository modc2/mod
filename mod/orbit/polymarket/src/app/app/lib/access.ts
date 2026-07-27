// Owner-only access gate — client side.
//
// The Rust API 401s every route except /health and /access/* unless the
// request carries a Bearer token minted by /access/verify (an EIP-191
// signature from the deployment's sudo/owner address over a challenge that
// embeds the Terms-of-Use hash — signing IS accepting the terms).
//
// Rather than threading an auth header through every one of the app's many
// fetch call sites (panels, copy engine, market ticker, strat sync…), we
// patch window.fetch ONCE and attach the token to any request aimed at the
// API base. A 401 on a gated call clears the token and broadcasts
// ACCESS_REVOKED_EVENT so AccessGate can drop back to the sign-in screen.

import { BrowserProvider } from "ethers";
import { API_BASE } from "./polymarket";

// Small fixed-size value — safe for the shared modc2.com localStorage
// origin (quota-contended; setItem must never throw uncaught).
const ACCESS_TOKEN_KEY = "poly_access_token";

export const ACCESS_REVOKED_EVENT = "poly-access-revoked";

// In-memory copy of the token, authoritative for the current tab. modc2.com
// shares ONE localStorage origin across every module, so it can be FULL —
// when `localStorage.setItem` throws on quota the write is silently dropped,
// the token never persists, and every gated call 401s → the console bounces
// straight back to the sign-in screen ("could not resolve deposit wallet",
// endless re-sign-in loop). Holding the token in memory keeps the session
// alive for this tab regardless of localStorage, and we still best-effort
// persist (evicting our own bulky caches on quota) so it survives a reload.
let memToken: string | null = null;

export function getAccessToken(): string | null {
  if (memToken) return memToken;
  try {
    const t = localStorage.getItem(ACCESS_TOKEN_KEY);
    if (t) memToken = t;
    return t;
  } catch {
    return memToken;
  }
}

export function setAccessToken(token: string): void {
  memToken = token; // never lost to quota — carries the session for this tab
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, token);
  } catch {
    // Shared origin is full. Free our own reconstructable caches and retry so
    // the token also survives a page reload; if it still fails, memToken holds.
    try {
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const k = localStorage.key(i);
        if (
          k &&
          (k.startsWith("poly_portfolio_history") ||
            k.startsWith("poly_cache") ||
            k.startsWith("poly_trades") ||
            k.startsWith("poly_redeem_marks"))
        ) {
          localStorage.removeItem(k);
        }
      }
      localStorage.setItem(ACCESS_TOKEN_KEY, token);
    } catch {
      /* memToken still carries the session for this tab */
    }
  }
}

export function clearAccessToken(): void {
  memToken = null;
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
  } catch {}
}

/**
 * The signed-in owner's EOA, parsed from the access token (`pma1.<addr>.…`).
 * This is the authenticated identity — and since the console is a private,
 * single-owner deployment, it's also the wallet whose funds/strats the panels
 * should show. Prefer it over the loosely-connected `auth.address`, which can
 * lag on a stale/previous wallet and make every panel read an empty wallet.
 */
export function getOwnerAddress(): string | null {
  const t = getAccessToken();
  if (!t) return null;
  const addr = t.split(".")[1];
  return addr && /^0x[0-9a-fA-F]{40}$/.test(addr) ? addr.toLowerCase() : null;
}

/**
 * Does a full address match the truncated owner hint the API serves
 * ("0x89bc…6cfa")? Used at sign-in to pick the owner's account out of the
 * wallet's authorized list instead of failing ACCESS DENIED on whichever
 * account MetaMask happens to have selected.
 */
export function matchesOwnerHint(address: string, hint: string): boolean {
  const a = address.toLowerCase();
  const h = hint.toLowerCase();
  if (!h.includes("…")) return a === h;
  const [pre, suf] = h.split("…");
  return !!pre && !!suf && a.startsWith(pre) && a.endsWith(suf);
}

function isApiUrl(url: string): boolean {
  if (url.startsWith(API_BASE)) return true;
  // Absolute form of the same base (NEXT_PUBLIC_API_URL or same-origin).
  try {
    const u = new URL(url, window.location.origin);
    return u.pathname.startsWith(
      API_BASE.startsWith("http") ? new URL(API_BASE).pathname : API_BASE,
    );
  } catch {
    return false;
  }
}

let installed = false;

/** Patch window.fetch so every API call carries the access token. Idempotent. */
export function installAccessFetch(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    if (!isApiUrl(url)) return orig(input, init);

    const token = getAccessToken();
    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    if (token && !headers.has("authorization")) {
      headers.set("authorization", `Bearer ${token}`);
    }
    const res = await orig(input, { ...init, headers });
    if (res.status === 401 && token && !url.includes("/access/")) {
      // Session expired / secret rotated / owner changed — force re-gate.
      // Only when a token WAS attached: tokenless 401s just mean the gate
      // is already down, and broadcasting for those would loop the lock UI.
      clearAccessToken();
      window.dispatchEvent(new Event(ACCESS_REVOKED_EVENT));
    }
    return res;
  };
}

export interface AccessInfo {
  gated: boolean;
  ownerHint?: string | null;
  termsVersion: string;
  termsSha256: string;
  terms: string;
}

export async function fetchAccessInfo(): Promise<AccessInfo> {
  const res = await fetch(`${API_BASE}/access/info`);
  if (!res.ok) throw new Error(`access info failed (${res.status})`);
  return res.json();
}

/** Probe whether the stored token is still accepted. */
export async function checkAccess(): Promise<boolean> {
  if (!getAccessToken()) return false;
  try {
    const res = await fetch(`${API_BASE}/access/check`);
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Full sign-in: fetch the server-built challenge (never constructed client
 * side — the signature must be over the server's exact bytes), personal_sign
 * it, exchange for a token. Throws with a readable message on denial.
 */
export async function signInAsOwner(address: string): Promise<string> {
  const chRes = await fetch(
    `${API_BASE}/access/challenge?address=${encodeURIComponent(address)}`,
  );
  if (!chRes.ok) throw new Error(`CHALLENGE FAILED (${chRes.status})`);
  const ch = (await chRes.json()) as { message: string; timestamp: number };

  if (!window.ethereum) throw new Error("NO WALLET DETECTED");
  const provider = new BrowserProvider(window.ethereum as never);
  const signer = await provider.getSigner(address);
  const signature = await signer.signMessage(ch.message);

  const vRes = await fetch(`${API_BASE}/access/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ address, timestamp: ch.timestamp, signature }),
  });
  const data = (await vRes.json().catch(() => ({}))) as {
    token?: string;
    error?: string;
    detail?: string;
  };
  if (!vRes.ok || !data.token) {
    throw new Error(
      data.detail || data.error || `SIGN-IN FAILED (${vRes.status})`,
    );
  }
  setAccessToken(data.token);
  return data.token;
}
