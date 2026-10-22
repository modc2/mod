"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  ago, fmtUsd, shortAddr,
  agentStatus, walletConfig, type WalletNetConfig,
  liveStart, liveStop, liveStatus,
  userState,
  type LiveTrader,
} from "../lib/api";
import { approveAgentFlow } from "../lib/hlActions";
import { useWallet } from "../lib/wallet";
import { useSession } from "../lib/auth";
import AuthGate from "./AuthGate";

type LiveStatusResp = Awaited<ReturnType<typeof liveStatus>>;

export default function LivePanel() {
  const wallet = useWallet();
  const { kind } = wallet;
  // The engine trades this account's money on a server that only accepts
  // orders signed for the token's own wallet, so `eoa` here is the *session*
  // address, never merely an attached one. A watched address would build a
  // valid-looking config and be refused at start.
  const { address, me } = useSession();
  const eoa = address ?? "";
  const searchParams = useSearchParams();
  const [agent, setAgent] = useState<string | null>(null);
  const [approved, setApproved] = useState<boolean | null>(null);
  const [agentErr, setAgentErr] = useState<string | null>(null);
  const [cfg, setCfg] = useState<WalletNetConfig | null>(null);

  const [traders, setTraders] = useState<LiveTrader[]>([]);
  const [addrInput, setAddrInput] = useState("");
  const [intervalMs, setIntervalMs] = useState(15000);
  const [sizePct, setSizePct] = useState(10);
  const [maxPerTrade, setMaxPerTrade] = useState(200);
  const [minOrder, setMinOrder] = useState(10);
  const [slipBps, setSlipBps] = useState(100);
  const [coinsAllow, setCoinsAllow] = useState("");
  const [coinsDeny, setCoinsDeny] = useState("");
  const [vault, setVault] = useState("");
  const [configLoaded, setConfigLoaded] = useState(false);

  // Pre-fill from ?traders= URL param (e.g. from scout/trader-page copy CTAs).
  // Only runs once before any live-session config is loaded — a running session wins.
  useEffect(() => {
    if (configLoaded) return;
    const param = searchParams.get("traders");
    if (param) {
      const addrs = param.split(/[\s,]+/).map((s) => s.trim()).filter((s) => /^0x[a-fA-F0-9]{40}$/.test(s));
      if (addrs.length) setTraders(addrs.map((address) => ({ address, weight: 1, enabled: true })));
    }
  }, [searchParams, configLoaded]);

  const [status, setStatus] = useState<LiveStatusResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [accountState, setAccountState] = useState<any>(null);

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

  // Poll account state every 5s while the engine is running.
  const isRunning = status?.state?.status === "running";
  useEffect(() => {
    if (!eoa || !isRunning) { setAccountState(null); return; }
    let live = true;
    const tick = () =>
      userState(eoa)
        .then((r) => { if (live) setAccountState(r); })
        .catch(() => {});
    tick();
    const h = setInterval(tick, 5000);
    return () => { live = false; clearInterval(h); };
  }, [eoa, isRunning]);

  // Pre-populate form from the running engine config on first status load.
  useEffect(() => {
    if (configLoaded || !status?.config) return;
    const c = status.config;
    if (Array.isArray(c.traders) && c.traders.length > 0) {
      setTraders(c.traders.map((t: any) => ({
        address: t.address,
        weight: t.weight ?? 1,
        enabled: t.enabled !== false,
      })));
    }
    if (c.intervalMs != null) setIntervalMs(c.intervalMs);
    if (c.sizePct != null) setSizePct(c.sizePct);
    if (c.maxPerTradeUsd != null) setMaxPerTrade(c.maxPerTradeUsd);
    if (c.minOrderSizeUsd != null) setMinOrder(c.minOrderSizeUsd);
    if (c.maxSlippageBps != null) setSlipBps(c.maxSlippageBps);
    if (Array.isArray(c.coinsAllow)) setCoinsAllow(c.coinsAllow.join(", "));
    if (Array.isArray(c.coinsDeny)) setCoinsDeny(c.coinsDeny.join(", "));
    if (c.vaultAddress != null) setVault(c.vaultAddress);
    setConfigLoaded(true);
  }, [status, configLoaded]);

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

  const parseAndAddAddresses = (raw: string) => {
    const newAddrs = raw.split(/[\s,]+/).map((s) => s.trim()).filter((s) => /^0x[a-fA-F0-9]{40}$/.test(s));
    if (!newAddrs.length) return;
    setTraders((prev) => {
      const existing = new Set(prev.map((t) => t.address.toLowerCase()));
      const toAdd = newAddrs.filter((a) => !existing.has(a.toLowerCase()));
      return [...prev, ...toAdd.map((address) => ({ address, weight: 1, enabled: true }))];
    });
    setAddrInput("");
  };

  const onStart = async () => {
    if (!me) return;
    if (!traders.length) { setErr("Add at least one leader wallet (0x…)"); return; }
    setBusy(true); setErr(null);
    try {
      await liveStart({
        eoa: me,
        traders,
        interval_ms: intervalMs,
        size_pct: sizePct,
        max_per_trade_usd: maxPerTrade,
        min_order_size_usd: minOrder,
        max_slippage_bps: slipBps,
        coins_allow: coinsAllow.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
        coins_deny: coinsDeny.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
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
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-muted">Copy-trade config</div>
          {running && <span className="text-[11px] text-accent2">live — fields show running values</span>}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2 space-y-2">
            <span className="text-[11px] text-muted">Leader wallets</span>
            {!running && (
              <div className="flex gap-2">
                <input
                  className="input flex-1 font-mono text-xs"
                  value={addrInput}
                  onChange={(e) => setAddrInput(e.target.value)}
                  onBlur={() => { if (addrInput.trim()) parseAndAddAddresses(addrInput); }}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); parseAndAddAddresses(addrInput); } }}
                  placeholder="0xabc… 0xdef… (comma or space separated)"
                />
                <button type="button" className="btn-primary text-xs px-3"
                  onClick={() => parseAndAddAddresses(addrInput)} disabled={!addrInput.trim()}>add</button>
              </div>
            )}
            {traders.length > 0 && (
              <div className="space-y-1">
                {traders.map((t, i) => (
                  <div key={t.address} className={`flex items-center gap-2 text-xs rounded px-2 py-1 bg-surface/40${running ? " opacity-60" : ""}`}>
                    <a href={`/trader/${t.address}`} className="font-mono hover:text-accent2 shrink-0 flex-1">{shortAddr(t.address)}</a>
                    <span className="text-muted shrink-0">wt</span>
                    <input type="number" className="input w-16 text-xs" min={0} max={5} step={0.1}
                      value={t.weight ?? 1}
                      onChange={(e) => setTraders((prev) => prev.map((x, j) => j === i ? { ...x, weight: +e.target.value } : x))}
                      disabled={running}
                    />
                    <label className="flex items-center gap-1 shrink-0 cursor-pointer select-none">
                      <input type="checkbox" checked={t.enabled !== false}
                        onChange={(e) => setTraders((prev) => prev.map((x, j) => j === i ? { ...x, enabled: e.target.checked } : x))}
                        disabled={running}
                      />
                      <span className="text-muted">{t.enabled !== false ? "on" : "off"}</span>
                    </label>
                    {!running && (
                      <button type="button" className="text-muted hover:text-danger shrink-0 text-base leading-none"
                        onClick={() => setTraders((prev) => prev.filter((_, j) => j !== i))}>×</button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
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
          <label className="block">
            <span className="text-[11px] text-muted">Coins allow-list (optional, comma-separated)</span>
            <input className="input w-full" value={coinsAllow}
              onChange={(e) => setCoinsAllow(e.target.value)} placeholder="BTC, ETH, SOL" />
          </label>
          <label className="block">
            <span className="text-[11px] text-muted">Coins deny-list (optional, comma-separated)</span>
            <input className="input w-full" value={coinsDeny}
              onChange={(e) => setCoinsDeny(e.target.value)} placeholder="BTC, ETH, SOL" />
          </label>
        </div>
        <div className="flex items-start gap-2 pt-2">
          {running ? (
            // Stopping is never gated behind anything but the session — if the
            // engine is running with your money in it, the brake works first.
            <AuthGate action="stop the engine">
              <button className="btn-danger" disabled={busy} onClick={onStop}>stop</button>
            </AuthGate>
          ) : (
            <AuthGate action="start copy-trading">
              <button className="btn-primary" disabled={busy || approved !== true} onClick={onStart}>start</button>
            </AuthGate>
          )}
          {!running && approved !== true && (
            <span className="text-warn text-xs">approve the agent above first</span>
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
          <>
            <div className="grid grid-cols-4 gap-3 text-sm">
              <Stat label="cycles" value={state.cycleCount ?? 0} />
              <Stat label="placed" value={state.totalOrdersPlaced ?? 0} />
              <Stat label="failed" value={state.totalOrdersFailed ?? 0} />
              <Stat label="volume" value={fmtUsd(state.totalVolumeMirrored ?? 0)} />
              <Stat label="last cycle" value={state.lastCycleAt ? ago(state.lastCycleAt) : "—"} />
              <Stat label="next cycle" value={state.nextCycleAt ? `in ${Math.max(0, Math.round((state.nextCycleAt - Date.now()) / 1000))}s` : "—"} />
            </div>
            {(() => {
              const errors = (state.log ?? []).filter((e: any) => e.type === "ERROR").slice(0, 10);
              if (!errors.length) return null;
              return (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-wider text-danger mb-1">Recent errors</div>
                  <div className="space-y-1">
                    {errors.map((e: any) => (
                      <div key={e.id} className="flex items-start gap-2 text-xs text-danger/80">
                        <span className="text-muted shrink-0">{ago(e.timestamp)}</span>
                        {e.traderAddress && <span className="font-mono shrink-0">{shortAddr(e.traderAddress)}</span>}
                        {e.coin && <span className="shrink-0">{e.coin}</span>}
                        <span className="truncate">{e.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}
          </>
        ) : <div className="text-muted text-sm">no session</div>}
      </section>

      {/* ── My positions ── */}
      {running && accountState && (() => {
        const positions = (accountState.assetPositions ?? [])
          .map((ap: any) => ap.position)
          .filter((p: any) => p && parseFloat(p.szi ?? "0") !== 0);
        if (!positions.length) return null;
        const summary = accountState.crossMarginSummary ?? {};
        const totalPnl = positions.reduce((s: number, p: any) => s + parseFloat(p.unrealizedPnl ?? "0"), 0);
        return (
          <section className="card p-4 space-y-3">
            <div className="text-xs uppercase tracking-wider text-muted">My positions</div>
            <div className="flex gap-4 text-sm">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted">equity</div>
                <div className="tabular-nums">{fmtUsd(parseFloat(summary.accountValue ?? "0"))}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted">margin used</div>
                <div className="tabular-nums">{fmtUsd(parseFloat(summary.totalMarginUsed ?? "0"))}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted">unrealized PnL</div>
                <div className={`tabular-nums ${totalPnl >= 0 ? "text-win" : "text-danger"}`}>
                  {totalPnl >= 0 ? "+" : ""}{fmtUsd(totalPnl)}
                </div>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted">
                  <tr>
                    <th className="text-left py-1 pr-2">coin</th>
                    <th className="text-right pr-2">size</th>
                    <th className="text-right pr-2">entry px</th>
                    <th className="text-right pr-2">unreal. PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p: any) => {
                    const sz = parseFloat(p.szi ?? "0");
                    const pnl = parseFloat(p.unrealizedPnl ?? "0");
                    return (
                      <tr key={p.coin} className="border-t border-border/40">
                        <td className="py-1 pr-2 font-medium">{p.coin}</td>
                        <td className={`pr-2 text-right tabular-nums ${sz >= 0 ? "text-win" : "text-danger"}`}>
                          {sz > 0 ? "+" : ""}{sz}
                        </td>
                        <td className="pr-2 text-right tabular-nums">{fmtUsd(parseFloat(p.entryPx ?? "0"))}</td>
                        <td className={`pr-2 text-right tabular-nums ${pnl >= 0 ? "text-win" : "text-danger"}`}>
                          {pnl >= 0 ? "+" : ""}{fmtUsd(pnl)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })()}

      {/* ── Per-leader activity ── */}
      {(status?.config?.traders?.length ?? 0) > 1 && state && (() => {
        const leaders: LiveTrader[] = status!.config.traders;
        const byLeader: Record<string, any[]> = {};
        for (const { address: addr } of leaders) byLeader[addr] = [];
        for (const t of (state.observedTrades ?? [])) {
          if (byLeader[t.trader] !== undefined) byLeader[t.trader].push(t);
        }
        return (
          <section className="card p-4">
            <div className="text-xs uppercase tracking-wider text-muted mb-2">Per-leader activity</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted">
                  <tr>
                    <th className="text-left py-1 pr-2">leader</th>
                    <th className="text-right pr-2">weight</th>
                    <th className="text-right pr-2">observed</th>
                    <th className="text-right pr-2">mirrored</th>
                    <th className="text-right pr-2">failed</th>
                    <th className="text-right pr-2">volume</th>
                    <th className="text-right pr-2">last sync</th>
                  </tr>
                </thead>
                <tbody>
                  {leaders.map((leader) => {
                    const addr = leader.address;
                    const trades = byLeader[addr] ?? [];
                    const observed = trades.length;
                    const mirrored = trades.filter((x: any) => x.mirrored).length;
                    const failed = trades.filter((x: any) => !x.mirrored).length;
                    const volume = trades.reduce((s: number, x: any) => s + (x.mirrored ? (x.notional ?? 0) : 0), 0);
                    const lastSync = state.traderLastSync?.[addr];
                    const wt = leader.weight ?? 1;
                    const dis = leader.enabled === false;
                    return (
                      <tr key={addr} className={`border-t border-border/40${dis ? " opacity-30" : observed === 0 ? " opacity-40" : ""}`}>
                        <td className="py-1 pr-2 font-mono">
                          <a href={`/trader/${addr}`} className="hover:text-accent2">{shortAddr(addr)}</a>
                          {dis && <span className="ml-1 text-[10px] text-muted">off</span>}
                        </td>
                        <td className="pr-2 text-right tabular-nums text-muted">×{wt.toFixed(1)}</td>
                        <td className="pr-2 text-right tabular-nums">{observed}</td>
                        <td className="pr-2 text-right tabular-nums text-accent2">{mirrored}</td>
                        <td className="pr-2 text-right tabular-nums text-danger">{failed}</td>
                        <td className="pr-2 text-right tabular-nums">{fmtUsd(volume)}</td>
                        <td className="pr-2 text-right text-muted">{lastSync ? ago(lastSync) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })()}

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
