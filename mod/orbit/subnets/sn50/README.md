# sn50 — Synth ש

Predictive intelligence for financial markets and beyond

Bittensor subnet **50** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/mode-network/synth-subnet) · [url](https://synthdata.co)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003927 | +0.02% | +0.01% | -11.82% | 23,498 | 10,155 | 855.56 |

## Last 24h flow

121 trades by 58 coldkeys · 43 buys (417.73 τ) / 78 sells (425.43 τ) · net -7.71 τ

## News

- 2026-10-19 · commit · [burn 22% of miner emission (#341)](https://github.com/synthdataco/synth-subnet/commit/c337e20495e659b5c278823a69a7daf183261c04) — mode-network/synth-subnet
- 2026-10-14 · commit · [Scope the cycle's asset rotation to its own asset list (#339)](https://github.com/synthdataco/synth-subnet/commit/d2daf2052a6b7821512ac55f3cc2d1986798be9d) — mode-network/synth-subnet
- 2026-10-09 · commit · [Cast the start_time bound operands so param types resolve (#338)](https://github.com/synthdataco/synth-subnet/commit/dda9b34cae0c76b8eed76538c5749b24266943e6) — mode-network/synth-subnet
- 2026-10-09 · commit · [Avoid a full miner_predictions scan when loading scores (#337)](https://github.com/synthdataco/synth-subnet/commit/f18efc654aa99ffc0bb70918de46085d5981e0a0) — mode-network/synth-subnet
- 2026-10-07 · release · [v1.14.0](https://github.com/synthdataco/synth-subnet/releases/tag/v1.14.0) — mode-network/synth-subnet
- 2026-10-06 · commit · [perf(validator): write predictions to Bigtable from the dendrite work…](https://github.com/synthdataco/synth-subnet/commit/5fc02e9ab5a6ae65b45dd2bf56dc225b6115e42b) — mode-network/synth-subnet
- 2026-10-06 · commit · [perf(validator): validate and encode miner responses in the dendrite …](https://github.com/synthdataco/synth-subnet/commit/e9495db80f2b69fa678d149bf0f5219626d673ff) — mode-network/synth-subnet
- 2026-10-06 · commit · [perf(validator): convert each Bigtable prediction to an array once (#…](https://github.com/synthdataco/synth-subnet/commit/c8f427f53d738e41825b901842bf40e0c58409f1) — mode-network/synth-subnet

## Use

```bash
m subnets.sn50/info        # live identity + market (snapshot if bt is down)
m subnets.sn50/news        # scraped news
m subnets.sn50/trades      # 24h alpha tape
m subnets.sn50/daily       # daily candles
python3 orbit/subnets/sn50/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
