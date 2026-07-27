// Master-wallet (MetaMask) flows: approve the backend agent, withdraw
// USDC to Arbitrum, move USDC across the bridge into Hyperliquid.
//
// Signing model — mirrors Hyperliquid's own frontend:
//   - user-signed actions (approveAgent, withdraw3, usdClassTransfer) are
//     EIP-712 payloads with the Arbitrum chainId domain → MetaMask signs
//     them directly (we switch it to Arbitrum first, since MetaMask refuses
//     typed data whose domain chainId ≠ the active chain).
//   - L1 actions (orders, vaultTransfer) use HL's chainId-1337 phantom-agent
//     domain, which MetaMask will NOT sign → those are signed by the backend
//     agent key the user approves once here.
//   - Bridge deposits are a plain ERC-20 USDC transfer to HL's bridge.

import {
  approveAgentIntent, depositBalances, depositQuote, depositStatus, exchangeRelay,
  intentWithdraw, intentUsdClassTransfer,
  type DepositChain, type SignedIntent, type WalletNetConfig,
} from "./api";

type Signer = {
  signTypedData: (typedData: any) => Promise<string>;
  ensureChain: (cfg: { chainIdHex: string; chainName: string; rpcUrl: string; explorerUrl: string }) => Promise<void>;
  sendTransaction: (tx: { to: string; data?: string; value?: string }) => Promise<string>;
};

/** Split a 0x-prefixed 65-byte hex signature into HL's {r,s,v} envelope. */
export function splitSignature(sig: string): { r: string; s: string; v: number } {
  const h = sig.startsWith("0x") ? sig.slice(2) : sig;
  if (h.length !== 130) throw new Error(`unexpected signature length ${h.length}`);
  let v = parseInt(h.slice(128, 130), 16);
  if (v < 27) v += 27; // some wallets return 0/1
  return { r: `0x${h.slice(0, 64)}`, s: `0x${h.slice(64, 128)}`, v };
}

/** Sign a prepared intent with MetaMask and relay it to /exchange. */
export async function signAndRelay(wallet: Signer, cfg: WalletNetConfig, intent: SignedIntent): Promise<any> {
  // Domain chainId must match the active chain or MetaMask rejects the sign.
  await wallet.ensureChain(cfg);
  const sig = await wallet.signTypedData(intent.typedData);
  return exchangeRelay({ action: intent.action, nonce: intent.nonce, signature: splitSignature(sig) });
}

/** One-time: authorize the backend agent to trade / move vault funds for `eoa`. */
export async function approveAgentFlow(wallet: Signer, cfg: WalletNetConfig, eoa: string, agentName = "hl-engine"): Promise<any> {
  const intent = await approveAgentIntent(eoa, agentName);
  return signAndRelay(wallet, cfg, intent);
}

/** Withdraw USDC from Hyperliquid to an Arbitrum address (~$1 fee, few minutes). */
export async function withdrawFlow(wallet: Signer, cfg: WalletNetConfig, destination: string, amountUsd: string): Promise<any> {
  const intent = await intentWithdraw(destination, amountUsd);
  return signAndRelay(wallet, cfg, intent);
}

/** Move USDC between HL spot and perp balances. */
export async function usdClassTransferFlow(wallet: Signer, cfg: WalletNetConfig, amountUsd: string, toPerp: boolean): Promise<any> {
  const intent = await intentUsdClassTransfer(amountUsd, toPerp);
  return signAndRelay(wallet, cfg, intent);
}

/**
 * Deposit into Hyperliquid: ERC-20 `transfer(bridge, amount)` of USDC on
 * Arbitrum. Credited to the sending address on HL within ~1 minute.
 */
export async function bridgeDepositFlow(wallet: Signer, cfg: WalletNetConfig, amountUsd: number): Promise<string> {
  if (amountUsd < cfg.minDepositUsd) {
    throw new Error(`Minimum deposit is $${cfg.minDepositUsd} — smaller amounts are lost by the bridge.`);
  }
  await wallet.ensureChain(cfg);
  const units = BigInt(Math.round(amountUsd * 1e6)); // USDC = 6 decimals
  const to = cfg.bridgeAddress.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const amt = units.toString(16).padStart(64, "0");
  const data = `0xa9059cbb${to}${amt}`; // transfer(address,uint256)
  return wallet.sendTransaction({ to: cfg.usdcAddress, data, value: "0x0" });
}

