// x402 paid-path helper. When a user has no BYOK key, chat requests hit the
// gateway's 402 challenge; `x402-fetch` reads it, signs an on-chain payment
// authorization with the user's wallet, and retries with the X-PAYMENT header.
//
// Everything is dynamically imported so the (heavy) viem / x402 deps never
// run during SSR and don't break the BYOK-only build if absent.

const CHAINS: Record<string, () => Promise<unknown>> = {
  base: async () => (await import("viem/chains")).base,
  "base-sepolia": async () => (await import("viem/chains")).baseSepolia,
};

/**
 * Build a payment-wrapped fetch bound to the connected wallet. Returns a
 * function with the same signature as `fetch`. Throws a friendly error if the
 * wallet or libraries aren't available.
 */
export async function makePaidFetch(
  address: string,
  network: string
): Promise<typeof fetch> {
  if (typeof window === "undefined" || !window.ethereum) {
    throw new Error("A browser wallet is required to pay per message");
  }
  // Typed as `any` because these are heavy deps resolved at runtime; the exact
  // generic signatures vary across x402-fetch / viem minor versions.
  /* eslint-disable @typescript-eslint/no-explicit-any */
  let mod: any;
  let viem: any;
  try {
    mod = await import("x402-fetch");
    viem = await import("viem");
  } catch {
    throw new Error("payment libraries unavailable — use bring-your-own-key");
  }

  const chainFactory = CHAINS[network] || CHAINS.base;
  const chain = await chainFactory();

  const walletClient = viem.createWalletClient({
    account: address as `0x${string}`,
    chain,
    transport: viem.custom(window.ethereum),
  });

  // x402-fetch needs the global fetch bound to window to avoid Illegal invocation.
  return mod.wrapFetchWithPayment(window.fetch.bind(window), walletClient) as typeof fetch;
  /* eslint-enable @typescript-eslint/no-explicit-any */
}
