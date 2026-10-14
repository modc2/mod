# starknet.zuma — Zuma

A Bittensor-style subnet where miners and validators stay anonymous: every action is a ring signature checked by a Starknet contract.

An app on Starknet, listed in the starknet **Apps** tab. Its UI is the `zuma` mod's own app (`orbit/zuma`); this submod is its card on Starknet.

- address: `/starknet/apps/zuma/`
- fns: `facts`  ->  `GET /starknet/_api/apps/zuma/<fn>`  or  `m starknet.zuma/<fn>`
- `facts()` is what the Apps card shows.
