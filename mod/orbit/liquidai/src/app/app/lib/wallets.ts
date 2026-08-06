"use client";

// Three ways to prove you're you, and nothing in common between them except
// the sentence they sign.
//
//   BROWSER    a P-256 keypair this tab generates with WebCrypto and keeps in
//              localStorage. No extension, no chain, no install. The identity
//              is per-device on purpose: it's a door, not a passport.
//   METAMASK   personal_sign — the server recovers the address from the
//              signature, so the page can't lie about who signed.
//   BITTENSOR  any Polkadot-family extension (Talisman, SubWallet,
//              Polkadot{.js}) signing raw bytes with an sr25519 key.
//
// Each connector returns {address, sign(message)} and the sign-in flow above
// doesn't care which one it got.

export type WalletKind = "browser" | "evm" | "bittensor";

export interface Connected {
  kind: WalletKind;
  address: string;
  label: string;
  pubkey?: string;                       // browser keys carry their own
  sign: (message: string) => Promise<string>;
}

declare global {
  interface Window {
    ethereum?: any;
    injectedWeb3?: Record<string, any>;
  }
}

const DEVICE_KEY = "lq_device_key";      // shared origin — namespace everything

// ── browser: a keypair this tab owns ────────────────────────────────

// Built with a loop rather than spread: the tsconfig targets ES5, where
// spreading a typed array needs downlevelIteration, and a base64 helper is not
// worth turning that on for.
function b64u(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function deviceKey(): Promise<CryptoKeyPair & { pubkey: string }> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("this browser has no WebCrypto — use a wallet instead");

  const stored = localStorage.getItem(DEVICE_KEY);
  if (stored) {
    try {
      const { priv, pub } = JSON.parse(stored);
      const algo = { name: "ECDSA", namedCurve: "P-256" };
      const privateKey = await subtle.importKey("jwk", priv, algo, true, ["sign"]);
      const publicKey = await subtle.importKey("jwk", pub, algo, true, ["verify"]);
      const raw = await subtle.exportKey("raw", publicKey);
      return Object.assign({ privateKey, publicKey }, { pubkey: b64u(raw) });
    } catch {
      // A key we can't read is a key we can't sign with; mint a fresh one
      // rather than leaving the visitor stuck on a corrupt entry.
      localStorage.removeItem(DEVICE_KEY);
    }
  }

  const pair = await subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"],
  ) as CryptoKeyPair;
  const [priv, pub, raw] = await Promise.all([
    subtle.exportKey("jwk", pair.privateKey),
    subtle.exportKey("jwk", pair.publicKey),
    subtle.exportKey("raw", pair.publicKey),
  ]);
  localStorage.setItem(DEVICE_KEY, JSON.stringify({ priv, pub }));
  return Object.assign(pair, { pubkey: b64u(raw) });
}

// Matches auth.browser_address on the server: sha256 of the raw public key.
async function fingerprint(pubkey: string): Promise<string> {
  const bytes = Uint8Array.from(
    atob(pubkey.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0),
  );
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  let hex = "";
  for (let i = 0; i < digest.length; i += 1) hex += digest[i].toString(16).padStart(2, "0");
  return `br1${hex.slice(0, 32)}`;
}

export async function connectBrowser(): Promise<Connected> {
  const key = await deviceKey();
  const address = await fingerprint(key.pubkey);
  return {
    kind: "browser",
    address,
    label: "this device",
    pubkey: key.pubkey,
    sign: async (message) => {
      const sig = await crypto.subtle.sign(
        { name: "ECDSA", hash: "SHA-256" }, key.privateKey,
        new TextEncoder().encode(message),
      );
      return b64u(sig);
    },
  };
}

/** Forget this device's key — the next browser sign-in is a new identity. */
export function forgetDevice() {
  try { localStorage.removeItem(DEVICE_KEY); } catch {}
}

// ── MetaMask ────────────────────────────────────────────────────────

export async function connectEvm(): Promise<Connected> {
  const eth = typeof window !== "undefined" ? window.ethereum : undefined;
  if (!eth) throw new Error("no EVM wallet found — install MetaMask, then reload");
  const accounts: string[] = await eth.request({ method: "eth_requestAccounts" });
  const address = accounts?.[0];
  if (!address) throw new Error("MetaMask shared no account");
  return {
    kind: "evm",
    address,
    label: "MetaMask",
    // personal_sign takes (message, address) in that order; the reverse is
    // silently accepted by some wallets and rejected by others.
    sign: (message) => eth.request({ method: "personal_sign", params: [message, address] }),
  };
}

// ── Bittensor / Polkadot extensions ─────────────────────────────────

// Extensions inject themselves after page scripts run, so an immediate read of
// window.injectedWeb3 finds nothing on a cold load.
export function findSubstrateExtensions(waitMs = 1500): Promise<string[]> {
  const t0 = performance.now();
  return new Promise((resolve) => {
    const look = () => {
      const names = Object.keys(window.injectedWeb3 || {});
      if (names.length || performance.now() - t0 > waitMs) resolve(names);
      else setTimeout(look, 150);
    };
    look();
  });
}

export interface SubstrateAccount {
  address: string;
  name?: string;
  type?: string;
}

export async function substrateAccounts(extension: string): Promise<SubstrateAccount[]> {
  const injected = window.injectedWeb3?.[extension];
  if (!injected) throw new Error(`${extension} is not installed`);
  const api = await injected.enable("liquidai");
  const accounts: SubstrateAccount[] = await api.accounts.get();
  if (!accounts.length) throw new Error(`${extension} shared no accounts`);
  return accounts;
}

export async function connectBittensor(
  extension: string, address: string,
): Promise<Connected> {
  const injected = window.injectedWeb3?.[extension];
  if (!injected) throw new Error(`${extension} is not installed`);
  const api = await injected.enable("liquidai");
  return {
    kind: "bittensor",
    address,
    label: extension,
    sign: async (message) => {
      const { signature } = await api.signer.signRaw({
        address, data: message, type: "bytes",
      });
      return signature;
    },
  };
}

/** 5FHneW…8Zk → 5FHn…q8Zk. Addresses are 48 characters nobody reads. */
export function shortAddress(address: string, head = 6, tail = 4): string {
  if (!address) return "";
  return address.length <= head + tail + 1
    ? address : `${address.slice(0, head)}…${address.slice(-tail)}`;
}
