# starknet.zkwasm — zkWASM

Run WebAssembly programs and prove what they did. Early scaffold: the prover is not wired in yet.

An app on Starknet, listed in the starknet **Apps** tab. Its UI is the `zkwasm` mod's own app (`orbit/zkwasm`); this submod is its card on Starknet.

- address: `/starknet/apps/zkwasm/`
- fns: `facts`  ->  `GET /starknet/_api/apps/zkwasm/<fn>`  or  `m starknet.zkwasm/<fn>`
- `facts()` is what the Apps card shows.
