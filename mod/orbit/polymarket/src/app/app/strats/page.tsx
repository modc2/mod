import { redirect } from "next/navigation";

// /strats — RETIRED, kept as a forwarder.
//
// This route used to be the console's second product: a hub of SAVED STRATS
// (multi-trader indexes in localStorage), a gallery of ~15 templates to fork
// from, a public shelf to publish to and import by CID, a per-strat agent chat
// that proposed parameter patches, and an uploader for Python strats. One
// leader with a dollar amount against them was a special case of all that.
//
// It's the other way round now. The console copies INDIVIDUAL TRADERS and
// nothing else: `/copy` is the desk and `/copy/<address>` is one leader's
// workspace. The strat layer is archived, not deleted — `src/_archive/README.md`
// says what it was and how to bring it back.
//
// The forward exists because the retired route's ids are still in bookmarks,
// in the browser's history, and in any chat log where the console was linked.
// A copy-desk id decodes back to the leader it copies (`copy-<address>`), so
// those land on the right workspace; everything else was a multi-trader strat
// with no single-trader equivalent, and lands on the desk.
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

export const dynamic = "force-dynamic";

/** `copy-<40 hex>` → `0x<40 hex>`; anything else → null. Deliberately strict:
    an old hub strat's opaque base36 id must NOT decode to an address, or a
    retired multi-trader strat would forward to some unrelated leader's desk
    row. Mirrors `addressFromStrategyId` in lib/identityStrat.ts, duplicated
    here because that module is client-only ("use client" transitively). */
function addressFromStratId(id: string | undefined): string | null {
  if (!id || !id.startsWith("copy-")) return null;
  const hex = id.slice(5);
  return /^[0-9a-fA-F]{40}$/.test(hex) ? `0x${hex.toLowerCase()}` : null;
}

export default function RetiredStratsPage({
  searchParams,
}: {
  searchParams?: { id?: string | string[] };
}) {
  const raw = searchParams?.id;
  const id = Array.isArray(raw) ? raw[0] : raw;
  const address = addressFromStratId(id);
  redirect(address ? `/copy/${address}` : "/copy");
}
