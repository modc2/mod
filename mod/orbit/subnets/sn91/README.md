# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://www.cascade.industries/) · [discord](https://discord.gg/bittensor)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004201 | +0.00% | -3.26% | -19.40% | 8,230 | 3,091 | 129.85 |

## Last 24h flow

40 trades by 27 coldkeys · 8 buys (37.42 τ) / 32 sells (86.02 τ) · net -48.60 τ

## News

- 2026-10-17 · commit · [Merge pull request #371 from TensorLink-AI/fix/promotion-carry-clock-…](https://github.com/TensorLink-AI/cascade/commit/82bb2145c96b12d046d29d199eb83ffff65e3fe9) — TensorLink-AI/cascade
- 2026-10-17 · commit · [fix(promotion): anchor the trainer's reign clock where validators res…](https://github.com/TensorLink-AI/cascade/commit/447ffe3e90d82b76b08a0f74a60c5c45ea2d0dc8) — TensorLink-AI/cascade
- 2026-10-16 · commit · [Merge pull request #370 from TensorLink-AI/docs/miner-admission-guard](https://github.com/TensorLink-AI/cascade/commit/6fc7db03edf2d08f38e0efc258230cbbccac942d) — TensorLink-AI/cascade
- 2026-10-16 · commit · [docs+cli: miner-facing admission guard (coldkey cap, near-duplicate, …](https://github.com/TensorLink-AI/cascade/commit/8b246512aefbef039546373b84021507758fd25d) — TensorLink-AI/cascade
- 2026-10-16 · commit · [Merge pull request #369 from TensorLink-AI/feat/admission-near-dup-guard](https://github.com/TensorLink-AI/cascade/commit/cd6f01da9dce504df8fb1a4d52c9582c4d76bc7b) — TensorLink-AI/cascade
- 2026-10-16 · commit · [test(near-dup): capture the shadow log line without caplog (propagati…](https://github.com/TensorLink-AI/cascade/commit/793b6e58d738337afd46b54450fb6ceb4bf375a7) — TensorLink-AI/cascade
- 2026-10-16 · commit · [feat(admission): near-duplicate tier + per-coldkey seat cap, also at …](https://github.com/TensorLink-AI/cascade/commit/8ef80161db716f166aea11221b6682f95af30546) — TensorLink-AI/cascade
- 2026-10-16 · commit · [Merge pull request #367 from TensorLink-AI/feat/blocked-coldkeys](https://github.com/TensorLink-AI/cascade/commit/68e9dd704c4c6ba9ae21c417bfd887c9b882140a) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
