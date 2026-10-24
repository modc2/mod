# starknet.cairo — Cairo workbench

Write a Cairo contract, let an agent edit it, build and test it, then poke it on a local devnet. Every contract gets its own app.

An app on Starknet, listed in the starknet **Apps** tab. Its UI is the `cairo` mod's own app (`orbit/cairo`); this submod is its card on Starknet.

- address: `/starknet/apps/cairo/`
- fns: `facts`  ->  `GET /starknet/_api/apps/cairo/<fn>`  or  `m starknet.cairo/<fn>`
- `facts()` is what the Apps card shows.
