"use client";

/// The browser-wallet execution layer of the desk.
///
/// The API plans, this file signs — with whichever wallet the OPERATION calls
/// for: an EVM operation (vault deposit, Aave/Comet supply, router swap) is
/// signed by window.ethereum; a Solana operation by the injected Phantom
/// provider through Jupiter; a Bittensor operation by no browser wallet at all,
/// because none exists, and the UI says so instead of pretending.
///
/// A quote's `wallet` object is the contract between the two sides: approve /
/// call / swap steps with "$you" for the connected address and "$shares" for
/// its full receipt balance, both resolved here where the wallet lives.

export type WalletStep = {
  action: "approve" | "call" | "swap";
  token?: string;
  spender?: string;
  amount_wei?: string;
  symbol?: string;
  to?: string;
  function?: string;
  args?: any[];
  abi?: any[];
  router?: string;
  wrapped?: string;
  token_in?: string;
  token_out?: string;
  fees?: number[];
  amount_in_wei?: string;
  min_out_wei?: string;
};

export type WalletPlan = {
  available: boolean;
  reason?: string;
  kind?: "evm" | "solana" | "evm-swap";
  chain_id?: number;
  network?: string;
  steps?: WalletStep[];
  // solana
  input_mint?: string;
  output_mint?: string;
  input_decimals?: number;
  amount?: string;
  slippage_bps?: number;
};

export type StepUpdate = { label: string; status: "running" | "done" | "failed"; tx?: string; error?: string };

/// Which wallet an operation on this chain would use, and whether the browser
/// actually has it. The chain of the operation decides — not a setting.
export function walletFor(chain: string | undefined): { kind: "evm" | "solana" | null; available: boolean; label: string } {
  const w = typeof window === "undefined" ? ({} as any) : (window as any);
  if (!chain) return { kind: null, available: false, label: "" };
  if (["ethereum", "base", "sepolia", "base-sepolia", "evm"].includes(chain))
    return { kind: "evm", available: !!w.ethereum, label: w.ethereum ? "browser wallet" : "browser wallet (none found)" };
  if (chain === "solana") {
    const provider = w.phantom?.solana ?? w.solana;
    return { kind: "solana", available: !!provider, label: provider ? "Phantom" : "Phantom (none found)" };
  }
  return { kind: null, available: false, label: "" };
}

/// Chains the wallet may not know yet. Mainnet is never added, only switched to.
const CHAIN_PARAMS: Record<number, any> = {
  8453: { chainName: "Base", rpcUrls: ["https://mainnet.base.org"], nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 }, blockExplorerUrls: ["https://basescan.org"] },
  11155111: { chainName: "Sepolia", rpcUrls: ["https://rpc.sepolia.org"], nativeCurrency: { name: "Sepolia Ether", symbol: "ETH", decimals: 18 }, blockExplorerUrls: ["https://sepolia.etherscan.io"] },
  84532: { chainName: "Base Sepolia", rpcUrls: ["https://sepolia.base.org"], nativeCurrency: { name: "Sepolia Ether", symbol: "ETH", decimals: 18 }, blockExplorerUrls: ["https://sepolia.basescan.org"] },
};

async function ensureEvmChain(ethereum: any, chainId: number) {
  const hex = "0x" + chainId.toString(16);
  const current = await ethereum.request({ method: "eth_chainId" });
  if (parseInt(current, 16) === chainId) return;
  try {
    await ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: hex }] });
  } catch (e: any) {
    // 4902: the wallet has never seen this chain.
    if ((e?.code === 4902 || /unrecognized|not.*added/i.test(e?.message ?? "")) && CHAIN_PARAMS[chainId]) {
      await ethereum.request({ method: "wallet_addEthereumChain", params: [{ chainId: hex, ...CHAIN_PARAMS[chainId] }] });
    } else {
      throw new Error(`switch your wallet to chain ${chainId} first — ${e?.message ?? "the wallet refused"}`);
    }
  }
}

const ERC20 = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function balanceOf(address owner) view returns (uint256)",
];

const ROUTER = [
  "function exactInputSingle((address tokenIn, address tokenOut, uint24 fee, address recipient, uint256 amountIn, uint256 amountOutMinimum, uint160 sqrtPriceLimitX96)) payable returns (uint256)",
  "function exactInput((bytes path, address recipient, uint256 amountIn, uint256 amountOutMinimum)) payable returns (uint256)",
];

function reason(e: any): string {
  return (e?.shortMessage || e?.reason || e?.message || "failed").slice(0, 240);
}

