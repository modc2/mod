"use client";

// HOW MUCH MONEY TO RUN THIS STRAT WITH.
//
// Under-funding a proportional copy strat doesn't make it trade smaller — it
// makes it not trade. Every mirror below the order floor is skipped
// (SUB_SCALE), so a $100 strat copying whales silently places nothing while
// its log fills with skips. Over-funding does the opposite: past the point
// where the proportional size clears the TRADE SIZE ceiling, extra dollars
// just sit as idle cash.
//
// This card answers the question with the strat's own numbers: it runs
// lib/capitalPlan.ts over the SAME already-filtered flow the backtest replays
// and reports the capital level at which most of that flow becomes copyable,
// what the current allocation actually covers, and what the strat would
// deploy per day at that size. One click adopts the recommendation.

import { useEffect } from "react";
import { planCapital, type CapitalPlan, type CapitalPlanInput } from "../lib/capitalPlan";

const money = (n: number): string =>
  n >= 1000 ? `$${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : `$${n < 10 ? n.toFixed(2) : Math.round(n)}`;

export default function CapitalPlanCard({
  input,
  onUse,
  onPlan,
}: {
  input: CapitalPlanInput;
  /** Adopt the recommendation as the strat's CAPITAL. */
  onUse: (capital: number) => void;
  /** Fires when the recommendation changes, so the strat can remember it and
      other surfaces (cards, sidebar) can answer "how much" without the
      trade history loaded. */
  onPlan?: (plan: CapitalPlan) => void;
}) {
  const plan = planCapital(input);
  const recommended = plan.recommendedCapital;
  useEffect(() => {
    if (recommended > 0) onPlan?.(planCapital(input));
    // Only when the NUMBER moves — `input`/`plan` are fresh objects each
    // render, and persisting on every one would write localStorage in a loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recommended]);

  if (plan.sampleSize === 0) {
    return (
      <div className="text-[10.5px] font-mono text-pixel-gray leading-snug">
        Not enough leader flow in the window to size this strat — add traders,
        widen the WINDOW, or loosen the filters, and the recommendation appears
        here.
      </div>
    );
  }

  const coveragePct = Math.round(plan.coverage * 100);
  const recPct = Math.round(plan.recommendedCoverage * 100);
  const underfunded = input.capital < plan.recommendedCapital;
  const saturated = plan.saturationCapital > 0 && input.capital > plan.saturationCapital;

  return (
    <div className="w-full space-y-2">
      <div className="flex items-end gap-3 flex-wrap">
        <div>
          <div className="text-[9px] text-pixel-gray font-semibold tracking-[0.16em]">RUN IT WITH</div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-[22px] leading-none font-mono font-bold text-green-400">
              {money(plan.recommendedCapital)}
            </span>
            <span className="text-[10px] font-mono text-pixel-gray">→ {recPct}% of flow</span>
          </div>
        </div>
        <button
          onClick={() => onUse(plan.recommendedCapital)}
          title={`Set this strat's CAPITAL to ${money(plan.recommendedCapital)} — the level at which ${recPct}% of the leader trades it just filtered become copyable in proportion.`}
          className="text-[10px] px-2 py-1 rounded border font-mono font-bold tracking-[0.08em] border-green-400/60 text-green-400 bg-green-400/10 hover:bg-green-400/20 transition-colors"
        >
          USE
        </button>
        <div className="flex-1 min-w-[120px]">
          {/* What the CURRENT allocation buys — the honest counterweight to
              the headline number. */}
          <div className="flex items-baseline justify-between text-[10px] font-mono">
            <span className="text-pixel-gray tracking-[0.14em]">AT {money(input.capital)}</span>
            <span className={coveragePct >= 75 ? "text-green-400" : coveragePct >= 40 ? "text-amber-300" : "text-red-400"}>
              {coveragePct}% copyable
            </span>
          </div>
          <div className="mt-1 h-[4px] rounded-full bg-pixel-border/60 overflow-hidden">
            <div
              className={`h-full rounded-full ${coveragePct >= 75 ? "bg-green-400" : coveragePct >= 40 ? "bg-amber-300" : "bg-red-400"}`}
              style={{ width: `${Math.min(100, Math.max(2, coveragePct))}%` }}
            />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-1.5">
        {[
          {
            label: "HALF THE FLOW",
            value: money(plan.medianCapital),
            title: `Below ${money(plan.medianCapital)} more than half the leader trades mirror too small to place (SUB_SCALE) — the strat looks idle.`,
          },
          {
            label: "ORDERS/DAY",
            value: plan.ordersPerDay < 1 ? plan.ordersPerDay.toFixed(1) : Math.round(plan.ordersPerDay).toString(),
            title: `At ${money(input.capital)} the strat would place about this many mirrors per day, before the MAX/CYCLE cap.`,
          },
          {
            label: "DEPLOYS/DAY",
            value: money(plan.deployPerDay),
            title: `USD it would put to work per day at ${money(input.capital)} — the cash flow the wallet has to keep available.`,
          },
        ].map((s) => (
          <div
            key={s.label}
            title={s.title}
            className="px-2 py-1.5 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-[var(--border)]"
          >
            <div className="text-[9px] text-pixel-gray font-semibold tracking-[0.14em]">{s.label}</div>
            <div className="text-[12.5px] text-pixel-white font-mono truncate">{s.value}</div>
          </div>
        ))}
      </div>

      <div className="text-[10px] font-mono leading-snug text-pixel-gray">
        {underfunded ? (
          <>
            At {money(input.capital)} this strat skips{" "}
            <span className="text-amber-300">{100 - coveragePct}%</span> of the
            trades it just filtered — their proportional mirror lands under the{" "}
            {money(plan.medianFloor)} order floor.
          </>
        ) : saturated ? (
          <>
            Funded past {money(plan.saturationCapital)}: the proportional size
            already clears the TRADE SIZE max on most trades, so extra capital
            sits idle instead of buying bigger mirrors.
          </>
        ) : (
          <>
            Sized right — {coveragePct}% of the filtered flow is copyable in
            proportion, and mirrors still fit under the TRADE SIZE max.
          </>
        )}
        {plan.ceilingBinds && (
          <>
            {" "}Raise TRADE SIZE max: proportionality asks for more than{" "}
            {money(input.maxTrade)} on most of this flow, so mirrors are being
            clamped down.
          </>
        )}
      </div>
    </div>
  );
}
