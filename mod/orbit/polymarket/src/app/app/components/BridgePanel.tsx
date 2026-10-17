"use client";

// FROM ANOTHER CHAIN — the bridge, flattened.
//
// The old funding panel made you open two folds, pick a chain from a
// dropdown, pick an asset from three chips, then type an amount — to answer
// a question the wallet sweep already answered: "which chains actually hold
// money". So this panel just lists those. One row per (chain, asset) that
// holds anything bridgeable, amount prefilled to the full balance (minus a
// gas reserve for native coins), one button. Everything lands as USDC.e in
// your Polygon wallet via LiFi; the move panel above takes it to trading.
//
// Holding nothing anywhere collapses the whole thing to your address and
// the chains we watch — send funds there on any of them and rows appear.

import { useEffect, useMemo, useState } from "react";
import { BrowserProvider, parseUnits } from "ethers";
import { useAuth } from "../context/AuthContext";
import { useChainBalances } from "../lib/chainBalances";
import {
  NETWORKS,
  NetworkConfig,
  NATIVE_TOKEN_ADDRESS,
  ensureChain,
  networkById,
} from "../lib/networks";
import { getLifiQuote, executeLifiBridge } from "../lib/lifi";

const POLYGON = networkById("polygon")!;

type Asset = "usdc" | "usdt" | "native";

interface BridgeRow {
  net: NetworkConfig;
  asset: Asset;
  balance: number;
  /** Suggested send: full balance for stables, balance − gas reserve for
      the native coin (the bridge tx itself still needs gas). */
  suggested: number;
  symbol: string;
  decimals: number;
  token: string;
}

const STABLE_DUST = 0.5; // ignore stablecoin crumbs under 50¢
// Native coin kept back for gas — mainnet is pricier than the L2s.
const gasReserve = (net: NetworkConfig) => (net.id === "ethereum" ? 0.004 : 0.0015);

function ChainBadge({ net, size = 18 }: { net: NetworkConfig; size?: number }) {
  return (
    <span
      className="shrink-0 inline-flex items-center justify-center font-mono leading-none rounded-full"
      style={{
        width: size,
        height: size,
        background: `${net.color}1f`,
        border: `1px solid ${net.color}`,
        color: net.color,
        fontSize: Math.floor(size * 0.55),
      }}
      aria-label={net.name}
    >
      {net.glyph}
    </span>
  );
}

