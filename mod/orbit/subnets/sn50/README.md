# sn50 — Synth ש

Predictive intelligence for financial markets and beyond

Bittensor subnet **50** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/mode-network/synth-subnet) · [url](https://synthdata.co)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004344 | -0.49% | -4.19% | +8.47% | 25,650 | 10,643 | 829.92 |

## Last 24h flow

103 trades by 57 coldkeys · 37 buys (297.42 τ) / 66 sells (528.80 τ) · net -231.38 τ

## News

- 2026-10-07 · release · [v1.14.0](https://github.com/synthdataco/synth-subnet/releases/tag/v1.14.0) — mode-network/synth-subnet
- 2026-10-06 · commit · [perf(validator): write predictions to Bigtable from the dendrite work…](https://github.com/synthdataco/synth-subnet/commit/5fc02e9ab5a6ae65b45dd2bf56dc225b6115e42b) — mode-network/synth-subnet
- 2026-10-06 · commit · [perf(validator): validate and encode miner responses in the dendrite …](https://github.com/synthdataco/synth-subnet/commit/e9495db80f2b69fa678d149bf0f5219626d673ff) — mode-network/synth-subnet
- 2026-10-06 · commit · [perf(validator): convert each Bigtable prediction to an array once (#…](https://github.com/synthdataco/synth-subnet/commit/c8f427f53d738e41825b901842bf40e0c58409f1) — mode-network/synth-subnet
- 2026-10-05 · commit · [chore(deps): bump virtualenv from 21.4.3 to 21.7.13 (#329)](https://github.com/synthdataco/synth-subnet/commit/d7693a264979b2808e6a8695e304ed1822037c8a) — mode-network/synth-subnet
- 2026-10-05 · commit · [chore(deps): bump gitpython from 3.1.59 to 3.1.62 (#331)](https://github.com/synthdataco/synth-subnet/commit/cbfc2380e0528eecab3d697a8c16c1d0509c4e59) — mode-network/synth-subnet
- 2026-10-05 · commit · [chore(deps): bump urllib3 from 2.7.0 to 2.8.0 (#330)](https://github.com/synthdataco/synth-subnet/commit/78ecb48be15bb85e9d43ca884dfe66b654a233f4) — mode-network/synth-subnet
- 2026-10-05 · commit · [perf(validator): reuse dendrite process pool, as_completed, drop per-…](https://github.com/synthdataco/synth-subnet/commit/2fe68b7dfc9bebb790eec72483e10ea23bcc71b7) — mode-network/synth-subnet

## Use

```bash
m subnets.sn50/info        # live identity + market (snapshot if bt is down)
m subnets.sn50/news        # scraped news
m subnets.sn50/trades      # 24h alpha tape
m subnets.sn50/daily       # daily candles
python3 orbit/subnets/sn50/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
