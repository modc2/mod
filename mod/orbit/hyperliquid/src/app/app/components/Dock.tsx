"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  agentStatus, deleteIndex, indexPerf, listIndexes, userState,
  ago, fmtPnl, fmtUsd, shortAddr, Index,
} from "../lib/api";
import { portfolio, type Portfolio } from "../lib/invest";
import { useWallet } from "../lib/wallet";
import { useSession } from "../lib/auth";
import { DOCK_TABS, useDock, type DockTab } from "../lib/dock";
import { Identicon } from "./BoardBits";
import AskConsole from "./AskConsole";

// ── The desk ──────────────────────────────────────────────────────────────
// Three things you need on every page and shouldn't have to navigate away to
// get: the agent, the strats you run, and the account they run on. Docks as a
// column at ≥1280px (globals.css reserves the width off `data-dock`), and
// slides over the page as a drawer below that.

const row = "flex items-center justify-between gap-2";

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "win" | "loss" }) {
  return (
    <div className="panel px-3 py-2">
      <div className="eyebrow">{label}</div>
      <div className={`mt-1 text-[15px] font-semibold num leading-none
        ${tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : "text-ink"}`}>{value}</div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="panel p-4 text-[11px] leading-snug text-muted">{children}</div>;
}

// ── My strats ─────────────────────────────────────────────────────────────
// "Mine" = baskets this wallet owns. Vaults and traders on the board belong
// to nobody, so they have no place in a book of what *you* manage.
type Perf = { weighted_pnl: number; days: number } | "loading" | "err";

function StratsTab({ onNavigate }: { onNavigate: () => void }) {
  const { address } = useWallet();
  const { canWrite } = useSession();
  const [mine, setMine] = useState<Index[] | null>(null);
  const [perf, setPerf] = useState<Record<string, Perf>>({});

  const load = useCallback(async () => {
    if (!address) { setMine(null); return; }
    const { indexes } = await listIndexes();
    const owned = indexes.filter((i) => i.owner?.toLowerCase() === address.toLowerCase());
    setMine(owned);
    setPerf(Object.fromEntries(owned.map((i) => [i.id, "loading" as Perf])));
    owned.forEach((i) => {
      indexPerf(i.id, i.days_window || 7)
        .then((p) => setPerf((m) => ({ ...m, [i.id]: { weighted_pnl: p.weighted_pnl ?? 0, days: p.days ?? i.days_window } })))
        .catch(() => setPerf((m) => ({ ...m, [i.id]: "err" })));
    });
  }, [address]);
  useEffect(() => { load().catch(() => setMine([])); }, [load]);

  const onDelete = async (id: string) => {
    if (!confirm("delete strat?")) return;
    await deleteIndex(id);
    load().catch(() => {});
  };

  if (!address) return <Empty>Connect a wallet to see the strats you manage.</Empty>;

  return (
    <div className="space-y-2">
      <div className={row}>
        <span className="eyebrow">{mine ? `${mine.length} managed` : "loading…"}</span>
        <Link href="/strats/new" className="btn-ghost" onClick={onNavigate}>+ new</Link>
      </div>

      {mine?.length === 0 && (
        <Empty>
          You don&apos;t manage any strats yet. A strat is a basket of traders you
          weight yourself — build one from the board, or let the desk agent
          propose the legs.
        </Empty>
      )}

      {mine?.map((i) => {
        const p = perf[i.id];
        const pnl = typeof p === "object" ? p.weighted_pnl : null;
        return (
          <div key={i.id} className="panel panel-hover group p-3 space-y-2">
            <div className={row}>
              <Link href={`/strats/${i.id}`} onClick={onNavigate}
                className="min-w-0 text-[13px] font-semibold text-ink truncate hover:text-accent transition-colors">
                {i.name}
              </Link>
              <span className={`text-[11px] num shrink-0 ${pnl == null ? "text-dim" : pnl >= 0 ? "text-win" : "text-loss"}`}>
                {p === "loading" ? "…" : pnl == null ? "—" : fmtPnl(pnl)}
              </span>
            </div>
            <div className="flex items-center gap-1 flex-wrap">
              {i.legs.slice(0, 5).map((l) => (
                <span key={l.address} title={`${l.address} · ${(l.weight * 100).toFixed(0)}%`}>
                  <Identicon address={l.address} size={14} />
                </span>
              ))}
              <span className="pill ml-1">{i.legs.length} legs · {i.days_window}d</span>
            </div>
            <div className={row}>
              <span className="text-[10px] text-dim">built {ago(i.created_ms)}</span>
              <div className="flex items-center gap-2">
                <Link href={`/invest/new?strat=${i.id}`} className="btn-ghost !py-0.5" onClick={onNavigate}>invest</Link>
                {canWrite && (
                  <button className="text-[10px] uppercase tracking-wider text-dim hover:text-loss transition-colors"
                    onClick={() => onDelete(i.id)}>delete</button>
                )}
              </div>
            </div>
          </div>
        );
      })}

      <Link href="/strats" className="btn w-full" onClick={onNavigate}>all strats →</Link>
    </div>
  );
}

// ── My wallet ─────────────────────────────────────────────────────────────
function WalletTab({ onNavigate }: { onNavigate: () => void }) {
  const { address, kind, hasProvider, connect, disconnect } = useWallet();
  const { canWrite, needsSignIn, signIn, me } = useSession();
  const [state, setState] = useState<any>(null);
  const [book, setBook] = useState<Portfolio | null>(null);
  const [agent, setAgent] = useState<{ approved: boolean } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!address) { setState(null); setBook(null); setAgent(null); return; }
    userState(address).then(setState).catch(() => setState(null));
    agentStatus(address).then((r) => setAgent({ approved: r.approved })).catch(() => setAgent(null));
  }, [address]);

  // The invest book is wallet-scoped: asking for it without a token is a
  // guaranteed 401, so a watch-only session simply doesn't have one.
  useEffect(() => {
    if (!me) { setBook(null); return; }
    portfolio(me).then(setBook).catch(() => setBook(null));
  }, [me]);

  if (!address) {
    return (
      <div className="space-y-3">
        <Empty>Connect MetaMask to see your balance, positions and the strats you back.</Empty>
        <button className="btn-primary w-full" onClick={() => connect()} disabled={!hasProvider}>
          {hasProvider ? "connect metamask" : "MetaMask not detected"}
        </button>
      </div>
    );
  }

  const accountValue = Number(state?.marginSummary?.accountValue ?? 0);
  const withdrawable = Number(state?.withdrawable ?? 0);
  const positions: any[] = state?.assetPositions ?? [];
  const unrealized = positions.reduce((s, p) => s + Number(p?.position?.unrealizedPnl ?? 0), 0);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  };

  return (
    <div className="space-y-3">
      <div className="panel p-3 space-y-2">
        <div className={row}>
          <div className="flex items-center gap-2 min-w-0">
            <Identicon address={address} size={18} />
            <button className="font-mono text-[12px] text-ink hover:text-accent transition-colors truncate"
              onClick={copy} title="Copy address">
              {copied ? "copied" : shortAddr(address)}
            </button>
          </div>
          <span className={`pill ${canWrite ? "text-accent border-accent/30" : "text-warn border-warn/30"}`}>
            {canWrite ? "signed in" : kind === "watch" ? "watch only" : "signed out"}
          </span>
        </div>
        {needsSignIn && (
          <button className="btn-primary w-full" disabled={busy}
            onClick={async () => { setBusy(true); try { await signIn(); } finally { setBusy(false); } }}>
            sign in to trade
          </button>
        )}
        {agent && !agent.approved && canWrite && (
          <Link href="/wallet" className="btn w-full" onClick={onNavigate}>authorize trading agent →</Link>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Stat label="Account value" value={fmtUsd(accountValue)} />
        <Stat label="Withdrawable" value={fmtUsd(withdrawable)} />
        <Stat label="Unrealized" value={fmtPnl(unrealized)} tone={unrealized >= 0 ? "win" : "loss"} />
        <Stat label="Invested" value={book ? fmtUsd(book.totals.equity) : canWrite ? "…" : "—"}
          tone={book && book.totals.pnl !== 0 ? (book.totals.pnl > 0 ? "win" : "loss") : undefined} />
      </div>

      {positions.length > 0 && (
        <div className="panel divide-y divide-white/[0.05]">
          <div className="eyebrow px-3 py-2">Open positions</div>
          {positions.slice(0, 8).map((p, i) => {
            const pos = p.position ?? {};
            const sz = Number(pos.szi ?? 0);
            const pnl = Number(pos.unrealizedPnl ?? 0);
            return (
              <div key={i} className={`${row} px-3 py-2 text-[11px]`}>
                <span className="font-semibold text-ink">{pos.coin}</span>
                <span className={`num ${sz >= 0 ? "text-win" : "text-loss"}`}>
                  {sz >= 0 ? "long" : "short"} {fmtUsd(Math.abs(Number(pos.positionValue ?? 0)))}
                </span>
                <span className={`num ${pnl >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(pnl)}</span>
              </div>
            );
          })}
        </div>
      )}

      {book && book.positions.length > 0 && (
        <div className="panel divide-y divide-white/[0.05]">
          <div className="eyebrow px-3 py-2">Backing</div>
          {book.positions.slice(0, 6).map((p) => (
            <Link key={p.id} href={`/invest/${p.id}`} onClick={onNavigate}
              className={`${row} px-3 py-2 text-[11px] hover:bg-white/[0.03] transition-colors`}>
              <span className="text-ink truncate">{p.name}</span>
              <span className={`num shrink-0 ${p.value.pnl >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(p.value.pnl)}</span>
            </Link>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <Link href="/wallet" className="btn" onClick={onNavigate}>deposit</Link>
        <Link href="/wallet" className="btn" onClick={onNavigate}>withdraw</Link>
      </div>
      <button className="btn-danger w-full" onClick={disconnect}>disconnect</button>
    </div>
  );
}

// ── Shell ─────────────────────────────────────────────────────────────────
export default function Dock() {
  const { open, tab, setTab, close } = useDock();
  // Mount on first open and stay mounted: closing the desk mid-answer must
  // not throw the transcript away.
  const [ever, setEver] = useState(false);
  useEffect(() => { if (open) setEver(true); }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  return (
    <>
      {/* Below the dock breakpoint the column floats over the page, so it
          needs a scrim to say the page behind it is not the subject. */}
      {open && <div className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px] xl:hidden" onClick={close} aria-hidden />}

      <aside
        aria-hidden={!open}
        className={`fixed top-0 right-0 z-40 h-screen w-[var(--dock-w)] max-w-[92vw]
          border-l border-white/[0.08] bg-bg/85 backdrop-blur-xl shadow-lift
          flex flex-col transition-transform duration-200 ease-out
          ${open ? "translate-x-0" : "translate-x-full pointer-events-none"}`}
      >
        <div className="flex items-center gap-2 px-3 h-14 shrink-0 border-b border-white/[0.06]">
          <div className="seg">
            {DOCK_TABS.map((t) => (
              <button key={t.key}
                className={`seg-btn ${tab === t.key ? "seg-btn-active" : ""}`}
                onClick={() => setTab(t.key as DockTab)}>
                {t.label}
              </button>
            ))}
          </div>
          <button className="ml-auto h-7 w-7 grid place-items-center rounded-md text-muted hover:text-ink hover:bg-white/[0.05] transition-colors"
            onClick={close} aria-label="Close desk">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2.5" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>

        {/* Only the mounted tab runs: the agent keeps its transcript while you
            flip to the wallet, so a long answer isn't lost to a tab click. */}
        <div className="flex-1 min-h-0 overflow-hidden">
          <div className={`h-full p-3 ${tab === "agent" ? "block" : "hidden"}`}>
            {ever && <AskConsole compact />}
          </div>
          <div className={`h-full overflow-y-auto p-3 ${tab === "strats" ? "block" : "hidden"}`}>
            {ever && <StratsTab onNavigate={close} />}
          </div>
          <div className={`h-full overflow-y-auto p-3 ${tab === "wallet" ? "block" : "hidden"}`}>
            {ever && <WalletTab onNavigate={close} />}
          </div>
        </div>
      </aside>
    </>
  );
}