// ── cross-chain deposit: any supported chain → Arbitrum USDC → HL bridge ──

const provider = () => {
  const p = (window as any).ethereum;
  if (!p) throw new Error("MetaMask not available");
  return p;
};

const pad32 = (hexNo0x: string) => hexNo0x.toLowerCase().padStart(64, "0");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** ERC-20 allowance(owner, spender) via eth_call on the active chain. */
async function erc20Allowance(token: string, owner: string, spender: string): Promise<bigint> {
  const data = `0xdd62ed3e${pad32(owner.replace(/^0x/, ""))}${pad32(spender.replace(/^0x/, ""))}`;
  const res: string = await provider().request({
    method: "eth_call",
    params: [{ to: token, data }, "latest"],
  });
  return BigInt(res === "0x" ? 0 : res);
}

/** Wait until a tx is mined on the active chain; throws if it reverted. */
async function waitMined(txHash: string, timeoutMs = 15 * 60_000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const r = await provider().request({
      method: "eth_getTransactionReceipt",
      params: [txHash],
    });
    if (r) {
      if (r.status === "0x0") throw new Error("transaction reverted on-chain");
      return;
    }
    await sleep(4000);
  }
  throw new Error("timed out waiting for the transaction to confirm");
}

export type DepositStep =
  | { step: "quote"; msg: string }
  | { step: "switch"; msg: string }
  | { step: "approve"; msg: string }
  | { step: "bridge"; msg: string; txHash?: string }
  | { step: "bridging"; msg: string; txHash: string }
  | { step: "deposit"; msg: string }
  | { step: "done"; msg: string; txHash: string; usdc: number };

/**
 * The whole "money on chain X → funded Hyperliquid account" journey:
 *   1. quote a LI.FI route into Arbitrum USDC (self → self)
 *   2. switch MetaMask to the source chain, approve if ERC-20, send the
 *      bridge transaction
 *   3. poll LI.FI until the USDC lands on Arbitrum
 *   4. switch to Arbitrum and transfer the received USDC to the HL bridge
 * Reports progress through `onStep` so the UI can render a stepper.
 */
