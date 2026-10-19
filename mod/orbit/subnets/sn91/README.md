# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://www.cascade.industries/) · [discord](https://discord.gg/bittensor)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003868 | — | -4.91% | -22.74% | 7,634 | 2,971 | 93.60 |

## Last 24h flow

23 trades by 17 coldkeys · 5 buys (8.82 τ) / 18 sells (83.72 τ) · net -74.90 τ

## News

- 2026-10-18 · commit · [king sync (finney): vault/direct@sha256:b20b875be09bda54ab6d69b0219cb…](https://github.com/TensorLink-AI/cascade/commit/aa654eb0602c19a994111e2c008d8b214eb5e53f) — TensorLink-AI/cascade
- 2026-10-17 · commit · [Merge pull request #373 from TensorLink-AI/feat/payer-bench-unverified](https://github.com/TensorLink-AI/cascade/commit/3edd4b3e484325a72296fa166742e6b40eb20650) — TensorLink-AI/cascade
- 2026-10-17 · commit · [Merge pull request #372 from TensorLink-AI/fix/near-dup-lineage-and-l…](https://github.com/TensorLink-AI/cascade/commit/55cd083d80f33f125f7165e59a1160bd68a9399e) — TensorLink-AI/cascade
- 2026-10-17 · commit · [feat(telemetry): funded_bench_unverified — publish payer-pod benches …](https://github.com/TensorLink-AI/cascade/commit/5b7cc6ae3bd00cd367771c840e36a5f8daa55c92) — TensorLink-AI/cascade
- 2026-10-17 · commit · [fix(near-dup): same-coldkey full-tree rule, era-unit lookback, visibl…](https://github.com/TensorLink-AI/cascade/commit/d7d4a06975d7784482d044fc63526aceb5291183) — TensorLink-AI/cascade
- 2026-10-17 · commit · [Merge pull request #371 from TensorLink-AI/fix/promotion-carry-clock-…](https://github.com/TensorLink-AI/cascade/commit/82bb2145c96b12d046d29d199eb83ffff65e3fe9) — TensorLink-AI/cascade
- 2026-10-17 · commit · [fix(promotion): anchor the trainer's reign clock where validators res…](https://github.com/TensorLink-AI/cascade/commit/447ffe3e90d82b76b08a0f74a60c5c45ea2d0dc8) — TensorLink-AI/cascade
- 2026-10-16 · commit · [Merge pull request #370 from TensorLink-AI/docs/miner-admission-guard](https://github.com/TensorLink-AI/cascade/commit/6fc7db03edf2d08f38e0efc258230cbbccac942d) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
