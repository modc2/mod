import { redirect } from "next/navigation";

// The console is three tabs — TRADERS, BACKTEST, LIVE — and they read as one
// sentence: pick who to copy, test them on history, run them for real. The
// front door is therefore the first of them.
//
// Everything else that was ever a destination is a view of one of these three
// or a drawer: STRATS folded into the workspace's SETTINGS panel (capital and
// the bench were always edited there anyway), money is the side panel, MARKETS
// is reachable from a trade row, and /docs is a link, not a tab.
//
// basePath ("/polymarket") is prepended automatically — pass the path WITHOUT it.
export default function Home() {
  redirect("/traders");
}
