import { BrowserProvider } from "ethers";
import { ClobCredentials } from "./types";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
      on: (event: string, handler: (...args: unknown[]) => void) => void;
      removeListener: (event: string, handler: (...args: unknown[]) => void) => void;
      isMetaMask?: boolean;
    };
  }
}

// The browser cannot call clob.polymarket.com directly (no CORS on auth
// endpoints). We proxy through the Rust API, which Caddy exposes at
// /api/polymarket/*.
const CLOB_PROXY = "/api/polymarket/clob";

export function detectWallet(): boolean {
  return typeof window !== "undefined" && !!window.ethereum;
}

export function shortAddress(address: string): string {
  if (!address || address.length < 10) return address || "---";
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}

export async function connectWallet(): Promise<{ address: string; chainId: number }> {
  if (!window.ethereum) throw new Error("NO WALLET DETECTED");

  const accounts = (await window.ethereum.request({
    method: "eth_requestAccounts",
  })) as string[];

  if (!accounts || accounts.length === 0) throw new Error("NO ACCOUNTS FOUND");

  const chainIdHex = (await window.ethereum.request({
    method: "eth_chainId",
  })) as string;

  return { address: accounts[0], chainId: parseInt(chainIdHex, 16) };
}

// Polymarket settles on Polygon (chainId 137). MetaMask's typed-data sign
// refuses to produce a signature whose EIP-712 domain.chainId does not match
// the wallet's active chain (phishing protection), so we must switch the
// wallet to Polygon before asking it to sign ClobAuth.
const POLYGON_CHAIN_ID_HEX = "0x89";
const POLYGON_CHAIN_ID = 137;

const POLYGON_PARAMS = {
  chainId: POLYGON_CHAIN_ID_HEX,
  chainName: "Polygon",
  nativeCurrency: { name: "MATIC", symbol: "MATIC", decimals: 18 },
  rpcUrls: ["https://polygon-rpc.com"],
  blockExplorerUrls: ["https://polygonscan.com"],
};

async function ensurePolygon(): Promise<void> {
  if (!window.ethereum) throw new Error("NO WALLET");
  const current = (await window.ethereum.request({ method: "eth_chainId" })) as string;
  if (parseInt(current, 16) === POLYGON_CHAIN_ID) return;

  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: POLYGON_CHAIN_ID_HEX }],
    });
  } catch (e: unknown) {
    const { code, message } = normalizeRpcError(e);
    // 4902 = chain not added to the wallet yet.
    if (code === 4902 || /unrecognized chain|not been added/i.test(message)) {
      await window.ethereum.request({
        method: "wallet_addEthereumChain",
        params: [POLYGON_PARAMS],
      });
      await window.ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: POLYGON_CHAIN_ID_HEX }],
      });
      return;
    }
    if (isUserRejection(code, message)) {
      throw new Error("CHAIN SWITCH REJECTED — approve Polygon in MetaMask");
    }
    throw new Error(`CHAIN SWITCH FAILED: ${message.slice(0, 200)}`);
  }
}

// EIP-1193 errors are plain objects with { code, message, data? }, not Error
// instances — so naive String(e) yields "[object Object]". Pull out the real
// message and code so we can show the user what went wrong.
interface ProviderError {
  code?: number;
  message?: string;
  data?: { message?: string };
}

function normalizeRpcError(e: unknown): { code?: number; message: string } {
  if (e instanceof Error) {
    const code = (e as unknown as ProviderError).code;
    return { code, message: e.message };
  }
  if (typeof e === "string") return { message: e };
  if (e && typeof e === "object") {
    const o = e as ProviderError;
    const message =
      o.data?.message ||
      o.message ||
      (() => {
        try { return JSON.stringify(o).slice(0, 300); } catch { return "unknown error"; }
      })();
    return { code: o.code, message };
  }
  return { message: String(e) };
}

function isUserRejection(code: number | undefined, message: string): boolean {
  if (code === 4001 || code === -32603) return true;
  const m = message.toLowerCase();
  return m.includes("reject") || m.includes("denied") || m.includes("cancel");
}

