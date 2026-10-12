# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005753 | -0.00% | +1.11% | -5.49% | 32,393 | 17,403 | 263.16 |

## Last 24h flow

52 trades by 28 coldkeys · 9 buys (179.62 τ) / 43 sells (80.00 τ) · net 99.62 τ

## News

- 2026-10-11 · commit · [Merge pull request #349 from reliquadotai/feat/corpus-pay-route](https://github.com/reliquadotai/reliquary/commit/b1698c62da8226194a2b9bc851aebc2bb3d7b23d) — reliquadotai/reliquary
- 2026-10-11 · commit · [feat(corpus): GET /corpus/pay estimates each active job's pay per token](https://github.com/reliquadotai/reliquary/commit/e380e2e0e4c4e7d12b8c010f7cc86df3675afb59) — reliquadotai/reliquary
- 2026-10-11 · commit · [Merge pull request #348 from reliquadotai/feat/corpus-token-price-cei…](https://github.com/reliquadotai/reliquary/commit/03678e182dc4025138393061bff134018127b09b) — reliquadotai/reliquary
- 2026-10-11 · commit · [test(corpus): the settlement state now records period_paid](https://github.com/reliquadotai/reliquary/commit/97a92d47a4d72b06ba6ff5694a3a5be368aeeb25) — reliquadotai/reliquary
- 2026-10-11 · commit · [feat(corpus): a period pays at most a price per verified token](https://github.com/reliquadotai/reliquary/commit/8c5f94815ce1eda924c80931639cb354d50463dc) — reliquadotai/reliquary
- 2026-10-11 · commit · [feat(cli): tasks set-cap sets or clears a corpus task's token price](https://github.com/reliquadotai/reliquary/commit/259c287bb9e66acd1586f0886c5b6e4957d35f48) — reliquadotai/reliquary
- 2026-10-11 · commit · [feat(corpus): running settlers follow the price; the job status shows it](https://github.com/reliquadotai/reliquary/commit/64fcd47b40e7047c4d034f66764603722b028c77) — reliquadotai/reliquary
- 2026-10-11 · commit · [Merge pull request #347 from reliquadotai/fix/corpus-export-finished-…](https://github.com/reliquadotai/reliquary/commit/0deae4f17851506a3deefb7cd81d909910b32d49) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
