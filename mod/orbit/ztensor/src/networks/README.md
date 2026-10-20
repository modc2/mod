# networks/ — one package per chain

ztensor signs once (core/signer.py, the Cairo prover) and verifies anywhere.
Each folder here is one chain: everything chain-specific lives inside it,
and mod.py never branches on a chain name.

```
networks/
  base.py          the Network contract every adapter implements
  __init__.py      registry: any package exporting ADAPTER is a chain
  starknet/        Cairo verifier        (devnet | sepolia | mainnet)
  near/            Rust/wasm verifier    (near | near-testnet)
  eth/             Solidity verifier     (eth | eth-sepolia)
  solana/          sBPF verifier, units  (solana | solana-devnet)
  _template/       copy this to add a chain (skipped by the registry)
```

Inside a chain package:

```
<chain>/
  __init__.py      from .adapter import ADAPTER
  adapter.py       class <Chain>(Network) — the only file mod.py talks to
  client.py        optional wire layer (RPC, tx encoding, local node)
  contract/        the on-chain verifier project (Scarb, Cargo, Foundry, ...)
  tools/           generators for that contract (constants, fixtures)
  tests/test_<chain>.py   that chain's own tests (see below)
```

## Adding a chain

1. `cp -r src/networks/_template src/networks/<chain>` and rename
   `tests/test_chain.py` to `tests/test_<chain>.py`.
2. Put the verifier in `contract/`. It must accept the prover's output
   unchanged: LSAG over the STARK curve, Poseidon challenges, key image
   `I = x * Hp(scope)`. Pin it to `near/contract/tests/fixture.json`
   (keys, Hp, a Cairo-made signature) in its native test suite.
3. Fill in `adapter.py`: set `chain`, `nets` (first = the local chain),
   `max_ring`, `cost_fields`, `ui`, `sources`, and implement every method
   in `base.REQUIRED`. Return shapes are documented at the top of base.py.
   If a verifier cannot afford a square root, pass `signer.hash_point(scope)`
   as a hint (eth, near do); if it cannot afford a whole verification per
   transaction, split it with hints from `core/stark.py` (solana does).
4. Override the simulate() hooks only if the chain's clock is special:
   `open_epoch` / `close_epoch` (solana waits real slots), `payee`,
   `contract_id`.
5. Run `python -m pytest`: `src/tests/test_networks.py` checks the new
   adapter against the contract (every method, signatures, metadata,
   sources exist, has its own tests). Then `ZTENSOR_NATIVE=1` for its suite and
   `ZTENSOR_LIVE=1 python -m pytest src/networks/<chain>` for a full epoch.

That is all: the new chain shows up in `m ztensor/networks`, every write fn
(`network=<its net>`), `simulate chain=<chain>`, `test chain=<chain>`,
`gas_sweep chain=<chain>`, COMPARE and the console's chain switch.

## Test tiers

| tier | how | what |
| --- | --- | --- |
| offline | `python -m pytest` | encoders, layouts, fixtures, adapter contract |
| native | `ZTENSOR_NATIVE=1 python -m pytest -m native` | snforge / cargo / forge suites |
| live | `ZTENSOR_LIVE=1 python -m pytest -m live` | full epoch on each local chain, replay rejected |
