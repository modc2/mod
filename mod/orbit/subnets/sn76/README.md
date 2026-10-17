# sn76 — Ormas ن

Ormas is an Outcomes API for coding work on Bittensor subnet 76, in development. Clients post a change and the test that proves it; miners quote a firm price for the passing result and deliver a branch. Miners will be paid only when independent validators accept the delivery, and clients will be charged the accepted quote and nothing on a miss. Protocol, thin client, reference miner and reference validator: MIT.

Bittensor subnet **76** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/heroncovelabs/ormas-subnet) · [url](https://ormas.ai) · discord `ormasheroncovelabs_43871`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005032 | +0.00% | +1.58% | -0.92% | 1,461 | 466.6343 | 39.37 |

## Last 24h flow

23 trades by 14 coldkeys · 11 buys (21.51 τ) / 12 sells (12.96 τ) · net 8.55 τ

## News

- 2026-10-14 · commit · [Merge pull request #44 from heroncovelabs/fix/accept-deployed-catalog…](https://github.com/heroncovelabs/ormas-subnet/commit/e732c923a81a98b6be98e0dc928a8df5aa914aba) — heroncovelabs/ormas-subnet
- 2026-10-14 · commit · [docs: clarify qualification and queued-job upgrade guidance](https://github.com/heroncovelabs/ormas-subnet/commit/8eed2b9999e9dada78d260b830e6e95a6abb22b7) — heroncovelabs/ormas-subnet
- 2026-10-14 · commit · [fix: accept deployed catalog with scoped draining compiler](https://github.com/heroncovelabs/ormas-subnet/commit/b370cbd1d5103cd256a5fd32d2164a483e0e22d1) — heroncovelabs/ormas-subnet
- 2026-10-14 · commit · [Merge pull request #41 from heroncovelabs/codex/security-public-combi…](https://github.com/heroncovelabs/ormas-subnet/commit/ec10e9b6e2fad6ba7d5c5a73c0bcb34ff828f116) — heroncovelabs/ormas-subnet
- 2026-10-14 · commit · [security: harden public miner and validator boundaries](https://github.com/heroncovelabs/ormas-subnet/commit/c4652f383a5e4f86d2d7bb1b6eaf8e01d9044acd) — heroncovelabs/ormas-subnet
- 2026-10-14 · commit · [Merge pull request #40 from heroncovelabs/codex/security-publication-…](https://github.com/heroncovelabs/ormas-subnet/commit/76df5635e02737fadc95aa4fea1271ad2b3bec60) — heroncovelabs/ormas-subnet
- 2026-10-14 · commit · [security: enforce publication path scope](https://github.com/heroncovelabs/ormas-subnet/commit/57b8da81ca4f561109faf3330cf11ff97fac8584) — heroncovelabs/ormas-subnet
- 2026-10-14 · commit · [Merge pull request #39 from heroncovelabs/codex/security-acceptance-d…](https://github.com/heroncovelabs/ormas-subnet/commit/65f14f203fdd611d87928ac2e003c273ba6534a1) — heroncovelabs/ormas-subnet

## Use

```bash
m subnets.sn76/info        # live identity + market (snapshot if bt is down)
m subnets.sn76/news        # scraped news
m subnets.sn76/trades      # 24h alpha tape
m subnets.sn76/daily       # daily candles
python3 orbit/subnets/sn76/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
