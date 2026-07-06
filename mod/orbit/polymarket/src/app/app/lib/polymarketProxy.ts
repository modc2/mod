// Polymarket "Contract Proxy" wallet — a smart-contract wallet deployed at
// a deterministic address derived from the user's EOA. Polymarket's CLOB
// binds every newly-created API key to this proxy, NOT the EOA. So:
//   - USDC must live at the proxy address for balance-allowance to see it
//   - Orders are signed with signatureType=1 (POLY_PROXY), maker=proxy
//   - The proxy is deployed lazily on first trade — funds can be sent to
//     the (yet-undeployed) address ahead of time, ERC-20 transfers don't
//     require the recipient to be a contract.
//
// Factory: 0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b on Polygon
//   - name() = "Polymarket Contract Proxy Factory"
//   - computeProxyAddress(address owner) → address proxy  (selector 0xd600539a)

import { Contract, JsonRpcProvider } from "ethers";
import { networkById, withRpcFallback } from "./networks";

export const POLY_PROXY_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b";

const FACTORY_ABI = [
  "function computeProxyAddress(address owner) view returns (address)",
];

const CACHE_KEY = "poly_proxy_addresses";

function loadCache(): Record<string, string> {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(CACHE_KEY) : null;
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveCache(c: Record<string, string>): void {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(c)); } catch {}
}

/**
 * Resolve the Polymarket Proxy address for an EOA. Pure view call against
 * the factory — deterministic, deploy-free. Cached in localStorage.
 */
export async function getProxyAddress(eoa: string): Promise<string> {
  const key = eoa.toLowerCase();
  const cache = loadCache();
  if (cache[key]) return cache[key];

  const polygon = networkById("polygon")!;
  const proxy = await withRpcFallback(polygon, async (url) => {
    const provider = new JsonRpcProvider(url);
    const factory = new Contract(POLY_PROXY_FACTORY, FACTORY_ABI, provider);
    return (await factory.computeProxyAddress(eoa)) as string;
  });

  cache[key] = proxy;
  saveCache(cache);
  return proxy;
}
