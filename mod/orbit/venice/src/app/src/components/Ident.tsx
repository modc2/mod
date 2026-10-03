import type { ReactElement } from "react";

/**
 * A pixel identicon: a 5×5 sprite, mirrored down the middle, derived from an
 * address. Deterministic and theme-independent — the same identity is the same
 * little sprite in every display mode, which is the whole point of an avatar.
 */

// An 8-bit-console palette, not the theme's — the sprite is meant to stay put
// while the mode changes around it.
const PALETTE = [
  "#e43b44", "#ffd83d", "#45d67d", "#3d9bff",
  "#b45bff", "#ff8a3d", "#2bd6c6", "#ff5dd2",
];

// FNV-1a: tiny, stable, and good enough to scatter addresses across sprites.
function hash32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export default function Ident({
  address,
  size = 20,
}: {
  address: string;
  size?: number;
}) {
  const h = hash32((address || "").toLowerCase());
  const fg = PALETTE[h % PALETTE.length];
  const bg = PALETTE[(h >>> 5) % PALETTE.length];

  // 15 bits fill the left half + centre column; the right half mirrors it.
  const cells: ReactElement[] = [];
  for (let y = 0; y < 5; y++) {
    for (let x = 0; x < 3; x++) {
      if (((h >>> (y * 3 + x)) & 1) === 0) continue;
      cells.push(<rect key={`${x}${y}`} x={x} y={y} width="1" height="1" fill={fg} />);
      if (x < 2) cells.push(<rect key={`m${x}${y}`} x={4 - x} y={y} width="1" height="1" fill={fg} />);
    }
  }

  return (
    <svg
      className="ident"
      width={size}
      height={size}
      viewBox="0 0 5 5"
      shapeRendering="crispEdges"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="0" y="0" width="5" height="5" fill={bg} opacity="0.18" />
      {cells}
    </svg>
  );
}
