# sn8 — Vanta θ

The first decentralized & trustless liquidity and execution engine for prop firms and traders

Bittensor subnet **8** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/taoshidev/vanta-network) · [url](https://www.vantanetwork.io/) · discord `tl_arrash`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.029510 | +0.01% | -0.21% | +1.69% | 183,351 | 82,204 | 891.07 |

## Last 24h flow

128 trades by 72 coldkeys · 46 buys (292.54 τ) / 82 sells (472.51 τ) · net -179.97 τ

## News

- 2026-10-05 · commit · [account size endpoint added (#946)](https://github.com/taoshidev/vanta-network/commit/618d5e43f059a4dd08eee67275db0e20eb275837) — taoshidev/vanta-network
- 2026-10-05 · commit · [After hours trading (#943) (#945)](https://github.com/taoshidev/vanta-network/commit/f4f7fc6b4975406514c830dcb9d56ccdb0fdeddd) — taoshidev/vanta-network
- 2026-10-02 · commit · [Merge pull request #944 from taoshidev/development](https://github.com/taoshidev/vanta-network/commit/a72b71db58fa6a5251e25c55a4b81067fd95c5cc) — taoshidev/vanta-network
- 2026-10-02 · commit · [Implement user selectable intraday drawdown threshold (daily loss lim…](https://github.com/taoshidev/vanta-network/commit/198c4db21a59d8b9cbd42d423ea9db393f10cebf) — taoshidev/vanta-network
- 2026-10-01 · commit · [Fix parse appropriate price (#941)](https://github.com/taoshidev/vanta-network/commit/c8751fba0d8ec67d367e613940aaccc731f323c7) — taoshidev/vanta-network
- 2026-09-30 · commit · [Fix to Reduce REST API Lags Due to Lock Contention (#935)](https://github.com/taoshidev/vanta-network/commit/1d090d2473bef0de4d70a450578bb4dbdafbeadd) — taoshidev/vanta-network
- 2026-09-30 · commit · [Merge pull request #927 from taoshidev/development](https://github.com/taoshidev/vanta-network/commit/747411e04e9221da2a7f01ec707e56e254d95248) — taoshidev/vanta-network
- 2026-09-30 · commit · [increase timeout for chain calls, prevent double slash from repeated …](https://github.com/taoshidev/vanta-network/commit/acc6cd517e0f9294da9d4ef40d2d7728afc664be) — taoshidev/vanta-network

## Use

```bash
m subnets.sn8/info        # live identity + market (snapshot if bt is down)
m subnets.sn8/news        # scraped news
m subnets.sn8/trades      # 24h alpha tape
m subnets.sn8/daily       # daily candles
python3 orbit/subnets/sn8/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
