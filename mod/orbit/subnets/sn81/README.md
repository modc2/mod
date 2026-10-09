# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005826 | -0.01% | -0.45% | -3.17% | 32,679 | 17,513 | 582.65 |

## Last 24h flow

68 trades by 39 coldkeys · 15 buys (271.68 τ) / 53 sells (310.37 τ) · net -38.70 τ

## News

- 2026-10-09 · commit · [Merge pull request #342 from reliquadotai/fix/task-catalog-artifact-r…](https://github.com/reliquadotai/reliquary/commit/3d45493915cfdd87124e4dfc9ef7208d906703d3) — reliquadotai/reliquary
- 2026-10-09 · commit · [Merge pull request #341 from reliquadotai/feat/corpus-period-only-aut…](https://github.com/reliquadotai/reliquary/commit/c356b073a087a07ef98c5a52f4eb0c3b439ae7f2) — reliquadotai/reliquary
- 2026-10-09 · commit · [ci: allow setup and authenticity checks after the full CPU suite](https://github.com/reliquadotai/reliquary/commit/2955e096c14c962ed7a370de49380e42b19c9087) — reliquadotai/reliquary
- 2026-10-09 · commit · [Verify environment artifacts before advertising task availability](https://github.com/reliquadotai/reliquary/commit/db4df87e631a7c718f2e7ec5d7069f87dd2408bb) — reliquadotai/reliquary
- 2026-10-08 · commit · [style(weights): wrap a docstring line](https://github.com/reliquadotai/reliquary/commit/0c7ecbd8860c8926d2cc1f40c3ae3ff5e6f2e899) — reliquadotai/reliquary
- 2026-10-08 · commit · [Merge pull request #340 from reliquadotai/feat/corpus-open-prompts](https://github.com/reliquadotai/reliquary/commit/d30b9fd7cd80cadfcfc3f3a4b7ccc2d24b85375e) — reliquadotai/reliquary
- 2026-10-08 · commit · [docs(corpus): the open route and the agentic miner's use of it](https://github.com/reliquadotai/reliquary/commit/7f942a4bb927a70a45ab5cfc441f9418253d56f3) — reliquadotai/reliquary
- 2026-10-08 · commit · [feat(miner): the agentic miner skips prompts with no slot left](https://github.com/reliquadotai/reliquary/commit/69b4de9605710eb715c07a7b76e19072c0461e0c) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
