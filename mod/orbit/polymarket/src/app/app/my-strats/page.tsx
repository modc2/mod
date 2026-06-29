"use client";

// Standalone MY STRATS page. The same UserStratsPanel is also embedded in the
// Strategy Hub on /strats, but burying it behind a collapsed block made it hard
// to find — this gives "your uploads / publish / community / fork" its own
// sidebar destination. Ownership is keyed on the connected wallet (`eoa`).

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import UserStratsPanel from "../components/UserStratsPanel";
import { useAuth } from "../context/AuthContext";
import { useUrlSync } from "../context/FiltersContext";

function MyStratsInner() {
  useUrlSync();
  const { auth } = useAuth();

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="SEARCH STRATS..." />
      <div className="p-4">
        <UserStratsPanel eoa={auth.address ?? undefined} />
      </div>
    </div>
  );
}

export default function MyStratsPage() {
  return (
    <Suspense>
      <MyStratsInner />
    </Suspense>
  );
}
