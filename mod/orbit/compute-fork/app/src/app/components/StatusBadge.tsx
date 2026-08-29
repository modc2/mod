"use client";

const colors: Record<string, string> = {
  running: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  stopped: "bg-zinc-500/20 text-zinc-400 border-zinc-500/30",
  suspended: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  released: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  destroyed: "bg-red-500/20 text-red-400 border-red-500/30",
};

export function StatusBadge({ status }: { status: string }) {
  const cls = colors[status] || colors.stopped;
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${cls}`}>
      {status}
    </span>
  );
}
