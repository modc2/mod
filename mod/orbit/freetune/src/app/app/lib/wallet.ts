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

export function shortAddress(addr: string): string {
  if (!addr || addr.length < 10) return addr || "";
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

export function hasWallet(): boolean {
  return typeof window !== "undefined" && !!window.ethereum;
}

export async function connectWallet(): Promise<{ address: string }> {
  if (!window.ethereum) throw new Error("NO WALLET DETECTED");
  const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
  if (!accounts?.length) throw new Error("NO ACCOUNTS FOUND");
  return { address: accounts[0] };
}

export async function personalSign(message: string, address: string): Promise<string> {
  if (!window.ethereum) throw new Error("NO WALLET DETECTED");
  return (await window.ethereum.request({
    method: "personal_sign",
    params: [message, address],
  })) as string;
}

// base64url JSON, matching auth.py's urlsafe_b64encode(...).rstrip(b"=").
function b64urlJson(obj: unknown): string {
  const s = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(s)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Build a mod-protocol token: a wallet-signed, time-bounded
 * `{data, time, key, signature}` envelope, base64url-encoded, sent as the
 * `token` header. The signed material is `JSON.stringify({data, time})` with
 * no spaces — exactly what the freetune Rust API (`auth.rs`) re-serializes
 * and verifies.
 */
export async function buildModToken(
  address: string,
  data: Record<string, unknown> = {},
): Promise<string> {
  const time = (Date.now() / 1000).toString();
  const sigData = JSON.stringify({ data, time });
  const signature = await personalSign(sigData, address);
  return b64urlJson({ data, time, key: address, signature });
}
