# sn13 — Data Universe ν

Scraping the world's social media data

Bittensor subnet **13** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-10 (block 9250674).

Links: [github](https://github.com/macrocosm-os/data-universe) · [url](https://datauniverse.macrocosmos.ai/) · [discord](https://discord.gg/adsQPnFRY)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004441 | -0.01% | -0.15% | -5.76% | 27,645 | 17,806 | 17.44 |

## Last 24h flow

366 trades by 134 coldkeys · 1 buys (1.47 τ) / 365 sells (14.99 τ) · net -13.52 τ

## News

- 2026-10-09 · commit · [Merge pull request #924 from macrocosm-os/dev](https://github.com/macrocosm-os/data-universe/commit/bb261aaec9a9a3153c90ccd5981553b4f5bf8561) — macrocosm-os/data-universe
- 2026-10-09 · release · [Release v1.18.74](https://github.com/macrocosm-os/data-universe/releases/tag/v1.18.74) — macrocosm-os/data-universe
- 2026-10-09 · commit · [Merge pull request #922 from macrocosm-os/fix/reddit-fresh-post-valid…](https://github.com/macrocosm-os/data-universe/commit/3a8246be8a70cf4e07b9289d9dbc9801d405bf5e) — macrocosm-os/data-universe
- 2026-10-08 · commit · [fix(reddit): stop failing fresh posts against the archive snapshot](https://github.com/macrocosm-os/data-universe/commit/503109734825a91eba57d11ebb821a4cb384d46b) — macrocosm-os/data-universe
- 2026-10-01 · release · [Release v1.18.73](https://github.com/macrocosm-os/data-universe/releases/tag/v1.18.73) — macrocosm-os/data-universe
- 2026-10-01 · commit · [Merge pull request #921 from macrocosm-os/dev](https://github.com/macrocosm-os/data-universe/commit/af8442153cc3f956b90d3e6aa179a14cb9cd6cb8) — macrocosm-os/data-universe
- 2026-10-01 · commit · [Merge pull request #919 from macrocosm-os/fix/job-window-full-scan](https://github.com/macrocosm-os/data-universe/commit/ffa8c3ef22130f3a5a0c95e52a71b6396c852d51) — macrocosm-os/data-universe
- 2026-10-01 · commit · [fix(s3): run the job-window scan only on locally cached files](https://github.com/macrocosm-os/data-universe/commit/15e731b7eeb37b5ea1e70e6ed138905d0d740828) — macrocosm-os/data-universe

## Use

```bash
m subnets.sn13/info        # live identity + market (snapshot if bt is down)
m subnets.sn13/news        # scraped news
m subnets.sn13/trades      # 24h alpha tape
m subnets.sn13/daily       # daily candles
python3 orbit/subnets/sn13/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