// Polymarket CLOB ClobAuth EIP-712 schema. Domain chainId is always 137
// regardless of which chain the wallet is currently connected to — the
// signature is verified by recovering the address from the typed hash, so
// the wallet's active chain doesn't matter.
const DERIVE_DOMAIN = {
  name: "ClobAuthDomain",
  version: "1",
  chainId: 137,
};

const DERIVE_TYPES = {
  ClobAuth: [
    { name: "address", type: "address" },
    { name: "timestamp", type: "string" },
    { name: "nonce", type: "uint256" },
    { name: "message", type: "string" },
  ],
};

const CLOB_AUTH_MESSAGE = "This message attests that I control the given wallet";

async function signClobAuth(
  address: string,
  timestamp: string,
  nonce: number,
): Promise<string> {
  if (!window.ethereum) throw new Error("NO WALLET");
  // MetaMask refuses to sign typed data whose EIP-712 domain.chainId doesn't
  // match the wallet's active chain — switch first.
  await ensurePolygon();
  const provider = new BrowserProvider(window.ethereum as never);
  const signer = await provider.getSigner(address);

  const value = {
    address,
    timestamp,
    nonce,
    message: CLOB_AUTH_MESSAGE,
  };

  try {
    return await signer.signTypedData(DERIVE_DOMAIN, DERIVE_TYPES, value);
  } catch (e: unknown) {
    const { code, message } = normalizeRpcError(e);
    console.error("CLOB sign error:", { code, message, raw: e });
    if (isUserRejection(code, message)) {
      throw new Error("SIGNATURE REJECTED — click Sign in MetaMask");
    }
    throw new Error(`SIGNATURE FAILED: ${message.slice(0, 200)}`);
  }
}

async function postClobAuth(
  path: string,
  body: { address: string; signature: string; timestamp: string; nonce: number },
): Promise<{ ok: boolean; status: number; data: unknown }> {
  const res = await fetch(`${CLOB_PROXY}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

function extractCreds(data: unknown): ClobCredentials | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  const apiKey = (d.apiKey || d.api_key) as string | undefined;
  const secret = d.secret as string | undefined;
  const passphrase = d.passphrase as string | undefined;
  if (!apiKey || !secret || !passphrase) return null;
  return { apiKey, secret, passphrase };
}

export async function deriveClobApiKey(address: string): Promise<ClobCredentials> {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = 0;
  const signature = await signClobAuth(address, timestamp, nonce);
  const payload = { address, signature, timestamp, nonce };

  // Try derive first (existing key). If the user has never created one,
  // Polymarket returns an error and we fall through to create.
  const derived = await postClobAuth("/auth/derive-api-key", payload);
  if (derived.ok) {
    const creds = extractCreds(derived.data);
    if (creds) return creds;
  }

  const created = await postClobAuth("/auth/create-api-key", payload);
  if (!created.ok) {
    const msg = (() => {
      try { return JSON.stringify(created.data).slice(0, 300); } catch { return "unknown"; }
    })();
    console.error("CLOB create-api-key failed:", created.status, created.data);
    throw new Error(`CLOB AUTH FAILED (${created.status}): ${msg}`);
  }
  const creds = extractCreds(created.data);
  if (!creds) {
    console.error("CLOB returned invalid credentials:", created.data);
    throw new Error("CLOB returned invalid credentials");
  }
  return creds;
}

// Kept for callers that explicitly want to create a fresh key.
export async function createClobApiKey(address: string): Promise<ClobCredentials> {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = 0;
  const signature = await signClobAuth(address, timestamp, nonce);
  const res = await postClobAuth("/auth/create-api-key", {
    address, signature, timestamp, nonce,
  });
  if (!res.ok) {
    const msg = (() => {
      try { return JSON.stringify(res.data).slice(0, 300); } catch { return "unknown"; }
    })();
    throw new Error(`CREATE KEY FAILED (${res.status}): ${msg}`);
  }
  const creds = extractCreds(res.data);
  if (!creds) throw new Error("CLOB returned invalid credentials");
  return creds;
}
