# Cross-chain deposits & withdrawals

Fund a Hyperliquid account from **twelve EVM chains** in one signed
transaction — and withdraw back out to any of them. This documents the
feature end-to-end: what the user sees, what the API does, and where the
sharp edges are. Verified live E2E (all 12 chains) on 2026-09-16.

## The one-sentence version

The wallet page scans every supported chain for ETH / USDC / USDT, the user
picks a balance and an amount, and a single MetaMask signature lands the
money as USDC in their Hyperliquid perps account — LI.FI routes to
Hyperliquid Core directly (LI.FI chain id **1337**), so there is no Arbitrum
layover and no second prompt.

## Supported chains

| Chain | Chain id | Path |
|---|---|---|
| Arbitrum | 42161 | **direct** — plain ERC-20 USDC transfer to Hyperliquid's bridge2, no routing fee |
| Ethereum | 1 | LI.FI |
| Base | 8453 | LI.FI |
| OP Mainnet | 10 | LI.FI |
| Polygon | 137 | LI.FI |
| BNB Chain | 56 | LI.FI |
| Avalanche | 43114 | LI.FI |
| Linea | 59144 | LI.FI |
| Scroll | 534352 | LI.FI |
| zkSync Era | 324 | LI.FI |
| Gnosis | 100 | LI.FI |
| Unichain | 130 | LI.FI |

Tokens: the chain's native coin plus USDC and USDT wherever they exist.
Routing tools observed: Polymer, Relay, Layerswap — fees ~$0.10–$1.70,
delivery from seconds to ~18 minutes. Hyperliquid's credit minimum is **$5**
(`minDepositUsd` in `/deposit/chains`).

## API surface (`src/api/src/deposit.rs`)

| Route | Auth | What it does |
|---|---|---|
| `GET /deposit/chains` | public | Chain + token catalog, `toChainId`, `minDepositUsd`, testnet flag |
| `GET /deposit/balances?eoa=0x…` | public | Scans all 12 chains in parallel; returns per-chain balances and a USD-ranked `sources` list |
| `POST /deposit/quote` | token | `{from_chain_id, token, amount, eoa}` → signable `transactionRequest`, `toUsdc`/`toUsdcMin`, fee/gas USD, ETA, `landsOnHyperliquid`. Also quotes **withdrawals** when `to_chain_id` names an EVM chain |
| `GET /deposit/status?tx_hash=&from_chain_id=` | public | LI.FI status poll: `PENDING → DONE` (+ `receivedUsdc`) |

Notes:

- **Balances** are one Multicall3 `aggregate3` `eth_call` per chain. zkSync's
  Multicall3 lives at a *different address* than every other chain (different
  CREATE2 deployment).
- **Pricing** comes off Hyperliquid's own mids (stables pinned at $1).
  `all_mids` has a 5 s fresh cache with a 10-minute stale-on-error fallback —
  without it a single transient 429 nulled every non-stable price in a scan.
- `POST /deposit/quote` is token-gated because it is the write-shaped half of
  the flow; the `eoa` in the body must match the bearer token's address.

## UI flow (`src/app/app/components/DepositPanel.tsx`)

Rendered on `/wallet` (mainnet only — testnet keeps the plain Arbitrum USDC
form). The panel is **collapsible**: the header is always visible, the fold
state persists in `localStorage` (`hl.wallet.depositOpen`), the 12-chain
balance scan only runs while the panel is open, and a transaction in flight
pins the panel open. A "how it works" fold at the bottom is the in-app twin
of this document.

1. **Scan** — `depositBalances(eoa)`, read-only, nothing signed. Dust under
   $1 is hidden (it can't clear the $5 minimum); unpriced tokens are still
   shown, because hiding real money over a missing mid is worse.
2. **Quote as you type** — debounced `depositQuote` so the arriving amount,
   fee and ETA are on screen *before* MetaMask opens. The same quote object
   is reused for the send — nobody is quoted twice across the click.
3. **One signature** — `crossChainDepositFlow` (or `bridgeDepositFlow` for
   direct Arbitrum USDC) switches chain if needed, signs once, then polls
   `depositStatus` until Hyperliquid credits.

## Withdrawals

Withdrawing is the mirror image, from the Withdraw panel on `/wallet`:

- **To Arbitrum** (Hyperliquid's native payout chain): a master-signed
  `withdraw3` — fee ~$1, arrives in minutes.
- **To any other chain**: pays out on Arbitrum first, then auto-bridges via
  the same LI.FI rails (`/deposit/quote` with `to_chain_id`). Needs ≥ $6
  (≈$1 HL fee + $5 bridge minimum) and a little ETH on Arbitrum for gas.
  Between the two hops the funds only ever sit in the user's own wallet.

## Security model

- Deposit and withdrawal transactions are signed **only by the user's wallet**
  (MetaMask). The server never holds keys for these flows; it prices routes
  and watches for credits.
- The module's backend *agent* key (used for orders/vault transfers) can
  never withdraw out of a Hyperliquid account — funds-exiting actions are
  master-signed by design (see `skill.md`, "wallet rails").
- Watch-only connections can browse balances but cannot sign.

## Testing

- `tests/` pytest + `src/api` cargo tests cover the rails; the throwaway-EOA
  token recipe (top of `tests/test_strats.py`) mints a valid bearer for
  QA-ing the gated quote route without a real wallet.
- Live smoke: `GET /deposit/chains` → 12 chains; `POST /deposit/quote` from
  each chain returns `transactionRequest` + `landsOnHyperliquid: true`.
