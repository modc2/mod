"use client";

import { useEffect, useState } from "react";
import { useThemeColors } from "../context/ThemeContext";

type Health = {
  connected: boolean;
  network?: string;
  block?: number;
  endpoint?: string | null;
  pool_size?: number;
  pool?: string[];
  error?: string;
};

const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/copytensor";

// Compact status chip showing the active Bittensor RPC endpoint + full pool
// in a tooltip. Green = connected, red = down. Polls every 30s.
export default function RpcPoolChip() {
  const skin = useThemeColors();
  const [h, setH] = useState<Health | null>(null);

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const r = await fetch(`${BASE}/health`, { cache: "no-store" });
        const data: Health = await r.json();
        if (alive) setH(data);
      } catch {
        if (alive) setH({ connected: false, error: "fetch failed" });
      }
    }
    poll();
    const t = setInterval(poll, 30_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!h) {
    return (
      <span className="pixel-btn topbar-ctl px-3 font-mono text-[15px] text-pixel-gray">
        rpc · …
      </span>
    );
  }

  const dotColor = h.connected ? skin.lime : skin.red;
  const host = (h.endpoint || "").replace(/^wss?:\/\//, "").split(":")[0];
  const shortHost = host.split(".").slice(0, 2).join(".") || "—";

  const tooltip = [
    h.connected ? `connected · block ${h.block}` : `down: ${h.error || "unknown"}`,
    `network: ${h.network}`,
    `pool (${h.pool_size}):`,
    ...(h.pool || []).map((p, i) => `  ${p === h.endpoint ? "▶" : " "} ${p}`),
  ].join("\n");

  return (
    <span
      title={tooltip}
      className="pixel-btn topbar-ctl px-3 font-mono text-[15px] text-pixel-gray-light flex items-center gap-2"
    >
      <span
        className="inline-block w-2 h-2 rounded-full"
        style={{ background: dotColor, boxShadow: `0 0 6px ${dotColor}` }}
      />
      rpc · {shortHost}
      <span className="text-pixel-gray">×{h.pool_size}</span>
    </span>
  );
}
