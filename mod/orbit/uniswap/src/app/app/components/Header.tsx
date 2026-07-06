"use client";
import { Chain, CHAINS, TIME_WINDOWS } from "../lib/types";

interface HeaderProps {
  chain: Chain;
  days: number;
  onChainChange: (c: Chain) => void;
  onDaysChange: (d: number) => void;
}

export default function Header({ chain, days, onChainChange, onDaysChange }: HeaderProps) {
  return (
    <header className="border-b border-uni-border px-6 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold">
            <span className="text-uni-pink">UNI</span>
            <span className="text-uni-muted">SCAN</span>
          </h1>
          <span className="text-[10px] text-uni-muted">v3 trader discovery</span>
        </div>

        <div className="flex items-center gap-4">
          {/* Chain selector */}
          <div className="flex gap-1">
            {CHAINS.map((c) => (
              <button
                key={c.id}
                onClick={() => onChainChange(c.id)}
                className={`btn text-[10px] px-3 py-1 ${chain === c.id ? "btn-active" : ""}`}
                style={chain === c.id ? { background: c.color, borderColor: c.color } : {}}
              >
                {c.label}
              </button>
            ))}
          </div>

          {/* Time window */}
          <div className="flex gap-1">
            {TIME_WINDOWS.map((w) => (
              <button
                key={w.days}
                onClick={() => onDaysChange(w.days)}
                className={`btn text-[10px] px-3 py-1 ${days === w.days ? "btn-active" : ""}`}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </header>
  );
}
