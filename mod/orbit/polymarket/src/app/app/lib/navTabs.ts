// The console's top tabs, and which one a path lights.
//
// COPY is the front door — copy traders and allocate to each. DESK runs the
// copies you saved. TRADERS is the deep board + profiles; STRATS is for
// building your own.

export const MAIN_TABS: { label: string; href: string }[] = [
  { label: "COPY", href: "/copytrade" },
  { label: "DESK", href: "/copy" },
  { label: "TRADERS", href: "/traders" },
  { label: "STRATS", href: "/strats" },
];

/** /copy/<addr> and /copy/basket are desk views; profiles and markets are
    views of the board, so TRADERS is the fallback. */
export function activeTab(pathname: string): string {
  if (pathname.startsWith("/copytrade")) return "/copytrade";
  if (pathname.startsWith("/strats")) return "/strats";
  if (pathname === "/copy" || pathname.startsWith("/copy/")) return "/copy";
  return "/traders";
}
