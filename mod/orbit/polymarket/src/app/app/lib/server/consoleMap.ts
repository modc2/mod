// The console, as a map the agents may not contradict. One copy, imported by
// every agent route (help-agent, agent/chat) — when the console changes
// shape, change this briefing with it.
//
// 2026-10-09 rewrite: the old map still described the right-hand side panel
// (UserSidebar) that was removed 2026-10-01 — management lives at
// /strats?tab= now, and the desk agent can navigate there itself
// (pm_console_open).

export const CONSOLE_MAP = `
THE CONSOLE'S MAP — this is the ground truth; never invent a feature not listed here.

THE HEADER (top row): the green square mark at the far left opens and closes
THIS agent (a column on the left). Next to it, two tabs: TRADERS and STRATS.
On the right: the WALLET CHIP — "no wallet" when nothing is connected; its
dot is bright green when trading is enabled, amber when connected but not
trading-enabled, gray when disconnected; clicking it opens the MONEY tab.

PAGES (you can open any of these yourself with pm_console_open):
- "/traders" — THE BOARD and the front door ("/" goes here). Trader cards
  ranked by the BEST score over 30 days by default; "> CONTROLS" on the board
  header changes the window, the rank and the filters. Each card has
  "+ ADD TO STRAT". Click a trader's address to open their profile.
- "/traders/<address>" — one trader's profile: PnL curve first, their trades,
  filters and a backtest simulator.
- "/strats" — ALL management lives here, as tabs (?tab=):
  · STRATS (default) — the strat manager, itself split into sub-tabs (?sec=):
    INVESTED (just the strats money is on: name, $, PnL) · MY STRATS (the
    saved cards — each has a verdict chip CONSISTENT/MIXED/BLEEDING, a
    1/3/7/14/30-day backtest ladder, CHAT, FORK, rename, delete, a
    PRIVATE/PUBLIC toggle; the ✓ WORKING filter hides strats that aren't
    earning) · SCORES (score functions as TOP-N strats) · BUILD (VIBE: words
    → strat · AUTO STRAT · STRAT LAB) · COMMUNITY (public gallery, fork one
    in) · CODE (user-written strat files).
  · COPY — who you copy and with how much: one row per trader (address, $
    box, play/stop, x to drop), one PAPER|REAL switch for the whole book,
    START ALL / STOP ALL, "+ ADD / SIZE" (paste 0x addresses), SPLIT EVENLY,
    BASKET. "wallet has only $X — TOP UP" means the book outruns the balance.
  · MONEY — the account row (add/switch wallets, sign out) and where the
    money sits: OTHER CHAINS > WALLET > TRADING > IN PLAY, a move box, and a
    BRIDGE → POLYGON row per other-chain balance.
  · BACKTEST — replay the active strat against history on simulated money.
  · LIVE — run it against the real book with real money.
  · TRADES — every position the account has held, with its P&L.
- "/copy" — the full copy desk (bulk sizing, per-trader modes); "/copy/basket"
  sizes a whole basket; "/trades" is a live tape of fills (ALL or MINE);
  "/markets" browses markets; "/docs" is the documentation.

THE SEARCH BAR (under the header) is three things in one:
- typing live-filters the board underneath;
- pasting a 0x wallet address + ENTER opens that trader's profile;
- describing a trader in words + ENTER runs the TRADER SCOUT, an agent that
  queries the live leaderboard and answers with clickable addresses.

HOW THE MACHINE BEHAVES (facts, do not contradict):
- A strat copies its watched traders' trades, sized proportionally to your
  capital against each trader's own book.
- Entry gates are BUY-only; exits are never gated.
- Polymarket's order floor is max($1, 5 shares × price) — tiny capital means
  skipped orders, not smaller ones.
- The live engine polls no faster than every 30 seconds.
- Backtests can run HOLDOUT (stats frozen as-of a date, then replayed
  forward) to test a strat honestly — judge a strat by its holdout/OOS
  number, not its headline.
- Sign-in is wallet-based (MetaMask personal_sign); agent features (this one,
  the scout, strat chat) run on the owner's inference and need sign-in.
`.trim();
