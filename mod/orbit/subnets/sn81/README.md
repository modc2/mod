# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005853 | +0.13% | -0.29% | -4.67% | 32,784 | 17,552 | 1,250 |

## Last 24h flow

98 trades by 52 coldkeys · 40 buys (612.30 τ) / 58 sells (636.00 τ) · net -23.70 τ

## News

- 2026-10-08 · commit · [fix: preserve accepted records across interrupted storage writes (#339)](https://github.com/reliquadotai/reliquary/commit/c6c0a34442a1c84b20e5f437aa684e77b00430ed) — reliquadotai/reliquary
- 2026-10-07 · commit · [Fix operator generation classification](https://github.com/reliquadotai/reliquary/commit/38351defbd179e62dbeee419ff46ef4250f9d526) — reliquadotai/reliquary
- 2026-10-07 · commit · [Fix pinned operator generation task admission](https://github.com/reliquadotai/reliquary/commit/9c0dddd0feacdb6fedbcf072b73fe0c1c9e30e99) — reliquadotai/reliquary
- 2026-10-07 · commit · [Merge pull request #337 from reliquadotai/fix/refresh-index-grade-only](https://github.com/reliquadotai/reliquary/commit/234b7c7ddcd7ba68b6fe5577d5dcfc32e6e77262) — reliquadotai/reliquary
- 2026-10-07 · commit · [fix(corpus): refresh the git index in grade boxes only, never in repl…](https://github.com/reliquadotai/reliquary/commit/f6734803d0df91236a9c9b77d2a2f3bf07fcc389) — reliquadotai/reliquary
- 2026-10-07 · commit · [Merge pull request #336 from reliquadotai/fix/grade-box-refresh-index](https://github.com/reliquadotai/reliquary/commit/3c1ecf0269ed836187fc556f8225f5c78ec84760) — reliquadotai/reliquary
- 2026-10-07 · commit · [perf(corpus): refresh a grade box's git index before the env prepares it](https://github.com/reliquadotai/reliquary/commit/f062b8f784e2de959e657b978290b3d9fe612471) — reliquadotai/reliquary
- 2026-10-07 · commit · [Merge pull request #335 from reliquadotai/feat/register-reliquary-har…](https://github.com/reliquadotai/reliquary/commit/f32b9c4613f3faf9adf760704b19a13f9554882e) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
