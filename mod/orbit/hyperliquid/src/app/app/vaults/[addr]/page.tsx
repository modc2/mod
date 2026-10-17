"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { vaultDetails, analyzeTrader, fmtUsd, fmtPnl, fmtPct, shortAddr } from "../../lib/api";
import { useWallet } from "../../lib/wallet";
import PnlChart from "../../components/PnlChart";
import VaultTransferPanel from "../../components/VaultTransferPanel";

const CHART_WINDOWS = [
  { label: "1D", days: 1 },
  { label: "7D", days: 7 },
  { label: "30D", days: 30 },
  { label: "ALL", days: 365 },
] as const;

export default function VaultDetailPage() {
  const params = useParams();
  const addr = String(params.addr || "").toLowerCase();
  const { address: eoa } = useWallet();

  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Chart window + the vault's own trading account (a vault IS an HL account,
  // so the trader-analyze endpoint yields its open positions).
  const [chartDays, setChartDays] = useState<number>(30);
  const [an, setAn] = useState<any>(null);
  const [anErr, setAnErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      setD(await vaultDetails(addr, eoa ?? undefined));
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setLoading(false); }
  }, [addr, eoa]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let alive = true;
    setAnErr(null);
    analyzeTrader(addr, 7)
      .then((a) => { if (alive) setAn(a); })
      .catch((e) => { if (alive) setAnErr(e.message ?? String(e)); });
    return () => { alive = false; };
  }, [addr]);

  if (loading && !d) return <div className="text-xs text-muted">loading vault…</div>;

  const apr = Number(d?.apr ?? 0) * 100; // detail endpoint returns a fraction
  const commission = Number(d?.leaderCommission ?? 0) * 100;
  const depositsOpen = d?.allowDeposits !== false && !d?.isClosed;

  return (
    <div className="max-w-3xl space-y-5">
      <Link href="/vaults" className="text-[11px] text-muted hover:text-ink">← all vaults</Link>

      <div>
        <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight">{d?.name || shortAddr(addr)}</h1>
        {d?.description && <p className="text-xs text-muted mt-1">{d.description}</p>}
        <div className="text-[11px] text-muted mt-1">
          leader <Link href={`/trader/${d?.leader}`} className="font-mono text-accent2 hover:text-accent">{shortAddr(d?.leader || "")}</Link>
          {" · "}<span className="font-mono">{shortAddr(addr)}</span>
        </div>
      </div>

      {/* Stats */}
      <div className="panel p-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Stat label="APR" value={`${apr >= 0 ? "+" : ""}${fmtPct(apr, 0)}`} cls={apr >= 0 ? "text-win" : "text-loss"} />
        <Stat label="TVL" value={fmtUsd(Number(d?.maxDistributable ?? 0))} />
        <Stat label="leader fee" value={fmtPct(commission, 0)} />
        <Stat label="deposits" value={depositsOpen ? "open" : "closed"} cls={depositsOpen ? "text-win" : "text-loss"} />
      </div>

      {/* Portfolio history — the vault's equity / PnL curve from HL's
          vaultDetails portfolio payload (same shape as a trader's). */}
      <div className="panel">
        <div className="px-4 py-2 border-b border-border flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wider text-muted">portfolio history</span>
          <div className="flex gap-1">
            {CHART_WINDOWS.map((wnd) => (
              <button key={wnd.label} onClick={() => setChartDays(wnd.days)}
                className={`px-2 py-0.5 rounded-full border text-[10px] uppercase tracking-wider transition-colors ${
                  chartDays === wnd.days
                    ? "border-accent/50 bg-accent/10 text-ink"
                    : "border-white/[0.08] bg-white/[0.03] text-muted hover:text-ink"
                }`}>
                {wnd.label}
              </button>
            ))}
          </div>
        </div>
        <PnlChart portfolio={d?.portfolio} days={chartDays} />
      </div>

      {/* Inside the vault — its live open positions */}
      <div className="panel">
        <div className="px-4 py-2 border-b border-border flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wider text-muted">inside the vault · open positions</span>
          <Link href={`/trader/${addr}`} className="text-[11px] text-accent2 hover:text-accent">
            full analytics →
          </Link>
        </div>
        <VaultPositions an={an} err={anErr} />
      </div>

      {/* Your position + invest / withdraw (agent-signed, MetaMask-authorized) */}
      <VaultTransferPanel vault={addr} vaultName={d?.name} />

      {err && <div className="text-xs text-loss break-words">{err}</div>}
    </div>
  );
}

function VaultPositions({ an, err }: { an: any; err: string | null }) {
  if (err) return <div className="px-4 py-6 text-xs text-loss break-words">{err}</div>;
  if (!an) return <div className="px-4 py-6 text-xs text-muted">loading positions…</div>;
  const positions = ((an.state?.assetPositions ?? []) as any[])
    .map((p) => p.position ?? p)
    .filter((pos) => Number(pos.szi || 0) !== 0);
  if (positions.length === 0)
    return <div className="px-4 py-6 text-xs text-muted">no open positions — the vault is fully in USDC right now.</div>;
  return (
    <>
      <div className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 text-[10px] uppercase tracking-wider text-muted border-b border-border">
        <div>coin</div><div className="text-right">size</div>
        <div className="text-right">value</div><div className="text-right">entry</div>
        <div className="text-right">unrealized</div><div className="text-right">leverage</div>
      </div>
      {positions.map((pos, i) => {
        const sz = Number(pos.szi || 0);
        const upnl = Number(pos.unrealizedPnl || 0);
        return (
          <div key={i} className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 table-row">
            <div className="text-ink">{pos.coin}</div>
            <div className={`num text-right ${sz >= 0 ? "text-win" : "text-loss"}`}>{sz}</div>
            <div className="num text-right">{fmtUsd(Number(pos.positionValue || 0))}</div>
            <div className="num text-right">{Number(pos.entryPx || 0).toFixed(4)}</div>
            <div className={`num text-right ${upnl >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(upnl)}</div>
            <div className="num text-right">{pos.leverage?.value ?? "—"}x</div>
          </div>
        );
      })}
    </>
  );
}

function Stat({ label, value, cls = "text-ink" }: { label: string; value: string; cls?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`num text-sm mt-0.5 ${cls}`}>{value}</div>
    </div>
  );
}
