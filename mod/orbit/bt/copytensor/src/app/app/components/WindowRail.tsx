"use client";

import { WINDOW_DAYS, windowLabel } from "../lib/api";
import { useFilters } from "../context/FiltersContext";
import { useCoverage, windowCoverage } from "../lib/useCoverage";

/**
 * The horizon control, and the honest label under it.
 *
 * Every number on the board is measured over one window, and the window used
 * to be five unexplained pills reading "1d 3d 7d 14d 30d" on the board only —
 * the front page had 7 days welded in. Two things were wrong with that: you
 * couldn't change it where you were actually looking, and nothing said how
 * much history existed behind the button you pressed. Ask for 30 days over an
 * index that has been running for twelve and the rows come back quietly
 * measured over twelve.
 *
 * So each chip carries its own coverage: lit when every ranked trader has
 * that much history, marked with the count when only some do, disabled when
 * the index simply does not go back that far. ALL is the window that can
 * never lie — it asks for whatever there is.
 */
export default function WindowRail({
  caption = true,
  className = "",
}: {
  caption?: boolean;
  className?: string;
}) {
  const { days, setDays } = useFilters();
  const cov = useCoverage();

  return (
    <div className={`window-rail ${className}`}>
      <div className="rail no-scrollbar min-w-0">
        <span className="window-rail-k">history</span>
        {WINDOW_DAYS.map((w) => {
          const c = windowCoverage(cov, w);
          // Unknown coverage is not the same as none — never grey out a
          // window just because the coverage call hasn't landed yet.
          const dead = c ? !c.ok : false;
          const thin = !!c && c.ok && c.pct < 95 && w !== 0;
          return (
            <button
              key={w}
              onClick={() => setDays(w)}
              disabled={dead}
              aria-pressed={days === w}
              title={
                w === 0
                  ? cov
                    ? `Every day indexed — ${cov.depth_days} days at the deepest`
                    : "Every day of history the index holds"
                  : dead
                    ? `The index only goes back ${cov?.depth_days ?? 0} days`
                    : thin
                      ? `${c!.covered} of ${cov?.priced ?? 0} traders have ${w} days of history`
                      : `${w}-day window · all ${c?.covered ?? ""} ranked traders cover it`
              }
              className={`pixel-btn window-chip ${
                days === w ? "window-chip-on" : "text-pixel-gray-light"
              } ${thin ? "window-chip-thin" : ""}`}
            >
              {windowLabel(w)}
              {w === 0 && cov ? (
                <span className="window-chip-note">{cov.depth_days}d</span>
              ) : thin ? (
                <span className="window-chip-note">{c!.pct.toFixed(0)}%</span>
              ) : null}
            </button>
          );
        })}
      </div>

      {caption && (
        <p className="window-rail-note">
          {cov ? (
            <>
              index reaches back to{" "}
              <span className="window-rail-em">{fmtBack(cov.oldest_ts)}</span>
              {" · "}
              {cov.depth_days} day{cov.depth_days === 1 ? "" : "s"} deep
              {" · "}
              {cov.priced} trader{cov.priced === 1 ? "" : "s"} priced
              {cov.median_days > 0 && (
                <> · typical trader has {Math.floor(cov.median_days)}d</>
              )}
            </>
          ) : (
            <>measuring how far back the index goes…</>
          )}
        </p>
      )}
    </div>
  );
}

/** "27 Jul 2026" — the day the oldest snapshot we can price against was taken. */
function fmtBack(ts: number | null) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
