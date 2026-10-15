# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://cascadesub.net) · discord `christensor_49068`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004662 | -0.01% | -1.81% | -5.57% | 9,063 | 3,249 | 222.78 |

## Last 24h flow

48 trades by 24 coldkeys · 16 buys (94.51 τ) / 32 sells (113.83 τ) · net -19.32 τ

## News

- 2026-10-14 · commit · [Merge pull request #365 from TensorLink-AI/fix/staged-promotion-carry](https://github.com/TensorLink-AI/cascade/commit/4e97f00053be4408fca542d035191f7f001fb2bc) — TensorLink-AI/cascade
- 2026-10-14 · commit · [fix(validator): carry staged promotions across a dethrone (DEC-CA-005…](https://github.com/TensorLink-AI/cascade/commit/b309c3819c494f7a822020512d217a3d03d91111) — TensorLink-AI/cascade
- 2026-10-13 · commit · [king sync (finney): vault/direct@sha256:f33f40a427ba15e1fee088763b1e9…](https://github.com/TensorLink-AI/cascade/commit/e9ea521b765dfc2c234a1eab664bd7e660c7f954) — TensorLink-AI/cascade
- 2026-10-13 · commit · [Merge pull request #364 from TensorLink-AI/fix/funded-rent-fcfs](https://github.com/TensorLink-AI/cascade/commit/025ed1cae91dfb677eb5556fbb933c465df2d673) — TensorLink-AI/cascade
- 2026-10-13 · commit · [fix(trainer): first-come-first-served funded rents (seniority-ordered…](https://github.com/TensorLink-AI/cascade/commit/61fc73fcf305478f7b069a05a7c158b9940c89b6) — TensorLink-AI/cascade
- 2026-10-10 · commit · [king sync (finney): vault/direct@sha256:a3e342a16db6cb6da0b24e4771813…](https://github.com/TensorLink-AI/cascade/commit/0fba3c0dcfd38aa6f9901ba4a9ec3f4ec20d843e) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Merge pull request #363 from TensorLink-AI/fix/rediscover-king-pod-af…](https://github.com/TensorLink-AI/cascade/commit/321b9402a5df4aa7ac3761c0df344263af67edc5) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Rolling: re-find a finished era's king pod after a restart](https://github.com/TensorLink-AI/cascade/commit/ac9883829c056c03a9fb72103fccaeb5d00550f4) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
