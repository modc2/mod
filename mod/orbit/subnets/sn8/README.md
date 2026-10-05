# sn8 — Vanta θ

The first decentralized & trustless liquidity and execution engine for prop firms and traders

Bittensor subnet **8** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/taoshidev/vanta-network) · [url](https://www.vantanetwork.io/) · discord `tl_arrash`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.029573 | +0.01% | +0.42% | +0.22% | 183,497 | 82,262 | 494.38 |

## Last 24h flow

100 trades by 64 coldkeys · 46 buys (222.83 τ) / 54 sells (153.90 τ) · net 68.93 τ

## News

- 2026-10-02 · commit · [Merge pull request #944 from taoshidev/development](https://github.com/taoshidev/vanta-network/commit/a72b71db58fa6a5251e25c55a4b81067fd95c5cc) — taoshidev/vanta-network
- 2026-10-02 · commit · [Implement user selectable intraday drawdown threshold (daily loss lim…](https://github.com/taoshidev/vanta-network/commit/198c4db21a59d8b9cbd42d423ea9db393f10cebf) — taoshidev/vanta-network
- 2026-10-01 · commit · [Fix parse appropriate price (#941)](https://github.com/taoshidev/vanta-network/commit/c8751fba0d8ec67d367e613940aaccc731f323c7) — taoshidev/vanta-network
- 2026-09-30 · commit · [Fix to Reduce REST API Lags Due to Lock Contention (#935)](https://github.com/taoshidev/vanta-network/commit/1d090d2473bef0de4d70a450578bb4dbdafbeadd) — taoshidev/vanta-network
- 2026-09-30 · commit · [Merge pull request #927 from taoshidev/development](https://github.com/taoshidev/vanta-network/commit/747411e04e9221da2a7f01ec707e56e254d95248) — taoshidev/vanta-network
- 2026-09-30 · commit · [increase timeout for chain calls, prevent double slash from repeated …](https://github.com/taoshidev/vanta-network/commit/acc6cd517e0f9294da9d4ef40d2d7728afc664be) — taoshidev/vanta-network
- 2026-09-28 · commit · [Add EOD HWM Endpoint (#929)](https://github.com/taoshidev/vanta-network/commit/bb71cd98e92f738fe56ab0d57113313b6c6ce45d) — taoshidev/vanta-network
- 2026-09-28 · commit · [Fix immediate trigger path (#938)](https://github.com/taoshidev/vanta-network/commit/9753793ab069deca79981bdfd6637f8c7289dc66) — taoshidev/vanta-network

## Use

```bash
m subnets.sn8/info        # live identity + market (snapshot if bt is down)
m subnets.sn8/news        # scraped news
m subnets.sn8/trades      # 24h alpha tape
m subnets.sn8/daily       # daily candles
python3 orbit/subnets/sn8/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
