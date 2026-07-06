"use client";

import { useEffect, useState } from "react";

/// `?embedded=1` query flag tells the app it's running inside a split-screen
/// iframe pane. Used to suppress the market ticker, split shell, split
/// button, etc. so embedded panes stay lightweight and don't nest splits.
/// Reads `window.location.search` directly to avoid `useSearchParams`
/// (which forces a Suspense boundary in Next.js 14 client components).
export function useEmbedded(): boolean {
  const [embedded, setEmbedded] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    setEmbedded(new URLSearchParams(window.location.search).get("embedded") === "1");
  }, []);
  return embedded;
}
