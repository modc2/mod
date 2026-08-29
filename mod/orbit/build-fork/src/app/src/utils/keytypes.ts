/**
 * Key types — the console is multichain, so an identity is a *key*, not an
 * Ethereum account.
 *
 * One place decides, for every signer:
 *   • which curve the key is on          (secp256k1 / ed25519 / sr25519)
 *   • what its address looks like        (0x-hex / base58 / SS58)
 *   • which networks that key reaches    (shown before you sign, not after)
 *   • how to ask its wallet for a signature
 *
 * The server verifies all three curves (see src/api/src/keys.rs) and reports
 * the same catalog at GET /auth/key-types; this file is the browser half.
 */

export type KeyTypeId = "secp256k1" | "ed25519" | "sr25519";

/** How a signer's key is held. Wallet-backed kinds carry a KeyTypeId. */
export type WalletType =
  | "metamask"
  | "subwallet"
  | "phantom"
  | "polkadot"
  | "local"
  | "password"
  | null;

export interface KeyTypeInfo {
  id: KeyTypeId;
  /** Curve name on its own — the badge text. */
  label: string;
  /** The family this key signs for, in one word. */
  family: string;
  /** Shape of an address on this curve, for the "what is this" line. */
  addressFormat: string;
  /** Networks the key can act on. Descriptive: shown so a signer knows the
      reach of the key they are about to prove. */
  networks: string[];
  /** Accent colour — each curve reads as its own thing at a glance. */
  color: string;
}

export const KEY_TYPES: Record<KeyTypeId, KeyTypeInfo> = {
  secp256k1: {
    id: "secp256k1",
    label: "secp256k1",
    family: "EVM",
    addressFormat: "0x + 40 hex",
    networks: ["Ethereum", "Base", "Arbitrum", "Optimism", "Polygon", "BNB Chain", "Avalanche"],
    color: "#627EEA",
  },
  ed25519: {
    id: "ed25519",
    label: "ed25519",
    family: "Solana",
    addressFormat: "base58 (32 bytes)",
    networks: ["Solana", "Eclipse"],
    color: "#9945FF",
  },
  sr25519: {
    id: "sr25519",
    label: "sr25519",
    family: "Substrate",
    addressFormat: "SS58",
    networks: ["Polkadot", "Kusama", "Bittensor", "Substrate parachains"],
    color: "#E6007A",
  },
};

// ── Networks ─────────────────────────────────────────────────────────
//
// Nobody thinks "I would like to prove an sr25519 key". They think "I want to
// act on Base". So the sign-in flow asks for the network first and derives the
// curve from it: the network you pick decides which key types can sign, which
// wallets can hold them, and — for EVM — which chain the wallet is switched to
// once the session is up.
//
// `chainId` values pair with EVM_NETWORKS in utils/wallet.ts (the chain params
// used for wallet_switchEthereumChain / wallet_addEthereumChain live there).

export interface SignInNetwork {
  id: string;
  name: string;
  keyType: KeyTypeId;
  /** EVM only — the chain the wallet is put on after signing. */
  chainId?: number;
  color: string;
  testnet?: boolean;
}

/** Ethereum first: the default network, and the default key. */
export const DEFAULT_NETWORK_ID = "ethereum";

export const SIGN_IN_NETWORKS: SignInNetwork[] = [
  { id: "ethereum",     name: "Ethereum",     keyType: "secp256k1", chainId: 1,        color: "#627EEA" },
  { id: "base",         name: "Base",         keyType: "secp256k1", chainId: 8453,     color: "#0052FF" },
  { id: "arbitrum",     name: "Arbitrum",     keyType: "secp256k1", chainId: 42161,    color: "#28A0F0" },
  { id: "optimism",     name: "Optimism",     keyType: "secp256k1", chainId: 10,       color: "#FF0420" },
  { id: "polygon",      name: "Polygon",      keyType: "secp256k1", chainId: 137,      color: "#8247E5" },
  { id: "bnb",          name: "BNB Chain",    keyType: "secp256k1", chainId: 56,       color: "#F0B90B" },
  { id: "avalanche",    name: "Avalanche",    keyType: "secp256k1", chainId: 43114,    color: "#E84142" },
  { id: "solana",       name: "Solana",       keyType: "ed25519",                      color: "#9945FF" },
  { id: "polkadot",     name: "Polkadot",     keyType: "sr25519",                      color: "#E6007A" },
  { id: "bittensor",    name: "Bittensor",    keyType: "sr25519",                      color: "#00CCB4" },
  { id: "sepolia",      name: "Sepolia",      keyType: "secp256k1", chainId: 11155111, color: "#627EEA", testnet: true },
  { id: "base-sepolia", name: "Base Sepolia", keyType: "secp256k1", chainId: 84532,    color: "#0052FF", testnet: true },
];

