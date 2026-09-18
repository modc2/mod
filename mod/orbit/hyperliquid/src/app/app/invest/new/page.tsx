"use client";

/**
 * The funnel: pick something, then say how much.
 *
 * Two steps, and the second one is always the same panel no matter what you
 * picked. Arriving with `?trader=0x…`, `?vault=0x…` or `?strat=<id>` skips
 * straight to the amount — which is how every "invest" button elsewhere in the
 * console links here.
 */

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  fetchTopTraders, listIndexes, listVaults, fmtPct, fmtPnl, fmtUsd, shortAddr,
  type Index, type TopTrader, type Vault,
} from "../../lib/api";
import InvestPanel from "../../components/InvestPanel";
import { Identicon, PageHead } from "../../components/BoardBits";

type Kind = "trader" | "vault" | "strat";

const KINDS: { key: Kind; label: string; blurb: string }[] = [
  { key: "trader", label: "a trader", blurb: "Your account holds what they hold, scaled to your money. Nothing locked up." },
  { key: "vault", label: "a vault", blurb: "Hyperliquid's own vaults. The leader trades your USDC; HL keeps the books." },
  { key: "strat", label: "a basket", blurb: "Split one amount across several traders at once, by weight." },
];

export default function NewInvestmentPage() {
  return (
    <Suspense fallback={<div className="text-xs text-muted">loading…</div>}>
      <NewInvestment />
    </Suspense>
  );
}

