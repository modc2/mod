// Wallet helpers + mod **protocol-auth** token builder.
//
// The token is a wallet-signed, time-bounded `{data, time, key, signature}`
// envelope, base64url-encoded — verified statelessly by the Rust gateway
// (src/api/src/auth.rs), which mirrors mod/core/server/auth.
//
// The signed material is `JSON.stringify({ data, time })` (compact, no spaces),
// signed via EIP-191 `personal_sign`. We keep `data` to a single key so its
// JSON serialization is unambiguous across JS / Rust / Python.

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
      on?: (event: string, handler: (...args: unknown[]) => void) => void;
      removeListener?: (event: string, handler: (...args: unknown[]) => void) => void;
      isMetaMask?: boolean;
    };
  }
}

export function shortAddress(addr: string): string {
  if (!addr || addr.length < 10) return addr || "";
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export function hasWallet(): boolean {
  return typeof window !== "undefined" && !!window.ethereum;
}

export async function connectWallet(): Promise<{ address: string; chainId: number }> {
  if (!window.ethereum) throw new Error("No Ethereum wallet detected");
  const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
  const chainIdHex = (await window.ethereum.request({ method: "eth_chainId" })) as string;
  return { address: accounts[0], chainId: parseInt(chainIdHex, 16) };
}

export async function personalSign(message: string, address: string): Promise<string> {
  if (!window.ethereum) throw new Error("No Ethereum wallet detected");
  return (await window.ethereum.request({
    method: "personal_sign",
    params: [message, address],
  })) as string;
}

// base64url JSON, matching Python's urlsafe_b64encode(...).rstrip(b"=").
function b64urlJson(obj: unknown): string {
  const s = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(s)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Build a mod protocol-auth token. `data` defaults to `{ scope: "dev" }`.
 * Signs `JSON.stringify({ data, time })` — exactly what the gateway rebuilds
 * and verifies (sig over ["data","time"], compact separators).
 */
export async function buildModToken(
  address: string,
  data: Record<string, unknown> = { scope: "dev" }
): Promise<string> {
  const time = (Date.now() / 1000).toString();
  const sigData = JSON.stringify({ data, time });
  const signature = await personalSign(sigData, address);
  return b64urlJson({ data, time, key: address, signature });
}