/** Which network this browser signed in on last — remembered, not guessed. */
export const NETWORK_STORAGE_KEY = "buildfork_jobs_signin_network";

export function networkById(id: string | null | undefined): SignInNetwork | null {
  return SIGN_IN_NETWORKS.find((n) => n.id === id) || null;
}

/**
 * The curve an address encodes, from its shape alone.
 *
 * Hex with an 0x head is EVM. Everything else is base58, and SS58 is told from
 * a bare Solana pubkey by length: SS58 carries a network prefix and a 2-byte
 * checksum around the 32-byte key, a Solana address is the 32 bytes alone.
 * (The checksum itself is verified server-side — here we only need the shape.)
 */
export function detectKeyType(address: string | null | undefined): KeyTypeId | null {
  const a = (address || "").trim();
  if (!a) return null;
  if (/^0x[0-9a-fA-F]{40}$/.test(a)) return "secp256k1";
  if (!/^[1-9A-HJ-NP-Za-km-z]+$/.test(a)) return null;
  // base58 of 32 bytes is 43–44 chars; of 35–36 bytes (SS58) is 47–50.
  if (a.length >= 46) return "sr25519";
  if (a.length >= 32 && a.length <= 45) return "ed25519";
  return null;
}

/** The key type behind a wallet kind, for identities we minted ourselves. */
export function keyTypeOf(address: string | null | undefined, walletType?: WalletType): KeyTypeId | null {
  if (walletType === "phantom") return "ed25519";
  if (walletType === "polkadot") return "sr25519";
  // local / password / metamask / subwallet all derive an EVM key here.
  const detected = detectKeyType(address);
  if (detected) return detected;
  return walletType === "local" || walletType === "password" ? "secp256k1" : null;
}

export function keyTypeInfo(address: string | null | undefined, walletType?: WalletType): KeyTypeInfo | null {
  const id = keyTypeOf(address, walletType);
  return id ? KEY_TYPES[id] : null;
}

/** Shorten any address format for a chip — base58 has no 0x to anchor on. */
export function shortAddress(address: string, head = 6, tail = 4): string {
  if (!address) return "";
  if (address.length <= head + tail + 1) return address;
  return `${address.slice(0, head)}…${address.slice(-tail)}`;
}

// ── Wallet connectors ────────────────────────────────────────────────
//
// Each returns the address plus a `sign` that produces exactly what the
// server's verifier for that curve expects. None of them pull in an SDK:
// every one of these wallets speaks a plain injected-object protocol, and a
// megabyte of npm to call `signMessage` is a megabyte the console pays for on
// every load.

export interface Connection {
  address: string;
  keyType: KeyTypeId;
  walletType: WalletType;
  sign: (message: string) => Promise<string>;
}

