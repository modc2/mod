# sn56 — Gradients ج

Best AutoML plaftorm in the world

Bittensor subnet **56** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/gradients-ai/G.O.D) · [url](https://www.gradients.io/) · discord `None`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.014834 | +0.00% | -0.01% | -0.41% | 92,767 | 49,528 | 212.77 |

## Last 24h flow

49 trades by 35 coldkeys · 17 buys (26.91 τ) / 32 sells (104.73 τ) · net -77.82 τ

## News

- 2026-10-15 · commit · [Raise training overhead for <1B and 1-4B model bands to 0.4h and 0.6h…](https://github.com/gradients-ai/G.O.D/commit/0b22808d7db2b20cde49851c8c9650875e50338d) — gradients-ai/G.O.D
- 2026-10-14 · commit · [Scale training overhead hours by model size (#1392)](https://github.com/gradients-ai/G.O.D/commit/d1f18afd4831b22c96a81931f301978b4313cbef) — gradients-ai/G.O.D
- 2026-10-09 · commit · [replacement for DQ (#1394)](https://github.com/gradients-ai/G.O.D/commit/de5ea9cd90bfff32d59a8403cb62433a8ce46bab) — gradients-ai/G.O.D
- 2026-10-07 · commit · [Fix/70b runpod eval path (#1393)](https://github.com/gradients-ai/G.O.D/commit/b49ba8644a0d705e9b035746eceaa05614742934) — gradients-ai/G.O.D
- 2026-10-05 · commit · [cheat check moved to round 2 (#1391)](https://github.com/gradients-ai/G.O.D/commit/e524ce62ce09b14b1cebdf169b46f114cdbaabf3) — gradients-ai/G.O.D
- 2026-10-02 · commit · [Oversampled pool: text-only <=4B models (#1388)](https://github.com/gradients-ai/G.O.D/commit/df81121a9b56f7d5f2a4261038aad8b6a93af56a) — gradients-ai/G.O.D
- 2026-09-27 · commit · [aggressive eval (#1390)](https://github.com/gradients-ai/G.O.D/commit/e23a35ae97b508f34c6b1bb4fe523fe12b66bdcb) — gradients-ai/G.O.D
- 2026-09-27 · commit · [3 task round 1 image (#1387)](https://github.com/gradients-ai/G.O.D/commit/17a2723756daa88561d3dcd5b5d86263523e9a12) — gradients-ai/G.O.D

## Use

```bash
m subnets.sn56/info        # live identity + market (snapshot if bt is down)
m subnets.sn56/news        # scraped news
m subnets.sn56/trades      # 24h alpha tape
m subnets.sn56/daily       # daily candles
python3 orbit/subnets/sn56/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
