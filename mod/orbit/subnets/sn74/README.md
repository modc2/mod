# sn74 — Gittensor ل

autonomous software development

Bittensor subnet **74** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/entrius/gittensor/tree/main) · [url](https://gittensor.io) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003157 | -0.00% | -0.39% | -1.89% | 16,811 | 5,894 | 79.17 |

## Last 24h flow

13 trades by 11 coldkeys · 2 buys (33.83 τ) / 11 sells (44.20 τ) · net -10.36 τ

## News

- 2026-10-06 · commit · [Pay: H100 row at the same posture as the 5090 against Lium's anchor (…](https://github.com/entrius/gittensor/commit/992cdf3255963ae629e7b442f10c5acf74efbb06) — entrius/gittensor
- 2026-10-06 · commit · [Pay rows for the six new GPU types (#1810)](https://github.com/entrius/gittensor/commit/ebcc20e6ac3b2cf5f6c8e87cd91cb309318cdc99) — entrius/gittensor
- 2026-10-06 · commit · [Compute pool at 30% of miner emissions (#1809)](https://github.com/entrius/gittensor/commit/a8180b86ccde35c829f828fd36d195073c2fb48a) — entrius/gittensor
- 2026-10-06 · commit · [Sync the NVML driver allowlist with Lium's (36 drivers added) (#1808)](https://github.com/entrius/gittensor/commit/19920531f50264f575f1c0850f474d77291530b7) — entrius/gittensor
- 2026-10-05 · commit · [Remove the compute v0 image builds (sparkinfer image, gt-checker, ser…](https://github.com/entrius/gittensor/commit/46d87715e78e59eb9f79542c9dff35561260982b) — entrius/gittensor
- 2026-10-02 · commit · [Remove compute (#1803)](https://github.com/entrius/gittensor/commit/c0d9dc8ddd7e4a0f95b8028df4ca4f52e8d7f4ca) — entrius/gittensor
- 2026-10-02 · release · [release-20261002-144641](https://github.com/entrius/gittensor/releases/tag/release-20261002-144641) — entrius/gittensor
- 2026-10-01 · commit · [chore(weights): slow sparkinfer's time decay to a 7-day midpoint with…](https://github.com/entrius/gittensor/commit/ba87ddec7db7410df2fa94737e649ce029447859) — entrius/gittensor

## Use

```bash
m subnets.sn74/info        # live identity + market (snapshot if bt is down)
m subnets.sn74/news        # scraped news
m subnets.sn74/trades      # 24h alpha tape
m subnets.sn74/daily       # daily candles
python3 orbit/subnets/sn74/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