function hexOf(bytes: Uint8Array): string {
  return "0x" + Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** EIP-1193 provider for a specific EVM wallet, when several are injected. */
function evmProvider(prefer?: "metamask" | "subwallet"): any {
  const eth = (window as any).ethereum;
  if (!eth) return null;
  const list: any[] = eth.providers && Array.isArray(eth.providers) ? eth.providers : [eth];
  if (prefer === "subwallet") return list.find((p) => p.isSubWallet) || null;
  if (prefer === "metamask") return list.find((p) => p.isMetaMask && !p.isSubWallet) || list[0];
  return eth;
}

export function hasEvmWallet(prefer?: "metamask" | "subwallet"): boolean {
  return !!evmProvider(prefer);
}

/** Phantom (and any Solana wallet that follows its shape). */
function solanaProvider(): any {
  const w = window as any;
  return w.phantom?.solana || (w.solana?.isPhantom ? w.solana : w.solana) || null;
}

export function hasPhantom(): boolean {
  return !!solanaProvider();
}

/**
 * Substrate extensions announce themselves on `window.injectedWeb3` — the
 * polkadot-js protocol SubWallet, Talisman and polkadot-js all implement.
 */
function substrateExtensions(): string[] {
  const injected = (window as any).injectedWeb3;
  return injected ? Object.keys(injected) : [];
}

export function hasSubstrateWallet(): boolean {
  return substrateExtensions().length > 0;
}

/** Friendly name of the Substrate extension we'd connect to. */
export function substrateWalletName(): string | null {
  const keys = substrateExtensions();
  if (!keys.length) return null;
  const pretty: Record<string, string> = {
    "subwallet-js": "SubWallet",
    talisman: "Talisman",
    "polkadot-js": "polkadot.js",
    enkrypt: "Enkrypt",
  };
  const preferred = ["subwallet-js", "talisman", "polkadot-js"].find((k) => keys.includes(k));
  const key = preferred || keys[0];
  return pretty[key] || key;
}

export async function connectEvm(prefer: "metamask" | "subwallet"): Promise<Connection> {
  const provider = evmProvider(prefer);
  if (!provider) throw new Error(prefer === "subwallet" ? "SUBWALLET NOT FOUND" : "NO BROWSER WALLET FOUND");
  const accounts: string[] = await provider.request({ method: "eth_requestAccounts" });
  if (!accounts?.length) throw new Error("NO ACCOUNTS FOUND");
  const address = accounts[0].toLowerCase();
  return {
    address,
    keyType: "secp256k1",
    walletType: prefer,
    sign: (message) => provider.request({ method: "personal_sign", params: [message, address] }),
  };
}

export async function connectPhantom(): Promise<Connection> {
  const provider = solanaProvider();
  if (!provider) throw new Error("PHANTOM NOT FOUND — install it, then reload");
  const resp = await provider.connect();
  // Phantom returns a PublicKey object; base58 is its toString().
  const address: string = (resp?.publicKey || provider.publicKey)?.toString();
  if (!address) throw new Error("NO SOLANA ACCOUNT FOUND");
  return {
    address,
    keyType: "ed25519",
    walletType: "phantom",
    sign: async (message) => {
      // Phantom signs raw bytes — the server verifies the same UTF-8 bytes.
      const encoded = new TextEncoder().encode(message);
      const signed = await provider.signMessage(encoded, "utf8");
      const sig: Uint8Array = signed?.signature || signed;
      return hexOf(sig instanceof Uint8Array ? sig : new Uint8Array(sig));
    },
  };
}

export async function connectSubstrate(): Promise<Connection> {
  const injected = (window as any).injectedWeb3;
  const keys = substrateExtensions();
  if (!keys.length) throw new Error("NO SUBSTRATE WALLET FOUND — install SubWallet or Talisman");
  const key = ["subwallet-js", "talisman", "polkadot-js"].find((k) => keys.includes(k)) || keys[0];
  const ext = await injected[key].enable("Build ✦ Orbit Console");
  const accounts = await ext.accounts.get();
  if (!accounts?.length) throw new Error("NO SUBSTRATE ACCOUNTS — unlock the extension and allow this site");
  // Prefer an sr25519 account; extensions report the curve per account.
  const account = accounts.find((a: any) => !a.type || a.type === "sr25519") || accounts[0];
  const address: string = account.address;
  const keyType: KeyTypeId = account.type === "ed25519" ? "ed25519" : "sr25519";
  return {
    address,
    keyType,
    walletType: "polkadot",
    sign: async (message) => {
      // signRaw wraps the payload in <Bytes>…</Bytes> before signing; the
      // server checks the wrapped form first for exactly this reason.
      const { signature } = await ext.signer.signRaw({
        address,
        data: message,
        type: "bytes",
      });
      return signature;
    },
  };
}
