"use client";

// "Your $N, trade by trade" — every fill the trader made inside the window,
// replayed at your size (capital / their equity, the live engine's ratio).
// Columns: when, what (HL's own Open/Close label), the trader's dollars vs
// yours, the realised result on your size, the copy's fee, and the running
// net of your money. Σ net over the full list = the backtest's realised mirror.

import { useMemo, useState } from "react";
import { ago, fmtPnl, fmtUsd, type CopyTrade } from "../lib/api";

const PAGE = 25;

export default function CopyTrades({
  trades, total, capital, coinFilter = true,
}: {
  trades: CopyTrade[];
  /** In-window trades before the server's cap. */
  total?: number;
  capital: number;
  coinFilter?: boolean;
}) {
  const [coin, setCoin] = useState<string>("all");
  const [closesOnly, setClosesOnly] = useState(false);
  const [shown, setShown] = useState(PAGE);

  const coins = useMemo(() => {
    const n = new Map<string, number>();
    for (const t of trades) n.set(t.coin, (n.get(t.coin) ?? 0) + 1);
    return [...n.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
  }, [trades]);

  const rows = trades.filter((t) =>
    (coin === "all" || t.coin === coin) && (!closesOnly || t.realized !== 0));
  const sum = (k: "realized" | "fee" | "net") => rows.reduce((a, t) => a + t[k], 0);
  const wins = rows.filter((t) => t.realized > 0).length;
  const closes = rows.filter((t) => t.realized !== 0).length;

  if (!trades.length) {
    return <div className="px-4 py-3 text-[11px] text-muted">No trades inside this window.</div>;
  }

  return (
    <div>
      <div className="px-4 py-2 flex items-center gap-2 flex-wrap text-[11px]">
        <span className="text-muted">
          {rows.length} trade{rows.length === 1 ? "" : "s"}
          {total && total > trades.length ? ` (newest ${trades.length} of ${total})` : ""}
          {" · "}net <span className={`num ${sum("net") >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(sum("net"))}</span>
          {" on "}{fmtUsd(capital)}
          {closes > 0 && <> · {wins}/{closes} closes won</>}
          {" · "}fees <span className="num">{fmtUsd(sum("fee"))}</span>
        </span>
        <span className="flex-1" />
        {coinFilter && coins.length > 1 && (
          <select className="input !w-auto !py-0.5 text-[11px]" value={coin}
            onChange={(e) => { setCoin(e.target.value); setShown(PAGE); }}>
            <option value="all">all coins</option>
            {coins.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        <button className={`pill !py-0 text-[10px] ${closesOnly ? "border-accent/50 text-accent" : "text-muted"}`}
          onClick={() => setClosesOnly((v) => !v)}>closes only</button>
      </div>

      <div className="grid grid-cols-[4.5rem_1.3fr_1fr_1fr_1fr_0.8fr_1fr] gap-2 px-4 py-1.5 text-[9px] uppercase tracking-wider text-muted border-y border-border">
        <span>when</span><span>trade</span>
        <span className="text-right">they traded</span>
        <span className="text-right">your size</span>
        <span className="text-right">your result</span>
        <span className="text-right">fee</span>
        <span className="text-right">running</span>
      </div>
      <div className="max-h-96 overflow-y-auto">
        {rows.slice(0, shown).map((t, i) => (
          <div key={`${t.time}-${i}`}
            className="grid grid-cols-[4.5rem_1.3fr_1fr_1fr_1fr_0.8fr_1fr] gap-2 px-4 py-1.5 items-baseline text-[11px] table-row"
            title={new Date(t.time).toLocaleString()}>
            <span className="text-[10px] text-muted">{ago(t.time)}</span>
            <span className="truncate">
              <span className={t.side === "buy" ? "text-win" : "text-loss"}>{t.dir || t.side}</span>
              <span className="text-ink"> {t.coin}</span>
              <span className="text-muted num"> @ {t.px.toPrecision(5)}</span>
            </span>
            <span className="num text-right text-muted">{fmtUsd(t.their_notional)}</span>
            <span className="num text-right">{fmtUsd(t.notional)}</span>
            <span className={`num text-right ${t.realized === 0 ? "text-muted" : t.realized > 0 ? "text-win" : "text-loss"}`}>
              {t.realized === 0 ? "open" : fmtPnl(t.realized)}
            </span>
            <span className="num text-right text-muted">{t.fee < 0.01 ? "<$0.01" : fmtUsd(t.fee)}</span>
            <span className={`num text-right ${t.cum_net >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(t.cum_net)}</span>
          </div>
        ))}
      </div>
      {rows.length > shown && (
        <button className="w-full py-1.5 text-[11px] text-accent2 hover:text-accent border-t border-border"
          onClick={() => setShown((n) => n + PAGE * 2)}>
          show more ({rows.length - shown} left)
        </button>
      )}
    </div>
  );
}
