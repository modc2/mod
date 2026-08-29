"use client";

// /copy — the COPY DESK, and the console's front door.
//
// The desk is deliberately thin: everything it knows comes from `/copy/book`
// on the Rust API, which is also what the `pm_copy_*` MCP tools read. See
// components/CopyDesk.tsx.

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyDesk from "../components/CopyDesk";

function CopyPageInner() {
  return (
    <div className="max-w-[1600px] mx-auto">
      <TopBar showSearch searchPlaceholder="PASTE A TRADER ADDRESS…" />
      <div className="p-4">
        <CopyDesk />
      </div>
    </div>
  );
}

export default function CopyPage() {
  return (
    <Suspense>
      <CopyPageInner />
    </Suspense>
  );
}
