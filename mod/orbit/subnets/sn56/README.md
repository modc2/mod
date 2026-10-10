# sn56 — Gradients ج

Best AutoML plaftorm in the world

Bittensor subnet **56** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/gradients-ai/G.O.D) · [url](https://www.gradients.io/) · discord `None`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.014866 | -0.01% | -0.19% | -0.44% | 92,235 | 49,494 | 208.17 |

## Last 24h flow

71 trades by 48 coldkeys · 44 buys (33.60 τ) / 27 sells (125.00 τ) · net -91.40 τ

## News

- 2026-10-09 · commit · [replacement for DQ (#1394)](https://github.com/gradients-ai/G.O.D/commit/de5ea9cd90bfff32d59a8403cb62433a8ce46bab) — gradients-ai/G.O.D
- 2026-10-07 · commit · [Fix/70b runpod eval path (#1393)](https://github.com/gradients-ai/G.O.D/commit/b49ba8644a0d705e9b035746eceaa05614742934) — gradients-ai/G.O.D
- 2026-10-05 · commit · [cheat check moved to round 2 (#1391)](https://github.com/gradients-ai/G.O.D/commit/e524ce62ce09b14b1cebdf169b46f114cdbaabf3) — gradients-ai/G.O.D
- 2026-10-02 · commit · [Oversampled pool: text-only <=4B models (#1388)](https://github.com/gradients-ai/G.O.D/commit/df81121a9b56f7d5f2a4261038aad8b6a93af56a) — gradients-ai/G.O.D
- 2026-09-27 · commit · [aggressive eval (#1390)](https://github.com/gradients-ai/G.O.D/commit/e23a35ae97b508f34c6b1bb4fe523fe12b66bdcb) — gradients-ai/G.O.D
- 2026-09-27 · commit · [3 task round 1 image (#1387)](https://github.com/gradients-ai/G.O.D/commit/17a2723756daa88561d3dcd5b5d86263523e9a12) — gradients-ai/G.O.D
- 2026-09-24 · commit · [Oversampled pool: drop >3B and Qwen models (#1389)](https://github.com/gradients-ai/G.O.D/commit/dd8e898b0262ad85ec8ae09dbdeef94a70b5dec5) — gradients-ai/G.O.D
- 2026-09-22 · commit · [Add Runpod evaluation backend via dstack (#1386)](https://github.com/gradients-ai/G.O.D/commit/fb8cff268f57a049371d7b4c3518660fae17863b) — gradients-ai/G.O.D

## Use

```bash
m subnets.sn56/info        # live identity + market (snapshot if bt is down)
m subnets.sn56/news        # scraped news
m subnets.sn56/trades      # 24h alpha tape
m subnets.sn56/daily       # daily candles
python3 orbit/subnets/sn56/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
