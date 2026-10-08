# sn97 — Albedo ა

Alchemical intelligence

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/unarbos/albedo) · [url](https://us-east-1.hippius.com/albedo/index.html) · discord `@arbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.015154 | +0.02% | -1.84% | -9.14% | 29,313 | 11,427 | 175.04 |

## Last 24h flow

36 trades by 24 coldkeys · 4 buys (4.55 τ) / 32 sells (131.42 τ) · net -126.87 τ

## News

- 2026-10-07 · commit · [fix: a code block in the reasoning no longer hides the git diff that …](https://github.com/unarbos/albedo/commit/0b7a1681f291a27a11f212f9068ca1a7a71c32c1) — unarbos/albedo
- 2026-10-06 · commit · [perf: fonts no longer block the first paint, no preds probes for fini…](https://github.com/unarbos/albedo/commit/6104011fe925c96f1c33b36495cd0d85fa3fba78) — unarbos/albedo
- 2026-10-06 · commit · [perf: index page loads its data at once and stops jumping while it loads](https://github.com/unarbos/albedo/commit/3bbcdbc974c6e9c1e8f38fdc935ef9e1dc43691f) — unarbos/albedo
- 2026-10-05 · commit · [chore: increase scoring timeout](https://github.com/unarbos/albedo/commit/0f321d7e1fa2109b85c17ab7ac47bd30a4096771) — unarbos/albedo
- 2026-10-05 · commit · [fix: engy timeouts fall through to OpenRouter at once, engy queue dep…](https://github.com/unarbos/albedo/commit/111a83455738eb372d460d3cda99f03d149a25ce) — unarbos/albedo
- 2026-10-05 · commit · [feat: pairs are judged as soon as both their trajectories end, while …](https://github.com/unarbos/albedo/commit/9e3ca6f52300a77042ab7739299c93bdea1d59e9) — unarbos/albedo
- 2026-10-05 · commit · [feat: engy serves judge and reference calls first, up to a bounded qu…](https://github.com/unarbos/albedo/commit/80c75a38d864ca339020dc5ad237d829bc45d5ad) — unarbos/albedo
- 2026-10-05 · commit · [feat: GLM-5.3-flash for judging, questions and references](https://github.com/unarbos/albedo/commit/2beef43b529c514f21ced9765acfab873b17b004) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
