# neartensor

A **dedicated Bittensor subnet for NEAR chain attestation** — plus the original
NEAR-side subnet contracts it grew out of.

The subnet's single job: miners serve verifiable answers about NEAR Protocol
state, validators independently re-verify every answer against their **own**
NEAR RPC view at the exact block hash the miner anchored to, score
correctness × freshness × latency, and set weights. Wrong or unsigned answers
earn zero, no matter how fast.

Read the **whitepaper**: `src/whitepaper.md` (also the app's Whitepaper tab).

## Create a subnet

```bash
m neartensor sn_create name=fast-headers tasks=block_header consensus=winner
m neartensor sn_subnets                       # every subnet: netuid, name, consensus, tasks
m neartensor sn_epoch netuid=1                # validate one subnet now
m neartensor sn_set_consensus netuid=1 consensus=yuma
```

Or use the **Create subnet** form on the app's Subnets tab. Each local subnet
is one file, `data/subnets/<netuid>.json`. Netuid 0 (`neartensor`) is genesis.
The miner joins every subnet; the validator loop validates every subnet.

## Modular consensus

Each rule is one file in `src/ntsubnet/consensus/` exposing
`NAME`, `ABOUT`, `run(weights, stakes) -> {uid: incentive}`:

| rule | behaviour |
|---|---|
| `yuma` (default) | stake-weighted median clip, then stake average |
| `mean` | plain stake-weighted average |
| `winner` | yuma, then 100% to the top miner |

Drop in a new file to add a rule. It's discovered automatically.

## Design principles

- **Local-first.** With `subnet.network = "local"` (the default) everything
  runs on one box: file-backed subnets, local sr25519 hotkeys, plain HTTP
  between neurons. No wallet and no subtensor; NEAR RPC rotates across public
  endpoints.
- **Same code on mainnet.** Set `subnet.network` to `"test"`/`"finney"` and a
  netuid; the identical miner/validator runs on subtensor via the bittensor
  SDK. (There, create subnets with `btcli subnet create`.)
- **One file per concern** in `src/ntsubnet/`: `near_client.py`, `protocol.py`,
  `reward.py`, `chain.py`, `consensus/*.py`, `miner.py`, `validator.py`.

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

Or through the API (`POST /neartensor/sn_*` on :50185) and the app's
**Bittensor** tab (:50181), which shows the metagraph and can run an epoch.

```bash
python3 -m pytest src/tests   # offline: fake NEAR, temp chain
```

## Joining real subtensor

1. Create/fund a bittensor wallet matching `subnet.wallet_name` / `wallet_hotkey`.
2. Set `subnet.network` to `"test"` or `"finney"` and `subnet.netuid` in `config.json`.
3. `m neartensor sn_register role=miner` (burned registration), then `sn_serve`.

Keys: local-mode hotkeys live in `data/keys/*.json` (0600) and never leave
the box. No secrets go in `config.json`.

## NEAR-side protocol (contracts)

The module also ships the original Bittensor-inspired subnet protocol **on
NEAR** (`src/contracts/`: registry, subnet, governance — Rust/WASM), driven by
`build` / `deploy` / `register_subnet` / `stake_on` / `produce_block` etc.
The two layers meet in the middle: the Bittensor subnet attests to the same
chain the contracts live on.

**Subnet registration is BlocTime** (a port of `orbit/bloctime`'s
`BlocTime.sol`, in `src/contracts/registry/src/bloctime.rs`). A registrant attaches
5 NEAR account funding + a stake and picks `lock_seconds`; the lock earns
`µNEAR × seconds × curve multiplier` and must reach `min_registration_bloctime`
(default 1 NEAR × 30 days). A subnet's score — what eviction ranks on — is the
sum of every lock held against it; anyone can add one (`stake_subnet`), and
locks come back via `unstake_position` only after they expire. When all slots
are full a newcomer must out-score the weakest non-immune subnet. The bonding
curve boost is a share market and no longer counts toward the score.
`quote_registration lock_seconds=…` gives the exact deposit needed.

```
neartensor/
├── config.json          # ports + `subnet` section
├── data/                # runtime state: subnets/<netuid>.json, keys/
└── src/                 # ALL code
    ├── mod.py           # Mod class: every action (sn_* = subnets)
    ├── whitepaper.md
    ├── ntsubnet/        # the subnet engine (consensus/ = pluggable rules)
    ├── api/api.py       # FastAPI dispatcher on :50185
    ├── app/             # Next.js console on :50181
    ├── contracts/       # NEAR WASM contracts (cargo workspace: src/Cargo.toml)
    └── tests/
```