/// Run an EVM wallet plan step by step. Returns the txs that were actually
/// sent — the caller records those, and only those, in the book.
export async function runEvmPlan(
  plan: WalletPlan,
  onStep: (u: StepUpdate) => void
): Promise<{ txs: string[]; owner: string }> {
  const ethereum = (window as any).ethereum;
  if (!ethereum) throw new Error("no browser wallet found — install MetaMask or Rabby");
  const { BrowserProvider, Contract, Interface, solidityPacked } = await import("ethers");
  const provider = new BrowserProvider(ethereum);
  await provider.send("eth_requestAccounts", []);
  if (plan.chain_id) await ensureEvmChain(ethereum, plan.chain_id);
  const signer = await provider.getSigner();
  const owner = await signer.getAddress();
  const txs: string[] = [];

  const resolveArg = async (a: any, to: string): Promise<any> => {
    if (a === "$you") return owner;
    if (a === "$shares") {
      const bal = await new Contract(to, ERC20, provider).balanceOf(owner);
      if (bal === 0n) throw new Error("your wallet holds none of the receipt token — nothing to exit");
      return bal;
    }
    return a;
  };

  for (const step of plan.steps ?? []) {
    const label =
      step.action === "approve"
        ? `approve ${step.symbol ?? "token"} → ${short(step.spender)}`
        : step.action === "swap"
          ? `swap on SwapRouter02 ${short(step.router)}`
          : `${step.function}() on ${short(step.to)}`;
    onStep({ label, status: "running" });
    try {
      if (step.action === "approve") {
        const erc20 = new Contract(step.token!, ERC20, signer);
        const have: bigint = await erc20.allowance(owner, step.spender);
        if (have >= BigInt(step.amount_wei ?? "0")) {
          onStep({ label: `${label} — already allowed`, status: "done" });
          continue;
        }
        const tx = await erc20.approve(step.spender, step.amount_wei);
        txs.push(tx.hash);
        await tx.wait();
      } else if (step.action === "swap") {
        const iface = new Interface(ROUTER);
        const fees = step.fees ?? [];
        const data =
          fees.length >= 2
            ? iface.encodeFunctionData("exactInput", [[
                solidityPacked(
                  ["address", "uint24", "address", "uint24", "address"],
                  [step.token_in, fees[0], step.wrapped, fees[1], step.token_out]
                ),
                owner,
                step.amount_in_wei,
                step.min_out_wei,
              ]])
            : iface.encodeFunctionData("exactInputSingle", [[
                step.token_in, step.token_out, fees[0] ?? 3000, owner,
                step.amount_in_wei, step.min_out_wei, 0,
              ]]);
        const tx = await signer.sendTransaction({ to: step.router, data });
        txs.push(tx.hash);
        await tx.wait();
      } else {
        const args = [];
        for (const a of step.args ?? []) args.push(await resolveArg(a, step.to!));
        const contract = new Contract(step.to!, step.abi ?? [], signer);
        const tx = await contract[step.function!](...args);
        txs.push(tx.hash);
        await tx.wait();
      }
      onStep({ label, status: "done", tx: txs[txs.length - 1] });
    } catch (e: any) {
      onStep({ label, status: "failed", error: reason(e) });
      throw new Error(`${label}: ${reason(e)}${txs.length ? ` (already sent: ${txs.join(", ")})` : ""}`);
    }
  }
  return { txs, owner };
}

/// A Solana operation, signed by the injected wallet: Jupiter prices and builds
/// the transaction for the CONNECTED key, Phantom signs and sends it. This desk
/// and its server never see the transaction.
export async function runSolanaSwap(
  plan: WalletPlan,
  onStep: (u: StepUpdate) => void
): Promise<{ txs: string[]; owner: string }> {
  const provider = (window as any).phantom?.solana ?? (window as any).solana;
  if (!provider) throw new Error("no Solana wallet found — install Phantom");
  if (plan.input_decimals === null || plan.input_decimals === undefined)
    throw new Error("the asset's decimals are not on record — use the server path for this one");
  await provider.connect();
  const owner = provider.publicKey?.toString?.();
  if (!owner) throw new Error("the Solana wallet did not share a public key");

  onStep({ label: "quote the route on Jupiter", status: "running" });
  const amount = toBaseUnits(plan.amount ?? "0", plan.input_decimals);
  const quote = await fetch(
    `https://lite-api.jup.ag/swap/v1/quote?inputMint=${plan.input_mint}&outputMint=${plan.output_mint}` +
      `&amount=${amount}&slippageBps=${plan.slippage_bps ?? 50}`
  ).then((r) => r.json());
  if (!quote?.outAmount) throw new Error(quote?.error ?? "Jupiter could not price the route");
  onStep({ label: "quote the route on Jupiter", status: "done" });

  onStep({ label: "build the transaction for your key", status: "running" });
  const swap = await fetch("https://lite-api.jup.ag/swap/v1/swap", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ quoteResponse: quote, userPublicKey: owner, dynamicComputeUnitLimit: true }),
  }).then((r) => r.json());
  if (!swap?.swapTransaction) throw new Error(swap?.error ?? "Jupiter did not return a transaction");
  onStep({ label: "build the transaction for your key", status: "done" });

  onStep({ label: "sign and send with your wallet", status: "running" });
  const bytes = Uint8Array.from(atob(swap.swapTransaction), (c) => c.charCodeAt(0));
  const { signature } = await provider.request({
    method: "signAndSendTransaction",
    params: { message: base58(bytes) },
  });
  onStep({ label: "sign and send with your wallet", status: "done", tx: signature });
  return { txs: [signature], owner };
}

/// Decimal string × 10^decimals without ever touching a float.
function toBaseUnits(amount: string, decimals: number): string {
  const [whole, frac = ""] = amount.trim().split(".");
  if (!/^\d*$/.test(whole) || !/^\d*$/.test(frac)) throw new Error(`"${amount}" is not a number`);
  const padded = (frac + "0".repeat(decimals)).slice(0, decimals);
  const joined = `${whole || "0"}${padded}`.replace(/^0+(?=\d)/, "");
  return joined;
}

function base58(bytes: Uint8Array): string {
  const ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  const digits = [0];
  for (const byte of bytes) {
    let carry = byte;
    for (let i = 0; i < digits.length; i++) {
      carry += digits[i] << 8;
      digits[i] = carry % 58;
      carry = (carry / 58) | 0;
    }
    while (carry) {
      digits.push(carry % 58);
      carry = (carry / 58) | 0;
    }
  }
  let out = "";
  for (const byte of bytes) {
    if (byte !== 0) break;
    out += "1";
  }
  for (let i = digits.length - 1; i >= 0; i--) out += ALPHABET[digits[i]];
  return out;
}

function short(a?: string): string {
  return a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : (a ?? "");
}
