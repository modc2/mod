import type { JsonRpcSigner } from "ethers";
import { ClobCredentials } from "./types";
import { signOrder } from "./polymarketOrderSigning";

// L2 calls (order, balance, orders, cancel) can't go to clob.polymarket.com
// directly from the browser — no CORS on these endpoints. Caddy at
// /api/polymarket-l2/* strips the prefix and reverse-proxies to the upstream
// (see docker-entrypoint.dev.sh / Caddyfile).
const CLOB_BASE = "/api/polymarket-l2";

// ── L2 Header Signing ──────────────────────────────────────────

// Polymarket's CLOB issues the API secret as base64url (RFC 4648 §5) — uses
// `-`/`_` and omits padding. atob() only accepts standard base64, so we
// translate first. The HMAC signature must also be base64url to match what
// their server verifies (see py-clob-client/clob-client TS lib).
function b64urlDecode(s: string): Uint8Array<ArrayBuffer> {
  if (!s) throw new Error("CLOB secret is empty — re-authenticate");
  const clean = s.replace(/\s+/g, "").replace(/=+$/, "");
  if (!/^[A-Za-z0-9_-]+$/.test(clean)) {
    const bad = (clean.match(/[^A-Za-z0-9_-]/g) || []).slice(0, 5).join("");
    throw new Error(`CLOB secret has non-base64url chars (${bad}) — re-authenticate`);
  }
  let b64 = clean.replace(/-/g, "+").replace(/_/g, "/");
  const pad = b64.length % 4;
  if (pad === 2) b64 += "==";
  else if (pad === 3) b64 += "=";
  else if (pad === 1) {
    throw new Error(`CLOB secret has invalid base64url length=${clean.length} — re-authenticate`);
  }
  try {
    return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  } catch (e) {
    throw new Error(
      `CLOB secret base64url decode failed: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
}

function b64urlEncode(bytes: Uint8Array): string {
  // Polymarket's verifier expects url-safe base64 WITH the `=` padding kept
  // — see @polymarket/clob-client buildPolyHmacSignature ("Must be url safe
  // base64 encoding, but keep base64 '=' suffix"). Stripping padding yields
  // "Invalid api key" even when the HMAC is otherwise correct.
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

async function hmacSign(secret: string, message: string): Promise<string> {
  // TS 5.7+ narrows Uint8Array to Uint8Array<ArrayBufferLike>, which doesn't
  // satisfy BufferSource's ArrayBuffer constraint. Re-wrap to widen the type.
  const keyBytes = new Uint8Array(b64urlDecode(secret)).buffer;
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(message),
  );
  return b64urlEncode(new Uint8Array(sig));
}

async function buildL2Headers(
  creds: ClobCredentials,
  address: string,
  method: string,
  requestPath: string,
  body: string = "",
): Promise<Record<string, string>> {
  if (!creds?.apiKey || !creds?.secret || !creds?.passphrase) {
    throw new Error("CLOB credentials missing — re-authenticate");
  }
  // py-clob-client L2 headers do NOT include POLY_NONCE — that's only for the
  // L1 EIP-712 ClobAuth flow. The HMAC message is timestamp+method+path+body.
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const message = timestamp + method.toUpperCase() + requestPath + body;
  const signature = await hmacSign(creds.secret, message);

  return {
    "POLY_ADDRESS": address,
    "POLY_SIGNATURE": signature,
    "POLY_TIMESTAMP": timestamp,
    "POLY_API_KEY": creds.apiKey,
    "POLY_PASSPHRASE": creds.passphrase,
    "Content-Type": "application/json",
  };
}

// ── Types ──────────────────────────────────────────────────────

export interface ClobOrder {
  tokenID: string;
  price: number;
  size: number;
  side: "BUY" | "SELL";
  type: "GTC" | "FOK" | "FAK" | "GTD";
  feeRateBps?: number;
}

export interface ClobOrderResult {
  success: boolean;
  orderID?: string;
  status?: string;
  errorMsg?: string;
  transactionsHashes?: string[];
}

export interface ClobBalance {
  /** Effective tradable balance = min(walletUsdc, allowance). */
  balance: number;
  /** Raw USDC.e held at this address on Polygon (decimals applied). */
  rawBalance?: number;
  /** USDC.e allowance granted to the Polymarket exchange (decimals applied). */
  allowance?: number;
}

export interface ClobOpenOrder {
  id: string;
  status: string;
  tokenID: string;
  side: string;
  price: string;
  size: string;
  createdAt: string;
}

// ── API Methods ────────────────────────────────────────────────

// Place a CLOB order.
//
// Two code paths:
//
//   sigType=2 (POLY_GNOSIS_SAFE — the AUTO-TRADING toggle path):
//     Order args are shipped to /api/polymarket/order/place. The backend
//     signs with a per-EOA key registered as a Safe co-owner (one-time
//     MetaMask popup in BackendSignerPanel) and forwards to CLOB. ZERO
//     MetaMask prompts per trade — this is what makes long-running copy
//     trading actually work.
//
//   sigType=0/1 (EOA / POLY_PROXY — legacy path):
//     Wallet signs each order via EIP-712 signTypedData. Kept for users
//     who haven't enabled backend trading.
//
// negRisk flips which exchange the order is signed against — using the
// wrong domain produces "invalid signature" rejections from the matcher.
export async function placeOrder(
  creds: ClobCredentials,
  signer: JsonRpcSigner,
  maker: string,
  order: ClobOrder,
  negRisk: boolean,
  sigType: 0 | 1 | 2 | 3 = 3,
): Promise<ClobOrderResult> {
  // sigType=2 (legacy Safe) AND sigType=3 (V2 deposit wallet) both go
  // through the backend. The backend overrides any incoming sigType to 3
  // internally for V2 placements, so callers can pass either and get the
  // same behavior — see order_place.rs `effective_sig_type` override.
  if (sigType === 2 || sigType === 3) {
    try {
      const eoa = await signer.getAddress();
      return await placeOrderViaBackend(creds, eoa, maker, order, negRisk);
    } catch (e) {
      const raw = e instanceof Error ? e.message : String(e);
      const lower = raw.toLowerCase();
      const friendly = lower.includes("invalid_signature") || lower.includes("invalid signature")
        ? "BACKEND_SIGNER_NOT_AUTHORIZED — open AUTO-TRADING panel and click TURN ON"
        : `BACKEND_PLACE_FAILED: ${raw.slice(0, 200)}`;
      return { success: false, errorMsg: friendly };
    }
  }

  let signed;
  try {
    signed = await signOrder(
      signer,
      maker,
      {
        tokenId: order.tokenID,
        side: order.side,
        price: order.price,
        size: order.size,
        feeRateBps: order.feeRateBps ?? 0,
        // GTC/FAK leave expiration at 0; FOK is enforced server-side by
        // type, not by signed expiration.
        expirationSec: 0,
        signatureType: sigType,
      },
      negRisk,
    );
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    // Wallet-rejected signing is the common case here — surface it cleanly
    // so the engine can decide whether to halt vs. retry next cycle.
    return {
      success: false,
      errorMsg: msg.toLowerCase().includes("user reject")
        ? "USER_REJECTED_SIGN"
        : `SIGN_FAILED: ${msg.slice(0, 160)}`,
    };
  }

  const path = "/order";
  // Polymarket's POST /order body wants `side` as the string "BUY"/"SELL",
  // even though the EIP-712 typed data (already hashed/signed in `signed`)
  // uses uint8 0/1. Sending the numeric form produces an opaque "Invalid
  // order payload" 400. Mirrors @polymarket/clob-client's `orderToJson`.
  const orderBody = { ...signed, side: signed.side === 0 ? "BUY" : "SELL" };
  const body = JSON.stringify({ order: orderBody, owner: creds.apiKey, orderType: order.type });
  const headers = await buildL2Headers(creds, maker, "POST", path, body);
  let res: Response;
  try {
    res = await fetch(`${CLOB_BASE}${path}`, { method: "POST", headers, body });
  } catch (e) {
    return { success: false, errorMsg: `NETWORK: ${e instanceof Error ? e.message : String(e)}` };
  }
  const text = await res.text().catch(() => "");
  if (!res.ok) {
    return { success: false, errorMsg: `POST /order HTTP ${res.status}: ${text.slice(0, 200)}` };
  }
  let data: Record<string, unknown> = {};
  try { data = JSON.parse(text); } catch {}
  const success = data.success !== false && !data.errorMsg;
  return {
    success,
    orderID: typeof data.orderID === "string" ? data.orderID : undefined,
    status: typeof data.status === "string" ? data.status : undefined,
    errorMsg: typeof data.errorMsg === "string" ? data.errorMsg : (success ? undefined : "REJECTED"),
    transactionsHashes: Array.isArray(data.transactionsHashes)
      ? (data.transactionsHashes as string[])
      : undefined,
  };
}

// Backend-signed placement. Ships {eoa, creds, args} to polymarket-api;
// the backend builds the EIP-712 digest server-side from the typed args
// (it never accepts a raw digest from the client — see signer.rs comment),
// signs with the per-EOA backend key, HMAC-authes the L2 call, and POSTs
// to CLOB. Returns CLOB's response verbatim so we can read order id and
// fills the same way as the wallet-signed path.
async function placeOrderViaBackend(
  creds: ClobCredentials,
  eoa: string,
  maker: string,
  order: ClobOrder,
  negRisk: boolean,
): Promise<ClobOrderResult> {
  const body = JSON.stringify({
    eoa,
    creds: {
      apiKey: creds.apiKey,
      secret: creds.secret,
      passphrase: creds.passphrase,
    },
    args: {
      tokenId: order.tokenID,
      side: order.side,
      price: order.price,
      size: order.size,
      feeRateBps: order.feeRateBps ?? 0,
      expiration: 0,
      signatureType: 2,
      orderType: order.type,
      negRisk,
      maker,
    },
  });
  const res = await fetch("/api/polymarket/order/place", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
  });
  const text = await res.text().catch(() => "");
  if (!res.ok) {
    let detail = text.slice(0, 200);
    try {
      const j = JSON.parse(text) as { error?: string };
      if (j.error) detail = j.error.slice(0, 200);
    } catch {}
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  let data: Record<string, unknown> = {};
  try {
    data = JSON.parse(text);
  } catch {}
  const success = data.success !== false && !data.errorMsg;
  return {
    success,
    orderID: typeof data.orderID === "string" ? data.orderID : undefined,
    status: typeof data.status === "string" ? data.status : undefined,
    errorMsg:
      typeof data.errorMsg === "string"
        ? data.errorMsg
        : success
        ? undefined
        : "REJECTED",
    transactionsHashes: Array.isArray(data.transactionsHashes)
      ? (data.transactionsHashes as string[])
      : undefined,
  };
}

/**
 * Probe all three signature_types and return the one that reports the
 * highest non-zero balance. Polymarket binds each apiKey to a specific
 * trading account (EOA / POLY_PROXY / POLY_GNOSIS_SAFE) at creation time
 * and we can't otherwise tell which without trying. Returns the winning
 * sigType + balance, or sigType=2 with zero balance if nothing has funds.
 */
export async function detectSigType(
  creds: ClobCredentials,
  address: string,
): Promise<{ sigType: 0 | 1 | 2 | 3; bal: ClobBalance }> {
  const types: (0 | 1 | 2)[] = [2, 1, 0];
  let best: { sigType: 0 | 1 | 2 | 3; bal: ClobBalance } = {
    sigType: 2,
    bal: { balance: 0, rawBalance: 0, allowance: 0 },
  };
  for (const t of types) {
    try {
      await refreshBalance(creds, address, t);
      const bal = await getBalance(creds, address, t);
      if ((bal.rawBalance ?? 0) > (best.bal.rawBalance ?? 0)) {
        best = { sigType: t, bal };
      }
    } catch {}
  }
  return best;
}

/**
 * Force Polymarket's CLOB to re-scan on-chain USDC balance + allowance for
 * this user. The read endpoint (`GET /balance-allowance`) returns a cached
 * snapshot — newly-funded users see $0 until this is called at least once.
 */
export async function refreshBalance(
  creds: ClobCredentials,
  address: string,
  sigType: 0 | 1 | 2 | 3 = 3,
): Promise<boolean> {
  const path = "/balance-allowance/update";
  const body = JSON.stringify({ asset_type: "COLLATERAL", signature_type: sigType });
  const headers = await buildL2Headers(creds, address, "POST", path, body);
  try {
    const res = await fetch(`${CLOB_BASE}${path}`, { method: "POST", headers, body });
    return res.ok;
  } catch {
    return false;
  }
}

export async function getBalance(
  creds: ClobCredentials,
  address: string,
  sigType: 0 | 1 | 2 | 3 = 3,
): Promise<ClobBalance> {
  // py-clob-client signs the HMAC over the *base* request_path (e.g.
  // "/balance-allowance") WITHOUT the query string, then fetches the URL
  // *with* the query string appended. Signing the full ?asset_type=... path
  // is what gives 401 "Invalid api key". asset_type=COLLATERAL is USDC.
  // signature_type=1 (POLY_PROXY) matches the proxy that Polymarket binds
  // every newly-minted apiKey to — funds at the proxy address.
  const signedPath = "/balance-allowance";
  const query = `?asset_type=COLLATERAL&signature_type=${sigType}`;
  const headers = await buildL2Headers(creds, address, "GET", signedPath);
  const res = await fetch(`${CLOB_BASE}${signedPath}${query}`, { method: "GET", headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`balance-allowance HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  const data = await res.json();
  // Log the raw response once per session so we can see fields beyond
  // {balance, allowance} — Polymarket may return `funder`, `proxyAddress`,
  // or similar that tell us which address the apiKey is actually bound to.
  if (typeof window !== "undefined") {
    const w = window as Window & { __polyBalanceResponse?: unknown };
    if (w.__polyBalanceResponse === undefined) {
      w.__polyBalanceResponse = data;
      console.log("[polymarket] balance-allowance raw response (sigType=" + sigType + "):", data);
    }
  }
  // Polymarket returns base-units strings for `balance` (wallet USDC) and
  // `allowance` (USDC approved for the exchange). Effective tradable balance
  // is the min — old responses sometimes lumped both into `availableBalance`.
  const toUsd = (v: unknown): number => {
    const n = Number(v ?? 0) / 1_000_000;
    return Number.isFinite(n) ? n : 0;
  };
  const rawBalance = toUsd(data.balance);
  const allowance = toUsd(data.allowance);
  const effective =
    data.availableBalance !== undefined
      ? toUsd(data.availableBalance)
      : Math.min(rawBalance, allowance);
  return { balance: effective, rawBalance, allowance };
}

export async function getOpenOrders(
  creds: ClobCredentials,
  address: string,
): Promise<ClobOpenOrder[]> {
  const path = "/data/orders";
  const headers = await buildL2Headers(creds, address, "GET", path);
  const res = await fetch(`${CLOB_BASE}${path}`, { method: "GET", headers });
  if (!res.ok) return [];
  const data = await res.json();
  if (Array.isArray(data)) return data;
  return data.data || data.orders || [];
}

export async function cancelOrder(
  creds: ClobCredentials,
  address: string,
  orderID: string,
): Promise<boolean> {
  // py-clob-client uses DELETE /order with the order id in the JSON body.
  const path = "/order";
  const body = JSON.stringify({ orderID });
  const headers = await buildL2Headers(creds, address, "DELETE", path, body);
  const res = await fetch(`${CLOB_BASE}${path}`, { method: "DELETE", headers, body });
  return res.ok;
}
