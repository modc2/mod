# sn97 — Albedo ა

Alchemical intelligence

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/unarbos/albedo) · [url](https://us-east-1.hippius.com/albedo/index.html) · discord `@arbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.015158 | +0.02% | +0.03% | -6.45% | 29,462 | 11,465 | 196.84 |

## Last 24h flow

39 trades by 23 coldkeys · 12 buys (68.81 τ) / 27 sells (91.24 τ) · net -22.43 τ

## News

- 2026-10-08 · commit · [feat: display new datasets on frontend](https://github.com/unarbos/albedo/commit/6ff609d0eaaf904d55731858d0d733eab1c0c6b3) — unarbos/albedo
- 2026-10-08 · commit · [feat: new affine dataset, new open-swe-traces splits](https://github.com/unarbos/albedo/commit/fcf2b97206c30f01cdd810ce6889fceea1240f0b) — unarbos/albedo
- 2026-10-08 · commit · [feat: unified fonts, layout, darker background. more readable benchma…](https://github.com/unarbos/albedo/commit/aee0b011ba317c8cd4c68512e284dd1709c6e05f) — unarbos/albedo
- 2026-10-07 · commit · [fix: a code block in the reasoning no longer hides the git diff that …](https://github.com/unarbos/albedo/commit/0b7a1681f291a27a11f212f9068ca1a7a71c32c1) — unarbos/albedo
- 2026-10-06 · commit · [perf: fonts no longer block the first paint, no preds probes for fini…](https://github.com/unarbos/albedo/commit/6104011fe925c96f1c33b36495cd0d85fa3fba78) — unarbos/albedo
- 2026-10-06 · commit · [perf: index page loads its data at once and stops jumping while it loads](https://github.com/unarbos/albedo/commit/3bbcdbc974c6e9c1e8f38fdc935ef9e1dc43691f) — unarbos/albedo
- 2026-10-05 · commit · [chore: increase scoring timeout](https://github.com/unarbos/albedo/commit/0f321d7e1fa2109b85c17ab7ac47bd30a4096771) — unarbos/albedo
- 2026-10-05 · commit · [fix: engy timeouts fall through to OpenRouter at once, engy queue dep…](https://github.com/unarbos/albedo/commit/111a83455738eb372d460d3cda99f03d149a25ce) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
