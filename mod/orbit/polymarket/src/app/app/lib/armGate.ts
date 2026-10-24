"use client";

// Arming a typed sentence as a real copy gate — the confirm, in one place.
//
// Two screens offer it (the sidebar book and /copy/trades) and they must say
// the SAME thing before they write, because what they write changes what a
// live session will and will not copy. The half of the sentence the engine
// cannot enforce is named in the dialog rather than dropped quietly: a filter
// that reads "missed longshots last 3 days" arms the price band and nothing
// else, and the person clicking has to know that.

import { type CompiledGate } from "./semanticFilter";

/** The patch a gate becomes on an allocation. Both halves always written, so
    arming a narrower sentence CLEARS the wider gate it replaces rather than
    leaving half of the old one behind. */
export function gatePatch(gate: CompiledGate) {
  return { marketQuery: gate.marketQuery, tradeFilters: gate.tradeFilters };
}
