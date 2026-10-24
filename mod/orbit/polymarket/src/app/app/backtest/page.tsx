"use client";

// /backtest — a FORWARDER for bookmarks and old links. The BACKTEST workspace is a
// tab of the STRATS page (/strats?tab=backtest) since the side panel was removed.
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { stratsHref } from "../lib/stratsNav";

export default function BacktestForwarder() {
  const router = useRouter();
  useEffect(() => {
    router.replace(stratsHref("backtest"));
  }, [router]);
  return null;
}
