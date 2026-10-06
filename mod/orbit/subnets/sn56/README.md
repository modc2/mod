# sn56 — Gradients ج

Best AutoML plaftorm in the world

Bittensor subnet **56** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/gradients-ai/G.O.D) · [url](https://www.gradients.io/) · discord `None`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.014905 | +0.01% | -0.18% | -3.70% | 91,991 | 49,500 | 162.48 |

## Last 24h flow

70 trades by 34 coldkeys · 32 buys (8.74 τ) / 38 sells (90.61 τ) · net -81.86 τ

## News

- 2026-10-05 · commit · [cheat check moved to round 2 (#1391)](https://github.com/gradients-ai/G.O.D/commit/e524ce62ce09b14b1cebdf169b46f114cdbaabf3) — gradients-ai/G.O.D
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
