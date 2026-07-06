// One-time on-chain approvals required before any Polymarket order can
// settle. Without these, the CLOB's `balance-allowance` endpoint returns 0
// even when the user holds USDC.e — because the matching engine pulls USDC
// (and CTF shares for sells) from the user's wallet at fill time via the
// exchange contracts.
//
// We approve max-uint for both the standard CTF Exchange and the NegRisk
// Exchange + Adapter, since the user's strategy may copy trades from either
// type of market. Six approvals total in the worst case (3 USDC + 3 CTF),
// but the helper skips any that are already in place.

import { BrowserProvider, Contract, JsonRpcProvider, MaxUint256 } from "ethers";
import {
  CTF,
  CTF_ABI,
  ERC20_ABI,
  POLYGON_CHAIN_ID,
  SPENDERS_CTF,
  SPENDERS_USDC,
  USDC_E,
} from "./polymarketContracts";
import { networkById, ensureChain, withRpcFallback } from "./networks";

export interface ApprovalStatus {
  /** Per-spender USDC.e allowance (in base units). */
  usdc: Record<string, bigint>;
  /** Per-spender CTF setApprovalForAll flag. */
  ctf: Record<string, boolean>;
  /** True when every required approval is in place. */
  allApproved: boolean;
}

// Threshold below which we consider an allowance "not approved." Polymarket
// approves max-uint by convention, so any value less than 2^128 likely means
// it was never set (or was revoked). Tighter than checking !== 0 to detect
// dust/legacy approvals that won't survive a large trade.
const APPROVAL_THRESHOLD = BigInt(1) << BigInt(128);

/** Read all required approvals at once. Read-only — uses public RPC. */
export async function readApprovals(owner: string): Promise<ApprovalStatus> {
  const polygon = networkById("polygon")!;

  const usdc: Record<string, bigint> = {};
  const ctf: Record<string, boolean> = {};

  await Promise.all([
    ...SPENDERS_USDC.map(async (spender) => {
      try {
        usdc[spender] = await withRpcFallback(polygon, async (url) => {
          const provider = new JsonRpcProvider(url);
          const c = new Contract(USDC_E, ERC20_ABI, provider);
          return (await c.allowance(owner, spender)) as bigint;
        });
      } catch {
        usdc[spender] = BigInt(0);
      }
    }),
    ...SPENDERS_CTF.map(async (spender) => {
      try {
        ctf[spender] = await withRpcFallback(polygon, async (url) => {
          const provider = new JsonRpcProvider(url);
          const c = new Contract(CTF, CTF_ABI, provider);
          return (await c.isApprovedForAll(owner, spender)) as boolean;
        });
      } catch {
        ctf[spender] = false;
      }
    }),
  ]);

  const allApproved =
    SPENDERS_USDC.every((s) => (usdc[s] ?? BigInt(0)) >= APPROVAL_THRESHOLD) &&
    SPENDERS_CTF.every((s) => ctf[s] === true);
  return { usdc, ctf, allApproved };
}

export interface ApprovalProgress {
  step: number;
  total: number;
  label: string;
}

/**
 * Run any missing approvals against the user's wallet. Returns the updated
 * status. Reuses the passed status to skip approvals already in place. The
 * caller should switch the wallet to Polygon before invoking — we double-
 * check via ensureChain.
 */
export async function executeApprovals(
  ethereum: { request: (a: { method: string; params?: unknown[] }) => Promise<unknown> },
  owner: string,
  status: ApprovalStatus,
  onProgress?: (p: ApprovalProgress) => void,
): Promise<ApprovalStatus> {
  const polygon = networkById("polygon")!;
  await ensureChain(ethereum, polygon);

  const provider = new BrowserProvider(ethereum as never);
  const network = await provider.getNetwork();
  if (Number(network.chainId) !== POLYGON_CHAIN_ID) {
    throw new Error(`Wallet not on Polygon (chainId ${network.chainId})`);
  }
  const signer = await provider.getSigner(owner);

  // Collect the list of approvals that actually need to run, so we can
  // report meaningful step/total progress to the user.
  type Step =
    | { kind: "usdc"; spender: string }
    | { kind: "ctf"; spender: string };
  const steps: Step[] = [];
  for (const s of SPENDERS_USDC) {
    if ((status.usdc[s] ?? BigInt(0)) < APPROVAL_THRESHOLD) steps.push({ kind: "usdc", spender: s });
  }
  for (const s of SPENDERS_CTF) {
    if (!status.ctf[s]) steps.push({ kind: "ctf", spender: s });
  }

  const total = steps.length;
  if (total === 0) return status;

  const usdcContract = new Contract(USDC_E, ERC20_ABI, signer);
  const ctfContract = new Contract(CTF, CTF_ABI, signer);

  const updated: ApprovalStatus = {
    usdc: { ...status.usdc },
    ctf: { ...status.ctf },
    allApproved: false,
  };

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    onProgress?.({
      step: i + 1,
      total,
      label:
        step.kind === "usdc"
          ? `APPROVE USDC → ${shortAddr(step.spender)}`
          : `APPROVE SHARES → ${shortAddr(step.spender)}`,
    });

    if (step.kind === "usdc") {
      const tx = await usdcContract.approve(step.spender, MaxUint256);
      await tx.wait();
      updated.usdc[step.spender] = MaxUint256;
    } else {
      const tx = await ctfContract.setApprovalForAll(step.spender, true);
      await tx.wait();
      updated.ctf[step.spender] = true;
    }
  }

  updated.allApproved =
    SPENDERS_USDC.every((s) => (updated.usdc[s] ?? BigInt(0)) >= APPROVAL_THRESHOLD) &&
    SPENDERS_CTF.every((s) => updated.ctf[s] === true);
  return updated;
}

function shortAddr(a: string): string {
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}
