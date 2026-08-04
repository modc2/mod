"use client";

import { useState, type ReactNode } from "react";

/**
 * The marquee across the top of every page: title in the lit band,
 * standfirst on the plate below it, optional controls parked on the
 * right of the band.
 *
 * All five top-level pages had their own `<header><h1/><p/></header>`
 * and they had drifted apart (text-sm here, text-[12px] there), so the
 * treatment lives here now — see `.page-head` in globals.css.
 *
 * On a phone the standfirst folds away behind the "?" cap. Five lines of
 * explanation is the right thing on a desktop board with room to spare,
 * and the wrong thing on a screen where it pushes the data you came for
 * below the fold on every single visit.
 */
export default function PageHeader({
  title,
  children,
  right,
}: {
  title: string;
  children?: ReactNode;
  right?: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <header className="page-head">
      <div className="page-head-band">
        {/* Size is set in CSS, not here — it has to land on Press Start's
            8px design grid, and the sprite shadow scales with it. */}
        <h1 className="arcade-title flex-1 min-w-0">{title}</h1>
        {children && (
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={open ? "Hide the description" : "What is this page?"}
            className={`pixel-btn text-[11px] px-3 py-1 md:hidden shrink-0 ${
              open ? "nav-active" : "text-pixel-gray-light"
            }`}
          >
            ?
          </button>
        )}
        {right && (
          <div className="flex items-center gap-2 shrink-0 w-full md:w-auto">{right}</div>
        )}
      </div>
      {children && (
        <div className={`arcade-prose px-[18px] py-3 ${open ? "" : "hidden"} md:block`}>
          {children}
        </div>
      )}
    </header>
  );
}
