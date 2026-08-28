"use client";

import { useEffect, useRef, useState } from "react";
import { fetchRuntimes } from "../lib/api";
import type { Runtimes } from "../lib/types";

// The three-lamp status cap: BROWSER / SERVER / CLOUD, lit when that runtime
// could take a job right now. Lime means go and red means it can't — the two
// colours this system reserves for state, never decoration. Pressing it drops
// the reasons, because "cloud is red" is useless without "no key".
export default function RuntimeChip() {
  const [rt, setRt] = useState<Runtimes | null>(null);
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const tick = () => fetchRuntimes().then((r) => { if (alive) setRt(r); }).catch(() => {});
    tick();
    const t = setInterval(tick, 20000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const lamps: { id: string; ok: boolean; why: string }[] = [
    { id: "BRW", ok: !!rt?.browser.ok, why: rt?.browser.engine ?? "—" },
    {
      id: "SRV",
      ok: !!rt?.server.ok,
      why: rt?.server.ok
        ? `${rt.server.device}${rt.server.gpu ? ` · ${rt.server.gpu}` : ""} · torch ${rt.server.torch}`
        : rt?.server.error ?? "—",
    },
    {
      id: "CLD",
      ok: !!rt?.cloud.ok,
      why: rt?.cloud.ok ? `${rt.cloud.count} models` : rt?.cloud.error ?? "—",
    },
  ];

  return (
    <div ref={wrap} className="relative shrink-0">
      <button
        className="pixel-btn topbar-ctl gap-2 px-3 font-mono"
        onClick={() => setOpen((o) => !o)}
        title="Where a model can run right now"
        aria-expanded={open}
      >
        {lamps.map((l) => (
          <span key={l.id} className={l.ok ? "text-green-400" : "text-red-400"}>
            {l.ok ? "●" : "○"}
            <span className="hidden sm:inline ml-1">{l.id}</span>
          </span>
        ))}
      </button>

      {open && (
        <div className="skin-menu" role="menu">
          {lamps.map((l) => (
            <div key={l.id} className="skin-menu__item cursor-default">
              <span className={`skin-menu__cursor ${l.ok ? "text-green-400" : "text-red-400"}`}>
                {l.ok ? "●" : "○"}
              </span>
              <span className="min-w-[44px]">{l.id}</span>
              <span className="font-mono text-xs text-pixel-gray-light truncate max-w-[300px]">
                {l.why}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
