# sn97 — Albedo ა

Alchemical intelligence

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/unarbos/albedo) · [url](https://us-east-1.hippius.com/albedo/index.html) · discord `@arbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.016180 | +0.41% | -2.50% | -5.87% | 30,434 | 11,587 | 785.66 |

## Last 24h flow

85 trades by 46 coldkeys · 32 buys (286.14 τ) / 53 sells (444.06 τ) · net -157.91 τ

## News

- 2026-09-30 · commit · [fix: Display on website nonet bench runs if avaiable](https://github.com/unarbos/albedo/commit/b99ca91a7c25b8cdd13324466063c384627e708c) — unarbos/albedo
- 2026-09-29 · commit · [fix: eval reads a command including the tag](https://github.com/unarbos/albedo/commit/7f331a7996325b5cfbb9f440bc4a5ebaa9a920d9) — unarbos/albedo
- 2026-09-29 · commit · [fix: pre-eval counts only submits it doesn't accept, 4 wrong in a row…](https://github.com/unarbos/albedo/commit/5ea0c190e5c6806132bfb73e391a6a2bbd19b36e) — unarbos/albedo
- 2026-09-29 · commit · [fix: copy check compares an upload with models from earlier blocks](https://github.com/unarbos/albedo/commit/8c5184a6341382548b26e5b9be73a4e700eb32b4) — unarbos/albedo
- 2026-09-29 · commit · [fix: copy check puts shuffled experts back in order](https://github.com/unarbos/albedo/commit/234049fd7e019127e30d2e48adcf7c6163d23d7d) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
