"use client";

import { ConsensusState } from "@/lib/types";
import { formatToken } from "@/lib/api";

interface Props {
  state: ConsensusState | null;
}

function inflationLabel(t: any): string {
  if (!t) return "Unknown";
  if (typeof t === "string") return t;
  const keys = Object.keys(t);
  return keys[0] || "Unknown";
}

export default function ConsensusPanel({ state }: Props) {
  if (!state) {
    return (
      <div className="text-center py-8 text-nt-muted text-xs">
        Select a subnet to view consensus state
      </div>
    );
  }

  const stats = [
    { label: "Consensus", value: state.consensus_type },
    { label: "Inflation", value: inflationLabel(state.inflation_type) },
    { label: "Epoch", value: state.current_epoch.toString() },
    { label: "Block", value: state.current_block.toString() },
    { label: "Epoch Length", value: `${state.epoch_length} blocks` },
    { label: "Total Supply", value: formatToken(state.total_supply) },
    { label: "Emission Rate", value: formatToken(state.emission_rate) },
    { label: "Total Blocktime", value: formatToken(state.total_blocktime) },
  ];

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-nt-text">Consensus State</h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {stats.map((s) => (
          <div
            key={s.label}
            className="p-3 rounded border border-nt-border bg-nt-panel"
          >
            <div className="text-[10px] text-nt-muted mb-1">{s.label}</div>
            <div className="text-sm font-medium truncate">{s.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
