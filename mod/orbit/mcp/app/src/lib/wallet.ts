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
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export function hasWallet(): boolean {
  return typeof window !== "undefined" && !!window.ethereum;
}

export async function connectWallet(): Promise<{ address: string; chainId: number }> {
  if (!window.ethereum) throw new Error("no browser wallet found");
  const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
  const chainIdHex = (await window.ethereum.request({ method: "eth_chainId" })) as string;
  return { address: accounts[0], chainId: parseInt(chainIdHex, 16) };
}

export async function personalSign(message: string, address: string): Promise<string> {
  if (!window.ethereum) throw new Error("no browser wallet found");
  return (await window.ethereum.request({
    method: "personal_sign",
    params: [message, address],
  })) as string;
}

// base64url JSON, matching python's urlsafe_b64encode(...).rstrip(b"=").
function b64urlJson(obj: unknown): string {
  const s = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(s)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Anything that can produce an EIP-191 `personal_sign` signature. */
export type Signer = (message: string, address: string) => Promise<string>;

/**
 * Build a mod **protocol auth** token: a signed, time-bounded
 * `{data, time, key, signature}` envelope, base64url-encoded.
 *
 * The signed material is `JSON.stringify({data, time})` with no spaces, which
 * is exactly what `mod core/server/auth` re-serializes and verifies server-side
 * (sig_features = ["data", "time"], separators (',',':')).
 *
 * `sign` defaults to the injected wallet; local sign-in passes the browser-held
 * key instead — the hub can't tell the two apart, and neither can store when it
 * pins your manifest.
 */
export async function buildModToken(
  address: string,
  data: Record<string, unknown> = {},
  sign: Signer = personalSign
): Promise<string> {
  const time = (Date.now() / 1000).toString();
  const sigData = JSON.stringify({ data, time });
  const signature = await sign(sigData, address);
  return b64urlJson({ data, time, key: address, signature });
}
