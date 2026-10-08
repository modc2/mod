# sn62 — Ridges ز

Software Engineering Agents

Bittensor subnet **62** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/ridgesai/ridges) · [url](https://www.ridges.ai/) · [discord](https://discord.gg/WeDvTnYDad)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.009636 | +0.01% | -0.05% | -7.73% | 54,736 | 28,614 | 141.27 |

## Last 24h flow

183 trades by 80 coldkeys · 127 buys (45.41 τ) / 56 sells (72.04 τ) · net -26.63 τ

## News

- 2026-10-03 · commit · [Merge pull request #518 from ridgesai/update/concurrency-endpoint](https://github.com/ridgesai/ridges/commit/ea43775c6e50a5c794ff878c32ecd541941d5451) — ridgesai/ridges
- 2026-10-03 · commit · [add tests](https://github.com/ridgesai/ridges/commit/a2a703908d85fb0aac201852af41065c719b2cae) — ridgesai/ridges
- 2026-10-03 · commit · [call new query](https://github.com/ridgesai/ridges/commit/3f0b73ae3300d67b0fde91be23ea5c1e2c0aaa61) — ridgesai/ridges
- 2026-10-03 · commit · [new query](https://github.com/ridgesai/ridges/commit/bd45321a58b9421d9e9db57fe43fe273e14199fc) — ridgesai/ridges
- 2026-10-03 · commit · [cache evals](https://github.com/ridgesai/ridges/commit/e3470c94e1aef842bf5338dd152b8581b6ffe3ba) — ridgesai/ridges
- 2026-10-02 · commit · [Merge pull request #517 from ridgesai/update/remove-time-limit-credits](https://github.com/ridgesai/ridges/commit/02a12dac06e3f60b47dd482e8791ccc46c56a257) — ridgesai/ridges
- 2026-10-02 · commit · [update tests](https://github.com/ridgesai/ridges/commit/e11c7e9f34802682809d9866c415b86088b1b9a3) — ridgesai/ridges
- 2026-10-02 · commit · [return remaining time properly](https://github.com/ridgesai/ridges/commit/df4b7fdce441ad39bbf0e66a82643bbbbe49d703) — ridgesai/ridges

## Use

```bash
m subnets.sn62/info        # live identity + market (snapshot if bt is down)
m subnets.sn62/news        # scraped news
m subnets.sn62/trades      # 24h alpha tape
m subnets.sn62/daily       # daily candles
python3 orbit/subnets/sn62/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
