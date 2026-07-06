/* LiFi bridge — direct REST integration so we don't pull in the v3 SDK
 * (which depends on viem and would conflict with our ethers v6 stack).
 *
 * Flow per cross-chain send:
 *   1. POST /v1/quote — single best route (1 tx after approval)
 *   2. If ERC-20 source, set allowance to LiFi's approvalAddress
 *   3. Send the route's transactionRequest via the connected signer
 *   4. Poll /v1/status until DONE / FAILED (best-effort; user can leave)
 *
 * Native source uses the 0x0000…0000 sentinel from networks.NATIVE_TOKEN_ADDRESS.
 * For ERC-20 sources we always re-check allowance — never blindly approve max.
 */

import { Contract, JsonRpcSigner } from "ethers";
import { NATIVE_TOKEN_ADDRESS } from "./networks";

const LIFI_API = "https://li.quest/v1";
const INTEGRATOR = "polymarket-8bit";

const ERC20_APPROVE_ABI = [
  "function allowance(address owner, address spender) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
];

export interface LifiQuoteParams {
  fromChain: number;
  fromToken: string;       // 0x0…0 for native asset
  toChain: number;
  toToken: string;
  fromAmount: string;      // string of base units (avoids precision loss)
  fromAddress: string;
  toAddress: string;
  slippage?: number;       // default 0.03 (3%)
}

interface LifiTokenInfo {
  address: string;
  symbol: string;
  decimals: number;
  chainId: number;
}

export interface LifiQuote {
  id: string;
  type: string;
  tool: string;
  toolDetails: { key: string; name: string; logoURI?: string };
  action: {
    fromChainId: number;
    toChainId: number;
    fromToken: LifiTokenInfo;
    toToken: LifiTokenInfo;
    fromAmount: string;
    fromAddress: string;
    toAddress: string;
    slippage: number;
  };
  estimate: {
    fromAmount: string;
    toAmount: string;
    toAmountMin: string;
    approvalAddress: string;
    executionDuration: number;        // seconds
    feeCosts?: Array<{ amount: string; amountUSD?: string; token: LifiTokenInfo; name?: string }>;
    gasCosts?: Array<{ amount: string; amountUSD?: string; token: LifiTokenInfo }>;
  };
  transactionRequest: {
    to: string;
    data: string;
    value?: string;
    chainId: number;
    gasLimit?: string;
    gasPrice?: string;
  };
}

export async function getLifiQuote(params: LifiQuoteParams): Promise<LifiQuote> {
  const qs = new URLSearchParams({
    fromChain: String(params.fromChain),
    fromToken: params.fromToken,
    toChain: String(params.toChain),
    toToken: params.toToken,
    fromAmount: params.fromAmount,
    fromAddress: params.fromAddress,
    toAddress: params.toAddress,
    integrator: INTEGRATOR,
    ...(params.slippage !== undefined ? { slippage: String(params.slippage) } : {}),
  });
  const res = await fetch(`${LIFI_API}/quote?${qs.toString()}`);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    // LiFi returns JSON with a `message` field on errors. Surface it.
    let msg = `${res.status}`;
    try {
      const j = JSON.parse(body);
      if (j.message) msg += `: ${j.message}`;
      else msg += `: ${body.slice(0, 200)}`;
    } catch {
      msg += `: ${body.slice(0, 200)}`;
    }
    throw new Error(`LiFi quote ${msg}`);
  }
  return res.json();
}

export type LifiStatusKind = "NOT_FOUND" | "INVALID" | "PENDING" | "DONE" | "FAILED";

export interface LifiStatus {
  status: LifiStatusKind;
  substatus?: string;
  substatusMessage?: string;
  sending?: { txHash?: string; chainId?: number };
  receiving?: { txHash?: string; chainId?: number; amount?: string };
}

export async function getLifiStatus(args: {
  txHash: string;
  fromChain: number;
  toChain: number;
  bridge?: string;
}): Promise<LifiStatus> {
  const qs = new URLSearchParams({
    txHash: args.txHash,
    fromChain: String(args.fromChain),
    toChain: String(args.toChain),
    ...(args.bridge ? { bridge: args.bridge } : {}),
  });
  const res = await fetch(`${LIFI_API}/status?${qs.toString()}`);
  if (!res.ok) {
    return { status: "NOT_FOUND" };
  }
  return res.json();
}

