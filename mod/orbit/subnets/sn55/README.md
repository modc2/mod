# sn55 — NIOME ث

NIOME is a decentralized AI subnet that enables privacy-safe genomic intelligence by replacing real human genomes with high-fidelity synthetic genomic profiles

Bittensor subnet **55** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/genomesio/subnet-niome) · [url](https://niome.genomes.io) · [discord](https://discord.gg/7mJkaJZX)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002653 | -0.05% | +24.71% | +13.19% | 15,235 | 6,545 | 6,370 |

## Last 24h flow

616 trades by 123 coldkeys · 324 buys (3,527 τ) / 292 sells (2,840 τ) · net 687.29 τ

## News

- 2026-10-19 · commit · [pass receiver ss58](https://github.com/genomesio/subnet-niome/commit/64b946ee6cb3abadaa4aa73699750e5bb0029524) — genomesio/subnet-niome
- 2026-10-18 · commit · [download file with retry logic](https://github.com/genomesio/subnet-niome/commit/4625b3c6e32edbed043a2940370def7de0d2daaf) — genomesio/subnet-niome
- 2026-10-18 · commit · [set weights using valid scores](https://github.com/genomesio/subnet-niome/commit/4f288294f219d9b343d1c4e04bf798f050b82aa7) — genomesio/subnet-niome
- 2026-10-18 · commit · [reduce burning rate to 0.1%](https://github.com/genomesio/subnet-niome/commit/21942bd1c9b3097cae21582ebb5ecc4a04cc2d2e) — genomesio/subnet-niome
- 2026-10-17 · commit · [improve uploading flow](https://github.com/genomesio/subnet-niome/commit/21ed3f0cb0f739dcfcdad7e39f535a08fc4be9de) — genomesio/subnet-niome
- 2026-10-17 · commit · [add google drive provider](https://github.com/genomesio/subnet-niome/commit/017aaae607ef784fccbb962e2bc9393f4ef59274) — genomesio/subnet-niome
- 2026-09-23 · commit · [use block hashes to generate seeds](https://github.com/genomesio/subnet-niome/commit/9d9347a7ffab85a04eda6c36b9e87c59c8bb4049) — genomesio/subnet-niome

## Use

```bash
m subnets.sn55/info        # live identity + market (snapshot if bt is down)
m subnets.sn55/news        # scraped news
m subnets.sn55/trades      # 24h alpha tape
m subnets.sn55/daily       # daily candles
python3 orbit/subnets/sn55/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
