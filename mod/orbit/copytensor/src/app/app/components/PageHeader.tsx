import type { ReactNode } from "react";

/**
 * The marquee across the top of every page: title in the lit band,
 * standfirst on the plate below it, optional controls parked on the
 * right of the band.
 *
 * All five top-level pages had their own `<header><h1/><p/></header>`
 * and they had drifted apart (text-sm here, text-[12px] there), so the
 * treatment lives here now — see `.page-head` in globals.css.
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
  return (
    <header className="page-head">
      <div className="page-head-band">
        {/* Size is set in CSS, not here — it has to land on Press Start's
            8px design grid, and the sprite shadow scales with it. */}
        <h1 className="arcade-title flex-1 min-w-0">{title}</h1>
        {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
      </div>
      {children && <div className="arcade-prose px-[18px] py-3">{children}</div>}
    </header>
  );
}
