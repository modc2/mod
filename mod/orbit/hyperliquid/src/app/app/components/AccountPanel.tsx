"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  agentStatus, depositChains, userFunding, userLedger, userPnl, userSpot, userState, walletConfig,
  ago, fmtPnl, fmtUsd, shortAddr,
  type DepositChains, type LedgerUpdate, type WalletNetConfig,
} from "../lib/api";
import {
  approveAgentFlow, bridgeDepositFlow, crossChainWithdrawFlow, usdClassTransferFlow,
  usdSendFlow, withdrawFlow, type WithdrawStep,
} from "../lib/hlActions";
import { portfolio, type Portfolio } from "../lib/invest";
import { useWallet } from "../lib/wallet";
import { useSession } from "../lib/auth";
import { Identicon } from "./BoardBits";
import DepositPanel from "./DepositPanel";
import PnlChart from "./PnlChart";

// ── The account sidebar ───────────────────────────────────────────────────
// Everything you do *to your own account* lives here, beside whatever page
// you're on: money in (12 chains), money out (any chain), perp <-> spot,
// sending to another HL account, the one-time agent signature, and a ledger
// of all of it. Each op is a MetaMask-signed user action — the backend agent
// can trade for you but never move money out.

export type WalletOp = "deposit" | "withdraw" | "move" | "send";
const OPS: { key: WalletOp; label: string }[] = [
  { key: "deposit", label: "Deposit" },
  { key: "withdraw", label: "Withdraw" },
  { key: "move", label: "Perp/Spot" },
  { key: "send", label: "Send" },
];
const OP_KEY = "hl.dock.op";
export const WALLET_OP_EVENT = "hl:wallet-op";
/** Open the desk's wallet tab on a given operation, from anywhere. */
export const openWalletOp = (op: WalletOp) => {
  // Persist first: on the desk's first open the panel isn't mounted yet to
  // hear the event, and it restores the op from here when it does.
  try { localStorage.setItem(OP_KEY, op); } catch {}
  window.dispatchEvent(new CustomEvent("hl:dock", { detail: "wallet" }));
  window.dispatchEvent(new CustomEvent(WALLET_OP_EVENT, { detail: op }));
};
/** Header owns the watch-address input; the sidebar just asks for it. */
export const WATCH_EVENT = "hl:watch";

const row = "flex items-center justify-between gap-2";
const isAddr = (s: string) => /^0x[a-fA-F0-9]{40}$/.test(s.trim());
const rejected = (e: any) => (e?.code === 4001 ? "Rejected in MetaMask." : String(e?.message ?? e));

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

function AmountField({ value, onChange, max, disabled }: {
  value: string; onChange: (v: string) => void; max?: number; disabled?: boolean;
}) {
  return (
    <div>
      <div className="label">amount (USDC)</div>
      <div className="flex gap-2 items-center">
        <input className="input flex-1 min-w-0 num" type="number" min={0} step={10} placeholder="0.00"
          value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} />
        {max != null && max > 0 && (
          <button className="btn" disabled={disabled}
            onClick={() => onChange(String(Math.floor(max * 100) / 100))}>max</button>
        )}
      </div>
    </div>
  );
}

// One line per ledger entry: what happened and which way the money went.
function describe(u: LedgerUpdate, me: string): { label: string; amt: number | null; note?: string } {
  const d = u.delta ?? ({} as LedgerUpdate["delta"]);
  const n = (v: any) => (v == null ? null : Number(v));
  const mine = (a?: string) => !!a && a.toLowerCase() === me.toLowerCase();
  switch (d.type) {
    case "deposit": return { label: "Deposit", amt: n(d.usdc) };
    case "withdraw": return { label: "Withdraw", amt: -(n(d.usdc) ?? 0), note: d.fee ? `fee $${d.fee}` : undefined };
    case "internalTransfer":
    case "send": {
      const out = mine(d.user) && !mine(d.destination);
      const v = n(d.usdc ?? d.usdcValue ?? d.amount) ?? 0;
      return { label: out ? `Sent to ${shortAddr(d.destination)}` : `From ${shortAddr(d.user)}`, amt: out ? -v : v };
    }
    case "spotTransfer": {
      const out = mine(d.user);
      const v = n(d.usdcValue) ?? 0;
      return { label: `${out ? "Sent" : "Got"} ${d.amount} ${d.token}`, amt: out ? -v : v };
    }
    case "accountClassTransfer":
      return { label: d.toPerp ? "Spot → perp" : "Perp → spot", amt: null, note: fmtUsd(n(d.usdc) ?? 0) };
    case "vaultDeposit": return { label: `Into vault ${shortAddr(d.vault)}`, amt: -(n(d.usdc) ?? 0) };
    case "vaultWithdraw":
      return { label: `From vault ${shortAddr(d.vault)}`, amt: n(d.netWithdrawnUsd ?? d.requestedUsd) };
    case "vaultCreate": return { label: "Vault created", amt: -(n(d.usdc) ?? 0) };
    case "liquidation": return { label: "Liquidation", amt: null, note: d.liquidatedNtlPos ? fmtUsd(Number(d.liquidatedNtlPos)) : undefined };
    default: return { label: d.type ?? "update", amt: n(d.usdc) };
  }
}

