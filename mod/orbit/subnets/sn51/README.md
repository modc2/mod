# sn51 — lium.io ת

Agent-first GPU cloud. Rent NVIDIA GPUs via web, CLI, SDK, or API. Providers earn 95% rental revenue + bonuses.

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · [discord](https://discord.gg/lium)

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.109540 | +0.03% | +0.42% | +8.58% | 673,361 | 178,708 | 9,181 |

## Last 24h flow

631 trades by 282 coldkeys · 282 buys (4,383 τ) / 349 sells (4,389 τ) · net -5.65 τ

## News

- 2026-10-16 · commit · [DAH-3980 - Validator: new loopback volumes on a v2 plugin installed b…](https://github.com/Datura-ai/lium-io/commit/e92272001c261250efd0d9a5038f94a8c407c07b) — Datura-ai/lium-io
- 2026-10-16 · commit · [DAH-4223 - \[P1\] validator: rented Spot nodes earn rental incentive at…](https://github.com/Datura-ai/lium-io/commit/991543da093228a29b0c3463feb24fc8584fd776) — Datura-ai/lium-io
- 2026-10-15 · release · [executor-v1.141](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.141) — Datura-ai/lium-io
- 2026-10-15 · commit · [DAH-4484 - \[P0\] Executor: send restore/backup progress at most every …](https://github.com/Datura-ai/lium-io/commit/5456d2a128f616bae177fc60a42acd3486686915) — Datura-ai/lium-io
- 2026-10-15 · commit · [DAH-4465 validator: fail an unrented node whose busy GPU is thermally…](https://github.com/Datura-ai/lium-io/commit/ecff658898e9babdf5b801d5ec2d84ac667263cb) — Datura-ai/lium-io
- 2026-10-15 · commit · [DAH-4257 - \[P1\] Run the GPU kernel-fault probe in shadow by default (…](https://github.com/Datura-ai/lium-io/commit/95d62153ba5cd55cdf4fb6041b1f66f3ebbf1cc2) — Datura-ai/lium-io
- 2026-10-15 · commit · [DAH-4278 filler sample skips all-cards probe; sample budget ge 4096 (…](https://github.com/Datura-ai/lium-io/commit/075461308a2d39d4e77e918c7fb32fbca460f036) — Datura-ai/lium-io
- 2026-10-15 · commit · [validator: name the reason when a rented node scores 0 because Sysbox…](https://github.com/Datura-ai/lium-io/commit/93af489f39eeb07e8ba632ac4d974c3d25b154d9) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
