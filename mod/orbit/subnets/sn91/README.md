# sn91 — cascade ᚁ

SOTA Time Series Foundation Models

Bittensor subnet **91** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/TensorLink-AI/cascade) · [url](https://cascadesub.net) · discord `christensor_49068`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005212 | -0.00% | +1.00% | -9.72% | 9,929 | 3,412 | 881.30 |

## Last 24h flow

163 trades by 57 coldkeys · 69 buys (447.48 τ) / 94 sells (431.44 τ) · net 16.04 τ

## News

- 2026-10-10 · commit · [Merge pull request #362 from TensorLink-AI/fix/era-end-admission-max-…](https://github.com/TensorLink-AI/cascade/commit/26b3d7324f8d1d69d0c12387d31a37c775e83d04) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Merge remote-tracking branch 'origin/main' into fix/era-end-admission…](https://github.com/TensorLink-AI/cascade/commit/41e11505ff4766cbb1ee91a13e1952df4836f68c) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Merge pull request #361 from TensorLink-AI/fix/step0-nan-new-host](https://github.com/TensorLink-AI/cascade/commit/fef4d7eac22062b7ef6b7204708df5e1b3d4d66a) — TensorLink-AI/cascade
- 2026-10-10 · commit · [Era cap: a fail-open module function, so partial runners keep their d…](https://github.com/TensorLink-AI/cascade/commit/df12796001ffbf4f3debd8ad7e93b8bf601b3742) — TensorLink-AI/cascade
- 2026-10-09 · commit · [Merge pull request #356 from TensorLink-AI/funded/expired-key-operato…](https://github.com/TensorLink-AI/cascade/commit/8202f4477620b5e0929250440b700d31a236be06) — TensorLink-AI/cascade
- 2026-10-08 · commit · [Funded legs: operator-rent a pod when the payer key expired and no la…](https://github.com/TensorLink-AI/cascade/commit/93ebfba932dddb9a05a6d99080628057e56ee9ac) — TensorLink-AI/cascade
- 2026-10-06 · commit · [king sync (finney): vault/direct@sha256:bc25aa4323c91d3313e038c1a62f4…](https://github.com/TensorLink-AI/cascade/commit/9404c3a87e2a00679d5ec15362c9ef165293d349) — TensorLink-AI/cascade
- 2026-10-06 · commit · [Merge pull request #355 from TensorLink-AI/web/missing-is-final](https://github.com/TensorLink-AI/cascade/commit/fa84d7617a795bf46f597274f605ddcfc4c84ef6) — TensorLink-AI/cascade

## Use

```bash
m subnets.sn91/info        # live identity + market (snapshot if bt is down)
m subnets.sn91/news        # scraped news
m subnets.sn91/trades      # 24h alpha tape
m subnets.sn91/daily       # daily candles
python3 orbit/subnets/sn91/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
