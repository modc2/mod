"use client";

import { useMemo, useState } from "react";
import { keyTypeInfo, type WalletType } from "../utils/keytypes";
import { ethers } from "ethers";
import { switchNetwork, getExplorerUrl } from "../utils/wallet";
import AddressChip from "./AddressChip";

/** Shape of GET /credits — dollars, backed by the chain module's Market. */
export interface CreditsData {
  account?: {
    identity: string;
    usd: string;
    usd6: string;
    onchain_usd: string;
    granted_usd: string;
    spent_usd: string;
    chain_error?: string | null;
    spends?: { usd6: string; reason: string; ts: number }[];
    grants?: { usd6: string; reason: string; ts: number }[];
  };
  chain?: {
    enabled: boolean;
    network: string;
    rpc: string;
    chain_id: number;
    symbol: string;
    market: string;
    tokengate: string;
    explorer: string;
    stables: { symbol: string; address: string; decimals: number }[];
  };
}

interface CreditsCardProps {
  data: CreditsData | null;
  address: string;
  walletType: WalletType;
  /** Re-fetch GET /credits — the page owns the authed fetch. */
  onRefresh: () => void | Promise<void>;
  accent?: string;
}

// Just the calls a top-up needs. The credit token itself is read server-side.
const ERC20_ABI = [
  "function decimals() view returns (uint8)",
  "function balanceOf(address) view returns (uint256)",
  "function allowance(address,address) view returns (uint256)",
  "function approve(address,uint256) returns (bool)",
];
const MARKET_ABI = ["function credit(address,uint256,uint256) returns (uint256)"];
const TOKENGATE_ABI = ["function getTokenPrice(address) view returns (uint256,uint8,uint256)"];

/** Market credit tokens carry 8 decimals — $1.00 is 1e8. */
const MARKET_DECIMALS = 8;

/** A signer for whichever wallet kind is signed in. Local/password identities
    are derived in the browser exactly as sign-in derived them. */
async function getSigner(
  walletType: CreditsCardProps["walletType"],
  address: string,
  rpc: string,
): Promise<ethers.Signer> {
  if (walletType === "phantom" || walletType === "polkadot") {
    // Credits are an EVM ERC20 purchase; an ed25519/sr25519 key has no
    // account on that chain to sign it with.
    throw new Error(
      "credits are bought on an EVM chain — sign in with an EVM key to top up",
    );
  }
  if (walletType === "metamask" || walletType === "subwallet") {
    const injected = (window as any).ethereum;
    if (!injected) throw new Error("no wallet extension found");
    const provider = new ethers.BrowserProvider(injected);
    return provider.getSigner(address);
  }
  let pk = "";
  if (walletType === "password") {
    const pw = localStorage.getItem("buildfork_jobs_password") || "";
    if (pw) pk = ethers.id(pw);
  } else if (walletType === "local") {
    const mnemonic = localStorage.getItem("buildfork_jobs_seed");
    if (mnemonic) pk = ethers.Wallet.fromPhrase(mnemonic).privateKey;
  }
  if (!pk) throw new Error("this session has no signing key — connect a wallet");
  return new ethers.Wallet(pk, new ethers.JsonRpcProvider(rpc));
}

/**
 * Credits, in dollars — the chain module's credit system as seen from here.
 *
 * The balance is the Market credit token ($1 = 1.00) held by YOUR wallet, so a
 * top-up is a wallet-signed purchase straight to the chain module's Market:
 * approve a stablecoin, call credit(). Nothing custodial passes through this
 * console. Grants and metered spends are the module's own off-chain half.
 */
