// /polymarket/api/copytrades — my fills and my leaders' trades, joined.
//
// The join itself is lib/copyTrades.ts and it is pure. This route is only the
// plumbing around it, and the plumbing is the reason it is a route at all
// rather than a browser fetch: the leaders' trades come out of the worker's
// on-disk feed store (the same bytes the hub's cards and /api/basket replay
// over), so asking "what did the eight people I copy do today, and which of it
// did I get" costs one wallet walk instead of nine paginated /activity walks
// from the browser — see the offset ceiling in lib/polymarket.ts.
//
// Owner-gated with the token the Rust API mints, same as /api/hub and
// /api/basket, and it mints itself one for its own calls to the gated API
// (`mintOwnerToken` + `setServerAuthToken`) because there is no fetch patch in
// Node and the background worker's `authenticate` may never have run.
//
// It answers with ROWS, not with a filtered view: the semantic filter
// (lib/semanticFilter.ts) runs in the browser so re-typing a query is instant
// and costs nothing. `?q=` is accepted anyway for an agent that wants the
// filtering done server-side.

import { NextResponse } from "next/server";

import { bearer, mintOwnerToken, ownerAddress, verifyOwnerToken } from "../../lib/server/ownerToken";
import { feedSession } from "../../lib/server/feedFetcher";
import { API_BASE, serverAuthHeaders, setServerAuthToken, fetchWalletTradesUntil } from "../../lib/polymarket";
import { buildCopyTrades, scoreLeaders, DEFAULT_MATCH_MS } from "../../lib/copyTrades";
import { applySemanticQuery, compileGate, parseSemanticQuery } from "../../lib/semanticFilter";
import type { TraderFeed } from "../../lib/hubReplay";
import type { PolymarketTrade } from "../../lib/types";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
/** A cold leader is backfilled inline; a book of ten can take a while. */
export const maxDuration = 300;

/** Rows returned at most. The feed is a reading surface, not an export. */
const MAX_ROWS = 800;

function deny() {
  return NextResponse.json({ error: "unauthorized", gate: "polymarket-access" }, { status: 401 });
}

interface BookRow {
  address: string;
  label?: string | null;
  allocationUsd: number;
  enabled: boolean;
}

async function copyBook(eoa: string | null): Promise<{ eoa: string | null; rows: BookRow[] }> {
  const res = await fetch(`${API_BASE}/copy/book${eoa ? `?eoa=${encodeURIComponent(eoa)}` : ""}`, {
    headers: { "Content-Type": "application/json", ...serverAuthHeaders() },
    cache: "no-store",
  });
  if (!res.ok) return { eoa, rows: [] };
  const book = (await res.json()) as { eoa?: string | null; allocations?: BookRow[] };
  return { eoa: eoa ?? book.eoa ?? null, rows: book.allocations ?? [] };
}

/** Where my orders actually land. V2 trades through a proxy wallet, so the
    fills are that wallet's activity, not the signing EOA's. */
async function depositWallet(eoa: string): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/deposit-wallet/info?eoa=${encodeURIComponent(eoa)}`, {
      headers: { "Content-Type": "application/json", ...serverAuthHeaders() },
      cache: "no-store",
    });
    if (!res.ok) return null;
    const info = (await res.json()) as { depositWallet?: string; proxyAddress?: string };
    return info.depositWallet ?? info.proxyAddress ?? null;
  } catch {
    return null;
  }
}

export async function GET(req: Request) {
  if (!verifyOwnerToken(bearer(req))) return deny();
  const minted = mintOwnerToken();
  if (minted) setServerAuthToken(minted);

  const url = new URL(req.url);
  const days = Math.max(1, Math.min(30, Number(url.searchParams.get("days")) || 7));
  const matchMs = Math.max(
    60_000,
    Math.min(6 * 3_600_000, (Number(url.searchParams.get("matchMinutes")) || 0) * 60_000 || DEFAULT_MATCH_MS),
  );
  const q = (url.searchParams.get("q") ?? "").trim();
  const askedEoa = (url.searchParams.get("eoa") ?? "").trim().toLowerCase() || null;

  const { eoa, rows: book } = await copyBook(askedEoa ?? ownerAddress());
  const wallet = eoa ? await depositWallet(eoa) : null;

  // ── My fills. One walk, cut at the window (the helper takes seconds). ──
  const now = Date.now();
  const cutoffSec = Math.floor((now - days * 86_400_000) / 1000);
  let mine: PolymarketTrade[] = [];
  let fillsError: string | null = null;
  if (wallet) {
    try {
      mine = await fetchWalletTradesUntil(wallet, cutoffSec);
    } catch (e) {
      fillsError = e instanceof Error ? e.message : String(e);
    }
  }

  // ── The leaders, out of the worker's store. Never a fan-out of upstream
  //    walks: a leader nobody has fetched comes back `warming`, not empty. ──
  const session = feedSession();
  const cache = new Map<string, Promise<TraderFeed>>();
  const leaders: Record<string, PolymarketTrade[]> = {};
  const labels: Record<string, string | null> = {};
  for (const row of book) {
    const address = String(row.address ?? "").toLowerCase();
    if (!address) continue;
    labels[address] = row.label ?? null;
    const feed = await session.load(address, cache);
    leaders[address] = feed.trades;
  }
  const warming = Array.from(session.pending);

  const built = buildCopyTrades(
    { mine, leaders },
    { now, windowMs: days * 86_400_000, matchMs, labels },
  );

  let rows = built.rows;
  let filtered: { dropped: number; reasons: Record<string, number> } | null = null;
  // What the sentence was read as, and what of it can be ARMED as a real copy
  // gate. An agent compiles here and then writes the gate with
  // `pm_copy_allocate params:{marketQuery, tradeFilters}` — the same two knobs
  // the console's ARM button sets, so both front ends arm the same thing.
  let query: { chips: unknown; gate: unknown } | null = null;
  if (q) {
    const parsed = parseSemanticQuery(q);
    const res = applySemanticQuery(rows, parsed, { now });
    rows = res.rows;
    filtered = { dropped: res.dropped, reasons: res.reasons };
    query = { chips: parsed.chips, gate: compileGate(parsed) };
  }

  return NextResponse.json({
    days,
    eoa,
    wallet,
    matchMinutes: Math.round(matchMs / 60_000),
    summary: built.summary,
    leaders: scoreLeaders(built.rows),
    rows: rows.slice(0, MAX_ROWS),
    truncated: rows.length > MAX_ROWS,
    // Facts about the CACHE, kept apart from facts about the traders: a leader
    // in this list traded nothing on screen because nothing has been fetched
    // for them yet, which is not the same as a leader who was quiet.
    warming,
    ...(fillsError ? { fillsError } : {}),
    ...(filtered ? { filtered } : {}),
    ...(query ? { query } : {}),
    ...(wallet ? {} : { note: "no funded wallet resolved — the leaders' half still shows" }),
  });
}
