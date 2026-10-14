# starknet.zquorum — zQuorum

A group reaches a decision without anyone learning who voted. Commitments in, nullifiers out, all hashing done in Cairo.

An app on Starknet, listed in the starknet **Apps** tab. Its UI is the `zquorum` mod's own app (`orbit/zquorum`); this submod is its card on Starknet.

- address: `/starknet/apps/zquorum/`
- fns: `facts`  ->  `GET /starknet/_api/apps/zquorum/<fn>`  or  `m starknet.zquorum/<fn>`
- `facts()` is what the Apps card shows.