export default function BridgePanel() {
  const { auth } = useAuth();
  const { holdings, loading, refresh } = useChainBalances(auth.address);
  // amounts keyed by `${netId}:${asset}` so edits survive re-renders
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [status, setStatus] = useState<{ key: string; msg: string; kind: "work" | "ok" | "err" } | null>(null);
  const [copied, setCopied] = useState(false);

  const rows = useMemo<BridgeRow[]>(() => {
    const out: BridgeRow[] = [];
    for (const net of NETWORKS) {
      if (net.id === "polygon") continue; // already home
      const h = holdings[net.id];
      if (!h) continue;
      if ((h.usdc ?? 0) >= STABLE_DUST) {
        out.push({ net, asset: "usdc", balance: h.usdc!, suggested: h.usdc!, symbol: "USDC", decimals: 6, token: net.usdc });
      }
      if ((h.usdt ?? 0) >= STABLE_DUST) {
        out.push({ net, asset: "usdt", balance: h.usdt!, suggested: h.usdt!, symbol: "USDT", decimals: 6, token: net.usdt });
      }
      const reserve = gasReserve(net);
      if ((h.native ?? 0) >= reserve * 2) {
        out.push({
          net,
          asset: "native",
          balance: h.native!,
          suggested: h.native! - reserve,
          symbol: net.nativeCurrency.symbol,
          decimals: 18,
          token: NATIVE_TOKEN_ADDRESS,
        });
      }
    }
    // Biggest stacks first — the row you came here for is on top.
    return out.sort((a, b) => (b.asset === "native" ? 0 : b.balance) - (a.asset === "native" ? 0 : a.balance));
  }, [holdings]);

  // Seed each row's amount once from its suggested size; user edits stick.
  useEffect(() => {
    setAmounts((prev) => {
      const next = { ...prev };
      for (const r of rows) {
        const key = `${r.net.id}:${r.asset}`;
        if (next[key] === undefined) {
          next[key] = r.asset === "native" ? r.suggested.toFixed(4) : r.suggested.toFixed(2);
        }
      }
      return next;
    });
  }, [rows]);

  if (!auth.connected || !auth.address) return null;

  const shortAddr = `${auth.address.slice(0, 6)}…${auth.address.slice(-4)}`;

  const copyAddr = async () => {
    try {
      await navigator.clipboard.writeText(auth.address!);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const bridge = async (row: BridgeRow) => {
    const key = `${row.net.id}:${row.asset}`;
    const amt = parseFloat(amounts[key] ?? "");
    if (!Number.isFinite(amt) || amt <= 0) {
      setStatus({ key, msg: "Enter an amount above 0.", kind: "err" });
      return;
    }
    if (amt > row.balance + 1e-9) {
      setStatus({ key, msg: `Only ${row.balance.toFixed(row.asset === "native" ? 4 : 2)} ${row.symbol} there.`, kind: "err" });
      return;
    }
    const ethereum = (window as unknown as { ethereum?: { request: (a: { method: string; params?: unknown[] }) => Promise<unknown> } }).ethereum;
    if (!ethereum) {
      setStatus({ key, msg: "No wallet found (install MetaMask).", kind: "err" });
      return;
    }
    setBusyKey(key);
    setStatus({ key, msg: `Switching wallet to ${row.net.name}…`, kind: "work" });
    try {
      await ensureChain(ethereum, row.net);
      const provider = new BrowserProvider(ethereum as never);
      const signer = await provider.getSigner(auth.address!);
      setStatus({ key, msg: "Getting the best route…", kind: "work" });
      const quote = await getLifiQuote({
        fromChain: row.net.chainId,
        fromToken: row.token,
        toChain: POLYGON.chainId,
        toToken: POLYGON.usdc,
        fromAmount: parseUnits(amt.toFixed(row.decimals), row.decimals).toString(),
        fromAddress: auth.address!,
        toAddress: auth.address!,
      });
      const out = Number(quote.estimate.toAmount) / 10 ** quote.action.toToken.decimals;
      const etaMin = Math.max(1, Math.round(quote.estimate.executionDuration / 60));
      setStatus({
        key,
        msg: `≈ $${out.toFixed(2)} USDC on Polygon via ${quote.toolDetails.name}, ~${etaMin} min — confirm in wallet…`,
        kind: "work",
      });
      const result = await executeLifiBridge(signer, quote, {
        onProgress: (msg) => setStatus({ key, msg, kind: "work" }),
      });
      const done = result.finalStatus?.status === "DONE";
      setStatus({
        key,
        msg: done
          ? `Landed on Polygon ✓ — it's in WALLET above; deposit it to trade.`
          : `Sent (${result.txHash.slice(0, 10)}…) — still crossing; WALLET updates when it lands.`,
        kind: "ok",
      });
      refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus({ key, msg: msg.slice(0, 200), kind: "err" });
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div id="bridge-panel" className="pixel-panel border-2 border-pixel-border p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs uppercase tracking-[0.2em] text-pixel-white">From another chain</span>
        <button
          onClick={refresh}
          className="ml-auto text-pixel-muted hover:text-green-400 text-sm"
          title="Re-scan every chain for funds"
        >
          ↻
        </button>
      </div>
      <div className="text-[10px] text-pixel-muted leading-snug -mt-1">
        Anything here arrives as USDC in your Polygon wallet (via LiFi, one wallet confirm).
      </div>

      {rows.length === 0 ? (
        <div className="space-y-1.5">
          <div className="text-[11px] font-mono text-pixel-gray leading-snug">
            {loading ? "Scanning your other chains…" : "Nothing found on your other chains."}
          </div>
          {!loading && (
            <>
              <div className="text-[10px] text-pixel-muted leading-snug">
                Send USDC to your address on any of these and a bridge row appears here:
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                {NETWORKS.filter((n) => n.id !== "polygon").map((n) => (
                  <span key={n.id} className="inline-flex items-center gap-1 text-[10px] font-mono text-pixel-gray">
                    <ChainBadge net={n} size={14} /> {n.short}
                  </span>
                ))}
                <button
                  onClick={() => { void copyAddr(); }}
                  className="ml-auto text-[10px] font-mono text-pixel-gray hover:text-green-400 transition-colors"
                  title={`Copy ${auth.address}`}
                >
                  {shortAddr} {copied ? "✓" : "⧉"}
                </button>
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="space-y-1.5">
          {rows.map((row) => {
            const key = `${row.net.id}:${row.asset}`;
            const busy = busyKey === key;
            const rowStatus = status?.key === key ? status : null;
            return (
              <div key={key} className="rounded-[var(--radius-sm)] border border-pixel-border bg-pixel-black/30 px-2 py-1.5 space-y-1">
                <div className="flex items-center gap-1.5">
                  <ChainBadge net={row.net} />
                  <span className="text-[11px] font-mono text-pixel-fg tracking-wider shrink-0">
                    {row.symbol} <span className="text-pixel-muted">on {row.net.short}</span>
                  </span>
                  <button
                    onClick={() =>
                      setAmounts((p) => ({
                        ...p,
                        [key]: row.asset === "native" ? row.suggested.toFixed(4) : row.suggested.toFixed(2),
                      }))
                    }
                    className="ml-auto text-[11px] font-mono text-pixel-gray hover:text-green-400 tabular-nums transition-colors"
                    title={
                      row.asset === "native"
                        ? `Balance ${row.balance.toFixed(4)} — click to fill (keeps ${gasReserve(row.net)} for gas)`
                        : `Balance ${row.balance.toFixed(2)} — click to fill`
                    }
                  >
                    {row.asset === "native" ? row.balance.toFixed(4) : `$${row.balance.toFixed(2)}`}
                  </button>
                </div>
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={amounts[key] ?? ""}
                    onChange={(e) =>
                      setAmounts((p) => ({ ...p, [key]: e.target.value.replace(/[^0-9.]/g, "") }))
                    }
                    disabled={busy}
                    className="bg-pixel-bg border border-pixel-border rounded px-2 py-1 w-24 min-w-0 font-mono text-[12px] outline-none tabular-nums"
                  />
                  <button
                    onClick={() => { void bridge(row); }}
                    disabled={busyKey !== null}
                    className="pixel-btn flex-1 py-1 text-[11px] font-mono tracking-wider border-green-400/80 text-green-400 hover:bg-green-400/10 disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    {busy ? "BRIDGING…" : "BRIDGE → POLYGON"}
                  </button>
                </div>
                {rowStatus && (
                  <div
                    className={`text-[10px] font-mono leading-snug break-words ${
                      rowStatus.kind === "err"
                        ? "text-red-400"
                        : rowStatus.kind === "ok"
                          ? "text-green-400"
                          : "text-amber-400"
                    }`}
                  >
                    {rowStatus.msg}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
