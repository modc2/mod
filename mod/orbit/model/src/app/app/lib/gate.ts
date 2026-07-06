"use client";

import { BrowserProvider, Contract, JsonRpcProvider } from "ethers";

export const GATE_ABI = [
  "function allowed(address) view returns (bool)",
  "function dailyQuota(address) view returns (uint64)",
  "function nonce(address) view returns (uint64)",
  "function status(address) view returns (bool, uint64, uint64)",
  "function challenge(address, uint64) view returns (string)",
] as const;

export const GATE_ADDRESS = process.env.NEXT_PUBLIC_MODEL_GATE_ADDRESS || "";
export const GATE_CHAIN_ID = Number(process.env.NEXT_PUBLIC_MODEL_GATE_CHAIN_ID || "84532");
const BASE_SEPOLIA_RPC = "https://sepolia.base.org";

declare global {
  interface Window { ethereum?: any }
}

export function hasWallet(): boolean {
  return typeof window !== "undefined" && !!window.ethereum;
}

export async function connectWallet(): Promise<string> {
  if (!hasWallet()) throw new Error("No wallet detected — install MetaMask or similar.");
  const provider = new BrowserProvider(window.ethereum);
  const accounts = await provider.send("eth_requestAccounts", []);
  if (!accounts?.length) throw new Error("No account returned");
  return accounts[0] as string;
}

export async function readGate(address: string): Promise<{ allowed: boolean; quota: bigint; nonce: bigint }> {
  if (!GATE_ADDRESS) {
    // No gate configured — fail open so dev still works locally.
    return { allowed: true, quota: BigInt(0), nonce: BigInt(0) };
  }
  const rpc = new JsonRpcProvider(BASE_SEPOLIA_RPC);
  const c = new Contract(GATE_ADDRESS, GATE_ABI, rpc);
  const [allowed, quota, nonce] = await c.status(address);
  return { allowed: Boolean(allowed), quota: BigInt(quota), nonce: BigInt(nonce) };
}

export async function fetchChallenge(address: string, issuedAt: number): Promise<string> {
  if (!GATE_ADDRESS) {
    // Local-dev fallback: build the same shape the contract would emit.
    return `mod-model-gate v1\naddress: ${address.toLowerCase()}\nnonce: 0\nissuedAt: ${issuedAt}`;
  }
  const rpc = new JsonRpcProvider(BASE_SEPOLIA_RPC);
  const c = new Contract(GATE_ADDRESS, GATE_ABI, rpc);
  return (await c.challenge(address, BigInt(issuedAt))) as string;
}

export async function signChallenge(challenge: string): Promise<string> {
  if (!hasWallet()) throw new Error("No wallet detected");
  const provider = new BrowserProvider(window.ethereum);
  const signer = await provider.getSigner();
  return await signer.signMessage(challenge);
}

const SESSION_KEY = "model_gate_session";

export interface GateSession {
  address: string;
  challenge: string;
  signature: string;
  issuedAt: number;
}

export function loadSession(): GateSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as GateSession;
    // Sessions expire after 24h.
    if (Date.now() / 1000 - s.issuedAt > 86400) return null;
    return s;
  } catch {
    return null;
  }
}

export function saveSession(s: GateSession): void {
  localStorage.setItem(SESSION_KEY, JSON.stringify(s));
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_KEY);
}
