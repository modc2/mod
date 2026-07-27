// Reconstruct entry → exit round trips from raw Hyperliquid fills.
//
// The exchange reports per-fill `startPosition` (signed position before the
// fill), which lets us segment a coin's fill stream into round trips without
// tracking state across the whole account history: a trip opens when the
// position leaves zero and closes when it returns to zero (or flips sign).

export type RawFill = {
  coin: string;
  side: string;           // "B" | "A"
  px: string;
  sz: string;
  time: number;
  startPosition?: string; // signed size before this fill
  closedPnl?: string;
  fee?: string;
  dir?: string;
};

export type RoundTrip = {
  coin: string;
  long: boolean;
  peakSize: number;      // largest absolute position held during the trip
  entryPx: number;       // size-weighted avg of entry legs seen in window
  exitPx: number;        // size-weighted avg of exit legs (0 if none yet)
  entrySz: number;
  exitSz: number;
  closedPnl: number;     // Σ closedPnl (gross, HL convention)
  fees: number;
  fills: number;
  openTime: number;
  closeTime: number | null; // null = still open at end of window
  partialEntry: boolean;    // position predates the window; entry avg incomplete
};

const EPS = 1e-7;

export function buildRoundTrips(fills: RawFill[]): RoundTrip[] {
  const byCoin = new Map<string, RawFill[]>();
  for (const f of fills) {
    const arr = byCoin.get(f.coin);
    if (arr) arr.push(f); else byCoin.set(f.coin, [f]);
  }

  const trips: RoundTrip[] = [];

  const push = (t: RoundTrip, closeTime: number | null) => {
    t.entryPx = t.entrySz > EPS ? t.entryPx / t.entrySz : 0;
    t.exitPx = t.exitSz > EPS ? t.exitPx / t.exitSz : 0;
    // float-sum residue (e.g. 63.190000000000005) reads as noise in the UI
    t.peakSize = Number(t.peakSize.toFixed(6));
    t.closeTime = closeTime;
    trips.push(t);
  };

  byCoin.forEach((fs, coin) => {
    fs.sort((a, b) => Number(a.time) - Number(b.time));
    let trip: RoundTrip | null = null;

    for (const f of fs) {
      const px = Number(f.px) || 0;
      const sz = Number(f.sz) || 0;
      const delta = f.side === "B" ? sz : -sz;
      const cp = Number(f.closedPnl) || 0;
      const fee = Number(f.fee) || 0;
      const sp = Number(f.startPosition);
      const before = Number.isFinite(sp) ? sp : 0;
      let after = before + delta;
      if (Math.abs(after) < EPS) after = 0;

      if (!trip) {
        trip = {
          coin,
          long: Math.abs(before) > EPS ? before > 0 : delta > 0,
          peakSize: Math.abs(before),
          entryPx: 0, exitPx: 0, entrySz: 0, exitSz: 0,
          closedPnl: 0, fees: 0, fills: 0,
          openTime: Number(f.time), closeTime: null,
          partialEntry: Math.abs(before) > EPS,
        };
      }

      trip.fills += 1;
      trip.closedPnl += cp;
      trip.fees += fee;
      trip.peakSize = Math.max(trip.peakSize, Math.abs(before), Math.abs(after));

      const extendsTrip = trip.long ? delta > 0 : delta < 0;
      if (extendsTrip) {
        trip.entrySz += Math.abs(delta);
        trip.entryPx += px * Math.abs(delta); // notional; averaged in push()
      } else {
        const closeAmt = Math.min(Math.abs(before), Math.abs(delta));
        trip.exitSz += closeAmt;
        trip.exitPx += px * closeAmt;
        const flipAmt = Math.abs(delta) - closeAmt;
        if (flipAmt > EPS) {
          // Position flipped in one fill: close this trip here and open the
          // opposite one with the remainder.
          push(trip, Number(f.time));
          trip = {
            coin,
            long: after > 0,
            peakSize: Math.abs(after),
            entryPx: px * flipAmt, exitPx: 0, entrySz: flipAmt, exitSz: 0,
            closedPnl: 0, fees: 0, fills: 1,
            openTime: Number(f.time), closeTime: null,
            partialEntry: false,
          };
          continue;
        }
      }

      if (after === 0) {
        push(trip, Number(f.time));
        trip = null;
      }
    }

    if (trip) push(trip, null); // still open at end of window
  });

  // Open trips first, then most recently closed.
  trips.sort((a, b) =>
    (b.closeTime ?? Number.POSITIVE_INFINITY) - (a.closeTime ?? Number.POSITIVE_INFINITY)
    || b.openTime - a.openTime
  );
  return trips;
}

export const fmtDuration = (ms: number) => {
  if (ms < 0) return "—";
  const m = Math.floor(ms / 60000);
  if (m < 1) return "<1m";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
};
