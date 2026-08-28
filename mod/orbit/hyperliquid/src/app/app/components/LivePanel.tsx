"use client";

import { useEffect, useState } from "react";
import {
  ago, fmtUsd, shortAddr,
  agentStatus, walletConfig, type WalletNetConfig,
  liveStart, liveStop, liveStatus,
  type LiveTrader,
} from "../lib/api";
import { approveAgentFlow } from "../lib/hlActions";
import { useWallet } from "../lib/wallet";

type LiveStatusResp = Awaited<ReturnType<typeof liveStatus>>;

export default function LivePanel() {
  const wallet = useWallet();
  const { address, kind } = wallet;
  const eoa = address ?? "";
  const [agent, setAgent] = useState<string | null>(null);
  const [approved, setApproved] = useState<boolean | null>(null);
  const [agentErr, setAgentErr] = useState<string | null>(null);
  const [cfg, setCfg] = useState<WalletNetConfig | null>(null);

  const [tradersRaw, setTradersRaw] = useState("");
  const [intervalMs, setIntervalMs] = useState(15000);
  const [sizePct, setSizePct] = useState(10);
  const [maxPerTrade, setMaxPerTrade] = useState(200);
  const [minOrder, setMinOrder] = useState(10);
  const [slipBps, setSlipBps] = useState(100);
  const [coinsAllow, setCoinsAllow] = useState("");
  const [vault, setVault] = useState("");

  const [status, setStatus] = useState<LiveStatusResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Resolve the backend agent address + its on-HL approval state.
  useEffect(() => {
    if (!eoa) return;
    setAgent(null); setApproved(null); setAgentErr(null);
    agentStatus(eoa)
      .then((r) => { setAgent(r.agentAddress); setApproved(r.approved); })
      .catch((e) => setAgentErr(String(e?.message ?? e)));
  }, [eoa]);
  useEffect(() => { walletConfig().then(setCfg).catch(() => {}); }, []);

  // Poll status every 3s while running.
  useEffect(() => {
    if (!eoa) return;
    let live = true;
    const tick = () =>
      liveStatus(eoa)
        .then((r) => { if (live) setStatus(r); })
        .catch(() => { /* stale UI is fine */ });
    tick();
    const h = setInterval(tick, 3000);
    return () => { live = false; clearInterval(h); };
  }, [eoa]);

  const onApproveAgent = async () => {
    if (!eoa || !cfg) return;
    setBusy(true); setErr(null);
    try {
      await approveAgentFlow(wallet, cfg, eoa, "hl-engine");
      setApproved(true);
    } catch (e: any) {
      setErr(e?.code === 4001 ? "Signature rejected in MetaMask." : String(e?.message ?? e));
    } finally { setBusy(false); }
  };

  const onStart = async () => {
    if (!eoa) return;
    const traders: LiveTrader[] = tradersRaw
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter((s) => /^0x[a-fA-F0-9]{40}$/.test(s))
      .map((address) => ({ address, weight: 1, enabled: true }));
    if (!traders.length) { setErr("Add at least one leader wallet (0x…)"); return; }
    setBusy(true); setErr(null);
    try {
      await liveStart({
        eoa,
        traders,
        interval_ms: intervalMs,
        size_pct: sizePct,
        max_per_trade_usd: maxPerTrade,
        min_order_size_usd: minOrder,
        max_slippage_bps: slipBps,
        coins_allow: coinsAllow.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
        vault_address: vault.trim() || undefined,
      });
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally { setBusy(false); }
  };

  const onStop = async () => {
    if (!eoa) return;
    setBusy(true); setErr(null);
    try { await liveStop(eoa); } catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setBusy(false); }
  };

  if (!eoa) {
    return <div className="text-muted text-sm">Connect a wallet to use the live engine.</div>;
  }

  const state = status?.state;
  const running = state?.status === "running";

  return (
    <div className="space-y-6">
      {/* ── Agent wallet ── */}
      <section className="card p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-wider text-muted">Backend agent</div>
            <div className="text-sm mt-1">
              {agent
                ? <>
                    <span className="text-accent2">{shortAddr(agent)}</span>
                    {approved === true && <span className="text-win text-xs ml-2">approved ✓</span>}
                    {approved === false && <span className="text-warn text-xs ml-2">not approved</span>}
                  </>
                : agentErr ? <span className="text-danger">{agentErr}</span>
                : <span className="text-muted">resolving…</span>}
            </div>
            <p className="text-[11px] text-muted mt-2 max-w-xl">
              Sign once in MetaMask to authorize this address. After that, the
              engine signs every order/cancel/modify/vault-transfer for you.
              {kind === "watch" && <span className="text-warn"> Watch-only mode can't sign — connect MetaMask.</span>}
            </p>
          </div>
          {approved !== true && (
            <button className="btn-primary" disabled={!agent || !cfg || busy || kind !== "metamask"}
              onClick={onApproveAgent}>
              {busy ? "check MetaMask…" : "approve agent"}
            </button>
          )}
        </div>
      </section>

      {/* ── Config ── */}
      <section className="card p-4 space-y-3">
        <div className="text-xs uppercase tracking-wider text-muted">Copy-trade config</div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block col-span-2">
            <span className="text-[11px] text-muted">Leader wallets (comma/space-separated 0x…)</span>
            <textarea
              className="input w-full font-mono text-xs h-20"
              value={tradersRaw}
              onChange={(e) => setTradersRaw(e.target.value)}
              placeholder="0xabc… 0xdef…"
            />
          </label>
          <label className="block">
            <span className="text-[11px] text-muted">Size %</span>
            <input className="input w-full" type="number" min={0} max={1000}
              value={sizePct} onChange={(e) => setSizePct(+e.target.value)} />
          </label>
          <label className="block">
            <span className="text-[11px] text-muted">Max / trade USD (0 = unlimited)</span>
            <input className="input w-full" type="number" min={0}
              value={maxPerTrade} onChange={(e) => setMaxPerTrade(+e.target.value)} />
          </label>
          <label className="block">
            <span className="text-[11px] text-muted">Min order USD</span>
            <input className="input w-full" type="number" min={0}
              value={minOrder} onChange={(e) => setMinOrder(+e.target.value)} />
          </label>
          <label className="block">
            <span className="text-[11px] text-muted">Slippage bps</span>
            <input className="input w-full" type="number" min={0} max={5000}
              value={slipBps} onChange={(e) => setSlipBps(+e.target.value)} />
          </label>
          <label className="block">
            <span className="text-[11px] text-muted">Interval ms</span>
            <input className="input w-full" type="number" min={2000}
              value={intervalMs} onChange={(e) => setIntervalMs(+e.target.value)} />
          </label>
          <label className="block">
            <span className="text-[11px] text-muted">Vault address (optional)</span>
            <input className="input w-full" value={vault} onChange={(e) => setVault(e.target.value)}
              placeholder="0x… vault" />
          </label>
          <label className="block col-span-2">
            <span className="text-[11px] text-muted">Coins allow-list (optional, comma-separated)</span>
            <input className="input w-full" value={coinsAllow}
              onChange={(e) => setCoinsAllow(e.target.value)} placeholder="BTC, ETH, SOL" />
          </label>
        </div>
        <div className="flex items-center gap-2 pt-2">
          {running ? (
            <button className="btn-danger" disabled={busy} onClick={onStop}>stop</button>
          ) : (
            <button className="btn-primary" disabled={busy} onClick={onStart}>start</button>
          )}
          {err && <span className="text-danger text-xs">{err}</span>}
        </div>
      </section>

      {/* ── Status ── */}
      <section className="card p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-muted">Status</div>
          <span className={`pill ${running ? "border-accent2/40 text-accent2" : "border-border text-muted"}`}>
            {state?.status ?? "—"}
          </span>
        </div>
        {state ? (
          <div className="grid grid-cols-4 gap-3 text-sm">
            <Stat label="cycles" value={state.cycleCount ?? 0} />
            <Stat label="placed" value={state.totalOrdersPlaced ?? 0} />
            <Stat label="failed" value={state.totalOrdersFailed ?? 0} />
            <Stat label="volume" value={fmtUsd(state.totalVolumeMirrored ?? 0)} />
            <Stat label="last cycle" value={state.lastCycleAt ? ago(state.lastCycleAt) : "—"} />
          </div>
        ) : <div className="text-muted text-sm">no session</div>}
      </section>

      {/* ── Observed trades ── */}
      {state?.observedTrades?.length ? (
        <section className="card p-4">
          <div className="text-xs uppercase tracking-wider text-muted mb-2">Recent mirrored trades</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="text-left py-1 pr-2">time</th>
                  <th className="text-left pr-2">leader</th>
                  <th className="text-left pr-2">coin</th>
                  <th className="text-left pr-2">side</th>
                  <th className="text-right pr-2">size</th>
                  <th className="text-right pr-2">px</th>
                  <th className="text-right pr-2">notional</th>
                  <th className="text-left pr-2">status</th>
                </tr>
              </thead>
              <tbody>
                {state.observedTrades.slice(0, 30).map((t: any) => (
                  <tr key={t.id} className="border-t border-border/40">
                    <td className="py-1 pr-2 text-muted">{ago(t.timestamp)}</td>
                    <td className="pr-2 font-mono">{shortAddr(t.trader)}</td>
                    <td className="pr-2">{t.coin}</td>
                    <td className={`pr-2 ${t.side === "BUY" ? "text-accent2" : "text-danger"}`}>{t.side}</td>
                    <td className="pr-2 text-right">{t.size}</td>
                    <td className="pr-2 text-right">{t.price}</td>
                    <td className="pr-2 text-right">{fmtUsd(t.notional)}</td>
                    <td className="pr-2">
                      {t.mirrored ? <span className="text-accent2">mirrored</span>
                        : t.mirror_error ? <span className="text-muted" title={t.mirror_error}>skipped</span>
                        : <span className="text-muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: any }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className="text-sm tabular-nums">{value}</div>
    </div>
  );
}
