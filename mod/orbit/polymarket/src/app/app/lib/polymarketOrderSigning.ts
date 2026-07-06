// EIP-712 signing for Polymarket CLOB orders. Mirrors the @polymarket/order-
// utils package — we implement it inline to avoid pulling in the viem-based
// SDK alongside our ethers v6 stack.
//
// Order amounts are denominated in USDC base units (10^6). Polymarket
// outcome shares share the same 10^6 decimal convention as USDC for the
// CLOB matcher, even though the on-chain CTF token is 18 decimals — the
// exchange does the conversion at settle time.

import type { JsonRpcSigner } from "ethers";
import { POLYGON_CHAIN_ID, exchangeFor } from "./polymarketContracts";

export type OrderSide = "BUY" | "SELL";
export type OrderType = "GTC" | "FOK" | "GTD" | "FAK";

// Polymarket order struct fields as serialized to JSON for /order POST and
// for EIP-712 signing. All numeric amounts are *stringified* base-unit
// integers — Polymarket rejects floats.
export interface SignedOrder {
  salt: string;
  maker: string;
  signer: string;
  taker: string;
  tokenId: string;
  makerAmount: string;
  takerAmount: string;
  expiration: string;
  nonce: string;
  feeRateBps: string;
  side: 0 | 1;            // 0 BUY, 1 SELL
  signatureType: 0 | 1 | 2; // 0 EOA, 1 POLY_PROXY, 2 POLY_GNOSIS_SAFE
  signature: string;
}

import type { TypedDataField } from "ethers";

const ORDER_TYPES: Record<string, TypedDataField[]> = {
  Order: [
    { name: "salt", type: "uint256" },
    { name: "maker", type: "address" },
    { name: "signer", type: "address" },
    { name: "taker", type: "address" },
    { name: "tokenId", type: "uint256" },
    { name: "makerAmount", type: "uint256" },
    { name: "takerAmount", type: "uint256" },
    { name: "expiration", type: "uint256" },
    { name: "nonce", type: "uint256" },
    { name: "feeRateBps", type: "uint256" },
    { name: "side", type: "uint8" },
    { name: "signatureType", type: "uint8" },
  ],
};

function randomSalt(): bigint {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let hex = "0x";
  for (const b of bytes) hex += b.toString(16).padStart(2, "0");
  return BigInt(hex);
}

// Polymarket rounds price to 2 decimals and size to 2 decimals before
// computing base-unit amounts. Matches py-clob-client's `round_normal` /
// `round_down` behavior so signed amounts agree with the server's.
function round2(x: number, mode: "down" | "normal" = "normal"): number {
  const f = 100;
  return mode === "down" ? Math.floor(x * f) / f : Math.round(x * f) / f;
}

function toBaseUnits(amount: number): bigint {
  // Use string round-trip to avoid float imprecision at 6 decimals.
  const fixed = amount.toFixed(6);
  return BigInt(fixed.replace(".", ""));
}

interface BuildArgs {
  tokenId: string;
  side: OrderSide;
  /** Decimal price in [0, 1]. */
  price: number;
  /** Decimal share size. */
  size: number;
  feeRateBps: number;
  /** Unix seconds; 0 for GTC orders. */
  expirationSec?: number;
  /** Polymarket cancellation nonce. Defaults to 0. */
  nonce?: bigint;
  /** 0=EOA, 1=POLY_PROXY (Maker-style), 2=POLY_GNOSIS_SAFE. Defaults to 2
   *  since Polymarket's Contract Proxy Factory deploys Gnosis Safes and
   *  that's what MetaMask-connected users end up bound to. */
  signatureType?: 0 | 1 | 2;
}

interface OrderAmounts {
  makerAmount: bigint;
  takerAmount: bigint;
}

function orderAmounts(side: OrderSide, price: number, size: number): OrderAmounts {
  const p = round2(price);
  const s = round2(size, "down");
  if (side === "BUY") {
    // maker pays USDC, takes shares
    const usdc = round2(s * p, "down");
    return { makerAmount: toBaseUnits(usdc), takerAmount: toBaseUnits(s) };
  } else {
    // maker gives shares, takes USDC
    const usdc = round2(s * p, "down");
    return { makerAmount: toBaseUnits(s), takerAmount: toBaseUnits(usdc) };
  }
}

/**
 * Build and EIP-712 sign a Polymarket order via the connected wallet. The
 * caller is responsible for ensuring the wallet is on Polygon.
 *
 * For proxy users (`signatureType=1`): `maker` is the proxy address, while
 * the EIP-712 signature comes from the EOA (`signer`). The exchange
 * verifies the signature against the EOA and pulls funds from the proxy.
 */
export async function signOrder(
  signer: JsonRpcSigner,
  maker: string,
  args: BuildArgs,
  negRisk: boolean,
): Promise<SignedOrder> {
  const { exchange, domainName } = exchangeFor(negRisk);
  const { makerAmount, takerAmount } = orderAmounts(args.side, args.price, args.size);
  const sigType = (args.signatureType ?? 2) as 0 | 1 | 2;
  const eoaSigner = await signer.getAddress();

  const order = {
    salt: randomSalt(),
    maker,
    // For proxy/safe modes the signer is the EOA, not the funder. For EOA
    // mode they collapse to the same address.
    signer: sigType === 0 ? maker : eoaSigner,
    taker: "0x0000000000000000000000000000000000000000",
    tokenId: BigInt(args.tokenId),
    makerAmount,
    takerAmount,
    expiration: BigInt(args.expirationSec ?? 0),
    nonce: args.nonce ?? BigInt(0),
    feeRateBps: BigInt(args.feeRateBps),
    side: (args.side === "BUY" ? 0 : 1) as 0 | 1,
    signatureType: sigType,
  };

  const domain = {
    name: domainName,
    version: "1",
    chainId: POLYGON_CHAIN_ID,
    verifyingContract: exchange,
  };

  const signature = await signer.signTypedData(domain, ORDER_TYPES, order);

  return {
    salt: order.salt.toString(),
    maker: order.maker,
    signer: order.signer,
    taker: order.taker,
    tokenId: order.tokenId.toString(),
    makerAmount: order.makerAmount.toString(),
    takerAmount: order.takerAmount.toString(),
    expiration: order.expiration.toString(),
    nonce: order.nonce.toString(),
    feeRateBps: order.feeRateBps.toString(),
    side: order.side,
    signatureType: order.signatureType as 0 | 1 | 2,
    signature,
  };
}
