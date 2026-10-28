"use client";

import { useMemo } from "react";

/**
 * Deterministic 5×5 sprite per coldkey — mirrored down the centre column,
 * the way an avatar sprite is drawn on a tile sheet.
 *
 * This was a smooth two-stop linear-gradient, which made it the one
 * element on the board with no hard edge anywhere in it; a row of them
 * read as blurred blobs against everything else. The cell size is a
 * whole number of device pixels so the grid can't land half-lit.
 */
const SPRITE_HUES = ["--neon-cyan", "--neon-lime", "--neon-magenta", "--neon-amber"];

export default function Identicon({ ss58, size = 24 }: { ss58: string; size?: number }) {
  const { hue, cells } = useMemo(() => {
    // FNV-ish walk — cheap, stable, and spreads adjacent ss58s apart.
    let h = 2166136261;
    for (const c of ss58) h = ((h ^ c.charCodeAt(0)) * 16777619) >>> 0;
    const on: boolean[] = [];
    for (let y = 0; y < 5; y++) {
      for (let x = 0; x < 3; x++) {
        on[y * 3 + x] = ((h >>> ((y * 3 + x) % 30)) & 1) === 1;
      }
    }
    // Mirror columns 0,1 back over 3,4 so the sprite reads as a face.
    const grid: boolean[] = [];
    for (let y = 0; y < 5; y++)
      for (let x = 0; x < 5; x++) grid.push(on[y * 3 + (x < 3 ? x : 4 - x)]);
    return { hue: SPRITE_HUES[h % SPRITE_HUES.length], cells: grid };
  }, [ss58]);

  return (
    <span
      className="shrink-0 grid grid-cols-5 border-2"
      style={{
        width: size,
        height: size,
        borderColor: "var(--shadow-hard)",
        background: "var(--input-bg)",
      }}
      aria-hidden
    >
      {cells.map((on, i) => (
        <span key={i} style={on ? { background: `var(${hue})` } : undefined} />
      ))}
    </span>
  );
}