export default function CreditsCard({
  data,
  address,
  walletType,
  onRefresh,
  accent = "var(--crt-green)",
}: CreditsCardProps) {
  const chain = data?.chain;
  const account = data?.account;
  const stables = chain?.stables || [];
  const [amount, setAmount] = useState("5");
  const [token, setToken] = useState(stables[0]?.symbol || "USDC");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [showLedger, setShowLedger] = useState(false);

  const stable = useMemo(
    () => stables.find((s) => s.symbol === token) || stables[0],
    [stables, token],
  );
  const live = !!(chain?.enabled && chain.market && chain.rpc);
  // Top-ups are EVM transactions, so only a secp256k1 identity can make one.
  const canSign =
    !!walletType && !!address && address !== "local" &&
    keyTypeInfo(address, walletType)?.id === "secp256k1";
  const dollars = Number(amount);
  const validAmount = Number.isFinite(dollars) && dollars > 0;

  const topUp = async () => {
    if (!live || !stable || !validAmount) return;
    setMsg(null);
    try {
      // The purchase must land on the chain the credit token lives on.
      if (walletType === "metamask" || walletType === "subwallet") {
        setBusy("network");
        const ok = await switchNetwork(chain!.chain_id);
        if (!ok) throw new Error(`switch your wallet to chain ${chain!.chain_id} first`);
      }
      const signer = await getSigner(walletType, address, chain!.rpc);
      const erc20 = new ethers.Contract(stable.address, ERC20_ABI, signer);
      const gate = new ethers.Contract(chain!.tokengate, TOKENGATE_ABI, signer);

      // Same arithmetic the Market does, so the slippage cap is exact:
      // payment = stable * 10^payDec * 10^priceDec / (price * 10^8)
      setBusy("quote");
      // (BigInt() calls, not 123n literals — this app targets ES2017.)
      const ten = BigInt(10);
      const zero = BigInt(0);
      const payDecimals: bigint = BigInt(await erc20.decimals());
      const [price, priceDecimals] = await gate.getTokenPrice(stable.address);
      if (BigInt(price) === zero) throw new Error(`${stable.symbol} has no price on this network`);
      const stableAmount = ethers.parseUnits(amount.trim(), MARKET_DECIMALS);
      const payment =
        (stableAmount * ten ** payDecimals * ten ** BigInt(priceDecimals)) /
        (BigInt(price) * ten ** BigInt(MARKET_DECIMALS));
      if (payment === zero) throw new Error("amount too small");
      // 2% headroom — the price can move between the quote and the mine.
      const maxPayment = (payment * BigInt(102)) / BigInt(100);

      const balance: bigint = await erc20.balanceOf(address);
      if (balance < maxPayment) {
        throw new Error(
          `need ${ethers.formatUnits(maxPayment, payDecimals)} ${stable.symbol}, wallet holds ${ethers.formatUnits(balance, payDecimals)}`,
        );
      }

      const allowance: bigint = await erc20.allowance(address, chain!.market);
      if (allowance < maxPayment) {
        setBusy("approve");
        setMsg(`approving ${stable.symbol}…`);
        const tx = await erc20.approve(chain!.market, maxPayment);
        await tx.wait();
      }

      setBusy("credit");
      setMsg(`buying $${amount} of credit…`);
      const market = new ethers.Contract(chain!.market, MARKET_ABI, signer);
      const tx = await market.credit(stable.address, stableAmount, maxPayment);
      const receipt = await tx.wait();
      setMsg(`✓ credited — ${String(receipt?.hash || tx.hash).slice(0, 14)}…`);
      await onRefresh();
    } catch (e: any) {
      // Wallet errors nest the useful part; surface the innermost sentence.
      const reason = e?.reason || e?.shortMessage || e?.info?.error?.message || e?.message || String(e);
      setMsg(`✗ ${reason}`);
    } finally {
      setBusy(null);
    }
  };

  const ledger = [
    ...(account?.grants || []).map((g) => ({ ...g, kind: "+" as const })),
    ...(account?.spends || []).map((s) => ({ ...s, kind: "−" as const })),
  ]
    .sort((a, b) => b.ts - a.ts)
    .slice(0, 6);

  return (
    <div className="section-card" style={{ ["--card-accent" as any]: accent }}>
      <span className="section-card__bar" />
      <div className="section-card__head">
        <div className="section-card__title">
          <span className="section-card__glyph">◈</span>
          Credits
        </div>
        <button
          onClick={() => onRefresh()}
          className="text-[10px] font-mono px-2 py-0.5 rounded-full focus-ring transition-all hover:brightness-125"
          style={{ color: "var(--text-tertiary)", border: "1px solid var(--border-color)" }}
          title="Re-read your on-chain credit"
        >
          ↻
        </button>
      </div>
      <div className="section-card__body flex flex-col gap-2.5">
        {/* The number, in dollars — everything else explains it. */}
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-[22px] font-mono font-bold leading-none" style={{ color: accent }}>
            ${account?.usd ?? "—"}
          </span>
          <span className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
            ${account?.onchain_usd ?? "0.00"} on-chain
            {account?.granted_usd && account.granted_usd !== "0.00" ? ` · $${account.granted_usd} granted` : ""}
            {account?.spent_usd && account.spent_usd !== "0.00" ? ` · $${account.spent_usd} spent` : ""}
          </span>
        </div>

        <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
          Credit is the chain module's Market token — $1.00 each, held by your own
          wallet on{" "}
          <span style={{ color: "var(--text-secondary)" }}>
            {chain?.network || "…"}
            {chain?.chain_id ? ` (${chain.chain_id})` : ""}
          </span>
          . Buying it here pays the Market directly; this console never holds your money.
        </div>

        {account?.chain_error && (
          <div className="text-[10px] font-mono" style={{ color: "var(--crt-amber)" }}>
            chain unreachable — showing granted credit only ({account.chain_error})
          </div>
        )}

        {!live ? (
          <div className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
            No Market deployed on {chain?.network || "this network"} — deploy the chain
            module there (or point <span className="font-mono">credits.network</span> at one)
            to sell credit.
          </div>
        ) : (
          <>
            {/* Top up — amount, what you pay with, go. */}
            <div className="flex items-center flex-wrap gap-2">
              <span
                className="text-[10px] font-bold uppercase shrink-0"
                style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}
              >
                TOP UP
              </span>
              <div className="flex items-center gap-1">
                <span className="text-[12px] font-mono" style={{ color: "var(--text-secondary)" }}>$</span>
                <input
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") topUp(); }}
                  inputMode="decimal"
                  className="w-[70px] text-[11px] font-mono px-2.5 py-1 rounded-full focus-ring outline-none"
                  style={{ color: "var(--text-primary)", border: "1px solid var(--border-color)", background: "transparent" }}
                  aria-label="Top-up amount in dollars"
                />
              </div>
              {stables.map((s) => (
                <button
                  key={s.symbol}
                  onClick={() => setToken(s.symbol)}
                  className="text-[10px] font-mono px-2 py-1 rounded-full focus-ring"
                  style={s.symbol === token
                    ? { color: accent, border: `1px solid color-mix(in srgb, ${accent} 45%, transparent)`, background: `color-mix(in srgb, ${accent} 12%, transparent)` }
                    : { color: "var(--text-tertiary)", border: "1px solid var(--border-color)" }}
                  aria-pressed={s.symbol === token}
                >
                  {s.symbol}
                </button>
              ))}
              <button
                onClick={topUp}
                disabled={!!busy || !validAmount || !canSign}
                className="text-[10px] font-bold uppercase px-2.5 py-1 rounded-full focus-ring transition-all hover:brightness-125"
                style={{
                  color: validAmount && canSign ? accent : "var(--text-tertiary)",
                  border: `1px solid ${validAmount && canSign ? `color-mix(in srgb, ${accent} 45%, transparent)` : "var(--border-color)"}`,
                  background: validAmount && canSign ? `color-mix(in srgb, ${accent} 12%, transparent)` : "transparent",
                  letterSpacing: "0.06em",
                  opacity: busy ? 0.7 : 1,
                }}
                title={canSign ? "Approve the stablecoin, then buy credit from the Market" : "Sign in with a wallet to buy credit"}
              >
                {busy === "network" ? "SWITCHING…"
                  : busy === "quote" ? "QUOTING…"
                  : busy === "approve" ? "APPROVING…"
                  : busy === "credit" ? "BUYING…"
                  : "BUY"}
              </button>
            </div>

            {msg && (
              <div className="text-[11px] font-mono break-words" style={{ color: msg.startsWith("✓") ? "#34d399" : msg.startsWith("✗") ? "#f87171" : "var(--text-tertiary)" }}>
                {msg}
              </div>
            )}

            <div className="flex items-center gap-2 text-[10px] font-mono flex-wrap" style={{ color: "var(--text-tertiary)" }}>
              <span>Market</span>
              <AddressChip address={chain!.market} size={10} color="var(--text-secondary)" label="Market contract" />
              {chain!.explorer && (
                <a
                  href={getExplorerUrl(chain!.chain_id, chain!.market, "address")}
                  target="_blank"
                  rel="noreferrer"
                  className="focus-ring underline"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  explorer ↗
                </a>
              )}
            </div>
          </>
        )}

        {ledger.length > 0 && (
          <div className="flex flex-col gap-1">
            <button
              onClick={() => setShowLedger((v) => !v)}
              className="text-[10px] font-bold uppercase self-start focus-ring"
              style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}
            >
              {showLedger ? "▾" : "▸"} LEDGER ({ledger.length})
            </button>
            {showLedger && (
              <div className="flex flex-col gap-0.5 text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                {ledger.map((e, i) => (
                  <div key={i} className="flex items-baseline gap-2">
                    <span style={{ color: e.kind === "+" ? "#34d399" : "var(--text-secondary)" }}>
                      {e.kind}${(Number(e.usd6) / 1e6).toFixed(2)}
                    </span>
                    <span className="truncate">{e.reason}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
