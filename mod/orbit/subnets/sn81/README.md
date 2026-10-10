# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005607 | -0.01% | -3.76% | -7.11% | 31,490 | 17,181 | 499.14 |

## Last 24h flow

51 trades by 32 coldkeys · 7 buys (83.42 τ) / 44 sells (414.92 τ) · net -331.50 τ

## News

- 2026-10-09 · commit · [Merge pull request #345 from reliquadotai/feat/register-reliquary-com…](https://github.com/reliquadotai/reliquary/commit/2701f005d269db9351484dcbc88580d4a84006bb) — reliquadotai/reliquary
- 2026-10-09 · commit · [feat(env): register reliquary-competitive-code as an SFT-only corpus …](https://github.com/reliquadotai/reliquary/commit/75bff4c668f609979ee3e1a41a8d6725f45da2bc) — reliquadotai/reliquary
- 2026-10-09 · commit · [feat(env): score reliquary/stdio-program/v1 sources in the sandbox](https://github.com/reliquadotai/reliquary/commit/0a76dc42e2bf084944ca23395327ded8982d4f1f) — reliquadotai/reliquary
- 2026-10-09 · commit · [feat(grader): run a whole stdin program in the sandbox (stdio mode)](https://github.com/reliquadotai/reliquary/commit/7742b187851470c9a3b31f11ba00a21f1645e447) — reliquadotai/reliquary
- 2026-10-09 · commit · [Merge pull request #344 from reliquadotai/fix/expired-leases-revoke-n…](https://github.com/reliquadotai/reliquary/commit/458c6a690a0c27340bb406ee59e44da4cae0cf02) — reliquadotai/reliquary
- 2026-10-09 · commit · [fix(executors): expired leases bench an executor, never quarantine it](https://github.com/reliquadotai/reliquary/commit/8df9d2cc447778e88287cab5e2fe367e216b9e03) — reliquadotai/reliquary
- 2026-10-09 · commit · [Merge pull request #342 from reliquadotai/fix/task-catalog-artifact-r…](https://github.com/reliquadotai/reliquary/commit/3d45493915cfdd87124e4dfc9ef7208d906703d3) — reliquadotai/reliquary
- 2026-10-09 · commit · [Merge pull request #341 from reliquadotai/feat/corpus-period-only-aut…](https://github.com/reliquadotai/reliquary/commit/c356b073a087a07ef98c5a52f4eb0c3b439ae7f2) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
