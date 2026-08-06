// Next's server-start hook — the one place the app gets to run something that
// isn't a request.
//
// Used for the hub's background backtest worker: every strat the console has
// published is replayed every 2 hours so the STRAT HUB has real numbers
// waiting instead of computing a wall of backtests in the browser on every
// visit. See app/lib/server/hubWorker.ts.

export async function register() {
  // The `if` (rather than an early return) is load-bearing: it's the form Next
  // recognizes to keep a node-only import out of the edge bundle. With an
  // early return, webpack still tries to resolve fs/os/path for edge and the
  // build fails.
  if (process.env.NEXT_RUNTIME === "nodejs" && process.env.POLYMARKET_HUB_WORKER !== "0") {
    const { startHubWorker } = await import("./app/lib/server/hubWorker");
    startHubWorker();
  }
}