export async function crossChainDepositFlow(
  wallet: Signer,
  hlCfg: WalletNetConfig,
  chain: DepositChain,
  token: "usdc" | "native",
  amount: string,
  eoa: string,
  minDepositUsd: number,
  onStep: (s: DepositStep) => void,
): Promise<{ txHash: string; usdc: number }> {
  onStep({ step: "quote", msg: `Finding the best route from ${chain.name}…` });
  const q = await depositQuote({ from_chain_id: chain.chainId, token, amount, eoa });
  if (!q.transactionRequest) throw new Error("No bridge route available for this amount.");
  if (q.toUsdcMin < minDepositUsd) {
    throw new Error(
      `This would arrive as ~$${q.toUsdcMin.toFixed(2)} USDC — Hyperliquid's minimum deposit is $${minDepositUsd}. Try a larger amount.`,
    );
  }

  onStep({ step: "switch", msg: `Switching MetaMask to ${chain.name}…` });
  await wallet.ensureChain({
    chainIdHex: chain.chainIdHex, chainName: chain.name,
    rpcUrl: chain.rpcUrl, explorerUrl: chain.explorerUrl,
  });

  if (token === "usdc" && q.approvalAddress) {
    const needed = BigInt(q.fromAmountUnits);
    const have = await erc20Allowance(chain.usdcAddress, eoa, q.approvalAddress);
    if (have < needed) {
      onStep({ step: "approve", msg: "Approve USDC in MetaMask (one-time per bridge)…" });
      const data = `0x095ea7b3${pad32(q.approvalAddress.replace(/^0x/, ""))}${pad32(needed.toString(16))}`;
      const approveTx = await wallet.sendTransaction({ to: chain.usdcAddress, data, value: "0x0" });
      onStep({ step: "approve", msg: "Waiting for the approval to confirm…" });
      await waitMined(approveTx);
    }
  }

  onStep({ step: "bridge", msg: "Confirm the bridge transaction in MetaMask…" });
  const tr = q.transactionRequest;
  const txHash = await wallet.sendTransaction({
    to: tr.to, data: tr.data, value: tr.value ?? "0x0",
  });

  onStep({ step: "bridging", msg: `Bridging to Arbitrum — usually ~${Math.max(1, Math.round(q.durationSec / 60))} min…`, txHash });
  await waitMined(txHash);

  // LI.FI's indexer can lag the chain by a minute — NOT_FOUND is not failure.
  let received = q.toUsdcMin;
  const deadline = Date.now() + 40 * 60_000;
  for (;;) {
    if (Date.now() > deadline) throw new Error("Bridge is taking unusually long — your funds are safe; check the tx on the explorer and retry the final deposit from Arbitrum.");
    await sleep(12_000);
    try {
      const st = await depositStatus(txHash, chain.chainId);
      if (st.status === "DONE") { received = st.receivedUsdc ?? received; break; }
      if (st.status === "FAILED" || st.status === "INVALID") {
        throw new Error(`Bridge reported ${st.status}${st.substatusMessage ? `: ${st.substatusMessage}` : ""}`);
      }
    } catch (e: any) {
      if (String(e?.message ?? "").startsWith("Bridge reported")) throw e;
      // transient status-API hiccup — keep polling
    }
    onStep({ step: "bridging", msg: "Bridging to Arbitrum — still in transit…", txHash });
  }

  onStep({ step: "deposit", msg: `$${received.toFixed(2)} USDC arrived on Arbitrum — confirm the deposit in MetaMask…` });
  // Floor to whole cents so rounding can never exceed the received balance.
  const depositUsd = Math.floor(received * 100) / 100;
  const finalTx = await bridgeDepositFlow(wallet, hlCfg, depositUsd);

  onStep({ step: "done", msg: "Deposit sent — Hyperliquid credits it in ~1 minute.", txHash: finalTx, usdc: depositUsd });
  return { txHash: finalTx, usdc: depositUsd };
}

// ── cross-chain withdraw: HL → Arbitrum USDC → any supported chain ──

export type WithdrawStep = {
  step: "sign" | "arbitrum" | "bridge" | "done";
  msg: string;
  txHash?: string;
};

/**
 * Hyperliquid's withdraw3 only pays out on Arbitrum, so withdrawing to
 * another network is a two-hop journey:
 *   1. withdraw3 to the user's OWN address on Arbitrum (HL deducts ~$1)
 *   2. wait for the USDC to land (poll balances server-side, ~5 min)
 *   3. LI.FI-bridge the arrived USDC from Arbitrum to `dest` on `toChain`
 * Self-custodial throughout: funds only ever sit in the user's own wallet
 * between the two hops, so any failure strands nothing with third parties.
 * Needs a little ETH on Arbitrum for the bridge hop's gas.
 */
