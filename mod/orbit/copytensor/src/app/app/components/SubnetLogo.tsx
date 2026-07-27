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
      className="shrink-0 inline-flex items-center justify-center overflow-hidden rounded-full"
      style={{
        width: size,
        height: size,
        background: `linear-gradient(140deg, hsl(${hue} 60% 22%), hsl(${(hue + 40) % 360} 55% 12%))`,
        border: `1px solid hsl(${hue} 55% 45% / 0.35)`,
        fontSize: Math.round(size * 0.44),
        lineHeight: 1,
        color: `hsl(${hue} 70% 78%)`,
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
          style={{ width: size, height: size, objectFit: "cover" }}
        />
      ) : (
        <span className="font-display font-bold select-none">{fallback}</span>
      )}
    </span>
  );
}
