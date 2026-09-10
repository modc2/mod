"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createCopy, setWallet, walletBalance, shortSs58 } from "../lib/api";
import { useCurrency } from "../context/CurrencyContext";
import Identicon from "./Identicon";

/**
 * The one-step copy dialog. Pick how much, press START — that's the whole
 * form. Everything the full CopyForm asks for (hotkey, per-tx cap, rebalance
 * band) has a sensible server default, and the strat maker in the drawer is
 * still there for anyone who wants to turn those dials.
 *
 * If no wallet is set yet the dialog asks for one first, inline, rather
 * than bouncing the visitor to an error.
 */
export default function SimpleCopy({
  ss58,
  label,
  onClose,
}: {
  ss58: string;
  label?: string | null;
  onClose: () => void;
}) {
  const { usdPerTao } = useCurrency();
  const [wallet, setWalletState] = useState<{ ss58: string; balance_tao: number } | null | undefined>(undefined);
  const [mnemonic, setMnemonic] = useState("");
  const [amount, setAmount] = useState("10");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    walletBalance().then(setWalletState).catch(() => setWalletState(null));
  }, []);

  // Escape closes, like every other overlay in the console.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const tao = parseFloat(amount);
  const usd = usdPerTao && tao > 0 ? `≈ $${(tao * usdPerTao).toFixed(0)}` : "";
  const name = label || shortSs58(ss58);

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await setWallet({ mnemonic: mnemonic.trim() });
      setMnemonic("");
      setWalletState(await walletBalance());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!(tao > 0)) { setError("enter how much TAO should follow this trader"); return; }
    if (wallet && tao > wallet.balance_tao) {
      setError(`you only have ${wallet.balance_tao.toFixed(2)} τ in this wallet`);
      return;
    }
    setBusy(true);
    try {
      await createCopy({
        target_ss58: ss58,
        our_hotkey: wallet?.ss58 || "",
        label: label || undefined,
        alloc_tao: tao,
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={`Copy ${name}`}>
        <div className="modal-head">
          <Identicon ss58={ss58} size={28} />
          <div className="min-w-0 flex-1">
            <p className="modal-kicker">copy trader</p>
            <p className="modal-title truncate">{name}</p>
          </div>
          <button onClick={onClose} className="pixel-btn px-3 py-1 text-[11px]" aria-label="close">✕</button>
        </div>

        {done ? (
          <div className="modal-body space-y-4">
            <p className="arcade-prose text-green-400">You&rsquo;re copying {name}.</p>
            <p className="arcade-prose-sm">
              {tao} τ now follows their moves. Every few minutes we look at what
              they hold and line your stake up with it. You can pause or stop
              any time.
            </p>
            <div className="flex gap-2">
              <Link href="/portfolio" className="pixel-btn border-green-400 text-green-400 no-underline">
                SEE MY COPIES
              </Link>
              <button onClick={onClose} className="pixel-btn">DONE</button>
            </div>
          </div>
        ) : wallet === undefined ? (
          <div className="modal-body"><p className="arcade-prose-sm">checking your wallet…</p></div>
        ) : wallet === null ? (
          <form onSubmit={connect} className="modal-body space-y-4">
            <p className="modal-step">step 1 of 2 — connect a wallet</p>
            <p className="arcade-prose-sm">
              Copying stakes real TAO from your own wallet. Paste the 12- or
              24-word recovery phrase of the wallet you want to trade from. It
              stays on this machine.
            </p>
            <textarea
              value={mnemonic}
              onChange={(e) => setMnemonic(e.target.value)}
              placeholder="word word word …"
              rows={3}
              className="pixel-input w-full font-mono text-sm"
              autoFocus
            />
            {error && <p className="modal-error">{error}</p>}
            <button type="submit" disabled={busy || mnemonic.trim().split(/\s+/).length < 12} className="pixel-btn border-green-400 text-green-400">
              {busy ? "CONNECTING…" : "CONNECT WALLET"}
            </button>
          </form>
        ) : (
          <form onSubmit={start} className="modal-body space-y-4">
            <p className="modal-step">how much should follow them?</p>
            <div className="flex items-stretch gap-2">
              <input
                type="number" min="0.5" step="0.5"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="pixel-input flex-1 font-mono text-[28px]"
                autoFocus
              />
              <span className="pixel-panel px-3 flex items-center font-mono text-[24px]">τ</span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {[5, 10, 25, 50, 100].map((n) => (
                <button
                  type="button"
                  key={n}
                  onClick={() => setAmount(String(n))}
                  className={`pixel-btn px-3 py-1 text-[11px] ${amount === String(n) ? "nav-active" : ""}`}
                >
                  {n} τ
                </button>
              ))}
              {usd && <span className="font-mono text-pixel-gray-light ml-auto">{usd}</span>}
            </div>
            <p className="arcade-prose-sm">
              Wallet <span className="font-mono text-pixel-white">{shortSs58(wallet.ss58)}</span> has{" "}
              <span className="font-mono text-pixel-white">{wallet.balance_tao.toFixed(2)} τ</span> free.
              This amount is split across the subnets {name} holds, in their proportions.
            </p>
            {error && <p className="modal-error">{error}</p>}
            <button type="submit" disabled={busy} className="pixel-btn border-green-400 text-green-400 w-full py-3 text-[13px]">
              {busy ? "STARTING…" : `START COPYING WITH ${tao > 0 ? tao : "…"} τ`}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
