# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004872 | +0.01% | +0.69% | +30.79% | 28,797 | 5,558 | 1,218 |

## Last 24h flow

194 trades by 89 coldkeys · 106 buys (618.57 τ) / 88 sells (598.34 τ) · net 20.24 τ

## News

- 2026-10-12 · commit · [Merge pull request #381 from leadpoet/fix/arena-oct12-credit-refusal-…](https://github.com/leadpoet/leadpoet/commit/47a54e3352322ed5d5747059865a7eaa634644b8) — leadpoet/leadpoet
- 2026-10-12 · commit · [Verify miner credit diagnostics through PostgreSQL reconciliation](https://github.com/leadpoet/leadpoet/commit/52088b590f52496399527bc4714d4fb7e56a8f3d) — leadpoet/leadpoet
- 2026-10-12 · commit · [Distinguish Deepline credit refusal diagnostics from fee finality](https://github.com/leadpoet/leadpoet/commit/8f2de028b706f6b1345a00619c19a3a887928d5d) — leadpoet/leadpoet
- 2026-10-11 · commit · [Merge pull request #380 from leadpoet/fix/oct11-sponsor-classificatio…](https://github.com/leadpoet/leadpoet/commit/4d5a0cb357927e7228e4ef1f947dedc8aea7c1d1) — leadpoet/leadpoet
- 2026-10-11 · commit · [Accept bounded sponsor classification certification wording](https://github.com/leadpoet/leadpoet/commit/aaf49cb8a76d06fa272c51c20b60608384ae6fac) — leadpoet/leadpoet
- 2026-10-11 · commit · [Merge pull request #379 from leadpoet/fix/oct11-openrouter-terminal-b…](https://github.com/leadpoet/leadpoet/commit/b8fa6509f5ef3ea3a6f4a5cd2851dfd4fa8e4d51) — leadpoet/leadpoet
- 2026-10-11 · commit · [Limit synchronous OpenRouter billing reads for complete errors](https://github.com/leadpoet/leadpoet/commit/4b0f888a8606a35d1da02a94d504d197f24103e8) — leadpoet/leadpoet
- 2026-10-11 · commit · [Merge pull request #378 from leadpoet/codex/oct11-deepline-payment-na…](https://github.com/leadpoet/leadpoet/commit/0ac51b535a3392ff78f18ce4564a8c4f9c0eb6e9) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
