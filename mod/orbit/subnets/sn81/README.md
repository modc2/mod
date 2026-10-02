# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006077 | -0.62% | +1.45% | +1.42% | 33,797 | 17,886 | 4,166 |

## Last 24h flow

215 trades by 80 coldkeys · 96 buys (2,148 τ) / 119 sells (2,007 τ) · net 141.17 τ

## News

- 2026-10-02 · commit · [Merge pull request #297 from reliquadotai/perf/corpus-tasks-instant](https://github.com/reliquadotai/reliquary/commit/dfa575d388e49427e737855c310d467bb08b3158) — reliquadotai/reliquary
- 2026-10-02 · commit · [perf(corpus): /corpus/tasks never waits on the registry](https://github.com/reliquadotai/reliquary/commit/1266c0101a8ea986a07546a4d31a19625cb02f3e) — reliquadotai/reliquary
- 2026-10-01 · commit · [Merge pull request #296 from reliquadotai/perf/corpus-settle-without-…](https://github.com/reliquadotai/reliquary/commit/2b3a5ea6ab2050bd9ba14db6fe6931a8dab2f413) — reliquadotai/reliquary
- 2026-10-01 · commit · [test(corpus): loosen the loop-gap bound so a busy box does not flake it](https://github.com/reliquadotai/reliquary/commit/1e4e345a61943a5afe63adcd1dc8583e6be56844) — reliquadotai/reliquary
- 2026-10-01 · commit · [perf(corpus): settle from the auditor's feed, list off the serving loop](https://github.com/reliquadotai/reliquary/commit/622d1b844e463156a77ab5fc0397f3e63e589791) — reliquadotai/reliquary
- 2026-10-01 · commit · [Merge pull request #295 from reliquadotai/perf/corpus-ledger-group-co…](https://github.com/reliquadotai/reliquary/commit/06de000af66c7218d09df7d166a0a04db6e16b82) — reliquadotai/reliquary
- 2026-10-01 · commit · [fix(corpus): a taken submission keeps its record write past a hang-up](https://github.com/reliquadotai/reliquary/commit/35d83976951698c61cac413467a16e729f058338) — reliquadotai/reliquary
- 2026-10-01 · commit · [perf(corpus): group-commit the ledger turn, one CAS write per batch](https://github.com/reliquadotai/reliquary/commit/c46a8a67904003b5fc0205bf8d6e29df63f5a2e7) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
