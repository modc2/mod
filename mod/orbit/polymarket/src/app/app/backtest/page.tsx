"use client";

// /backtest — a FORWARDER. The backtest workspace is the side panel's
// BACKTEST tab now (components/UserSidebar.tsx); the console's one page is
// the trader board. This route survives for bookmarks and old links: it
// opens the panel on BACKTEST and lands you on /traders.
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { requestSidebarTab } from "../components/UserSidebar";

export default function BacktestForwarder() {
  const router = useRouter();
  useEffect(() => {
    requestSidebarTab("BACKTEST");
    router.replace("/traders");
  }, [router]);
  return null;
}
