// THE STRATS PAGE'S TABS — one address for every piece of management.
//
// 2026-10-01 the right-hand side panel (UserSidebar) was removed — "too
// complicated, let all the management live in strats". Everything it held is
// now a tab of /strats, addressed by ?tab=:
//
//   strats    build, manage, share (the default)
//   copy      who you copy, with how much — start / stop, plus the finder's
//             checked shortlist (SelectionTray)
//   money     your account + liquidity: top up, take out, bridge
//   backtest  replay the active strat on history (Workspace)
//   live      run it against the real book (Workspace)
//   trades    every position the account has held, with its P&L
//
// The URL is the state, so a tab is linkable and the back button works. The
// old "open the panel" events still fire all over the console (wallet chip,
// FUND NOW, SHOW PANEL →); StratsNav turns each into a navigation here.

export type StratsView = "strats" | "copy" | "money" | "backtest" | "live" | "trades";

export const STRATS_VIEWS: StratsView[] = ["strats", "copy", "money", "backtest", "live", "trades"];

export const isStratsView = (v: unknown): v is StratsView => STRATS_VIEWS.includes(v as StratsView);

/** Path WITHOUT basePath — Next prepends "/polymarket" itself. */
export function stratsHref(view: StratsView = "strats"): string {
  return view === "strats" ? "/strats" : `/strats?tab=${view}`;
}

/** Ask for a tab of /strats from anywhere without importing the router —
    StratsNav (mounted in TopBar) does the navigation. */
export const STRATS_VIEW_EVENT = "poly-strats-view";
export function openStratsView(view: StratsView): void {
  window.dispatchEvent(new CustomEvent(STRATS_VIEW_EVENT, { detail: view }));
}

// ── Legacy event names ──
// Fired by components that predate the removal; kept so nothing has to know
// the panel is gone. "poly-sidebar-tab" carried the old rail's tab ids.
export const OPEN_SIDEBAR_EVENT = "poly-open-sidebar";
export const SIDEBAR_TAB_EVENT = "poly-sidebar-tab";
const LEGACY_TAB: Record<string, StratsView> = {
  INDEX: "copy", MONEY: "money", BACKTEST: "backtest", LIVE: "live", STRATS: "strats",
};
export function viewFromLegacyTab(tab: unknown): StratsView {
  return (typeof tab === "string" && LEGACY_TAB[tab]) || "copy";
}
