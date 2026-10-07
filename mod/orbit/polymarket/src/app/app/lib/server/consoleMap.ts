// The console, as a map the agents may not contradict. One copy, imported by
// every agent route (help-agent, agent/chat) — when the console changes
// shape, change this briefing with it.

export const CONSOLE_MAP = `
THE CONSOLE'S MAP — this is the ground truth; never invent a feature not listed here.

THE HEADER (top row): the green square mark at the far left opens and closes
THIS agent (a column on the left). Next to it, two tabs: TRADERS and STRATS.
On the right: the side-panel icon (a small square) and the WALLET CHIP.

PAGES:
- "/traders" — THE BOARD and the front door ("/" goes here). Trader cards
  ranked by the BEST score over 30 days by default; "> CONTROLS" on the board
  header changes the window, the rank and the filters. Each card has
  "+ ADD TO STRAT". Click a trader's address to open their profile.
- "/traders/<address>" — one trader's profile: PnL curve first, their trades,
  filters and a backtest simulator in the right rail.
- "/strats" — the STRATS tab: build and manage strategies. STRATS | TRADES
  pills at the top (TRADES = the P&L of every closed position), "+ NEW STRAT"
  to create one. Each strat card has its own CHAT tab (an agent that proposes
  settings changes you APPLY), FORK, rename, delete, and a PRIVATE/PUBLIC
  toggle; COMMUNITY lists published strats you can copy into your own.
- "/copy" — the full copy desk (bulk sizing, per-trader modes); "/copy/basket"
  sizes a whole basket; "/trades" is a live tape of fills (ALL, or MINE =
  what your own wallet actually traded); "/docs" is the documentation.

THE SIDE PANEL (right-hand column; the side-panel icon in the header opens
and closes it). At its top: one account row — dot, wallet, balance, "..." menu
(copy address, rename, sign out, SWITCH ACCOUNT), and "sign in" when signed
out. Below that, four tabs:
- COPY — who you copy and with how much. A total ("$400 on 4 traders"), ONE
  PAPER | REAL switch for the whole book, "START ALL" / "STOP ALL", then one
  row per trader: address, a $ amount box, a play/stop button, and x to drop
  them. A row's dot is green when it runs REAL, amber when PAPER. Under the
  rows, "+ ADD / SIZE" opens the add line: paste one or more 0x addresses,
  type $ each, press "+ COPY"; it also holds "SPLIT $ ... EVENLY" (with 2+
  traders), "BASKET ->" and "FIND TRADERS ->". The BACKTEST fold says what
  your $ would have done; RESULTS compares their trades with yours. The
  STRATS fold at the very bottom (closed by default) holds strat allocation
  and the bench. "wallet has only $X - TOP UP ->" means the book is bigger
  than the trading balance.
- MONEY — where the money sits and how to move it: a strip OTHER CHAINS >
  WALLET > TRADING > IN PLAY, a move box (wallet to trading and back, one
  amount, one button), and one "BRIDGE -> POLYGON" row per other-chain balance
  the wallet holds.
- BACKTEST — replay the bench against history on simulated money.
- LIVE — run the bench against the real book with real money.

THE SEARCH BAR (under the header) is three things in one:
- typing live-filters the board underneath;
- pasting a 0x wallet address + ENTER opens that trader's profile;
- describing a trader in words + ENTER runs the TRADER SCOUT, an agent that
  queries the live leaderboard and answers with clickable addresses.

THE WALLET CHIP (top-right): "no wallet" when nothing is connected. The dot is
bright green when trading is enabled, amber when connected but not yet
trading-enabled, gray when disconnected. Clicking it opens the side panel's
account row, where wallets are added and switched. xN on the chip = N known
accounts.

HOW THE MACHINE BEHAVES (facts, do not contradict):
- A strat copies its watched traders' trades, sized proportionally to your
  capital against each trader's own book.
- Entry gates are BUY-only; exits are never gated.
- Polymarket's order floor is max($1, 5 shares × price) — tiny capital means
  skipped orders, not smaller ones.
- The live engine polls no faster than every 30 seconds.
- Backtests can run HOLDOUT (stats frozen as-of a date, then replayed forward)
  to test a strat honestly.
- Sign-in is wallet-based (MetaMask personal_sign); agent features (this one,
  the scout, strat chat) run on the owner's inference and need sign-in.
`.trim();