export async function crossChainWithdrawFlow(
  wallet: Signer,
  hlCfg: WalletNetConfig,
  arb: DepositChain,
  toChain: DepositChain,
  dest: string,
  amountUsd: string,
  eoa: string,
  onStep: (s: WithdrawStep) => void,
): Promise<{ txHash: string; usdc: number }> {
  const amt = Number(amountUsd);

  // Arbitrum USDC balance via the backend (no chain switch needed to read).
  const arbUsdc = async (): Promise<number | null> => {
    try {
      const r = await depositBalances(eoa);
      const c = r.chains.find((x) => x.chainId === arb.chainId);
      return c && c.ok ? c.usdc.balance : null;
    } catch { return null; }
  };
  const before = (await arbUsdc()) ?? 0;

  onStep({ step: "sign", msg: "Sign the withdrawal in MetaMask…" });
  const intent = await intentWithdraw(eoa, amountUsd);
  await signAndRelay(wallet, hlCfg, intent);

  // HL deducts ~$1; accept anything close so fee drift can't strand the flow.
  const expectArrive = Math.max(0.5, amt - 1.2);
  onStep({ step: "arbitrum", msg: "Withdrawal accepted — waiting for USDC to land on Arbitrum (usually ~5 min)…" });
  const deadline = Date.now() + 30 * 60_000;
  let arrived = 0;
  for (;;) {
    if (Date.now() > deadline) {
      throw new Error(
        "The withdrawal hasn't landed on Arbitrum after 30 minutes — your USDC is safe and will arrive in your own wallet on Arbitrum; withdraw again with network Arbitrum set, or bridge manually once it shows up.",
      );
    }
    await sleep(15_000);
    const now = await arbUsdc();
    if (now == null) continue; // transient RPC hiccup — keep polling
    arrived = now - before;
    if (arrived >= expectArrive) break;
  }

  const bridgeAmt = Math.floor(Math.min(arrived, amt) * 100) / 100;
  onStep({ step: "bridge", msg: `$${bridgeAmt.toFixed(2)} USDC arrived on Arbitrum — finding the best route to ${toChain.name}…` });
  const q = await depositQuote({
    from_chain_id: arb.chainId, token: "usdc", amount: String(bridgeAmt), eoa,
    to_chain_id: toChain.chainId, to_address: dest,
  });
  if (!q.transactionRequest) {
    throw new Error(`No bridge route to ${toChain.name} for this amount — your USDC is in your own wallet on Arbitrum.`);
  }

  await wallet.ensureChain({
    chainIdHex: arb.chainIdHex, chainName: arb.name,
    rpcUrl: arb.rpcUrl, explorerUrl: arb.explorerUrl,
  });

  if (q.approvalAddress) {
    const needed = BigInt(q.fromAmountUnits);
    const have = await erc20Allowance(arb.usdcAddress, eoa, q.approvalAddress);
    if (have < needed) {
      onStep({ step: "bridge", msg: "Approve USDC in MetaMask (one-time per bridge)…" });
      const data = `0x095ea7b3${pad32(q.approvalAddress.replace(/^0x/, ""))}${pad32(needed.toString(16))}`;
      const approveTx = await wallet.sendTransaction({ to: arb.usdcAddress, data, value: "0x0" });
      onStep({ step: "bridge", msg: "Waiting for the approval to confirm…" });
      await waitMined(approveTx);
    }
  }

  onStep({ step: "bridge", msg: "Confirm the bridge transaction in MetaMask…" });
  const tr = q.transactionRequest;
  const txHash = await wallet.sendTransaction({ to: tr.to, data: tr.data, value: tr.value ?? "0x0" });

  onStep({ step: "bridge", msg: `Bridging to ${toChain.name} — usually ~${Math.max(1, Math.round(q.durationSec / 60))} min…`, txHash });
  await waitMined(txHash);

  // Same indexer-lag caveat as deposits: NOT_FOUND is not failure.
  let delivered = q.toUsdcMin || bridgeAmt;
  const bDeadline = Date.now() + 40 * 60_000;
  for (;;) {
    if (Date.now() > bDeadline) {
      throw new Error(`Bridge is taking unusually long — your funds are safe; check the tx on ${arb.explorerUrl}.`);
    }
    await sleep(12_000);
    try {
      const st = await depositStatus(txHash, arb.chainId, toChain.chainId);
      if (st.status === "DONE") { delivered = st.receivedUsdc ?? delivered; break; }
      if (st.status === "FAILED" || st.status === "INVALID") {
        throw new Error(`Bridge reported ${st.status}${st.substatusMessage ? `: ${st.substatusMessage}` : ""}`);
      }
    } catch (e: any) {
      if (String(e?.message ?? "").startsWith("Bridge reported")) throw e;
      // transient status-API hiccup — keep polling
    }
    onStep({ step: "bridge", msg: `Bridging to ${toChain.name} — still in transit…`, txHash });
  }

  onStep({ step: "done", msg: `$${delivered.toFixed(2)} USDC delivered on ${toChain.name}.`, txHash });
  return { txHash, usdc: delivered };
}
