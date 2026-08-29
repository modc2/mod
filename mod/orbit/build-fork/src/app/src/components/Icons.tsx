"use client";

// Crisp inline-SVG icons for the console chrome. They replace the flat
// unicode glyphs (◈ ◇ ◆ ⌬ ▦ ▤ 📁) so the header reads as one designed
// system: every icon is stroke-based, inherits `currentColor` (so the
// existing hover/active color logic keeps working untouched), and sits on
// the same 24×24 grid.

import type { CSSProperties, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base({ size = 14, ...rest }: IconProps): SVGProps<SVGSVGElement> {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.9,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...rest,
  };
}

/** APP — a browser window: frame, title bar, three buttons. The old mark
 *  put a sparkle in the pane, which read as "magic" rather than "the live
 *  screen"; a window with its chrome is the thing everyone already knows. */
export function AppIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
      <path d="M3 9h18" />
      <circle cx="6.4" cy="6.75" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="9.4" cy="6.75" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="12.4" cy="6.75" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** API — two plugs meeting: a socket dot between call chevrons. */
export function ApiIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M7 8l-4 4 4 4" />
      <path d="M17 8l4 4-4 4" />
      <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** CODE — angle brackets. */
export function CodeIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M8.5 7L3.5 12l5 5" />
      <path d="M15.5 7l5 5-5 5" />
    </svg>
  );
}

/** INFO — the letter i in a ring. It used to be a compass, which nobody
 *  reads as "read about this module": ⓘ is the one glyph that needs no
 *  tooltip. */
export function OverviewIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="7.9" r="1.15" fill="currentColor" stroke="none" />
      <path d="M12 11.2v5.4" />
    </svg>
  );
}

/** IDEAS — a lightbulb with its filament and base. Replaces the bare
 *  diamond, which said nothing about suggestions. */
export function IdeaIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M9 17.2a6.2 6.2 0 1 1 6 0" />
      <path d="M9.6 17.5h4.8" />
      <path d="M10.4 20.3h3.2" />
    </svg>
  );
}

/** FILES — folder. */
export function FilesIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3.5 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
    </svg>
  );
}

/** VERSIONS — git branch: trunk, fork, nodes. */
export function VersionsIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="7" cy="6" r="2.4" />
      <circle cx="7" cy="18" r="2.4" />
      <circle cx="17" cy="8" r="2.4" />
      <path d="M7 8.4v7.2" />
      <path d="M17 10.4c0 3-3.5 3.6-7.2 5" />
    </svg>
  );
}

/** GRAPH — a node-link tree: root node fanning out to two leaves. */
export function GraphIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="5.5" cy="12" r="2.4" />
      <circle cx="18.5" cy="5.5" r="2.4" />
      <circle cx="18.5" cy="18.5" r="2.4" />
      <path d="M7.9 12c4 0 4-6.5 8.2-6.5" />
      <path d="M7.9 12c4 0 4 6.5 8.2 6.5" />
    </svg>
  );
}

/* ── Module actions — the ✎ ⑂ ✧ trio next to the module name ──────────
 * They were unicode (U+270E / U+2442 / U+2727), which was fine while a word
 * sat next to each one. Symbol-only, it isn't: ⑂ in particular tofus into an
 * empty box on any box without a font that carries it, and a blank square is
 * the one thing a button can't be. */

/** EDIT — a pencil. */
export function EditIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4.5 19.5l.9-3.6L15.8 5.5a2 2 0 0 1 2.8 2.8L8.1 18.6z" />
      <path d="M14.2 7.1l2.7 2.7" />
    </svg>
  );
}

/** FORK — one trunk splitting into a copy that goes its own way. */
export function ForkIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="7" cy="18" r="2.4" />
      <circle cx="7" cy="6" r="2.4" />
      <circle cx="17" cy="6" r="2.4" />
      <path d="M7 8.4v7.2" />
      <path d="M9.4 6h5.2" />
      <path d="M17 8.4v2.2" />
    </svg>
  );
}

