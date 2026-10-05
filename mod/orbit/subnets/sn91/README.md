# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://cascadesub.net) · discord `christensor_49068`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005020 | +0.12% | -3.63% | +1.40% | 9,364 | 3,325 | 448.74 |

## Last 24h flow

81 trades by 37 coldkeys · 29 buys (192.81 τ) / 52 sells (250.38 τ) · net -57.57 τ

## News

- 2026-10-05 · commit · [Merge pull request #353 from TensorLink-AI/fix/king-pod-bench-driver](https://github.com/TensorLink-AI/cascade/commit/127f03cc5b20056c6aca2daaaa077cb97d02a21f) — TensorLink-AI/cascade
- 2026-10-04 · commit · [King pod must be able to run the public benchmarks (driver >= 580)](https://github.com/TensorLink-AI/cascade/commit/5ffa7b0ced7408a22d071d0303f9b70456abba90) — TensorLink-AI/cascade
- 2026-10-04 · commit · [king sync (finney): vault/direct@sha256:298591a20129a4b1326f4ffcd7b56…](https://github.com/TensorLink-AI/cascade/commit/7d64fc02a7dd4dbce2549eb593fe01cef8a1c402) — TensorLink-AI/cascade
- 2026-10-04 · commit · [Merge pull request #352 from TensorLink-AI/pool/per-feed-cap-5](https://github.com/TensorLink-AI/cascade/commit/1f4364a2260aaa528af43fbcbf12d8297042d4df) — TensorLink-AI/cascade
- 2026-10-03 · commit · [publish-pool: per-feed panel cap 25 -> 5 (more distinct clusters per …](https://github.com/TensorLink-AI/cascade/commit/6a2d5b412111750d478eb2dd5e3b1c07cd852d2f) — TensorLink-AI/cascade
- 2026-10-03 · commit · [Merge pull request #351 from TensorLink-AI/fix/king-pod-on-demand](https://github.com/TensorLink-AI/cascade/commit/f0abef4989b3bd7ceac0580510daa23ce45c33a1) — TensorLink-AI/cascade
- 2026-10-03 · commit · [Rolling: release an era's king pod as soon as it is idle](https://github.com/TensorLink-AI/cascade/commit/4a6f30c3302dfc2c804183aa88974628936cb6cd) — TensorLink-AI/cascade
- 2026-10-03 · commit · [Merge pull request #350 from TensorLink-AI/fix/settle-before-bench](https://github.com/TensorLink-AI/cascade/commit/8b802afbdeb8bd58be57cc84013578b5c6759a89) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
