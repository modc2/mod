"use client";
import { TokenStats } from "../lib/types";
import { fmtUsd } from "../lib/format";

interface Props {
  tokens: TokenStats[];
}

export default function TokenFlow({ tokens }: Props) {
  if (!tokens || tokens.length === 0) return null;

  const maxVol = Math.max(...tokens.map((t) => t.volume_usd));

  return (
    <div className="card p-4">
      <h3 className="text-xs font-bold text-uni-muted mb-3 uppercase">Token Flow</h3>
      <div className="space-y-2">
        {tokens.slice(0, 8).map((token) => {
          const pct = (token.volume_usd / maxVol) * 100;
          const isInflow = token.net_flow_usd > 0;

          return (
            <div key={token.symbol} className="flex items-center gap-2">
              <span className="text-[11px] w-16 font-bold truncate">{token.symbol}</span>
              <div className="flex-1 h-4 bg-uni-dark rounded overflow-hidden relative">
                <div
                  className="h-full rounded"
                  style={{
                    width: `${pct}%`,
                    background: isInflow
                      ? "linear-gradient(90deg, #4ade80aa, #4ade8044)"
                      : "linear-gradient(90deg, #f87171aa, #f8717144)",
                  }}
                />
              </div>
              <span className="text-[10px] text-uni-muted w-16 text-right">
                {fmtUsd(token.volume_usd)}
              </span>
              <span
                className={`text-[10px] w-16 text-right ${
                  isInflow ? "text-uni-green" : "text-uni-red"
                }`}
              >
                {isInflow ? "+" : ""}{fmtUsd(token.net_flow_usd)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
