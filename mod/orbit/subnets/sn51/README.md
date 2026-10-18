# sn51 — lium.io ת

Agent-first GPU cloud. Rent NVIDIA GPUs via web, CLI, SDK, or API. Providers earn 95% rental revenue + bonuses.

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · [discord](https://discord.gg/lium)

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.109014 | +0.00% | +0.09% | +4.84% | 671,891 | 178,491 | 2,251 |

## Last 24h flow

422 trades by 208 coldkeys · 195 buys (778.61 τ) / 227 sells (1,072 τ) · net -293.60 τ

## News

- 2026-10-17 · release · [miner-v1.006](https://github.com/Datura-ai/lium-io/releases/tag/miner-v1.006) — Datura-ai/lium-io
- 2026-10-17 · commit · [DAH-4539 - Miner: a portal delete removes only the node it names (#1661)](https://github.com/Datura-ai/lium-io/commit/a579f070816eb051b05109f46d25a7b821f27415) — Datura-ai/lium-io
- 2026-10-17 · commit · [DAH-4601 - \[P0\] Validator: fail a node where sshd does not start in t…](https://github.com/Datura-ai/lium-io/commit/2ba69167b895545fd0a5c1ddbc84615b8fd7ac9e) — Datura-ai/lium-io
- 2026-10-17 · commit · [DAH-4299 - \[P1\] Spot idle pay: decide whether a Spot node is paid the…](https://github.com/Datura-ai/lium-io/commit/b0a374b5c6876efd1f0d588f05bc5ee469c9b79e) — Datura-ai/lium-io
- 2026-10-17 · commit · [DAH-4255 - \[P0\] Pinned-memory probe: log a GPU whose CUDA driver cann…](https://github.com/Datura-ai/lium-io/commit/52f6538c626334cdbab2305cd0fb6bb03f27246d) — Datura-ai/lium-io
- 2026-10-17 · commit · [Sysbox installer: show the verify container's error when step 6 fails…](https://github.com/Datura-ai/lium-io/commit/463d54128e223354ad5707b512fd1ea758fe97cd) — Datura-ai/lium-io
- 2026-10-17 · release · [executor-v1.142](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.142) — Datura-ai/lium-io
- 2026-10-17 · commit · [DAH-4375 - \[P0\] Executor reports its configured miner hotkey on reque…](https://github.com/Datura-ai/lium-io/commit/f43a0ca725a4d2000d2cc5dd52fc5ca81fa73c45) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
