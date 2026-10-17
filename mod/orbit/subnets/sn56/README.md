# sn56 — Gradients ج

Best AutoML plaftorm in the world

Bittensor subnet **56** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/gradients-ai/G.O.D) · [url](https://www.gradients.io/) · discord `None`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.014783 | +0.00% | -0.35% | -0.56% | 92,560 | 49,456 | 262.17 |

## Last 24h flow

45 trades by 32 coldkeys · 17 buys (22.46 τ) / 28 sells (168.82 τ) · net -146.36 τ

## News

- 2026-10-16 · commit · [Pause environment tournament scheduling while keeping emissions. (#1398)](https://github.com/gradients-ai/G.O.D/commit/27bc1a974cddfc56f71998e9ee54f021fa1556a8) — gradients-ai/G.O.D
- 2026-10-16 · commit · [Send GitHub token as the password in authenticated clone URLs (#1396)](https://github.com/gradients-ai/G.O.D/commit/bc182594a93eef79e502a678c450b6c17a3578b3) — gradients-ai/G.O.D
- 2026-10-15 · commit · [Raise training overhead for <1B and 1-4B model bands to 0.4h and 0.6h…](https://github.com/gradients-ai/G.O.D/commit/0b22808d7db2b20cde49851c8c9650875e50338d) — gradients-ai/G.O.D
- 2026-10-14 · commit · [Scale training overhead hours by model size (#1392)](https://github.com/gradients-ai/G.O.D/commit/d1f18afd4831b22c96a81931f301978b4313cbef) — gradients-ai/G.O.D
- 2026-10-09 · commit · [replacement for DQ (#1394)](https://github.com/gradients-ai/G.O.D/commit/de5ea9cd90bfff32d59a8403cb62433a8ce46bab) — gradients-ai/G.O.D
- 2026-10-07 · commit · [Fix/70b runpod eval path (#1393)](https://github.com/gradients-ai/G.O.D/commit/b49ba8644a0d705e9b035746eceaa05614742934) — gradients-ai/G.O.D
- 2026-10-05 · commit · [cheat check moved to round 2 (#1391)](https://github.com/gradients-ai/G.O.D/commit/e524ce62ce09b14b1cebdf169b46f114cdbaabf3) — gradients-ai/G.O.D
- 2026-10-02 · commit · [Oversampled pool: text-only <=4B models (#1388)](https://github.com/gradients-ai/G.O.D/commit/df81121a9b56f7d5f2a4261038aad8b6a93af56a) — gradients-ai/G.O.D

## Use

```bash
m subnets.sn56/info        # live identity + market (snapshot if bt is down)
m subnets.sn56/news        # scraped news
m subnets.sn56/trades      # 24h alpha tape
m subnets.sn56/daily       # daily candles
python3 orbit/subnets/sn56/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
