# sn62 — Ridges ز

Software Engineering Agents

Bittensor subnet **62** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/ridgesai/ridges) · [url](https://www.ridges.ai/) · [discord](https://discord.gg/WeDvTnYDad)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.010402 | -0.04% | -0.14% | -3.00% | 58,617 | 29,669 | 516.84 |

## Last 24h flow

259 trades by 96 coldkeys · 198 buys (218.48 τ) / 61 sells (257.12 τ) · net -38.63 τ

## News

- 2026-10-01 · release · [v0.3.9](https://github.com/ridgesai/ridges/releases/tag/v0.3.9) — ridgesai/ridges
- 2026-10-01 · commit · [Merge pull request #516 from ridgesai/update/preference-to-inprocess-…](https://github.com/ridgesai/ridges/commit/f3f2107639caa356b45dd9efbdc0fce08b728049) — ridgesai/ridges
- 2026-10-01 · release · [v0.3.8](https://github.com/ridgesai/ridges/releases/tag/v0.3.8) — ridgesai/ridges
- 2026-10-01 · commit · [Merge branch 'main' into update/preference-to-inprocess-valis](https://github.com/ridgesai/ridges/commit/8eb058f2592ec9518e8e80c5dc5004626b7ad578) — ridgesai/ridges
- 2026-10-01 · commit · [give in process valis preference](https://github.com/ridgesai/ridges/commit/915454ed775d765d0ff579d0e5df806ccdc6c6af) — ridgesai/ridges
- 2026-09-29 · commit · [Merge pull request #515 from ridgesai/feat/use-api-key-coingecko](https://github.com/ridgesai/ridges/commit/096feb7d18fe72de3f70847551d6a2327be06cee) — ridgesai/ridges
- 2026-09-29 · commit · [update config](https://github.com/ridgesai/ridges/commit/d37b15630f601dc4494b292db6ef1747d6e4d9e8) — ridgesai/ridges
- 2026-09-29 · commit · [update env example](https://github.com/ridgesai/ridges/commit/afa2cacecea441c898bd0633dfe8931357a36ea3) — ridgesai/ridges

## Use

```bash
m subnets.sn62/info        # live identity + market (snapshot if bt is down)
m subnets.sn62/news        # scraped news
m subnets.sn62/trades      # 24h alpha tape
m subnets.sn62/daily       # daily candles
python3 orbit/subnets/sn62/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
