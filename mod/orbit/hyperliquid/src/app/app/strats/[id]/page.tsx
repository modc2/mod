"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  getIndex, indexPerf, deleteIndex, vaultIntent, updateIndex,
  Index, fmtPnl, fmtUsd, fmtPct, shortAddr, ago,
} from "../../lib/api";
import { useSession } from "../../lib/auth";
import AuthGate, { AuthGateInline } from "../../components/AuthGate";
import VaultTransferPanel from "../../components/VaultTransferPanel";
import InvestPanel from "../../components/InvestPanel";

export default function StratDetail() {
  const { id } = useParams<{ id: string }>();
  // Ownership decides what is *shown* — a watched address should still see
  // that a strat is its own. Whether the buttons *work* is a separate
  // question, and AuthGate is the one that asks it.
  const { address } = useSession();
  const [idx, setIdx] = useState<Index | null>(null);
  const [perf, setPerf] = useState<any>(null);
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [intent, setIntent] = useState<any>(null);
  const [initialUsd, setInitialUsd] = useState(100);
  const [vaultAddr, setVaultAddr] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const isOwner = idx && address && idx.owner.toLowerCase() === address.toLowerCase();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const i = await getIndex(id);
      setIdx(i);
      setVaultAddr(i.vault_address || "");
      setDays(i.days_window || 7);
      const p = await indexPerf(id, i.days_window || 7);
      setPerf(p);
    } finally { setLoading(false); }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Every write on this page went through a bare `await` with no catch, so a
  // refused one failed in total silence — the button clicked, nothing moved,
  // and the reason stayed in the network tab. One wrapper, one error line.
  const attempt = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); } catch (e: any) { setErr(e?.message ?? String(e)); }
    finally { setBusy(false); }
  };

  const onDelete = () => attempt(async () => {
    if (!confirm(`Delete "${idx?.name ?? "this strat"}"? This can't be undone.`)) return;
    await deleteIndex(id);
    window.location.href = "/strats";
  });

  const buildVaultIntent = () => attempt(async () => setIntent(await vaultIntent(id, initialUsd)));

  const linkVault = () => attempt(async () => {
    if (!idx || !vaultAddr.trim()) return;
    await updateIndex(id, { vault_address: vaultAddr.trim() });
    await load();
  });

  const reload = async (d?: number) => {
    if (!idx) return;
    const p = await indexPerf(id, d ?? days);
    setPerf(p);
    if (d) setDays(d);
  };

  if (loading) return <div className="text-xs text-muted">loading…</div>;
  if (!idx) return <div className="text-xs text-loss">strat not found</div>;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Link href="/strats" className="text-[11px] text-muted hover:text-ink">← strats</Link>
          <h1 className="font-display font-bold text-2xl tracking-tight text-ink mt-1">{idx.name}</h1>
          <div className="text-[10px] uppercase tracking-wider text-muted">
            by {shortAddr(idx.owner)} · {idx.legs.length} traders · created {ago(idx.created_ms)}
          </div>
        </div>
        <div className="flex gap-2 shrink-0">
          <Link href={`/strats/new?fork=${idx.id}`} className="btn-primary">fork this strat</Link>
          {isOwner && (
            <AuthGateInline action="delete this strat">
              <button className="btn-danger" onClick={onDelete} disabled={busy}>delete</button>
            </AuthGateInline>
          )}
        </div>
      </div>

      {err && <div className="panel p-3 text-xs text-loss">{err}</div>}

      {idx.description && (
        <div className="panel p-3 text-xs text-muted">{idx.description}</div>
      )}

      {/* Perf */}
      <div className="grid md:grid-cols-3 gap-3">
        <Tile label={`weighted pnl (${perf?.days || days}d)`}
          value={fmtPnl(perf?.weighted_pnl ?? 0)}
          tone={(perf?.weighted_pnl ?? 0) >= 0 ? "win" : "loss"} />
        <Tile label="raw pnl (sum traders)" value={fmtPnl(perf?.total_pnl ?? 0)} />
        <Tile label="traders" value={`${idx.legs.length}`} />
      </div>

      {/* Back the whole basket in one amount: each leg becomes its own
          position, sized by weight, managed from the Invest page. */}
      <InvestPanel kind="strat" target={idx.id} name={idx.name} legs={idx.legs} />

      <div className="panel">
        <div className="px-4 py-2 border-b border-border flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wider text-muted">trader performance</span>
          <div className="flex gap-1">
            {[1,7,30].map((d) =>
              <button key={d} onClick={() => reload(d)}
                className={`btn ${perf?.days === d ? "border-accent text-accent" : ""}`}>{d}d</button>
            )}
          </div>
        </div>
        <div className="grid grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 border-b border-border text-[10px] uppercase tracking-wider text-muted">
          <div>address</div>
          <div className="text-right">weight</div>
          <div className="text-right">pnl</div>
          <div className="text-right">volume</div>
          <div className="text-right">win%</div>
          <div className="text-right">trades</div>
        </div>
        {(perf?.legs ?? []).map((l: any) => (
          <Link key={l.address} href={`/trader/${l.address}?days=${perf?.days || days}`}
            className="grid grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 table-row hover:bg-panel2/40">
            <div className="text-accent2 text-xs num">{shortAddr(l.address)}</div>
            <div className="num text-right">{(l.weight * 100).toFixed(1)}%</div>
            <div className={`num text-right ${l.pnl >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(l.pnl)}</div>
            <div className="num text-right">{fmtUsd(l.volume)}</div>
            <div className="num text-right">{l.win_rate < 0 ? "—" : fmtPct(l.win_rate, 0)}</div>
            <div className="num text-right">{l.trades}</div>
          </Link>
        ))}
      </div>

      {/* Vault */}
      <div className="panel p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base text-ink">private vault</h2>
            <p className="text-[11px] text-muted">
              An on-chain Hyperliquid vault that mirrors this strat. Only you (the owner) can deposit/withdraw.
            </p>
          </div>
          {idx.vault_address && (
            <span className="pill border-accent/40 text-accent">linked</span>
          )}
        </div>

        {idx.vault_address ? (
          <div className="space-y-3">
            <div className="text-xs">
              linked vault:&nbsp;
              <a className="text-accent2 hover:underline num"
                href={`https://app.hyperliquid.xyz/explorer/address/${idx.vault_address}`}
                target="_blank" rel="noreferrer">{idx.vault_address}</a>
            </div>
            {/* Deposit/withdraw USDC into the strat's vault — same rails as
                a native vault (agent-signed, authorized once via MetaMask). */}
            <VaultTransferPanel vault={idx.vault_address} vaultName={idx.name} />
          </div>
        ) : (
          isOwner ? (
            <div className="space-y-3">
              <div className="grid grid-cols-[1fr_auto] gap-2">
                <input className="input num" type="number" min={1} step={1}
                  value={initialUsd} onChange={(e) => setInitialUsd(Number(e.target.value))} />
                <AuthGate action="create this strat's vault">
                  <button className="btn-primary" onClick={buildVaultIntent} disabled={busy}>
                    build vault-create payload
                  </button>
                </AuthGate>
              </div>
              {intent && (
                <div className="bg-panel2 p-3 rounded text-[11px] space-y-2">
                  <div className="text-muted">
                    Sign this action with your owner key (e.g. via Hyperliquid web UI / SDK), then submit
                    via <code>POST /hl/forward</code> with <code>fn:"exchange_post"</code>.
                  </div>
                  <pre className="overflow-auto text-xs num leading-snug">{JSON.stringify(intent, null, 2)}</pre>
                </div>
              )}
              <div className="border-t border-border pt-3">
                <div className="label">already created? paste vault address to link</div>
                <div className="grid grid-cols-[1fr_auto] gap-2">
                  <input className="input num" placeholder="0xvault…"
                    value={vaultAddr} onChange={(e) => setVaultAddr(e.target.value)} />
                  <AuthGateInline action="link a vault">
                    <button className="btn" onClick={linkVault} disabled={busy}>link</button>
                  </AuthGateInline>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-xs text-muted">only the owner can create or link a vault.</div>
          )
        )}
      </div>
    </div>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: "win" | "loss" }) {
  return (
    <div className="panel p-3">
      <div className="stat">{label}</div>
      <div className={`text-lg num mt-1 ${tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : ""}`}>
        {value}
      </div>
    </div>
  );
}
