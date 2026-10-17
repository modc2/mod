"use client";

// /copytrade — THE COPY STUDIO, the console's front door: find traders,
// backtest copying them as you point at them, allocate to each, copy them
// all. See components/CopyStudio.tsx.

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyStudio from "../components/CopyStudio";

export default function CopyTradePage() {
  return (
    <Suspense>
      <div className="max-w-[1600px] mx-auto">
        <TopBar showSearch={false} />
        <div className="p-4">
          <CopyStudio />
        </div>
      </div>
    </Suspense>
  );
}
