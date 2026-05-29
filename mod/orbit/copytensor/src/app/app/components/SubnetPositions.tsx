"use client";

import type { Allocation } from "../lib/types";
import { fmtTao, shortSs58 } from "../lib/api";

export default function SubnetPositions({
  allocations,
}: {
  allocations: Allocation[];
}) {
  if (!allocations.length) {
    return <p className="text-pixel-gray text-sm">No alpha positions found.</p>;
  }

  const sorted = [...allocations].sort((a, b) => b.value_tao - a.value_tao);

  return (
    <div className="space-y-4">
      <div className="flex gap-[2px] h-8 rounded-lg overflow-hidden border border-pixel-border">
        {sorted.map((a, i) => {
          const hue = (a.netuid * 47) % 360;
          return (
            <div
              key={`${a.netuid}-${a.hotkey}-${i}`}
              style={{
                width: `${Math.max(a.pct_of_total, 0.5)}%`,
                background: `hsla(${hue}, 70%, 55%, 0.55)`,
              }}
              className="relative group transition-all hover:brightness-150"
              title={`SN${a.netuid} ${a.subnet_name}: ${a.pct_of_total.toFixed(1)}% (${fmtTao(a.value_tao)})`}
            >
              {a.pct_of_total > 6 && (
                <span className="absolute inset-0 flex items-center justify-center text-[10px] font-mono text-pixel-white">
                  SN{a.netuid}
                </span>
              )}
            </div>
          );
        })}
      </div>

      <div className="pixel-panel overflow-hidden">
        <table className="pixel-table">
          <thead className="sticky">
            <tr>
              <th>Subnet</th>
              <th className="num">Alpha</th>
              <th className="num">Price (τ/α)</th>
              <th className="num">Value</th>
              <th className="num">% port</th>
              <th>Hotkey</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((a, i) => (
              <tr key={`${a.netuid}-${a.hotkey}-${i}`}>
                <td>
                  <span className="font-mono text-pixel-white">SN{a.netuid}</span>
                  <span className="text-pixel-gray text-xs ml-2">{a.subnet_name}</span>
                </td>
                <td className="num font-mono">{a.alpha_amount.toFixed(4)}</td>
                <td className="num font-mono">{a.alpha_price_tao.toFixed(6)}</td>
                <td className="num font-mono text-pixel-white">{fmtTao(a.value_tao)}</td>
                <td className="num font-mono text-pixel-gray-light">
                  {a.pct_of_total.toFixed(1)}%
                </td>
                <td className="font-mono text-xs text-pixel-gray">
                  {shortSs58(a.hotkey)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
