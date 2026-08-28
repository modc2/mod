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
//
// More capital is one of TWO answers, and it's the one you can't always give.
// The other is UPSCALE: the skip only happens because a floor-clamped mirror
// is capped at `maxUpscale`× the proportional size. Turn that cap off and
// every filtered trade is placed at the floor instead — the strat trades at
// the size it has rather than the size proportionality wants. That's a real
// trade-off (a conviction bet and a punt land on the same $2.55), so the card
// offers it as an explicit second button, not a silent default.
//
// The headline is capped at the deposit wallet's balance. Copying whales in
// proportion asks for six figures, and "RUN IT WITH $200k" is a number nobody
// can act on — so the card leads with what the money on hand actually buys,
// keeps the full-proportionality figure as the thing to deposit toward, and
// says plainly when the balance is under the floor where nothing trades.

import { useEffect } from "react";
import { planCapital, type CapitalPlan, type CapitalPlanInput } from "../lib/capitalPlan";
import { DEFAULT_MAX_UPSCALE } from "../lib/strats/strat";

const money = (n: number): string =>
  n >= 1000 ? `$${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : `$${n < 10 ? n.toFixed(2) : Math.round(n)}`;

export default function CapitalPlanCard({
  input,
  onUse,
  onCopyAll,
  onPlan,
}: {
  input: CapitalPlanInput;
  /** Adopt the recommendation as the strat's CAPITAL. */
  onUse: (capital: number) => void;
  /** Turn the proportional-fidelity cap OFF (UPSCALE ∞) so nothing is skipped
      for being small — the answer for an account that can't buy its way to
      proportionality. */
  onCopyAll?: () => void;
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
  const feasPct = Math.round(plan.feasibleCoverage * 100);
  const capped = plan.cappedByAvailable;
  // Balance under the cheapest mirror in the flow: no capital-sized version of
  // this strat exists — it needs different traders or a lower floor.
  const dead = capped && plan.feasibleCapital < plan.minCapital;
  const underfunded = input.capital < plan.feasibleCapital;
  const saturated = plan.saturationCapital > 0 && input.capital > plan.saturationCapital;
  // Is anything being skipped for SIZE at all? With UPSCALE off, sub-floor
  // mirrors are rounded up rather than refused, so coverage is already 100%
  // and offering COPY ALL would be offering a no-op.
  const upscale = input.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : input.maxUpscale;
  const upscaleOff = !upscale || upscale <= 0;
  const upscaleCaps = !upscaleOff && coveragePct < 100;

  return (
    <div className="w-full space-y-2">
      <div className="flex items-end gap-3 flex-wrap">
        <div>
          <div className="text-[9px] text-pixel-gray font-semibold tracking-[0.16em]">RUN IT WITH</div>
          <div className="flex items-baseline gap-1.5">
            <span className={`text-[22px] leading-none font-mono font-bold ${dead ? "text-red-400" : "text-green-400"}`}>
              {money(plan.feasibleCapital)}
            </span>
            <span className="text-[10px] font-mono text-pixel-gray">→ {feasPct}% of flow</span>
          </div>
          {capped && (
            <div className="text-[9px] font-mono text-amber-300/90 mt-0.5">
              your balance — full proportionality wants {money(plan.recommendedCapital)} ({recPct}%)
            </div>
          )}
        </div>
        <button
          onClick={() => onUse(plan.feasibleCapital)}
          title={
            capped
              ? `Set this strat's CAPITAL to ${money(plan.feasibleCapital)} — everything the deposit wallet holds, which copies ${feasPct}% of the flow it just filtered. Full proportionality would take ${money(plan.recommendedCapital)}.`
              : `Set this strat's CAPITAL to ${money(plan.feasibleCapital)} — the level at which ${feasPct}% of the leader trades it just filtered become copyable in proportion.`
          }
          className="text-[10px] px-2 py-1 rounded border font-mono font-bold tracking-[0.08em] border-green-400/60 text-green-400 bg-green-400/10 hover:bg-green-400/20 transition-colors"
        >
          USE
        </button>
        {/* The other answer, for when depositing the recommendation isn't on
            the table: stop refusing small mirrors. Same button row as USE
            because they solve the same complaint. */}
        {onCopyAll && upscaleCaps && (
          <button
            onClick={onCopyAll}
            title={`Set UPSCALE to ∞: place EVERY trade this strat filters, rounding the sub-floor ones up to the ${money(plan.medianFloor)} order floor instead of skipping them as SUB_SCALE. Proportionality goes with it — a conviction bet and a throwaway punt both land on the floor — and at ${money(input.capital)} the capital is spent that much faster.`}
            className="text-[10px] px-2 py-1 rounded border font-mono font-bold tracking-[0.08em] border-amber-300/60 text-amber-300 bg-amber-300/10 hover:bg-amber-300/20 transition-colors"
          >
            COPY ALL
          </button>
        )}
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
        {dead ? (
          <>
            <span className="text-red-400">Nothing is copyable at {money(plan.feasibleCapital)}.</span>{" "}
            The cheapest mirror in this flow needs {money(plan.minCapital)} — below
            that every proportional size lands under the {money(plan.medianFloor)}{" "}
            order floor and is skipped as SUB_SCALE. Copy traders with smaller
            books, raise UPSCALE, or deposit more.
          </>
        ) : capped ? (
          <>
            {money(plan.feasibleCapital)} is what the deposit wallet holds, so
            the strat copies the{" "}
            <span className="text-green-400">{feasPct}%</span> of this flow whose
            proportional mirror still clears the {money(plan.medianFloor)} order
            floor — the rest is skipped as SUB_SCALE, not traded small. Deposit
            toward {money(plan.recommendedCapital)} to copy {recPct}% of it.
          </>
        ) : underfunded ? (
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
        ) : upscaleOff ? (
          <>
            UPSCALE is <span className="text-amber-300">∞</span> — nothing is
            skipped for size, so all {coveragePct}% of the filtered flow trades
            and capital only decides how long it lasts: about{" "}
            {money(plan.deployPerDay)}/day at {money(plan.medianFloor)} an
            order, most of it at the floor rather than in proportion.
          </>
        ) : (
          <>
            Sized right — {coveragePct}% of the filtered flow is copyable in
            proportion, and mirrors still fit under the TRADE SIZE max.
          </>
        )}
        {upscaleCaps && !dead && (
          <>
            {" "}They&apos;re skipped, not traded small: a mirror may be rounded
            up to at most {upscale}× its proportional size.{" "}
            <span className="text-amber-300">COPY ALL</span> lifts that and
            places every one at {money(plan.medianFloor)} — same trades, no
            proportionality.
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
