// Funded-USDC helpers — one shared definition of "how much money does this
// wallet have in its trading account" so every surface (wallet switcher,
// boot-time default-account pick) sums the same three balances the backend
// reports: CLOB collateral + native USDC + un-deposited USDC.e.

import { API_BASE } from "./polymarket";
import { installAccessFetch } from "./access";

/** Total funded USDC in dollars for an EOA's deposit wallet, or null when
 *  unavailable (gate locked, API down, wallet unresolvable). */
export async function fundedUsd(address: string): Promise<number | null> {
  // The endpoint is behind the owner gate — make sure the token rides along
  // even when we're called before AccessGate has mounted.
  installAccessFetch();
  try {
    const r = await fetch(
      `${API_BASE}/deposit-wallet/info?eoa=${encodeURIComponent(address)}`,
      { cache: "no-store" },
    );
    if (!r.ok) return null;
    const j = (await r.json()) as {
      usdcBalance?: string;
      nativeUsdcBalance?: string;
      rawUsdceBalance?: string;
      balanceUnavailable?: boolean;
    };
    if (j.balanceUnavailable) return null;
    const dollars =
      (Number(j.usdcBalance ?? 0) +
        Number(j.nativeUsdcBalance ?? 0) +
        Number(j.rawUsdceBalance ?? 0)) /
      1e6;
    return Number.isFinite(dollars) ? dollars : null;
  } catch {
    return null;
  }
}

/** The address holding the most funded capital, or null when no balance could
 *  be read at all — callers keep their own fallback in that case. */
export async function pickMostFunded(addresses: string[]): Promise<string | null> {
  const balances = await Promise.all(addresses.map(fundedUsd));
  let best: string | null = null;
  let bestUsd = -1;
  addresses.forEach((a, i) => {
    const v = balances[i];
    if (v !== null && v > bestUsd) {
      best = a;
      bestUsd = v;
    }
  });
  return best;
}
