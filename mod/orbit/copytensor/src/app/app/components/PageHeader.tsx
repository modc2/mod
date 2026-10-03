"use client";

import type { ReactNode } from "react";

/**
 * Page title + one quiet line under it. It used to be a boxed marquee
 * with a 32px title, a lit band and a paragraph of standfirst on its own
 * plate — a third of the first screen before any data. Now it's a heading.
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
      <div className="min-w-0 flex-1">
        <h1 className="arcade-title">{title}</h1>
        {children && <p className="page-head-sub">{children}</p>}
      </div>
      {right && <div className="flex items-center gap-2 shrink-0 w-full md:w-auto">{right}</div>}
    </header>
  );
}
