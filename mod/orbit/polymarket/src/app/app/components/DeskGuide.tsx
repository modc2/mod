"use client";

// HOW COPYING WORKS — the four steps, in the order they happen.
//
// Rendered twice: as the desk's empty state (big, where the rows will be) and
// in the side column while you are on the desk (small, so the column isn't a
// blank strip). One list, so the two can't describe different products.

export const STEPS = [
  {
    title: "FIND A TRADER",
    body: "Paste a 0x… address if you already have one. Otherwise pick a market (BITCOIN, SPORTS…) and press FIND TRADERS to rank who does best there.",
  },
  {
    title: "PUT DOLLARS BEHIND THEM",
    body: "The $ next to a name is your whole position size for that trader. Each of their trades is copied at that scale, on the same markets they trade.",
  },
  {
    title: "START ON PAPER",
    body: "Press START with the switch on PAPER. The engine follows their trades live but sends no orders — nothing can move. Watch the backtest and RESULTS.",
  },
  {
    title: "GO REAL WHEN YOU'RE HAPPY",
    body: "Flip PAPER → REAL and confirm. Real orders on Polymarket, paid from your trading wallet. STOP at any time; open positions are left alone.",
  },
] as const;

export default function DeskGuide({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <div className="p-3 space-y-3">
        <div className="font-mono text-[9.5px] tracking-[0.14em] text-pixel-gray">HOW COPYING WORKS</div>
        {STEPS.map((s, i) => (
          <div key={s.title} className="flex gap-2">
            <span className="shrink-0 w-[18px] h-[18px] grid place-items-center rounded-full border border-pixel-green/60 text-pixel-green font-mono text-[10px]">
              {i + 1}
            </span>
            <div className="min-w-0">
              <div className="font-mono text-[10.5px] tracking-[0.08em] text-pixel-white">{s.title}</div>
              <div className="font-mono text-[10px] leading-snug text-pixel-gray">{s.body}</div>
            </div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="pixel-panel p-5 space-y-4">
      <div className="space-y-1">
        <div className="font-mono text-[13px] tracking-[0.14em] text-pixel-white">YOU AREN&apos;T COPYING ANYONE YET</div>
        <div className="font-mono text-[11px] text-pixel-gray">Four steps. Nothing moves money until step 4.</div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        {STEPS.map((s, i) => (
          <div key={s.title} className="rounded-[var(--radius-sm)] border border-pixel-gray/20 p-3 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="w-[22px] h-[22px] grid place-items-center rounded-full border border-pixel-green/60 text-pixel-green font-mono text-[11px]">
                {i + 1}
              </span>
              <span className="font-mono text-[11px] tracking-[0.1em] text-pixel-white">{s.title}</span>
            </div>
            <div className="font-mono text-[10.5px] leading-snug text-pixel-gray">{s.body}</div>
          </div>
        ))}
      </div>
      <div className="font-mono text-[10px] text-pixel-gray">
        Step 1 is the box above — start there.
      </div>
    </div>
  );
}
