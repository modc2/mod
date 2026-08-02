"use client";

import { useState } from "react";
import { netuidHue } from "../lib/api";

/**
 * Subnet avatar: the on-chain `logo_url` when the team registered one,
 * otherwise the subnet's alpha symbol (or netuid) on a hue derived from
 * the netuid, so every subnet still looks like itself.
 *
 * Plain <img> on purpose — next/image would need every subnet team's
 * domain in remotePatterns, and these URLs change on-chain.
 */
export default function SubnetLogo({
  netuid,
  name,
  symbol,
  logo,
  size = 36,
}: {
  netuid: number;
  name?: string;
  symbol?: string | null;
  logo?: string | null;
  size?: number;
}) {
  const [broken, setBroken] = useState(false);
  const hue = netuidHue(netuid);
  const fallback = symbol || (name || "").slice(0, 1).toUpperCase() || String(netuid);

  return (
    <span
      className="shrink-0 inline-flex items-center justify-center overflow-hidden"
      style={{
        width: size,
        height: size,
        // Flat fill + hard border + a 2px inset bevel — a sprite tile.
        // A gradient here would be the one soft thing on the screen.
        background: `hsl(${hue} 55% 18%)`,
        border: `2px solid hsl(${hue} 70% 55%)`,
        boxShadow: `inset 2px 2px 0 hsl(${hue} 60% 30%)`,
        fontSize: Math.round(size * 0.4),
        lineHeight: 1,
        color: `hsl(${hue} 85% 72%)`,
      }}
      title={name || `SN${netuid}`}
    >
      {logo && !broken ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={logo}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setBroken(true)}
          // Nearest-neighbour scaling so team logos land on the pixel grid
          // instead of arriving as the only smooth thing on screen.
          style={{ width: size, height: size, objectFit: "cover", imageRendering: "pixelated" }}
        />
      ) : (
        <span className="font-display font-bold select-none">{fallback}</span>
      )}
    </span>
  );
}
