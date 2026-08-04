"use client";

import { ReactNode } from "react";
import {
  useSidebar, SIDEBAR_DEFAULT, type SidebarPanel, type ResizeEdge,
} from "../context/SidebarContext";
import WatchlistDrawer from "./WatchlistDrawer";
import StratPicker from "./StratPicker";

const TABS: { id: SidebarPanel; label: string }[] = [
  { id: "watch", label: "WATCHLIST" },
  { id: "strat", label: "STRAT MAKER" },
];

/** The eight grab zones of the popped-out window, as CSS-positioned strips. */
const EDGES: { edge: ResizeEdge; className: string }[] = [
  { edge: "n", className: "top-0 left-2 right-2 h-1.5 cursor-n-resize" },
  { edge: "s", className: "bottom-0 left-2 right-2 h-1.5 cursor-s-resize" },
  { edge: "w", className: "left-0 top-2 bottom-2 w-1.5 cursor-w-resize" },
  { edge: "e", className: "right-0 top-2 bottom-2 w-1.5 cursor-e-resize" },
  { edge: "nw", className: "left-0 top-0 w-3 h-3 cursor-nw-resize" },
  { edge: "ne", className: "right-0 top-0 w-3 h-3 cursor-ne-resize" },
  { edge: "sw", className: "left-0 bottom-0 w-3 h-3 cursor-sw-resize" },
  { edge: "se", className: "right-0 bottom-0 w-4 h-4 cursor-se-resize" },
];

export default function SidebarShell({ children }: { children: ReactNode }) {
  const {
    docked, width, hydrated, panel, expanded, mode, rect, rolled,
    setWidth, setDocked, setPanel, setMode, toggleExpanded, toggleRolled,
    startDrag, startMove, startResize,
  } = useSidebar();

  if (!hydrated) return <>{children}</>;
  if (!docked) return <>{children}</>;

  const floating = mode === "float";
  const resetWidth = () => setWidth(SIDEBAR_DEFAULT);
  // The builder's table only earns its second column past ~620px, whichever
  // shape the drawer is in.
  const compact = (floating ? rect.w : width) < 620;

  // Drag the window by its bar, but never by the caps sitting on it.
  const onBarDown = (e: React.MouseEvent) => {
    if (!floating) return;
    if ((e.target as HTMLElement).closest("button")) return;
    startMove(e);
  };

  return (
    <div className="flex items-stretch min-h-[calc(100vh-3rem)]">
      <main className="flex-1 min-w-0 overflow-x-hidden">{children}</main>
      <div
        onMouseDown={startDrag}
        onDoubleClick={resetWidth}
        className={`drawer-grip shrink-0 ${floating ? "hidden" : ""}`}
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize · Double-click to reset"
      />
      {/* Dock and pop-out are the SAME element with different chrome — moving
          it in the tree would remount both panels and throw away a half-built
          basket every time you popped the drawer out. */}
      <aside
        style={
          floating
            ? { left: rect.x, top: rect.y, width: rect.w, height: rolled ? undefined : rect.h }
            : { width }
        }
        className={
          floating
            ? "drawer-win flex flex-col overflow-hidden"
            : "shrink-0 border-l-2 border-pixel-border bg-pixel-black/60 overflow-y-auto"
        }
      >
        {/* Popped out, the window gets a title bar of its own. It's the drag
            handle: sharing one rail with the tabs left barely a caption's
            width of empty bar to grab, and every near-miss hit a button. */}
        {floating && (
          <div
            onMouseDown={onBarDown}
            onDoubleClick={toggleExpanded}
            className="drawer-bar drawer-bar-drag"
          >
            <span className="drawer-title">
              {TABS.find((t) => t.id === panel)?.label}
            </span>
            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setMode("dock")}
                className="pixel-btn text-[10px] px-2 py-1"
                title="Snap the window back to the edge"
              >
                DOCK
              </button>
              <button
                onClick={toggleExpanded}
                aria-pressed={expanded}
                className={`pixel-btn text-[10px] px-2 py-1 ${
                  expanded ? "border-green-400 text-green-400" : ""
                }`}
                title={expanded ? "Restore the window" : "Fill the screen"}
              >
                {expanded ? "RESTORE" : "MAX"}
              </button>
              <button
                onClick={toggleRolled}
                aria-pressed={rolled}
                className="pixel-btn text-[10px] px-2 py-1"
                title={rolled ? "Unroll the window" : "Roll the window up into its bar"}
              >
                {rolled ? "OPEN" : "ROLL"}
              </button>
              <button
                onClick={() => setDocked(false)}
                className="pixel-btn text-[10px] px-2 py-1"
                title="Close drawer"
              >
                ✕
              </button>
            </div>
          </div>
        )}
        {/* One rail switches the drawer between the two things you do with a
            trader: keep an eye on it, or put it in a basket. */}
        <div
          className={`drawer-bar ${floating ? "drawer-bar-sub" : "sticky top-0 z-10"}`}
        >
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setPanel(t.id)}
              aria-pressed={panel === t.id}
              className={`pixel-btn text-[10px] px-2 py-1 ${
                panel === t.id ? "nav-active" : "text-pixel-gray"
              }`}
            >
              {t.label}
            </button>
          ))}
          {!floating && (
            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setMode("float")}
                className="pixel-btn text-[10px] px-2 py-1"
                title="Tear the drawer off into a window"
              >
                POP OUT
              </button>
              <button
                onClick={toggleExpanded}
                aria-pressed={expanded}
                className={`pixel-btn text-[10px] px-2 py-1 ${
                  expanded ? "border-green-400 text-green-400" : ""
                }`}
                title={expanded ? "Shrink the drawer" : "Expand the drawer"}
              >
                {expanded ? "SHRINK" : "EXPAND"}
              </button>
              <button
                onClick={() => setDocked(false)}
                className="pixel-btn text-[10px] px-2 py-1"
                title="Close drawer"
              >
                ✕
              </button>
            </div>
          )}
        </div>
        {/* Both panels stay mounted: switching to the watchlist mid-build must
            not throw away a half-filled basket. */}
        <div className={floating ? `flex-1 min-h-0 overflow-y-auto ${rolled ? "hidden" : ""}` : ""}>
          <div className={`p-2 ${panel === "watch" ? "" : "hidden"}`}>
            <WatchlistDrawer />
          </div>
          <div className={`p-2 ${panel === "strat" ? "" : "hidden"}`}>
            <StratPicker compact={compact} />
          </div>
        </div>
        {floating && !rolled && EDGES.map((g) => (
          <div
            key={g.edge}
            onMouseDown={(e) => startResize(e, g.edge)}
            className={`absolute ${g.className} ${g.edge === "se" ? "drawer-corner" : ""}`}
          />
        ))}
      </aside>
    </div>
  );
}
