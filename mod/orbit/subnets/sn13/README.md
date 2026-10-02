# sn13 — Data Universe ν

Scraping the world's social media data

Bittensor subnet **13** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/macrocosm-os/data-universe) · [url](https://datauniverse.macrocosmos.ai/) · [discord](https://discord.gg/adsQPnFRY)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004716 | -0.00% | -0.11% | -7.04% | 29,098 | 18,349 | 32.12 |

## Last 24h flow

247 trades by 136 coldkeys · 8 buys (10.75 τ) / 239 sells (20.77 τ) · net -10.02 τ

## News

- 2026-10-01 · release · [Release v1.18.73](https://github.com/macrocosm-os/data-universe/releases/tag/v1.18.73) — macrocosm-os/data-universe
- 2026-10-01 · commit · [Merge pull request #921 from macrocosm-os/dev](https://github.com/macrocosm-os/data-universe/commit/af8442153cc3f956b90d3e6aa179a14cb9cd6cb8) — macrocosm-os/data-universe
- 2026-10-01 · commit · [Merge pull request #919 from macrocosm-os/fix/job-window-full-scan](https://github.com/macrocosm-os/data-universe/commit/ffa8c3ef22130f3a5a0c95e52a71b6396c852d51) — macrocosm-os/data-universe
- 2026-10-01 · commit · [fix(s3): run the job-window scan only on locally cached files](https://github.com/macrocosm-os/data-universe/commit/15e731b7eeb37b5ea1e70e6ed138905d0d740828) — macrocosm-os/data-universe
- 2026-10-01 · commit · [Merge pull request #920 from macrocosm-os/docs/agents-md](https://github.com/macrocosm-os/data-universe/commit/673f187e720fcc241c35a44960741cd5278a5c7a) — macrocosm-os/data-universe
- 2026-10-01 · commit · [Merge pull request #916 from macrocosm-os/improve-gravity-logging](https://github.com/macrocosm-os/data-universe/commit/8ea25391a773d295adaad4cd25688c9ef2a6827f) — macrocosm-os/data-universe
- 2026-09-02 · release · [Release v1.18.72](https://github.com/macrocosm-os/data-universe/releases/tag/v1.18.72) — macrocosm-os/data-universe

## Use

```bash
m subnets.sn13/info        # live identity + market (snapshot if bt is down)
m subnets.sn13/news        # scraped news
m subnets.sn13/trades      # 24h alpha tape
m subnets.sn13/daily       # daily candles
python3 orbit/subnets/sn13/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
