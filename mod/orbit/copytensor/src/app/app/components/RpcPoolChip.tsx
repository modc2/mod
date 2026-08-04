"use client";

import { useEffect, useRef, useState } from "react";
import { useThemeColors } from "../context/ThemeContext";

type Health = {
  connected: boolean;
  network?: string;
  block?: number;
  endpoint?: string | null;
  pool_size?: number;
  pool?: string[];
  reads?: string;
  error?: string;
};

const BASE = process.env.NEXT_PUBLIC_API_URL || "/api/copytensor";

// Compact status chip showing the active Bittensor RPC endpoint. Green =
// connected, red = down. Polls every 30s. Pressing it drops the full pool:
// the chip only has room for a truncated host, and the thing you actually
// want — which node we're reading, at what block — was hidden in a native
// `title` that never appears on touch.
export default function RpcPoolChip() {
  const skin = useThemeColors();
  const [h, setH] = useState<Health | null>(null);
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

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

  // Click-away and Escape both close it — same contract as the skin menu.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

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
  const pool = h.pool || (h.endpoint ? [h.endpoint] : []);

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(url);
      setTimeout(() => setCopied((c) => (c === url ? null : c)), 1500);
    } catch {}
  };

  return (
    <div ref={wrap} className="relative shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`pixel-btn topbar-ctl px-3 font-mono text-[15px] text-pixel-gray-light flex items-center gap-2 ${
          open ? "nav-active" : ""
        }`}
      >
        <span
          className="inline-block w-2 h-2 rounded-full"
          style={{ background: dotColor, boxShadow: `0 0 6px ${dotColor}` }}
        />
        rpc · {shortHost}
        <span className="text-pixel-gray">×{pool.length || h.pool_size || 0}</span>
      </button>

      {open && (
        <div className="rpc-menu" role="dialog" aria-label="RPC pool">
          <div className="rpc-menu__head">rpc pool</div>

          <Row k="status">
            <span style={{ color: dotColor }}>
              {h.connected ? "connected" : `down — ${h.error || "unknown"}`}
            </span>
          </Row>
          <Row k="network">{h.network || "—"}</Row>
          <Row k="block">
            {h.block ? h.block.toLocaleString() : "—"}
          </Row>
          {h.reads && <Row k="reads">{h.reads}</Row>}

          {/* The pool itself, active node first-marked. Each row copies its
              own URL — the reason you open this panel is usually to paste
              the endpoint somewhere else. */}
          <div className="rpc-menu__sep" />
          {pool.map((p) => {
            const active = p === h.endpoint;
            return (
              <button
                key={p}
                onClick={() => copy(p)}
                className="rpc-menu__node"
                data-active={active ? "1" : "0"}
                title="copy endpoint"
              >
                <span className="rpc-menu__cursor" aria-hidden>{active ? "▶" : ""}</span>
                <span className="rpc-menu__url">
                  {copied === p ? "copied" : p}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="rpc-menu__row">
      <span className="rpc-menu__k">{k}</span>
      <span className="rpc-menu__v">{children}</span>
    </div>
  );
}