export default function AccountPanel({ onNavigate }: { onNavigate: () => void }) {
  const wallet = useWallet();
  const { address: eoa, kind, hasProvider, connect, disconnect } = wallet;
  const { canWrite, needsSignIn, signIn, me } = useSession();
  const canSign = kind === "metamask";

  const [cfg, setCfg] = useState<WalletNetConfig | null>(null);
  const [nets, setNets] = useState<DepositChains | null>(null);
  const [state, setState] = useState<any>(null);
  const [spot, setSpot] = useState<any>(null);
  const [ledger, setLedger] = useState<LedgerUpdate[] | null>(null);
  const [book, setBook] = useState<Portfolio | null>(null);
  const [agent, setAgent] = useState<{ agentAddress: string; approved: boolean } | null>(null);
  const [funding, setFunding] = useState<any[] | null>(null);
  const [pnlData, setPnlData] = useState<any | null>(null);
  const [pnlLoading, setPnlLoading] = useState(false);
  const [pnlDays, setPnlDays] = useState(7);

  const [op, setOp] = useState<WalletOp>("deposit");
  const [busy, setBusy] = useState<null | string>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showAllLedger, setShowAllLedger] = useState(false);

  // form state, per op
  const [depAmt, setDepAmt] = useState("");
  const [wAmt, setWAmt] = useState("");
  const [wDest, setWDest] = useState("");
  const [netKey, setNetKey] = useState("arbitrum");
  const [wStep, setWStep] = useState<WithdrawStep | null>(null);
  const [mAmt, setMAmt] = useState("");
  const [toPerp, setToPerp] = useState(false);
  const [sAmt, setSAmt] = useState("");
  const [sDest, setSDest] = useState("");

  useEffect(() => {
    try {
      const v = localStorage.getItem(OP_KEY) as WalletOp | null;
      if (v && OPS.some((o) => o.key === v)) setOp(v);
    } catch {}
    const onOp = (e: Event) => setOp((e as CustomEvent).detail as WalletOp);
    window.addEventListener(WALLET_OP_EVENT, onOp);
    return () => window.removeEventListener(WALLET_OP_EVENT, onOp);
  }, []);
  const pickOp = (k: WalletOp) => {
    setOp(k); setMsg(null); setErr(null);
    try { localStorage.setItem(OP_KEY, k); } catch {}
  };

  useEffect(() => { walletConfig().then(setCfg).catch(() => {}); }, []);
  useEffect(() => { depositChains().then(setNets).catch(() => {}); }, []);
  useEffect(() => { if (eoa) setWDest((d) => d || eoa); }, [eoa]);

  const refresh = useCallback(() => {
    if (!eoa) { setState(null); setSpot(null); setLedger(null); setAgent(null); setFunding(null); return; }
    userState(eoa).then(setState).catch(() => setState(null));
    userSpot(eoa).then(setSpot).catch(() => setSpot(null));
    userLedger(eoa).then((l) => setLedger(Array.isArray(l) ? [...l].sort((a, b) => b.time - a.time) : []))
      .catch(() => setLedger(null));
    agentStatus(eoa).then((r) => setAgent({ agentAddress: r.agentAddress, approved: r.approved }))
      .catch(() => setAgent(null));
    userFunding(eoa).then(setFunding).catch(() => setFunding(null));
  }, [eoa]);
  useEffect(() => { refresh(); }, [refresh]);

  // The invest book is wallet-scoped: asking for it without a token is a
  // guaranteed 401, so a watch-only session simply doesn't have one.
  useEffect(() => {
    if (!me) { setBook(null); return; }
    portfolio(me).then(setBook).catch(() => setBook(null));
  }, [me]);

  useEffect(() => {
    if (!eoa) { setPnlData(null); return; }
    setPnlLoading(true);
    userPnl(eoa).then(setPnlData).catch(() => setPnlData(null)).finally(() => setPnlLoading(false));
  }, [eoa]);

  if (!eoa) {
    return (
      <div className="space-y-3">
        <Empty>
          Connect MetaMask to deposit from any of 12 chains, withdraw anywhere, move
          between perp and spot, send to another account, and authorize copy-trading —
          all signed by you.
        </Empty>
        <button className="btn-primary w-full" onClick={() => connect()} disabled={!hasProvider}>
          {hasProvider ? "connect metamask" : "MetaMask not detected"}
        </button>
        <button className="btn w-full" onClick={() => window.dispatchEvent(new Event(WATCH_EVENT))}>
          watch an address
        </button>
      </div>
    );
  }

  const accountValue = Number(state?.marginSummary?.accountValue ?? 0);
  const withdrawable = Number(state?.withdrawable ?? 0);
  const positions: any[] = state?.assetPositions ?? [];
  const unrealized = positions.reduce((s, p) => s + Number(p?.position?.unrealizedPnl ?? 0), 0);
  const net24hFunding = (funding ?? []).filter((f: any) => f.time > Date.now() - 86_400_000)
    .reduce((s: number, f: any) => s + Number(f.usdc ?? 0), 0);
  const spotRows: any[] = (spot?.balances ?? []).filter((b: any) => Number(b.total) > 0);
  const spotUsdcRow = spotRows.find((b) => b.coin === "USDC");
  const spotUsdc = spotUsdcRow ? Number(spotUsdcRow.total) - Number(spotUsdcRow.hold ?? 0) : 0;
  const otherSpot = spotRows.filter((b) => b.coin !== "USDC");

  const chains = nets && !nets.testnet ? nets.chains : [];
  const arbChain = chains.find((c) => c.direct) ?? null;
  const toChain = chains.find((c) => c.key === netKey) ?? null;
  const crossChain = !!toChain && !toChain.direct;
  const netName = crossChain ? toChain!.name : cfg?.chainName ?? "Arbitrum";

  const run = async (what: string, fn: () => Promise<string>) => {
    setBusy(what); setMsg(null); setErr(null);
    try { setMsg(await fn()); }
    catch (e: any) { setErr(rejected(e)); }
    finally { setBusy(null); refresh(); }
  };
  const need = (amt: number, max: number) => {
    if (!cfg) throw new Error("Network config not loaded yet.");
    if (!(amt > 0)) throw new Error("Enter a USDC amount.");
    if (amt > max + 1e-9) throw new Error(`You have ${fmtUsd(max)} available.`);
  };

  const onApprove = () => run("approve", async () => {
    await approveAgentFlow(wallet, cfg!, eoa);
    return "Trading agent authorized — vaults, strats and copy-trading can now trade for you.";
  });

  const onTestnetDeposit = () => run("deposit", async () => {
    if (!cfg) throw new Error("Network config not loaded yet.");
    const amt = Number(depAmt);
    if (!(amt > 0)) throw new Error("Enter a USDC amount.");
    const tx = await bridgeDepositFlow(wallet, cfg, amt);
    setDepAmt("");
    return `Deposit sent — tx ${shortAddr(tx)}. Credited in ~1 minute.`;
  });

  const onWithdraw = () => run("withdraw", async () => {
    const amt = Number(wAmt);
    need(amt, withdrawable);
    if (!isAddr(wDest)) throw new Error("Destination must be a 0x address.");
    setWStep(null);
    try {
      if (crossChain && toChain && arbChain) {
        if (amt < 6) throw new Error(`Withdrawals to ${toChain.name} need at least $6 (≈$1 Hyperliquid fee + $5 bridge minimum). Pick Arbitrum for smaller amounts.`);
        const r = await crossChainWithdrawFlow(wallet, cfg!, arbChain, toChain, wDest.trim(), String(amt), eoa, setWStep);
        setWAmt("");
        return `${fmtUsd(r.usdc)} USDC delivered to ${shortAddr(wDest.trim())} on ${toChain.name}.`;
      }
      await withdrawFlow(wallet, cfg!, wDest.trim(), String(amt));
      setWAmt("");
      return `Withdrawal of ${fmtUsd(amt)} submitted — arrives on ${cfg!.chainName} in a few minutes (fee ~$${cfg!.withdrawalFeeUsd}).`;
    } finally { setWStep(null); }
  });

  const onMove = () => run("move", async () => {
    const amt = Number(mAmt);
    need(amt, toPerp ? spotUsdc : withdrawable);
    await usdClassTransferFlow(wallet, cfg!, String(amt), toPerp);
    setMAmt("");
    return `Moved ${fmtUsd(amt)} ${toPerp ? "spot → perp" : "perp → spot"}.`;
  });

  const onSend = () => run("send", async () => {
    const amt = Number(sAmt);
    need(amt, withdrawable);
    if (!isAddr(sDest)) throw new Error("Recipient must be a 0x address.");
    if (sDest.trim().toLowerCase() === eoa.toLowerCase()) throw new Error("That's your own address.");
    await usdSendFlow(wallet, cfg!, sDest.trim(), String(amt));
    setSAmt(""); setSDest("");
    return `Sent ${fmtUsd(amt)} to ${shortAddr(sDest.trim())} on Hyperliquid.`;
  });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(eoa);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  };

  const locked = !canSign || busy !== null;
  const signBtn = (what: string, label: string, onClick: () => void) => (
    <button className="btn-primary w-full" onClick={onClick} disabled={locked}>
      {busy === what ? "check MetaMask…" : label}
    </button>
  );
  const ledgerRows = (ledger ?? []).slice(0, showAllLedger ? 50 : 6);

  return (
    <div className="space-y-3">
      {/* who */}
      <div className="panel p-3 space-y-2">
        <div className={row}>
          <div className="flex items-center gap-2 min-w-0">
            <Identicon address={eoa} size={18} />
            <button className="font-mono text-[12px] text-ink hover:text-accent transition-colors truncate"
              onClick={copy} title="Copy address">
              {copied ? "copied" : shortAddr(eoa)}
            </button>
            {cfg?.testnet && <span className="pill text-warn border-warn/30">testnet</span>}
          </div>
          <span className={`pill ${canWrite ? "text-accent border-accent/30" : "text-warn border-warn/30"}`}>
            {canWrite ? "signed in" : kind === "watch" ? "watch only" : "signed out"}
          </span>
        </div>
        {needsSignIn && (
          <button className="btn-primary w-full" disabled={busy !== null}
            onClick={() => run("signin", async () => { await signIn(); return "Signed in."; })}>
            {busy === "signin" ? "check MetaMask…" : "sign in to trade"}
          </button>
        )}
        {kind === "watch" && hasProvider && (
          <button className="btn w-full" onClick={() => connect().catch((e) => setErr(rejected(e)))}>
            connect metamask to sign
          </button>
        )}
        {/* the agent: one signature, then strats/vaults/copy can trade */}
        {agent && (
          <div className={`${row} border-t border-white/[0.06] pt-2 text-[11px]`}>
            <span className="text-muted" title={agent.agentAddress}>
              trading agent{" "}
              {agent.approved
                ? <span className="text-win">authorized</span>
                : <span className="text-warn">not authorized</span>}
            </span>
            {!agent.approved && (
              <button className="btn-ghost !py-0.5" onClick={onApprove} disabled={locked}>
                {busy === "approve" ? "check MetaMask…" : "authorize"}
              </button>
            )}
          </div>
        )}
      </div>

      {/* balances */}
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Perp account" value={fmtUsd(accountValue)} />
        <Stat label="Withdrawable" value={fmtUsd(withdrawable)} />
        <Stat label="Spot USDC" value={spot ? fmtUsd(spotUsdc) : "…"} />
        <Stat label="Unrealized" value={fmtPnl(unrealized)} tone={unrealized >= 0 ? "win" : "loss"} />
        <Stat label="Funding 24h" value={funding ? fmtPnl(net24hFunding) : "…"}
          tone={net24hFunding > 0 ? "win" : net24hFunding < 0 ? "loss" : undefined} />
      </div>

      {/* my performance */}
      <div className="panel overflow-hidden">
        <div className={`${row} px-3 py-2`}>
          <span className="eyebrow">My performance</span>
          <div className="flex gap-1">
            {[1, 7, 30].map((d) => (
              <button key={d} onClick={() => setPnlDays(d)}
                className={`px-2 py-0.5 rounded-full border text-[10px] uppercase tracking-wider transition-colors ${
                  pnlDays === d
                    ? "border-accent/50 bg-accent/10 text-ink"
                    : "border-white/[0.08] bg-white/[0.03] text-muted hover:text-ink"
                }`}>
                {d}d
              </button>
            ))}
          </div>
        </div>
        {pnlLoading ? (
          <div className="px-3 py-6 text-[11px] text-muted">loading…</div>
        ) : pnlData ? (
          <PnlChart portfolio={pnlData} days={pnlDays} />
        ) : (
          <div className="px-3 py-4 text-[11px] text-muted">no performance history</div>
        )}
      </div>

      {/* operations */}
      <div className="panel p-3 space-y-3">
        <div className="seg w-full">
          {OPS.map((o) => (
            <button key={o.key} className={`seg-btn flex-1 ${op === o.key ? "seg-btn-active" : ""}`}
              onClick={() => pickOp(o.key)} disabled={busy !== null}>
              {o.label}
            </button>
          ))}
        </div>

        {(msg || err) && (
          <div className={`text-[11px] break-words ${err ? "text-loss" : "text-win"}`}>{err ?? msg}</div>
        )}

        {op === "deposit" && (
          cfg && !cfg.testnet ? (
            <DepositPanel wallet={wallet} cfg={cfg} eoa={eoa} canSign={canSign}
              onDone={(m) => { setMsg(m); setErr(null); refresh(); }} />
          ) : (
            <div className="space-y-3">
              <p className="text-[11px] text-muted">
                Sends USDC on {cfg?.chainName ?? "Arbitrum"} to the Hyperliquid bridge — one
                transaction, credited in ~1 minute. Minimum ${cfg?.minDepositUsd ?? 5}.
              </p>
              <AmountField value={depAmt} onChange={setDepAmt} disabled={busy !== null} />
              {signBtn("deposit", "deposit via metamask", onTestnetDeposit)}
            </div>
          )
        )}

        {op === "withdraw" && (
          <div className="space-y-3">
            <p className="text-[11px] text-muted">
              To {netName}, signed by <span className="text-ink">your wallet only</span>.
              {crossChain
                ? <> Pays out on Arbitrum, then bridges to {toChain!.name} — fee ~${cfg?.withdrawalFeeUsd ?? 1} + bridge; needs a little ETH on Arbitrum for gas.</>
                : <> Fee ~${cfg?.withdrawalFeeUsd ?? 1}, arrives in a few minutes.</>}
            </p>
            {chains.length > 1 && (
              <div>
                <div className="label">network</div>
                <div className="flex flex-wrap gap-1.5">
                  {chains.map((c) => (
                    <button key={c.key} onClick={() => setNetKey(c.key)} disabled={busy !== null}
                      className={`pill font-mono text-[11px] transition-colors ${c.key === netKey
                        ? "border-accent/50 text-accent bg-accent/10"
                        : "border-white/10 text-muted hover:text-ink"}`}>
                      {c.direct ? cfg?.chainName ?? c.name : c.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <AmountField value={wAmt} onChange={setWAmt} max={withdrawable} disabled={busy !== null} />
            <div>
              <div className="label">destination ({netName})</div>
              <input className="input w-full font-mono text-xs" placeholder="0x…"
                value={wDest} onChange={(e) => setWDest(e.target.value)} disabled={busy !== null} />
            </div>
            <button className="btn-primary w-full" onClick={onWithdraw} disabled={locked}>
              {busy === "withdraw" ? (crossChain ? "working — watch MetaMask" : "check MetaMask…") : "withdraw"}
            </button>
            {busy === "withdraw" && wStep && (
              <div className="space-y-1">
                <p className="text-[11px] text-accent">{wStep.msg}</p>
                <p className="text-[10px] text-muted/70">
                  Keep this tab open — funds only ever sit in your own wallet between the two hops.
                </p>
              </div>
            )}
          </div>
        )}

        {op === "move" && (
          <div className="space-y-3">
            <p className="text-[11px] text-muted">
              Perps trade from the perp balance; spot tokens are bought with spot USDC.
              Moving between them is instant and free.
            </p>
            <div className="seg w-full">
              <button className={`seg-btn flex-1 ${!toPerp ? "seg-btn-active" : ""}`} onClick={() => setToPerp(false)}>
                perp → spot
              </button>
              <button className={`seg-btn flex-1 ${toPerp ? "seg-btn-active" : ""}`} onClick={() => setToPerp(true)}>
                spot → perp
              </button>
            </div>
            <AmountField value={mAmt} onChange={setMAmt} max={toPerp ? spotUsdc : withdrawable} disabled={busy !== null} />
            {signBtn("move", toPerp ? "move to perp" : "move to spot", onMove)}
          </div>
        )}

        {op === "send" && (
          <div className="space-y-3">
            <p className="text-[11px] text-muted">
              USDC to another Hyperliquid account — arrives instantly, no gas, no bridge.
              Comes out of your withdrawable perp balance.
            </p>
            <div>
              <div className="label">recipient</div>
              <input className="input w-full font-mono text-xs" placeholder="0x…"
                value={sDest} onChange={(e) => setSDest(e.target.value)} disabled={busy !== null} />
            </div>
            <AmountField value={sAmt} onChange={setSAmt} max={withdrawable} disabled={busy !== null} />
            {signBtn("send", "send usdc", onSend)}
          </div>
        )}

        {!canSign && op !== "deposit" && (
          <p className="text-[10px] text-warn">Watch-only connection can&apos;t sign — connect MetaMask.</p>
        )}
      </div>

      {/* what you hold */}
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

      {otherSpot.length > 0 && (
        <div className="panel divide-y divide-white/[0.05]">
          <div className="eyebrow px-3 py-2">Spot tokens</div>
          {otherSpot.slice(0, 8).map((b) => (
            <div key={b.coin} className={`${row} px-3 py-2 text-[11px]`}>
              <span className="font-semibold text-ink">{b.coin}</span>
              <span className="num text-muted">{Number(b.total).toLocaleString(undefined, { maximumFractionDigits: 4 })}</span>
            </div>
          ))}
        </div>
      )}

      {book && book.positions.length > 0 && (
        <div className="panel divide-y divide-white/[0.05]">
          <div className={`${row} px-3 py-2`}>
            <span className="eyebrow">Backing</span>
            <span className="text-[11px] num text-muted">{fmtUsd(book.totals.equity)}</span>
          </div>
          {book.positions.slice(0, 6).map((p) => (
            <Link key={p.id} href={`/invest/${p.id}`} onClick={onNavigate}
              className={`${row} px-3 py-2 text-[11px] hover:bg-white/[0.03] transition-colors`}>
              <span className="text-ink truncate">{p.name}</span>
              <span className={`num shrink-0 ${p.value.pnl >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(p.value.pnl)}</span>
            </Link>
          ))}
        </div>
      )}

      {/* the paper trail of everything above */}
      <div className="panel divide-y divide-white/[0.05]">
        <div className={`${row} px-3 py-2`}>
          <span className="eyebrow">Activity · 90d</span>
          <button className="text-[10px] uppercase tracking-wider text-dim hover:text-ink transition-colors"
            onClick={refresh}>refresh</button>
        </div>
        {ledger === null ? (
          <div className="px-3 py-2 text-[11px] text-muted">loading…</div>
        ) : ledger.length === 0 ? (
          <div className="px-3 py-2 text-[11px] text-muted">No deposits, withdrawals or transfers yet.</div>
        ) : (
          <>
            {ledgerRows.map((u) => {
              const { label, amt, note } = describe(u, eoa);
              return (
                <div key={`${u.hash}:${u.time}`} className={`${row} px-3 py-2 text-[11px]`}>
                  <div className="min-w-0">
                    <div className="text-ink truncate">{label}</div>
                    <div className="text-[10px] text-dim">{ago(u.time)}{note ? ` · ${note}` : ""}</div>
                  </div>
                  {amt != null && (
                    <span className={`num shrink-0 ${amt >= 0 ? "text-win" : "text-loss"}`}>
                      {amt >= 0 ? "+" : "−"}{fmtUsd(Math.abs(amt))}
                    </span>
                  )}
                </div>
              );
            })}
            {ledger.length > 6 && (
              <button className="w-full px-3 py-2 text-[10px] uppercase tracking-wider text-dim hover:text-ink transition-colors"
                onClick={() => setShowAllLedger((v) => !v)}>
                {showAllLedger ? "show less" : `show all ${Math.min(ledger.length, 50)}`}
              </button>
            )}
          </>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Link href="/live" className="btn" onClick={onNavigate}>live engine</Link>
        <Link href="/follows" className="btn" onClick={onNavigate}>follows</Link>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button className="btn" onClick={() => window.dispatchEvent(new Event(WATCH_EVENT))}>watch another</button>
        <button className="btn-danger" onClick={disconnect}>disconnect</button>
      </div>
    </div>
  );
}
