"use client";

import { PoolInfo } from "@/lib/types";
import { formatToken, formatNear } from "@/lib/api";

interface Props {
  subnetCount: number;
  registrationCost: string;
}

export default function RegistryPanel({ subnetCount, registrationCost }: Props) {
  const slotsUsed = subnetCount;
  const slotsTotal = 420;
  const pct = ((slotsUsed / slotsTotal) * 100).toFixed(1);

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-nt-text">Registry</h2>

      <div className="p-4 rounded border border-nt-border bg-nt-panel space-y-4">
        {/* Capacity bar */}
        <div>
          <div className="flex justify-between text-[10px] text-nt-muted mb-1">
            <span>Capacity</span>
            <span>{slotsUsed}/{slotsTotal} ({pct}%)</span>
          </div>
          <div className="w-full h-2 bg-nt-bg rounded overflow-hidden">
            <div
              className="h-full bg-nt-accent/60 rounded transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-3">
          <div className="p-2 rounded bg-nt-bg border border-nt-border">
            <div className="text-[10px] text-nt-muted">Registration Cost</div>
            <div className="text-sm font-medium">{formatNear(registrationCost)} NEAR</div>
          </div>
          <div className="p-2 rounded bg-nt-bg border border-nt-border">
            <div className="text-[10px] text-nt-muted">Active Subnets</div>
            <div className="text-sm font-medium">{slotsUsed}</div>
          </div>
        </div>

        <div className="text-[10px] text-nt-muted">
          When at capacity, the weakest non-immune subnet is evicted to make room for new registrations.
          New subnets receive immunity for ~24 hours (86,400 blocks).
        </div>
      </div>
    </div>
  );
}
