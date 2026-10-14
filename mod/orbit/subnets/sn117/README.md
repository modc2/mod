# sn117 — everyframe.studio Ⲁ

Every frame gets craft

Bittensor subnet **117** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/everyframe-studios/everyframe-miner) · [url](https://everyframe.studio/) · [discord](https://discord.com/channels/799672011265015819/1544011415692644444)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002626 | -1.02% | -2.07% | -0.36% | 8,035 | 2,930 | 41.56 |

## Last 24h flow

10 trades by 6 coldkeys · 4 buys (5.35 τ) / 6 sells (32.58 τ) · net -27.23 τ

## News

- 2026-10-14 · release · [everycli v0.4.1](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.4.1) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [fix: native Vivix W-credit telemetry and zero-balance gating support …](https://github.com/everyframe-studio/everyframe-miner/commit/3856aad6b477b68166b966ee783599b95cfa1612) — everyframe-studios/everyframe-miner
- 2026-10-14 · commit · [fix: report safe provider rejections without retrying uncertain submi…](https://github.com/everyframe-studio/everyframe-miner/commit/ac2c0d9c67c6a2e1a4260c2fd58fd8d7e9b66581) — everyframe-studios/everyframe-miner
- 2026-10-13 · commit · [docs: clarify v0.4.0 worker upgrades and verified mining coverage (#24)](https://github.com/everyframe-studio/everyframe-miner/commit/6308225c18a016839accf88ca66f01e37699dd1f) — everyframe-studios/everyframe-miner
- 2026-10-13 · release · [everycli v0.4.0](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.4.0) — everyframe-studios/everyframe-miner
- 2026-10-13 · commit · [feat: add H3 reference, image and audio workers for v0.4.0 (#23)](https://github.com/everyframe-studio/everyframe-miner/commit/73a8b68c893b22d96b85cb2dbd901eb095c7ae3b) — everyframe-studios/everyframe-miner
- 2026-10-13 · release · [everycli v0.3.9](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.3.9) — everyframe-studios/everyframe-miner
- 2026-10-13 · commit · [feat: add settings-bound Studio video workers and live balances (v0.3…](https://github.com/everyframe-studio/everyframe-miner/commit/af5d03f4678e6987d8919f930aa1a8e51d1de190) — everyframe-studios/everyframe-miner

## Use

```bash
m subnets.sn117/info        # live identity + market (snapshot if bt is down)
m subnets.sn117/news        # scraped news
m subnets.sn117/trades      # 24h alpha tape
m subnets.sn117/daily       # daily candles
python3 orbit/subnets/sn117/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
