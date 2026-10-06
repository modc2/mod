# sn97 — Albedo ა

Alchemical intelligence

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/unarbos/albedo) · [url](https://us-east-1.hippius.com/albedo/index.html) · discord `@arbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.015372 | +0.02% | -3.97% | -3.32% | 29,444 | 11,434 | 472.32 |

## Last 24h flow

45 trades by 30 coldkeys · 16 buys (86.84 τ) / 29 sells (341.28 τ) · net -254.44 τ

## News

- 2026-10-05 · commit · [chore: increase scoring timeout](https://github.com/unarbos/albedo/commit/0f321d7e1fa2109b85c17ab7ac47bd30a4096771) — unarbos/albedo
- 2026-10-05 · commit · [fix: engy timeouts fall through to OpenRouter at once, engy queue dep…](https://github.com/unarbos/albedo/commit/111a83455738eb372d460d3cda99f03d149a25ce) — unarbos/albedo
- 2026-10-05 · commit · [feat: pairs are judged as soon as both their trajectories end, while …](https://github.com/unarbos/albedo/commit/9e3ca6f52300a77042ab7739299c93bdea1d59e9) — unarbos/albedo
- 2026-10-05 · commit · [feat: engy serves judge and reference calls first, up to a bounded qu…](https://github.com/unarbos/albedo/commit/80c75a38d864ca339020dc5ad237d829bc45d5ad) — unarbos/albedo
- 2026-10-05 · commit · [feat: GLM-5.3-flash for judging, questions and references](https://github.com/unarbos/albedo/commit/2beef43b529c514f21ced9765acfab873b17b004) — unarbos/albedo
- 2026-10-05 · commit · [feat: Jev for milestone alignment, question dedup and reference pruning](https://github.com/unarbos/albedo/commit/e5b4919873d0748629d8e9f3329b5c359793923f) — unarbos/albedo
- 2026-10-05 · commit · [chore: rotate deepseek providers](https://github.com/unarbos/albedo/commit/2032ac98f7d0e5f48747ea0a008313257664dde9) — unarbos/albedo
- 2026-10-02 · commit · [fix: the simulator stops inventing git history for git show and git log](https://github.com/unarbos/albedo/commit/7cf5f2a45df149a1b6fc76af4db3713a90f2d8d9) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
