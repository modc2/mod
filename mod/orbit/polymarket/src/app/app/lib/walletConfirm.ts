// MetaMask confirmation for money moves on the COPY DESK.
//
// Every change to who is copied and with how much — add a trader, put more
// dollars behind one, take dollars off one, drop one from the book, rebalance
// the lot — is confirmed by the wallet, not by a browser dialog: the owner
// personal_signs a plain-English description of the exact change. Rejecting
// the MetaMask prompt cancels the change; nothing is posted.
//
// This is a CONFIRMATION, not authentication (the Bearer token from the
// access gate is what authorizes the request) and not a transaction: no
// transaction is sent and no gas is spent. The point is that the same wallet
// that funds the desk approves each change to it, with the change spelled
// out in the prompt where a misclick can still be read and refused.
//
// Same signing path as the gate (lib/access.ts): BrowserProvider over
// window.ethereum, signer picked by the token's owner address.

import { BrowserProvider } from "ethers";
import { getOwnerAddress } from "./access";

/** The owner looked at the MetaMask prompt and pressed Reject. A valid
    answer, not a failure — callers drop the change without an error banner. */
export class WalletDeclinedError extends Error {
  constructor() {
    super("change declined in the wallet");
    this.name = "WalletDeclinedError";
  }
}

export interface WalletConfirmation {
  address: string;
  message: string;
  signature: string;
  signedAt: number;
}

function isUserRejection(e: unknown): boolean {
  const err = e as { code?: unknown; message?: unknown; info?: { error?: { code?: unknown } } };
  return (
    err?.code === "ACTION_REJECTED" || // ethers v6
    err?.code === 4001 || // EIP-1193
    err?.info?.error?.code === 4001 ||
    /rejected|denied/i.test(String(err?.message ?? ""))
  );
}

/**
 * Put the change in front of MetaMask and wait for a signature.
 *
 * `lines` describe the one change being made ("Add $25.00 to COPY 0xab…cd",
 * "Allocation: $50.00 → $75.00"). Resolves with the signature once approved;
 * throws `WalletDeclinedError` on Reject, a readable Error on anything else
 * (no wallet, wrong account selected).
 */
export async function confirmWithWallet(lines: string[]): Promise<WalletConfirmation> {
  const address = getOwnerAddress();
  if (!address) {
    throw new Error("not signed in — the gate's owner token is what names the confirming wallet");
  }
  const signedAt = Date.now();
  const message = [
    "POLYMARKET COPY DESK — CONFIRM CHANGE",
    "",
    ...lines,
    "",
    "Signing approves this one change to the copy desk.",
    "No transaction is sent and no gas is spent.",
    `Wallet: ${address}`,
    `Time: ${new Date(signedAt).toISOString()}`,
  ].join("\n");
  const { signature } = await signAsOwner(message);
  return { address, message, signature, signedAt };
}

/**
 * Low-level: personal_sign `message` with the gate's owner wallet, mapping
 * wallet errors to the desk's vocabulary (Reject → WalletDeclinedError,
 * wrong account → a readable instruction). Shared by the confirmation prompt
 * above and by the server-VERIFIED signed actions (lib/copyBook.ts
 * `signedCopyAction`), so both paths fail the same way in the UI.
 */
export async function signAsOwner(
  message: string,
): Promise<{ address: string; signature: string }> {
  if (typeof window === "undefined" || !window.ethereum) {
    throw new Error("NO WALLET DETECTED — MetaMask is needed to confirm changes to the desk");
  }
  const address = getOwnerAddress();
  if (!address) {
    throw new Error("not signed in — the gate's owner token is what names the confirming wallet");
  }
  const provider = new BrowserProvider(window.ethereum as never);
  try {
    const signer = await provider.getSigner(address);
    return { address, signature: await signer.signMessage(message) };
  } catch (e) {
    if (isUserRejection(e)) throw new WalletDeclinedError();
    const msg = String((e as Error)?.message ?? e);
    if (/unknown account|does not support|account/i.test(msg)) {
      throw new Error(
        `MetaMask doesn't have the owner wallet ${address.slice(0, 6)}…${address.slice(-4)} selected — switch accounts and retry`,
      );
    }
    throw new Error(`wallet confirmation failed: ${msg}`);
  }
}

/** "$25", "$25.50" — the amounts as the prompt spells them. */
export function promptUsd(v: number): string {
  const digits = Math.abs(v % 1) < 0.005 ? 0 : 2;
  return `$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

/** The lines for an allocation change, phrased as the delta the user meant
    ("Add $25 to X", "Take $10 off X", "Set X to $40") — not the raw upsert. */
export function describeAllocationChange(
  name: string,
  currentUsd: number | null,
  nextUsd: number,
): string[] {
  if (currentUsd === null) {
    return [`Add ${name} to the copy desk with ${promptUsd(nextUsd)}`];
  }
  const delta = nextUsd - currentUsd;
  const head =
    Math.abs(delta) < 0.005
      ? `Keep ${name} at ${promptUsd(nextUsd)}`
      : delta > 0
        ? `Add ${promptUsd(delta)} to ${name}`
        : `Take ${promptUsd(delta)} off ${name}`;
  return [head, `Allocation: ${promptUsd(currentUsd)} → ${promptUsd(nextUsd)}`];
}
