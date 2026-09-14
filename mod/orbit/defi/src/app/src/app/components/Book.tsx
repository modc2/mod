"use client";

import { useCallback, useEffect, useState } from "react";
import * as api from "../lib/api";
import { runEvmPlan, runSolanaSwap, type StepUpdate, type WalletPlan } from "../lib/wallet";
import { pct } from "./Modules";

type Props = { say: (text: string, bad?: boolean) => void; onOpenModules: () => void };

/// The book: money that went into modules through this desk. Each row keeps
/// the rate it was entered at beside the rate now, so drift is a number on the
/// row rather than a surprise later. Leaving goes back out through the same
/// adapter that came in.
export default function Book({ say, onOpenModules }: Props) {
  const [book, setBook] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [amount, setAmount] = useState("all");
  const [account, setAccount] = useState("");
  const [auth, setAuth] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState("");
  const [values, setValues] = useState<Record<string, any>>({});
  const [steps, setSteps] = useState<StepUpdate[]>([]);

  const load = useCallback(async () => {
    try {
      setBook(await api.getPositions());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const readValue = async (id: string) => {
    setBusy(`value:${id}`);
    try {
      const out = await api.positionValue(id);
      setValues((v) => ({ ...v, [id]: out.value }));
    } catch (e: any) {
      setValues((v) => ({ ...v, [id]: { error: e.message } }));
    } finally {
      setBusy("");
    }
  };

  /// A swap_receipt position held by the user's own wallet: read the receipt
  /// balance where the wallet lives, quote the route (a public read), then
  /// approve + swap on the router — all signed in the browser.
  const swapExit = async (w: any, onStep: (u: StepUpdate) => void) => {
    const ethereum = (window as any).ethereum;
    if (!ethereum) throw new Error("no browser wallet found");
    const { BrowserProvider, Contract, formatUnits } = await import("ethers");
    const provider = new BrowserProvider(ethereum);
    await provider.send("eth_requestAccounts", []);
    const owner = await (await provider.getSigner()).getAddress();
    let amt = amount.trim().toLowerCase() === "all" || !amount.trim() ? "" : amount.trim();
    if (!amt) {
      const bal = await new Contract(w.sell, ["function balanceOf(address) view returns (uint256)"], provider).balanceOf(owner);
      if (bal === 0n) throw new Error(`your wallet holds no ${w.sell_symbol ?? "receipt"} — nothing to exit`);
      amt = formatUnits(bal, w.sell_decimals ?? 18);
    }
    const q = await api.dexQuote({ chain: w.chain, sell: w.sell, buy: w.buy, amount: amt, slippageBps: 50 });
    const plan: WalletPlan = {
      available: true, kind: "evm", chain_id: w.chain_id,
      steps: [
        { action: "approve", token: q.sell.address, spender: w.router, amount_wei: q.sell.base_units, symbol: q.sell.symbol },
        { action: "swap", router: w.router, wrapped: w.wrapped, token_in: q.sell.address, token_out: q.buy.address,
          fees: q.fee_tiers, amount_in_wei: q.sell.base_units, min_out_wei: q.min_received_base_units },
      ],
    };
    return runEvmPlan(plan, onStep);
  };

  const exit = async (p: any) => {
    setBusy(`exit:${p.id}`);
    setSteps([]);
    const onStep = (u: StepUpdate) =>
      setSteps((s) => {
        const next = [...s];
        const i = next.findIndex((x) => x.label === u.label || u.label.startsWith(x.label));
        if (i >= 0) next[i] = u;
        else next.push(u);
        return next;
      });
    try {
      const body: any = { amount: amount.trim() || "all", confirm };
      if (account.trim()) body.account = account.trim();
      if (auth.trim()) body.auth = auth.trim();
      const out = await api.exitPosition(p.id, body);
      // A browser-signed position comes back as a plan, not an execution — the
      // wallet that owns the receipt signs the way out, then the book settles.
      if (out?.browser && out?.wallet) {
        const w = out.wallet;
        if (!w.available) throw new Error(w.reason ?? "no browser wallet path for this exit");
        let res: { txs: string[] };
        if (w.kind === "evm-swap") res = await swapExit(w, onStep);
        else if (w.kind === "solana") {
          if (!amount.trim() || amount.trim().toLowerCase() === "all")
            throw new Error("say how much of the receipt to sell — 'all' is not resolvable in the browser on Solana");
          res = await runSolanaSwap({ ...w, amount: amount.trim() }, onStep);
        } else res = await runEvmPlan(w, onStep);
        const settled = await api.settlePosition(p.id, { txs: res.txs, amount: amount.trim() || "all" });
        say(settled?.closed ? `out of ${p.project} — your wallet signed it, row closed` : `partial exit of ${p.project} recorded`);
        await load();
        return;
      }
      say(out?.exited ? `out of ${p.project}` : out?.needs_confirm ? "tick confirm to send it" : out?.swap?.reason ?? "not sent", !out?.exited && !out?.needs_confirm);
      await load();
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const forget = async (id: string) => {
    try {
      await api.forgetPosition(id);
      say("row forgotten — whatever is on chain is still there");
      await load();
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const rows: any[] = book?.positions ?? [];

  return (
    <div className="fin">
      <div className="stats">
        <div className="stat">
          <span className="stat-n">{book?.open ?? "…"}</span>
          <span className="stat-l">open positions</span>
        </div>
        <div className="stat">
          <span className="stat-n">{book?.count ?? "…"}</span>
          <span className="stat-l">ever entered</span>
        </div>
        {Object.entries(book?.open_by_chain ?? {}).map(([c, n]: any) => (
          <div className="stat" key={c}>
            <span className="stat-n">{n}</span>
            <span className="stat-l">on {c}</span>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        <button className="ghost" onClick={onOpenModules}>← modules</button>
      </div>

      <div className="scroll" style={{ flex: 1, padding: "10px 14px 30px" }}>
        {error && (
          <div className="issue" style={{ marginBottom: 10 }}>
            <span>!</span>
            <span>{error}</span>
          </div>
        )}
        {book && rows.length === 0 && (
          <div className="rail-empty" style={{ maxWidth: 520 }}>
            <div style={{ fontSize: 22, color: "var(--accent)" }}>✦</div>
            <div style={{ marginTop: 10, lineHeight: 1.7 }}>
              Nothing in the book yet. Pick a module, quote it, and add money — a row lands here the moment a
              chain module actually sends something. Dry runs and refused confirms leave no trace.
            </div>
            <button className="primary" onClick={onOpenModules} style={{ marginTop: 14 }}>browse the modules</button>
          </div>
        )}
        <div className="positions">
          {rows.map((p) => {
            const isOpen = open === p.id;
            const drift = p.apy_drift;
            const v = values[p.id];
            return (
              <div key={p.id} className={`pos ${p.status}`}>
                <div className="pos-head" onClick={() => setOpen(isOpen ? null : p.id)}>
                  <span className={`chain-dot ${p.chain}`} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>
                      {p.amount} {p.asset} <span className="dim">→</span> {p.project}
                    </div>
                    <div className="mod-sub">
                      {p.symbol} · {p.kind} · {p.adapter} · {p.account || p.owner}
                      {p.signer === "browser" ? " · ◈ browser wallet" : ""}
                    </div>
                  </div>
                  <div className="pos-nums">
                    <div><span className="dim">at entry</span> {pct(p.apy_at_entry)}</div>
                    <div>
                      <span className="dim">now</span> {p.apy_now == null ? "—" : pct(p.apy_now)}
                      {drift != null && (
                        <span style={{ color: drift < 0 ? "var(--danger)" : "var(--accent)" }}> {drift > 0 ? "+" : ""}{Number(drift).toFixed(2)}</span>
                      )}
                    </div>
                  </div>
                  <div className="pos-nums">
                    <div><span className="dim">days in</span> {p.days_in}</div>
                    <div><span className="dim">projected</span> {p.projected_earned} {p.asset}</div>
                  </div>
                  <span className={`pill ${p.status === "open" ? "ok" : ""}`}>{p.status}</span>
                </div>

                {isOpen && (
                  <div className="pos-body">
                    <div className="mono-small" style={{ lineHeight: 1.7 }}>
                      module {p.module} · entered {new Date(p.entered_at * 1000).toISOString().slice(0, 16).replace("T", " ")} UTC
                      {p.receipt?.symbol ? ` · holding ${p.receipt.symbol}${p.receipt.address ? ` (${p.receipt.address})` : ""}` : ""}
                      {(p.txs ?? []).map((t: string) => (
                        <div key={t}>{t}</div>
                      ))}
                      {p.liquidity_now && <div>exit now: {p.liquidity_now.exit} · {p.liquidity_now.exit_note}</div>}
                      <div style={{ color: "var(--dim)" }}>{p.projected_basis}</div>
                    </div>

                    <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
                      <button onClick={() => readValue(p.id)} disabled={busy !== ""}>
                        {busy === `value:${p.id}` ? "reading…" : "read value on chain"}
                      </button>
                      {v && (
                        <span className="mono-small" style={{ flex: 1 }}>
                          {v.error ?? v.note ?? `${v.assets ?? v.shares ?? ""} ${v.symbol ?? ""} · ${v.basis ?? ""}`}
                        </span>
                      )}
                    </div>

                    {p.status === "open" && (
                      <div className="card" style={{ marginTop: 10 }}>
                        <div className="label">Take money out</div>
                        <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                          <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="all, or an amount" />
                          {p.signer !== "browser" && (
                            <input value={account} onChange={(e) => setAccount(e.target.value)} placeholder={p.account ? `account (${p.account})` : "account"} />
                          )}
                        </div>
                        {p.signer !== "browser" && (
                          <input value={auth} onChange={(e) => setAuth(e.target.value)} type="password" placeholder="bearer for the chain module (optional)" style={{ marginTop: 6 }} />
                        )}
                        <div style={{ display: "flex", gap: 10, marginTop: 8, alignItems: "center" }}>
                          {p.signer !== "browser" && (
                            <label className="tick">
                              <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> real money
                            </label>
                          )}
                          <div style={{ flex: 1 }} />
                          <button className="ghost danger" onClick={() => forget(p.id)} style={{ fontSize: 11 }}>forget row</button>
                          <button className="primary" onClick={() => exit(p)} disabled={busy !== ""}>
                            {busy === `exit:${p.id}`
                              ? p.signer === "browser" ? "signing…" : "sending…"
                              : p.signer === "browser" ? "SIGN EXIT" : confirm ? "EXIT" : "exit (dry until confirmed)"}
                          </button>
                        </div>
                        {busy === `exit:${p.id}` && steps.length > 0 && (
                          <div className="mono-small" style={{ marginTop: 8, lineHeight: 1.7 }}>
                            {steps.map((s, i) => (
                              <div key={i} style={{ color: s.status === "failed" ? "var(--warn)" : undefined }}>
                                {s.status === "done" ? "✓" : s.status === "failed" ? "✕" : "…"} {s.label}
                                {s.tx ? ` · ${s.tx.slice(0, 10)}…` : ""}
                                {s.error ? ` — ${s.error}` : ""}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.5 }}>
                          {p.signer === "browser"
                            ? "your own wallet signs the way out — the wallet's confirmation screen is the confirm"
                            : p.adapter === "swap_receipt" ? "amount is in the receipt token" : p.adapter === "tao_subnet" ? "amount is TAO-equivalent of alpha" : "amount is in the asset; 'all' redeems every share"}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {book?.ledger && <div className="foot">{book.ledger}</div>}
      </div>
    </div>
  );
}
