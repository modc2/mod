# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-13 (block 9272278).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://cascadesub.net) · discord `christensor_49068`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004635 | +0.00% | -7.43% | -3.27% | 8,940 | 3,231 | 349.13 |

## Last 24h flow

48 trades by 24 coldkeys · 9 buys (109.50 τ) / 39 sells (235.16 τ) · net -125.66 τ

## News

- 2026-10-10 · commit · [king sync (finney): vault/direct@sha256:a3e342a16db6cb6da0b24e4771813…](https://github.com/TensorLink-AI/cascade/commit/0fba3c0dcfd38aa6f9901ba4a9ec3f4ec20d843e) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Merge pull request #363 from TensorLink-AI/fix/rediscover-king-pod-af…](https://github.com/TensorLink-AI/cascade/commit/321b9402a5df4aa7ac3761c0df344263af67edc5) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Rolling: re-find a finished era's king pod after a restart](https://github.com/TensorLink-AI/cascade/commit/ac9883829c056c03a9fb72103fccaeb5d00550f4) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Merge pull request #362 from TensorLink-AI/fix/era-end-admission-max-…](https://github.com/TensorLink-AI/cascade/commit/26b3d7324f8d1d69d0c12387d31a37c775e83d04) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Merge remote-tracking branch 'origin/main' into fix/era-end-admission…](https://github.com/TensorLink-AI/cascade/commit/41e11505ff4766cbb1ee91a13e1952df4836f68c) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Merge pull request #361 from TensorLink-AI/fix/step0-nan-new-host](https://github.com/TensorLink-AI/cascade/commit/fef4d7eac22062b7ef6b7204708df5e1b3d4d66a) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Era cap: a fail-open module function, so partial runners keep their d…](https://github.com/TensorLink-AI/cascade/commit/df12796001ffbf4f3debd8ad7e93b8bf601b3742) — TensorLink-AI/cascade
- 2026-10-09 · commit · [Merge pull request #356 from TensorLink-AI/funded/expired-key-operato…](https://github.com/TensorLink-AI/cascade/commit/8202f4477620b5e0929250440b700d31a236be06) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
