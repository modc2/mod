# sn56 — Gradients ج

Best AutoML plaftorm in the world

Bittensor subnet **56** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/gradients-ai/G.O.D) · [url](https://www.gradients.io/) · discord `None`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.014897 | +0.01% | -0.24% | -4.65% | 91,694 | 49,456 | 214.74 |

## Last 24h flow

55 trades by 45 coldkeys · 23 buys (6.84 τ) / 32 sells (134.77 τ) · net -127.92 τ

## News

- 2026-10-02 · commit · [Oversampled pool: text-only <=4B models (#1388)](https://github.com/gradients-ai/G.O.D/commit/df81121a9b56f7d5f2a4261038aad8b6a93af56a) — gradients-ai/G.O.D
- 2026-09-27 · commit · [aggressive eval (#1390)](https://github.com/gradients-ai/G.O.D/commit/e23a35ae97b508f34c6b1bb4fe523fe12b66bdcb) — gradients-ai/G.O.D
- 2026-09-27 · commit · [3 task round 1 image (#1387)](https://github.com/gradients-ai/G.O.D/commit/17a2723756daa88561d3dcd5b5d86263523e9a12) — gradients-ai/G.O.D
- 2026-09-24 · commit · [Oversampled pool: drop >3B and Qwen models (#1389)](https://github.com/gradients-ai/G.O.D/commit/dd8e898b0262ad85ec8ae09dbdeef94a70b5dec5) — gradients-ai/G.O.D
- 2026-09-22 · commit · [Add Runpod evaluation backend via dstack (#1386)](https://github.com/gradients-ai/G.O.D/commit/fb8cff268f57a049371d7b4c3518660fae17863b) — gradients-ai/G.O.D
- 2026-09-22 · commit · [Drop the forced pre-boss model, widen boss large instruct to 30-72B (…](https://github.com/gradients-ai/G.O.D/commit/0791bb6e345533cae8088ffcc4d214c70c22efaf) — gradients-ai/G.O.D

## Use

```bash
m subnets.sn56/info        # live identity + market (snapshot if bt is down)
m subnets.sn56/news        # scraped news
m subnets.sn56/trades      # 24h alpha tape
m subnets.sn56/daily       # daily candles
python3 orbit/subnets/sn56/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
