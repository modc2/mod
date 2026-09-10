"use client";

// Arming a typed sentence as a real copy gate — the confirm, in one place.
//
// Two screens offer it (the sidebar book and /copy/trades) and they must say
// the SAME thing before they write, because what they write changes what a
// live session will and will not copy. The half of the sentence the engine
// cannot enforce is named in the dialog rather than dropped quietly: a filter
// that reads "missed longshots last 3 days" arms the price band and nothing
// else, and the person clicking has to know that.

import { describeGate, type CompiledGate } from "./semanticFilter";

/** The confirm. `names` is who it will apply to, in the order they'll be
    written. Returns false when there is nothing to arm or the user declined. */
export function confirmGate(gate: CompiledGate, names: string[]): boolean {
  if (typeof window === "undefined" || names.length === 0 || !gate.any) return false;
  return window.confirm(
    `Gate ${names.length} trader${names.length === 1 ? "" : "s"} (${names.join(", ")}) to:\n\n` +
      `${describeGate(gate)}\n\n` +
      (gate.marketQuery
        ? `markets matching: ${gate.marketQuery.slice(0, 400)}${gate.marketQuery.length > 400 ? "…" : ""}\n\n`
        : "any market\n\n") +
      (gate.viewOnly.length
        ? `NOT armed — nothing in the engine enforces these, they filter the screen only: ${gate.viewOnly.join(", ")}\n\n`
        : "") +
      "Running sessions are reconfigured in place. Trades outside the gate stop being copied.",
  );
}

/** The patch a gate becomes on an allocation. Both halves always written, so
    arming a narrower sentence CLEARS the wider gate it replaces rather than
    leaving half of the old one behind. */
export function gatePatch(gate: CompiledGate) {
  return { marketQuery: gate.marketQuery, tradeFilters: gate.tradeFilters };
}
