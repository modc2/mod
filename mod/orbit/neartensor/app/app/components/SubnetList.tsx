"use client";

import { SubnetInfo } from "@/lib/types";

interface Props {
  subnets: SubnetInfo[];
  selectedSubnet: number;
  onSelect: (id: number) => void;
}

export default function SubnetList({ subnets, selectedSubnet, onSelect }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-nt-text">
          Subnets <span className="text-nt-muted">({subnets.length}/420)</span>
        </h2>
      </div>

      {subnets.length === 0 ? (
        <div className="text-center py-12 text-nt-muted text-xs">
          No subnets registered yet
        </div>
      ) : (
        <div className="grid gap-2">
          {subnets.map((s) => (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={`w-full text-left p-3 rounded border transition-colors ${
                selectedSubnet === s.id
                  ? "border-nt-accent/50 bg-nt-accent/5"
                  : "border-nt-border bg-nt-panel hover:border-nt-border/80"
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium">
                  #{s.id} {s.name}
                </span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded ${
                    s.is_immune
                      ? "bg-nt-yellow/10 text-nt-yellow"
                      : s.active
                      ? "bg-nt-green/10 text-nt-green"
                      : "bg-nt-red/10 text-nt-red"
                  }`}
                >
                  {s.is_immune ? "IMMUNE" : s.active ? "ACTIVE" : "INACTIVE"}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[10px] text-nt-muted">
                <span>{s.consensus_type}</span>
                <span>{s.account_id}</span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
