# sn82 — Noah ᚢ

Bittensor subnet **82** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/Vora-Inc/NOAH)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.052058 | — | +0.09% | +110.92% | 9,788 | 2,538 | 1,511 |

## Last 24h flow

51 trades by 24 coldkeys · 23 buys (756.29 τ) / 28 sells (721.31 τ) · net 34.98 τ

## News

- 2026-10-18 · release · [v0.1.0 — Miner and validator source release](https://github.com/Vora-Inc/NOAH/releases/tag/v0.1.0) — Vora-Inc/NOAH
- 2026-10-18 · commit · [Release v0.1.0: multisig receipts and surplus-bonus scoring](https://github.com/Vora-Inc/NOAH/commit/bf171203f5149ec15d3c8a9cc85c47519bd5bdb6) — Vora-Inc/NOAH
- 2026-10-18 · commit · [Release multisig receipts and surplus-bonus scoring documentation](https://github.com/Vora-Inc/NOAH/commit/b593f181e7299ba05fb1d3e3ed447b92c9872e96) — Vora-Inc/NOAH
- 2026-10-17 · commit · [Harden release dependencies and configuration backups](https://github.com/Vora-Inc/NOAH/commit/44b04ce16e0561d957efb623a1d55bc812fd20a1) — Vora-Inc/NOAH
- 2026-10-16 · commit · [Initial public Noah miner and validator release](https://github.com/Vora-Inc/NOAH/commit/fc50abe33cfe2f28ef722d2e41701fccbd640468) — Vora-Inc/NOAH

## Use

```bash
m subnets.sn82/info        # live identity + market (snapshot if bt is down)
m subnets.sn82/news        # scraped news
m subnets.sn82/trades      # 24h alpha tape
m subnets.sn82/daily       # daily candles
python3 orbit/subnets/sn82/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
