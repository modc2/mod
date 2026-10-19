# sn92 — REAX र

Verified decision-model inference on Bittensor.

Bittensor subnet **92** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/reaxlabs/reax-subnet) · [url](https://reax.co/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.042293 | — | +6.07% | +836.31% | 9,561 | 2,830 | 4,402 |

## Last 24h flow

1074 trades by 169 coldkeys · 463 buys (2,213 τ) / 611 sells (2,134 τ) · net 79.09 τ

## News

- 2026-10-18 · commit · [tests: add httpx and huggingface_hub to the test extra; make the v3 f…](https://github.com/reaxlabs/reax-subnet/commit/ff057432e921ed1087f3d9f67c895206d9fddc70) — reaxlabs/reax-subnet
- 2026-10-18 · release · [v1.2.0-rc9.post2](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc9.post2) — reaxlabs/reax-subnet
- 2026-10-18 · release · [v1.2.0-rc9](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc9) — reaxlabs/reax-subnet
- 2026-10-18 · commit · [1.2.0rc9: mechanism v2 release B, per-hotkey speed cap x2 for s1-fast…](https://github.com/reaxlabs/reax-subnet/commit/6ce318045d12bcbc4e0e51b787e58a54626ef8fd) — reaxlabs/reax-subnet
- 2026-10-18 · commit · [1.2.0rc9.post2 (competition worker only): start-up failures count ove…](https://github.com/reaxlabs/reax-subnet/commit/fccf67633d5058381afb55baaeb29941690e4273) — reaxlabs/reax-subnet
- 2026-10-17 · release · [v1.2.0-rc8.post2](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc8.post2) — reaxlabs/reax-subnet
- 2026-10-17 · commit · [1.2.0rc8.post2: competition worker queue liveness fixes + first-draw …](https://github.com/reaxlabs/reax-subnet/commit/6d1271ac9528a363cda4b40ef0048cab2cf0e21a) — reaxlabs/reax-subnet
- 2026-10-17 · release · [v1.2.0-rc8.post1](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc8.post1) — reaxlabs/reax-subnet

## Use

```bash
m subnets.sn92/info        # live identity + market (snapshot if bt is down)
m subnets.sn92/news        # scraped news
m subnets.sn92/trades      # 24h alpha tape
m subnets.sn92/daily       # daily candles
python3 orbit/subnets/sn92/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
