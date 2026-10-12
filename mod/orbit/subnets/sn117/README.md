# sn117 — everyframe.studio Ⲁ

Every frame gets craft

Bittensor subnet **117** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-12 (block 9265081).

Links: [github](https://github.com/everyframe-studios/everyframe-miner) · [url](https://everyframe.studio/) · [discord](https://discord.com/channels/799672011265015819/1544011415692644444)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002747 | -0.00% | +4.73% | +0.20% | 8,385 | 2,997 | 243.85 |

## Last 24h flow

50 trades by 25 coldkeys · 26 buys (156.20 τ) / 24 sells (87.06 τ) · net 69.14 τ

## News

- 2026-10-12 · release · [everycli v0.3.2](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.3.2) — everyframe-studios/everyframe-miner
- 2026-10-12 · commit · [feat(cli): show update download progress with JSON opt-in (#14)](https://github.com/everyframe-studio/everyframe-miner/commit/9e627ec2f2dd2f29c82cfc65d06214b589d5f220) — everyframe-studios/everyframe-miner
- 2026-10-12 · release · [everycli v0.3.1](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.3.1) — everyframe-studios/everyframe-miner
- 2026-10-12 · commit · [fix(cli): polish diagnostics, key management and setup guidance for v…](https://github.com/everyframe-studio/everyframe-miner/commit/f1c067f187b9c241a6a812bded7bac6978770e2c) — everyframe-studios/everyframe-miner
- 2026-10-11 · release · [everycli v0.3.0](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.3.0) — everyframe-studios/everyframe-miner
- 2026-10-11 · commit · [feat(cli): guided setup, command help, and actionable diagnostics (#12)](https://github.com/everyframe-studio/everyframe-miner/commit/e642060732aa52ff41fdc97b521288f46e65490d) — everyframe-studios/everyframe-miner
- 2026-10-11 · release · [everycli v0.2.5](https://github.com/everyframe-studio/everyframe-miner/releases/tag/v0.2.5) — everyframe-studios/everyframe-miner
- 2026-10-11 · commit · [fix: migrate everycli downloads to everyframe-studio (#11)](https://github.com/everyframe-studio/everyframe-miner/commit/f217f8b0e3e3d6164131b97c89fc5b8877af1f41) — everyframe-studios/everyframe-miner

## Use

```bash
m subnets.sn117/info        # live identity + market (snapshot if bt is down)
m subnets.sn117/news        # scraped news
m subnets.sn117/trades      # 24h alpha tape
m subnets.sn117/daily       # daily candles
python3 orbit/subnets/sn117/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
