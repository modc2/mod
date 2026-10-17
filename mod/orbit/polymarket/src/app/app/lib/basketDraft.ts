// THE BASKET DRAFT — a set of traders you are still deciding about.
//
// The copy book (api/src/copy.rs) is the committed desk: every row in it is a
// leader this deployment copies, and writing to it is an act. A basket you are
// still assembling is not that — you want to throw six names at it, price the
// split, and keep two. So the draft lives in the browser, and only pressing
// APPLY TO DESK turns it into allocations.
//
// It is deliberately the only client-side state this feature has:
//
//   • It's a SHOPPING LIST, not a position. Nothing here is money, nothing
//     here runs, and losing it costs you a re-pick, not a trade.
//   • It has to accumulate ACROSS SCREENS — add a name from the leaderboard,
//     another from a profile, open the basket and they're both there. A
//     server round-trip per click would make "+ BASKET" a commitment again,
//     which is the thing we're avoiding.
//
// Everything that must survive a browser (what you actually copy, with how
// much) is in the copy book, server-side, where an agent can read it too.
//
// Shared-origin note: every mod on this deployment shares one localStorage
// origin, so writes are quota-guarded and a failed write is a re-pick, never
// an exception (see the console's other snapshot stores).

import type { AllocationParams } from "./identityStrat";
import type { BasketLeg } from "./basketSim";

const KEY = "poly_basket_draft_v1";
/** Fired on every mutation so a header badge ("BASKET · 4") tracks the draft
    without prop-drilling through pages that don't own it. */
export const BASKET_EVENT = "poly:basket";

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;

export function isAddress(a: string): boolean {
  return ADDR_RE.test((a || "").trim());
}

function normalize(legs: unknown): BasketLeg[] {
  if (!Array.isArray(legs)) return [];
  const seen = new Set<string>();
  const out: BasketLeg[] = [];
  for (const raw of legs) {
    const l = raw as Partial<BasketLeg>;
    const address = String(l?.address ?? "").trim().toLowerCase();
    if (!isAddress(address) || seen.has(address)) continue;
    seen.add(address);
    out.push({
      address,
      allocationUsd: Number.isFinite(l?.allocationUsd) ? Math.max(0, Number(l!.allocationUsd)) : 0,
      label: typeof l?.label === "string" ? l.label : null,
      enabled: l?.enabled !== false,
      ...(l?.params && typeof l.params === "object" ? { params: l.params as AllocationParams } : {}),
    });
  }
  return out;
}

export function readDraft(): BasketLeg[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? normalize(JSON.parse(raw)) : [];
  } catch {
    return [];
  }
}

export function writeDraft(legs: BasketLeg[]): BasketLeg[] {
  const clean = normalize(legs);
  if (typeof window === "undefined") return clean;
  try {
    localStorage.setItem(KEY, JSON.stringify(clean));
  } catch {
    // Shared-origin quota — the draft is a shopping list, and a lost one
    // costs a re-pick.
  }
  try {
    window.dispatchEvent(new CustomEvent(BASKET_EVENT, { detail: clean.length }));
  } catch {
    /* no CustomEvent in a non-DOM context */
  }
  return clean;
}

/** Add a name (or re-point one already there). Idempotent by address, same
    rule as the copy book: "put this trader in the basket" twice is one leg. */
export function addToDraft(leg: BasketLeg): BasketLeg[] {
  const legs = readDraft();
  const i = legs.findIndex((l) => l.address === leg.address.toLowerCase());
  if (i >= 0) {
    legs[i] = {
      ...legs[i],
      ...leg,
      address: legs[i].address,
      // An explicit $0 means "in the basket, unfunded" — keep it. Only an
      // ABSENT amount inherits what was already there.
      allocationUsd: leg.allocationUsd ?? legs[i].allocationUsd,
      params: { ...(legs[i].params ?? {}), ...(leg.params ?? {}) },
    };
  } else {
    legs.push({ ...leg, address: leg.address.toLowerCase() });
  }
  return writeDraft(legs);
}

export function removeFromDraft(address: string): BasketLeg[] {
  const a = address.trim().toLowerCase();
  return writeDraft(readDraft().filter((l) => l.address !== a));
}

export function inDraft(address: string): boolean {
  const a = (address || "").trim().toLowerCase();
  return readDraft().some((l) => l.address === a);
}

export function clearDraft(): BasketLeg[] {
  return writeDraft([]);
}

/** `?add=0xabc,0xdef` — how another screen hands a set over without knowing
    anything about the draft's storage. Returns the addresses it accepted so
    the caller can strip them from the URL. */
export function seedFromQuery(param: string | null, defaultUsd: number): string[] {
  if (!param) return [];
  const added: string[] = [];
  for (const raw of param.split(",")) {
    const a = raw.trim().toLowerCase();
    if (!isAddress(a)) continue;
    if (!inDraft(a)) addToDraft({ address: a, allocationUsd: defaultUsd, enabled: true });
    added.push(a);
  }
  return added;
}

/** The href that opens the basket with these names already in it. */
export function basketHref(addresses: string[], basePath = "/copy/basket"): string {
  const list = addresses.map((a) => a.trim().toLowerCase()).filter(isAddress);
  return list.length ? `${basePath}?add=${list.join(",")}` : basePath;
}
