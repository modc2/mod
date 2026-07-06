"use client";

// Crisp inline-SVG icons for the console chrome. They replace the flat
// unicode glyphs (◈ ◇ ◆ ⌬ ▦ ▤ 📁) so the header reads as one designed
// system: every icon is stroke-based, inherits `currentColor` (so the
// existing hover/active color logic keeps working untouched), and sits on
// the same 24×24 grid.

import type { SVGProps } from "react";

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

/** APP — a window with a live spark inside. */
export function AppIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <path d="M3 8.5h18" />
      <path d="M12 11l1.1 2.4 2.4 1.1-2.4 1.1L12 18l-1.1-2.4-2.4-1.1 2.4-1.1z" fill="currentColor" stroke="none" />
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

/** OVERVIEW — a compass: ring + needle. */
export function OverviewIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M15.5 8.5l-2 5-5 2 2-5z" fill="currentColor" stroke="none" />
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

/**
 * The console's signature mark: a four-point star caught in an orbit ring.
 * Rendered with a gradient stroke so it pops as a *logo* rather than one
 * more icon; scales from the header brand down to a favicon.
 */
export function ClaudeMark({ size = 18, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
      {...rest}
    >
      <defs>
        <linearGradient id="claude-mark-grad" x1="3" y1="21" x2="21" y2="3" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="var(--crt-green, #34d399)" />
          <stop offset="55%" stopColor="var(--crt-amber, #fbbf24)" />
          <stop offset="100%" stopColor="#cc785c" />
        </linearGradient>
      </defs>
      {/* orbit ring, tilted */}
      <ellipse
        cx="12" cy="12" rx="10" ry="4.4"
        transform="rotate(-24 12 12)"
        stroke="url(#claude-mark-grad)"
        strokeWidth="1.6"
      />
      {/* the star */}
      <path
        d="M12 5.6l1.7 4.7 4.7 1.7-4.7 1.7L12 18.4l-1.7-4.7-4.7-1.7 4.7-1.7z"
        fill="url(#claude-mark-grad)"
      />
      {/* companion moon on the ring */}
      <circle cx="20.4" cy="8.2" r="1.5" fill="var(--crt-green, #34d399)" />
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