export interface ExecuteOpts {
  onProgress?: (msg: string) => void;
  /** Total polling window in ms after the source tx confirms. Default 5 min. */
  pollTimeoutMs?: number;
  pollIntervalMs?: number;
}

export interface ExecuteResult {
  /** Source-chain tx hash (always returned even on dest timeout). */
  txHash: string;
  /** Final LiFi status; undefined if polling timed out. */
  finalStatus?: LifiStatus;
}

export async function executeLifiBridge(
  signer: JsonRpcSigner,
  quote: LifiQuote,
  opts: ExecuteOpts = {},
): Promise<ExecuteResult> {
  const { onProgress, pollTimeoutMs = 5 * 60_000, pollIntervalMs = 5_000 } = opts;
  const isNative = quote.action.fromToken.address.toLowerCase() === NATIVE_TOKEN_ADDRESS;

  // ── ERC-20 approval (skip for native) ─────────────────────────
  if (!isNative) {
    onProgress?.("CHECKING ALLOWANCE...");
    const erc20 = new Contract(quote.action.fromToken.address, ERC20_APPROVE_ABI, signer);
    const fromAddr = await signer.getAddress();
    const required = BigInt(quote.action.fromAmount);
    const current: bigint = await erc20.allowance(fromAddr, quote.estimate.approvalAddress);
    if (current < required) {
      onProgress?.(`APPROVE ${quote.action.fromToken.symbol} IN WALLET...`);
      const ax = await erc20.approve(quote.estimate.approvalAddress, required);
      onProgress?.(`APPROVE TX ${ax.hash.slice(0, 10)}... CONFIRMING`);
      await ax.wait();
    }
  }

  // ── Bridge tx ─────────────────────────────────────────────────
  onProgress?.(`CONFIRM BRIDGE VIA ${quote.toolDetails.name.toUpperCase()}...`);
  const tx = await signer.sendTransaction({
    to: quote.transactionRequest.to,
    data: quote.transactionRequest.data,
    value: quote.transactionRequest.value ? BigInt(quote.transactionRequest.value) : undefined,
    gasLimit: quote.transactionRequest.gasLimit ? BigInt(quote.transactionRequest.gasLimit) : undefined,
  });
  onProgress?.(`SOURCE TX ${tx.hash.slice(0, 10)}... — WAITING SOURCE CONFIRM`);
  await tx.wait();

  // ── Poll destination status ──────────────────────────────────
  const deadline = Date.now() + pollTimeoutMs;
  let lastStatus: LifiStatus | undefined;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, pollIntervalMs));
    try {
      lastStatus = await getLifiStatus({
        txHash: tx.hash,
        fromChain: quote.action.fromChainId,
        toChain: quote.action.toChainId,
        bridge: quote.tool,
      });
    } catch {
      continue;
    }
    if (lastStatus.status === "DONE") {
      const recv = lastStatus.receiving?.amount;
      const dstDecimals = quote.action.toToken.decimals;
      const recvStr = recv
        ? `${(Number(recv) / 10 ** dstDecimals).toFixed(2)} ${quote.action.toToken.symbol}`
        : "complete";
      onProgress?.(`BRIDGED — RECEIVED ${recvStr} ON DEST`);
      return { txHash: tx.hash, finalStatus: lastStatus };
    }
    if (lastStatus.status === "FAILED") {
      throw new Error(`Bridge failed: ${lastStatus.substatusMessage || lastStatus.substatus || "unknown"}`);
    }
    onProgress?.(`PENDING ${lastStatus.substatus || "..."} (${lastStatus.substatusMessage || "waiting for destination"})`);
  }

  // Polling timed out — source tx is on chain, destination is in flight.
  onProgress?.("BRIDGED ON SOURCE — DESTINATION STILL IN FLIGHT (CHECK LATER)");
  return { txHash: tx.hash, finalStatus: lastStatus };
}
