# sn94 — Cathedral ᚄ

CPU sandboxes powering reinforcement learning on Bittensor.

Bittensor subnet **94** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/cathedralai/cathedral-sandbox) · [url](https://cathedral.computer/) · [discord](https://discord.com/channels/799672011265015819/1526241812589711571)

Fleet mods for this subnet: `cathedral`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004321 | +0.62% | +1.28% | -4.38% | 11,522 | 2,249 | 668.46 |

## Last 24h flow

96 trades by 47 coldkeys · 48 buys (341.46 τ) / 48 sells (323.73 τ) · net 17.72 τ

## News

- 2026-10-16 · commit · [Merge pull request #277 from cathedralai/fix/gvisor-release-20261005](https://github.com/cathedralai/cathedral-sandbox/commit/ec992827b445e195142bc62e35ca9f6b2db12e93) — cathedralai/cathedral-sandbox
- 2026-10-16 · commit · [Merge pull request #281 from cathedralai/docs/sn94-snp-miner-dd7182c](https://github.com/cathedralai/cathedral-sandbox/commit/b65f8456d933b9c905c3464742f43c2c80219fab) — cathedralai/cathedral-sandbox
- 2026-10-16 · commit · [Pin the SN94 SNP miner image built from dd7182c](https://github.com/cathedralai/cathedral-sandbox/commit/d3fc6b1720c9bae7ca3e4d7f1450a97af59f25a9) — cathedralai/cathedral-sandbox
- 2026-10-16 · commit · [Merge pull request #278 from skyrocket2026/docs/miner-guide-sn94-images](https://github.com/cathedralai/cathedral-sandbox/commit/177cc73c750e6b6eb77d0631adf066cc33b81b46) — cathedralai/cathedral-sandbox
- 2026-10-16 · commit · [Merge pull request #280 from cathedralai/feature/miner-image-chain-te…](https://github.com/cathedralai/cathedral-sandbox/commit/dd7182ccd91ca25b566ff210025bdea5fda57c4c) — cathedralai/cathedral-sandbox
- 2026-10-12 · commit · [Merge pull request #254 from skyrocket2026/feat/verifier-release-next](https://github.com/cathedralai/cathedral-sandbox/commit/30bee005c62ed65595027e27a2447dbcef163bc5) — cathedralai/cathedral-sandbox
- 2026-10-12 · commit · [Merge pull request #253 from skyrocket2026/fix/verifier-review-followups](https://github.com/cathedralai/cathedral-sandbox/commit/5a57fb50ee5dabe012b7859d307cb5870c269a70) — cathedralai/cathedral-sandbox
- 2026-10-12 · commit · [release(tdx-verifier): expect the Go 1.26.9 build of the next release](https://github.com/cathedralai/cathedral-sandbox/commit/78f631209fc632d10f1e8d1bf73ad557f5c14830) — cathedralai/cathedral-sandbox

## Use

```bash
m subnets.sn94/info        # live identity + market (snapshot if bt is down)
m subnets.sn94/news        # scraped news
m subnets.sn94/trades      # 24h alpha tape
m subnets.sn94/daily       # daily candles
python3 orbit/subnets/sn94/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
