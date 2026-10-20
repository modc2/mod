"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { openWalletOp, type WalletOp } from "../components/AccountPanel";

// The wallet is a sidebar now (desk ▸ Wallet) so it sits beside whatever
// you're doing. Old links (/wallet, /wallet?op=withdraw) open it and land
// on the board underneath.
export default function WalletPage() {
  const router = useRouter();
  useEffect(() => {
    const op = new URLSearchParams(window.location.search).get("op") as WalletOp | null;
    // Child effects run before the DockProvider's restore-from-storage
    // effect, which would shut the desk again — open on the next tick.
    const t = setTimeout(() => {
      openWalletOp(op && ["deposit", "withdraw", "move", "send"].includes(op) ? op : "deposit");
      router.replace("/");
    }, 0);
    return () => clearTimeout(t);
  }, [router]);
  return null;
}
