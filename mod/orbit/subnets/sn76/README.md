# sn76 — Ormas ن

Ormas is an Outcomes API for coding work on Bittensor subnet 76, in development. Clients post a change and the test that proves it; miners quote a firm price for the passing result and deliver a branch. Miners will be paid only when independent validators accept the delivery, and clients will be charged the accepted quote and nothing on a miss. Protocol, thin client, reference miner and reference validator: MIT.

Bittensor subnet **76** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/heroncovelabs/ormas-subnet) · [url](https://ormas.ai) · discord `ormasheroncovelabs_43871`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006004 | -0.33% | -5.86% | +12.36% | 1,615 | 509.6897 | 551.26 |

## Last 24h flow

165 trades by 48 coldkeys · 97 buys (267.88 τ) / 68 sells (278.86 τ) · net -10.98 τ

## News

- 2026-10-11 · commit · [Merge pull request #34 from heroncovelabs/btl/onboarding-register-bef…](https://github.com/heroncovelabs/ormas-subnet/commit/f8ad351e0934a270af6f0f6a9d84bf1d961bcc96) — heroncovelabs/ormas-subnet
- 2026-10-11 · commit · [Fix miner registration-first onboarding](https://github.com/heroncovelabs/ormas-subnet/commit/4c422ee0a7dcae540015fd20b258159cf7f70f74) — heroncovelabs/ormas-subnet
- 2026-10-11 · commit · [Merge pull request #33 from heroncovelabs/sync/protected-split-pins-2…](https://github.com/heroncovelabs/ormas-subnet/commit/e4d084ce2d07c1495c526f8bb105c1df1fe10a87) — heroncovelabs/ormas-subnet
- 2026-10-11 · commit · [Merge pull request #32 from heroncovelabs/sync/protected-pluggable-wo…](https://github.com/heroncovelabs/ormas-subnet/commit/aa8e0ba979bdb11cc5703f077cf6447a8f69262c) — heroncovelabs/ormas-subnet
- 2026-10-11 · commit · [Sync: compose_hash.py pin-state and transition-policy fixes (f46f6d46…](https://github.com/heroncovelabs/ormas-subnet/commit/22ff0d0e8d3940dde024fd8fd4debf2197a2a6d3) — heroncovelabs/ormas-subnet
- 2026-10-11 · commit · [Sync from monorepo public_subnet: pinned split shell recipe (docker-c…](https://github.com/heroncovelabs/ormas-subnet/commit/1df6d9a13901e414c6f3274d2ac643a1823e2d0f) — heroncovelabs/ormas-subnet
- 2026-10-10 · commit · [Sync from monorepo public_subnet: Protected pluggable worker contract…](https://github.com/heroncovelabs/ormas-subnet/commit/bf843fa9fd2817267881fcf1bfd3e502dc95a6b4) — heroncovelabs/ormas-subnet
- 2026-10-09 · commit · [Pool deliveries: claim-refusal backoff + claim_health(); validator --…](https://github.com/heroncovelabs/ormas-subnet/commit/c9a03f33fc5dbf5cf9a32cea5d1251aba8e8a311) — heroncovelabs/ormas-subnet

## Use

```bash
m subnets.sn76/info        # live identity + market (snapshot if bt is down)
m subnets.sn76/news        # scraped news
m subnets.sn76/trades      # 24h alpha tape
m subnets.sn76/daily       # daily candles
python3 orbit/subnets/sn76/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
