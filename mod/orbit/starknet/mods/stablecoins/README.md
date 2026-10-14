# starknet.stablecoins — Stablecoins

Every dollar on Starknet: native USDC, bridged USDC.e and USDT, DAI and CASH, with live supply and the Pragma oracle peg.

An app on Starknet, listed in the starknet **Apps** tab. Its UI is the `/stablecoins` page of the starknet app (`app/app/stablecoins/page.tsx`).

- address: `/starknet/apps/stablecoins/`
- fns: `facts`, `stablecoins`, `registry`  ->  `GET /starknet/_api/apps/stablecoins/<fn>`  or  `m starknet.stablecoins/<fn>`
- `facts()` is what the Apps card shows.
