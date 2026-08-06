"use client";

import type { Model } from "../lib/types";

// Where a model can run, said in one glyph each. The three runtimes keep the
// same colour everywhere on the board: cyan is the tab, magenta is this box,
// amber is Liquid's cloud, and a runtime a model can't reach is simply absent
// rather than greyed — an empty slot is faster to read than a dim one.
export const RUNTIME_TONE: Record<string, string> = {
  browser: "text-cyan-400 border-cyan-400",
  server: "text-purple-400 border-purple-400",
  edge: "text-amber-400 border-amber-400",
  cloud: "text-amber-400 border-amber-400",
};

const RUNTIME_LABEL: Record<string, string> = {
  browser: "BROWSER",
  server: "SERVER",
  edge: "EDGE",
  cloud: "CLOUD",
};

// Three-letter caps in tables. Spelled out they wrapped onto a second line
// and doubled every row's height — a 50-row catalog paid for that in air.
const RUNTIME_ABBR: Record<string, string> = {
  browser: "BRW",
  server: "SRV",
  edge: "EDG",
  cloud: "CLD",
};

export function RuntimeBadges({ runtimes, short = false }: { runtimes: string[]; short?: boolean }) {
  return (
    <span className="inline-flex gap-1 whitespace-nowrap">
      {runtimes.map((r) => (
        <span
          key={r}
          className={`pixel-badge ${RUNTIME_TONE[r] ?? "text-pixel-gray-light border-pixel-border"}`}
          title={
            r === "browser" ? "ONNX build — runs in the tab on WebGPU"
              : r === "server" ? "safetensors — runs on this box"
              : "GGUF / MLX — runs on a laptop or phone via llama.cpp, LEAP or MLX"
          }
        >
          {short ? RUNTIME_ABBR[r] : RUNTIME_LABEL[r]}
        </span>
      ))}
    </span>
  );
}

const KIND_TONE: Record<string, string> = {
  text: "text-pixel-white border-pixel-border",
  vision: "text-pink-400 border-pink-400",
  audio: "text-amber-400 border-amber-400",
  embed: "text-blue-400 border-blue-400",
};

export function KindBadge({ kind }: { kind: string }) {
  return <span className={`pixel-badge ${KIND_TONE[kind] ?? KIND_TONE.text}`}>{kind}</span>;
}

export function RoleBadge({ role }: { role: string | null }) {
  if (!role) return null;
  return <span className="pixel-badge text-green-400 border-green-400">{role}</span>;
}

export function params(m: Model): string {
  if (!m.params_b) return "—";
  const total = m.params_b < 1 ? `${Math.round(m.params_b * 1000)}M` : `${m.params_b}B`;
  return m.active_b ? `${total}·A${m.active_b}B` : total;
}

export function fmtCount(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}