/** NEW — a four-point spark: something that wasn't there a second ago. */
export function NewIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 3.5l1.9 4.6 4.6 1.9-4.6 1.9L12 16.5l-1.9-4.6L5.5 10l4.6-1.9z" />
      <path d="M18.5 15.5l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** HUB — four rounded tiles. */
export function HubIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="4" y="4" width="7" height="7" rx="1.6" />
      <rect x="13" y="4" width="7" height="7" rx="1.6" />
      <rect x="4" y="13" width="7" height="7" rx="1.6" />
      <rect x="13" y="13" width="7" height="7" rx="1.6" />
    </svg>
  );
}

/** GLOBE — the world. Sits on the left of the address bar as the HUB
 *  button, where a browser puts its site icon. */
export function GlobeIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17" />
      <path d="M12 3.5c2.5 2.3 3.8 5.1 3.8 8.5s-1.3 6.2-3.8 8.5c-2.5-2.3-3.8-5.1-3.8-8.5s1.3-6.2 3.8-8.5z" />
    </svg>
  );
}

/** TASKS — checklist. */
export function TasksIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 6.5l1.6 1.6L8.5 5" />
      <path d="M12 7h8.5" />
      <path d="M4 13.5l1.6 1.6 2.9-3.1" />
      <path d="M12 14h8.5" />
      <path d="M12 19.5h6" />
    </svg>
  );
}

/** ASK — a speech bubble with a prompt caret: the composer, collapsed. */
export function AskIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 6.5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H10l-4 3.5V16.5H6a2 2 0 0 1-2-2z" />
      <path d="M9 8.5l2.5 2L9 12.5" />
      <path d="M13.5 12.5h2.5" />
    </svg>
  );
}

/** PARAMS — sliders: the knobs that ride along with every task. */
export function ParamsIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 7.5h16" />
      <path d="M4 16.5h16" />
      <circle cx="9.5" cy="7.5" r="2.4" fill="var(--bg-secondary)" />
      <circle cx="15" cy="16.5" r="2.4" fill="var(--bg-secondary)" />
    </svg>
  );
}

/** SPINNER — a ring with a leading arc, spun by `animate-spin`. Drawn as an
 *  SVG (not the border-trick span) because at 8–16px a 1.5px CSS border
 *  rounds to different device pixels per edge, so the ring isn't concentric
 *  with the box and visibly wobbles off its rotation axis. */
export function SpinnerIcon({
  size = 12,
  thickness = 1.5,
  color = "currentColor",
  className,
  style,
}: {
  size?: number;
  thickness?: number;
  color?: string;
  className?: string;
  style?: CSSProperties;
}) {
  const sw = thickness * (24 / size); // px → viewBox units
  const r = 12 - sw / 2;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
      className={`animate-spin shrink-0${className ? ` ${className}` : ""}`}
      style={{ display: "block", ...style }}
    >
      <circle cx="12" cy="12" r={r} stroke={color} strokeOpacity="0.25" strokeWidth={sw} />
      <path
        d={`M12 ${12 - r} A ${r} ${r} 0 0 1 ${12 + r} 12`}
        stroke={color}
        strokeWidth={sw}
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * The mod protocol's mark: the CUBE. Same solid every module in the fleet
 * wears (core/app draws it with heroicons' `CubeIcon`), redrawn here on the
 * console's own 24×24 stroke grid so it inherits `currentColor` and sits at
 * the same weight as the chips beside it. This is the console's brand — the
 * orbit-star that used to hold that job is gone; a module in the protocol
 * should read as one of the protocol's, not as its own species.
 */
export function ModCube({ size = 18, strokeWidth = 1.5, ...rest }: IconProps & { strokeWidth?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      {/* heroicons/24/outline "cube" — the protocol's own solid, unmodified
          so the mark matches the one on the mod app's home. */}
      <path d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" />
    </svg>
  );
}

/** Title-case a module slug for display: "claude" → "Claude",
 *  "open-house" / "open_house" → "Open House". Identity strings (routes,
 *  API paths) keep using the raw name — this is presentation only. */
export function prettyModName(name?: string | null): string {
  if (!name) return "";
  return name
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
