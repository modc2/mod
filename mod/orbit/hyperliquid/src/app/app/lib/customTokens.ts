// Tokens the user added to the deposit scan — any EVM chain LI.FI routes
// from. Kept in this browser (they're not secret and not per-account); the
// server only reads balances, prices and routes them.

import type { CustomToken } from "./api";

const KEY = "hl.deposit.customTokens";

export function loadCustomTokens(): CustomToken[] {
  if (typeof window === "undefined") return [];
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((t) => t && typeof t.chainId === "number" && typeof t.address === "string") : [];
  } catch { return []; }
}

export function saveCustomTokens(list: CustomToken[]) {
  localStorage.setItem(KEY, JSON.stringify(list.slice(0, 40)));
}

export const tokenKey = (t: { chainId: number; address: string }) =>
  `${t.chainId}:${t.address.toLowerCase()}`;
