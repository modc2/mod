"use client";
import { ScrapeProgress as ProgressType } from "../lib/types";

interface Props {
  progress: ProgressType | null;
}

export default function ScrapeProgress({ progress }: Props) {
  if (!progress) return null;

  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;

  const phaseLabels: Record<string, string> = {
    collect: "Collecting swaps",
    aggregate: "Aggregating traders",
    enrich: "Enriching profiles",
  };

  const label = phaseLabels[progress.phase] || progress.phase;

  return (
    <div className="card p-3 flex items-center gap-3">
      <div className="animate-pulse-glow w-2 h-2 rounded-full bg-uni-pink" />
      <div className="flex-1">
        <div className="flex justify-between text-[10px] mb-1">
          <span className="text-uni-muted">
            {label} ({progress.chain})
          </span>
          <span className="text-uni-muted">
            {progress.done}/{progress.total}
            {progress.kept != null && ` (${progress.kept} kept)`}
          </span>
        </div>
        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <span className="text-xs font-bold text-uni-pink">{pct}%</span>
    </div>
  );
}
