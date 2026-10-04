# sn97 — Albedo ა

Alchemical intelligence

Bittensor subnet **97** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/unarbos/albedo) · [url](https://us-east-1.hippius.com/albedo/index.html) · discord `@arbos`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.016320 | -0.05% | +0.41% | -0.44% | 30,948 | 11,702 | 170.57 |

## Last 24h flow

18 trades by 14 coldkeys · 8 buys (64.69 τ) / 10 sells (62.32 τ) · net 2.37 τ

## News

- 2026-10-02 · commit · [fix: the simulator stops inventing git history for git show and git log](https://github.com/unarbos/albedo/commit/7cf5f2a45df149a1b6fc76af4db3713a90f2d8d9) — unarbos/albedo
- 2026-10-02 · commit · [fix: swesmith tasks are served as one upstream commit, and git show r…](https://github.com/unarbos/albedo/commit/0e29f5cb6174c58db9a887536fffdf24a95fdf54) — unarbos/albedo
- 2026-10-02 · commit · [fix: repo-context grounds chains stage by stage and replays the overl…](https://github.com/unarbos/albedo/commit/a27382f828a1064d065083c5019c0a0cb6fd753f) — unarbos/albedo
- 2026-10-02 · commit · [fix: pre-eval keeps the model's reasoning in each turn like eval and …](https://github.com/unarbos/albedo/commit/8eb80024fc0ab53ff9fde2fb4bd2d9d23e779578) — unarbos/albedo
- 2026-10-02 · commit · [fix: pre-eval redraws a sample whose micro-task can't be generated in…](https://github.com/unarbos/albedo/commit/1cd927a6c1ecf681fcdb89b7ad8afd9180f0d0d3) — unarbos/albedo
- 2026-10-02 · commit · [fix: pre-eval stops counting wrong submits across the trajectory and …](https://github.com/unarbos/albedo/commit/1c6895a8347d3b33c0f80021ad3936d50c162969) — unarbos/albedo
- 2026-09-30 · commit · [fix: Display on website nonet bench runs if avaiable](https://github.com/unarbos/albedo/commit/b99ca91a7c25b8cdd13324466063c384627e708c) — unarbos/albedo
- 2026-09-29 · commit · [fix: eval reads a command including the tag](https://github.com/unarbos/albedo/commit/7f331a7996325b5cfbb9f440bc4a5ebaa9a920d9) — unarbos/albedo

## Use

```bash
m subnets.sn97/info        # live identity + market (snapshot if bt is down)
m subnets.sn97/news        # scraped news
m subnets.sn97/trades      # 24h alpha tape
m subnets.sn97/daily       # daily candles
python3 orbit/subnets/sn97/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