function NewInvestment() {
  const params = useSearchParams();
  const router = useRouter();

  const preTrader = params.get("trader");
  const preVault = params.get("vault");
  const preStrat = params.get("strat");
  const initialKind = (params.get("kind") as Kind | null)
    ?? (preVault ? "vault" : preStrat ? "strat" : "trader");

  const [kind, setKind] = useState<Kind>(initialKind);
  const [target, setTarget] = useState<string | null>(preTrader ?? preVault ?? preStrat);
  const [name, setName] = useState<string | undefined>(params.get("name") ?? undefined);
  const [legs, setLegs] = useState<{ address: string; weight: number }[] | undefined>();

  // A strat's legs are needed to preview the basket honestly.
  useEffect(() => {
    if (kind !== "strat" || !target) { setLegs(undefined); return; }
    listIndexes()
      .then(({ indexes }) => {
        const idx = indexes.find((i) => i.id === target);
        if (idx) { setLegs(idx.legs); setName((n) => n ?? idx.name); }
      })
      .catch(() => {});
  }, [kind, target]);

  const pick = useCallback((k: Kind, addr: string, label?: string, l?: { address: string; weight: number }[]) => {
    setKind(k); setTarget(addr); setName(label); setLegs(l);
  }, []);

  return (
    <section className="space-y-5 max-w-3xl">
      <PageHead
        title={target ? "How much?" : "What do you want to back?"}
        blurb={target
          ? "The number you type is the number that gets deployed. You'll see exactly what it buys before anything happens."
          : "Pick someone to trade your money. You can change your mind, pause, or pull it back at any time."}
        right={<Link href="/invest" className="hover:text-ink">your positions →</Link>}
      />

      {/* Step 1 — what */}
      {!target ? (
        <>
          <div className="flex flex-wrap gap-1">
            {KINDS.map((k) => (
              <button key={k.key} onClick={() => setKind(k.key)}
                className={`btn ${kind === k.key ? "border-accent text-accent" : ""}`}>
                {k.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted -mt-2">{KINDS.find((k) => k.key === kind)?.blurb}</p>

          {kind === "trader" && <TraderPicker onPick={(t) => pick("trader", t.address)} />}
          {kind === "vault" && <VaultPicker onPick={(v) => pick("vault", v.address, v.name)} />}
          {kind === "strat" && <StratPicker onPick={(i) => pick("strat", i.id, i.name, i.legs)} />}
        </>
      ) : (
        <>
          <div className="panel px-4 py-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 min-w-0">
              <Identicon address={target} size={26} />
              <div className="min-w-0">
                <div className="text-sm text-ink truncate">{name || shortAddr(target)}</div>
                <div className="text-[11px] text-muted font-mono truncate">
                  {kind === "strat" ? `${legs?.length ?? 0} traders` : shortAddr(target)}
                </div>
              </div>
            </div>
            <button className="btn" onClick={() => { setTarget(null); setName(undefined); setLegs(undefined); router.replace("/invest/new"); }}>
              change
            </button>
          </div>

          <InvestPanel kind={kind} target={target} name={name} legs={legs} />

          {kind === "trader" && (
            <Link href={`/trader/${target}`} className="text-[11px] text-accent2 hover:text-accent">
              look at this trader's record first →
            </Link>
          )}
          {kind === "vault" && (
            <Link href={`/vaults/${target}`} className="text-[11px] text-accent2 hover:text-accent">
              look inside this vault first →
            </Link>
          )}
        </>
      )}
    </section>
  );
}

// ─── pickers ────────────────────────────────────────────────────────────

function Frame({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="panel divide-y divide-white/[0.05]">
      {children}
      {hint && <div className="px-4 py-2 text-[11px] text-muted">{hint}</div>}
    </div>
  );
}

function PasteRow({ label, onPick }: { label: string; onPick: (addr: string) => void }) {
  const [v, setV] = useState("");
  const ok = /^0x[a-fA-F0-9]{40}$/.test(v.trim());
  return (
    <div className="px-4 py-3 flex items-center gap-2">
      <input className="input font-mono text-xs flex-1" placeholder={label}
        value={v} onChange={(e) => setV(e.target.value)} />
      <button className="btn" disabled={!ok} onClick={() => onPick(v.trim().toLowerCase())}>use</button>
    </div>
  );
}

function TraderPicker({ onPick }: { onPick: (t: TopTrader) => void }) {
  const [rows, setRows] = useState<TopTrader[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetchTopTraders(7, 1, 25).then((r) => setRows(r.traders)).catch((e) => setErr(String(e?.message ?? e)));
  }, []);

  return (
    <Frame hint="Ranked by 7-day return on the whole Hyperliquid leaderboard. Any wallet works — paste one above.">
      <PasteRow label="or paste any trader's 0x… address" onPick={(a) => onPick({ address: a } as TopTrader)} />
      {err && <div className="px-4 py-3 text-xs text-loss">{err}</div>}
      {!rows && !err && [...Array(5)].map((_, i) => (
        <div key={i} className="px-4 py-3"><div className="skeleton h-4 w-full" /></div>
      ))}
      {rows?.slice(0, 12).map((t, i) => (
        <button key={t.address} onClick={() => onPick(t)}
          className="group w-full px-4 py-2.5 grid grid-cols-[auto_1.4fr_repeat(3,1fr)_auto] gap-3 items-center text-left hover:bg-accent/[0.04] transition-colors">
          <span className="num text-[11px] text-muted w-5">{i + 1}</span>
          <span className="flex items-center gap-2 min-w-0">
            <Identicon address={t.address} size={20} />
            <span className="font-mono text-xs truncate">{shortAddr(t.address)}</span>
          </span>
          <Stat label="7d return" value={fmtPct(t.roi ?? 0, 0)} cls={(t.roi ?? 0) >= 0 ? "text-win" : "text-loss"} />
          <Stat label="7d profit" value={fmtPnl(t.pnl ?? 0)} cls={(t.pnl ?? 0) >= 0 ? "text-win" : "text-loss"} />
          <Stat label="account" value={fmtUsd(t.account_value ?? 0)} />
          <span className="btn-ghost opacity-0 group-hover:opacity-100 transition-opacity">pick</span>
        </button>
      ))}
    </Frame>
  );
}

function VaultPicker({ onPick }: { onPick: (v: Vault) => void }) {
  const [rows, setRows] = useState<Vault[] | null>(null);
  useEffect(() => { listVaults(25, 50_000).then((r) => setRows(r.vaults)).catch(() => setRows([])); }, []);
  return (
    <Frame hint="Open vaults with real money in them, ranked by Hyperliquid's published APR. That figure is trailing, not a promise.">
      <PasteRow label="or paste a vault's 0x… address" onPick={(a) => onPick({ address: a, name: "" } as Vault)} />
      {!rows && [...Array(5)].map((_, i) => (
        <div key={i} className="px-4 py-3"><div className="skeleton h-4 w-full" /></div>
      ))}
      {rows?.slice(0, 12).map((v) => (
        <button key={v.address} onClick={() => onPick(v)}
          className="group w-full px-4 py-2.5 grid grid-cols-[1.6fr_repeat(3,1fr)_auto] gap-3 items-center text-left hover:bg-accent/[0.04] transition-colors">
          <span className="flex items-center gap-2 min-w-0">
            <Identicon address={v.address} size={20} />
            <span className="text-xs truncate">{v.name || shortAddr(v.address)}</span>
          </span>
          <Stat label="apr" value={fmtPct(v.apr, 0)} cls={v.apr >= 0 ? "text-win" : "text-loss"} />
          <Stat label="size" value={fmtUsd(v.tvl)} />
          <Stat label="age" value={`${v.age_days}d`} />
          <span className="btn-ghost opacity-0 group-hover:opacity-100 transition-opacity">pick</span>
        </button>
      ))}
    </Frame>
  );
}

function StratPicker({ onPick }: { onPick: (i: Index) => void }) {
  const [rows, setRows] = useState<Index[] | null>(null);
  useEffect(() => { listIndexes().then((r) => setRows(r.indexes)).catch(() => setRows([])); }, []);
  if (rows && rows.length === 0) {
    return (
      <div className="panel p-6 text-center text-xs text-muted">
        No baskets saved yet. <Link href="/strats/new" className="text-accent2 hover:text-accent">Build one →</Link>
      </div>
    );
  }
  return (
    <Frame hint="A basket splits your money across its traders by weight — each leg becomes its own position.">
      {!rows && [...Array(3)].map((_, i) => (
        <div key={i} className="px-4 py-3"><div className="skeleton h-4 w-full" /></div>
      ))}
      {rows?.map((i) => (
        <button key={i.id} onClick={() => onPick(i)}
          className="group w-full px-4 py-3 flex items-center justify-between gap-3 text-left hover:bg-accent/[0.04] transition-colors">
          <span className="min-w-0">
            <span className="text-sm text-ink block truncate">{i.name}</span>
            <span className="text-[11px] text-muted">
              {i.legs.length} trader{i.legs.length === 1 ? "" : "s"}
              {i.description ? ` · ${i.description}` : ""}
            </span>
          </span>
          <span className="btn-ghost opacity-0 group-hover:opacity-100 transition-opacity">pick</span>
        </button>
      ))}
    </Frame>
  );
}

function Stat({ label, value, cls = "text-ink" }: { label: string; value: string; cls?: string }) {
  return (
    <span className="text-right">
      <span className="block text-[9px] uppercase tracking-wider text-muted">{label}</span>
      <span className={`block num text-xs ${cls}`}>{value}</span>
    </span>
  );
}
