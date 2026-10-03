# neartensor

A **dedicated Bittensor subnet for NEAR chain attestation** — plus the original
NEAR-side subnet contracts it grew out of.

The subnet's single job: miners serve verifiable answers about NEAR Protocol
state, validators independently re-verify every answer against their **own**
NEAR RPC view at the exact block hash the miner anchored to, score
correctness × freshness × latency, and set weights. Wrong or unsigned answers
earn zero, no matter how fast.

## Design principles

- **Local-first.** With `subnet.network = "local"` (the default) the entire
  subnet runs on one box: a file-backed metagraph (`data/subnet_chain.json`),
  local sr25519 hotkeys, plain HTTP between neurons, and a Yuma-lite
  (stake-weighted, median-clipped) epoch. No wallet, no subtensor connection,
  no third-party service beyond public NEAR RPC — and even that rotates
  across independent endpoints so no single provider is load-bearing.
- **Same code on mainnet.** Set `subnet.network` to `"test"` or `"finney"`
  and a netuid, and the identical protocol/miner/validator code runs against
  real subtensor via the bittensor SDK (burned registration, `serve_axon`,
  `set_weights`).
- **Modular.** Each concern is one small file with one seam:
  `near_client.py` (NEAR RPC), `protocol.py` (tasks + canonical answers +
  signing), `reward.py` (pure scoring functions), `chain.py`
  (LocalChain | SubtensorChain), `miner.py`, `validator.py`.

## The dedicated task set

Every answer must be anchored to a concrete `block_hash`, which makes it
deterministically re-verifiable at that exact block:

| task | answer |
|---|---|
| `block_header` | height, hash, prev_hash, epoch_id, timestamp of the final block |
| `gas_price` | gas price at the anchored block |
| `account_state` | balance / locked / storage of a probe account at the anchored block |

Responses carry a sha256 digest over `{task, nonce, answer}` signed by the
miner's hotkey; the nonce binds each answer to the validator's query.

## Run it

```bash
m neartensor sn_serve        # miner (:50184) + validator loop under pm2
m neartensor sn_status       # chain, netuid, metagraph, validator scores
m neartensor sn_epoch        # run one validation epoch synchronously
m neartensor sn_task task=block_header   # ask the local miner directly
m neartensor sn_kill
```

Or through the API (`POST /neartensor/sn_*` on :50180) and the app's
**Bittensor** tab (:50181), which shows the metagraph and can run an epoch.

```bash
python3 -m pytest tests/test_subnet.py   # offline: fake NEAR, temp chain
```

## Joining real subtensor

1. Create/fund a bittensor wallet matching `subnet.wallet_name` / `wallet_hotkey`.
2. Set `subnet.network` to `"test"` or `"finney"` and `subnet.netuid` in `config.json`.
3. `m neartensor sn_register role=miner` (burned registration), then `sn_serve`.

Keys: local-mode hotkeys live in `data/keys/*.json` (0600) and never leave
the box. No secrets go in `config.json`.

## NEAR-side protocol (contracts)

The module also ships the original Bittensor-inspired subnet protocol **on
NEAR** (`contracts/`: registry, subnet, governance — Rust/WASM), driven by
`build` / `deploy` / `register_subnet` / `stake_on` / `produce_block` etc.
The two layers meet in the middle: the Bittensor subnet attests to the same
chain the contracts live on.

```
neartensor/
├── neartensor/mod.py   # Mod class: all actions incl. sn_* subnet actions
├── subnet/             # the dedicated Bittensor subnet (see above)
├── api/api.py          # FastAPI dispatcher on :50180
├── app/                # Next.js console on :50181 (Bittensor tab = subnet)
├── contracts/          # NEAR WASM contracts (registry, subnet, governance)
└── tests/test_subnet.py
```
