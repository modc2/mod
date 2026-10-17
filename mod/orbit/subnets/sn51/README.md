# sn51 — lium.io ת

Agent-first GPU cloud. Rent NVIDIA GPUs via web, CLI, SDK, or API. Providers earn 95% rental revenue + bonuses.

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · [discord](https://discord.gg/lium)

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.108935 | +0.00% | -0.55% | +7.01% | 670,500 | 178,317 | 5,910 |

## Last 24h flow

551 trades by 283 coldkeys · 235 buys (2,332 τ) / 316 sells (3,170 τ) · net -837.10 τ

## News

- 2026-10-16 · commit · [DAH-4123 - Validator: split build-log streams into frames the backend…](https://github.com/Datura-ai/lium-io/commit/0f97a146cb34cf047fae296acb1c8c7100d83299) — Datura-ai/lium-io
- 2026-10-16 · commit · [DAH-4434 - \[P1\] Inspector: rebuilt library with longer deadlines; 120…](https://github.com/Datura-ai/lium-io/commit/d43eecdb3807f15ee7e0cd84eed12d8b6ee66416) — Datura-ai/lium-io
- 2026-10-16 · commit · [Revert #1543: v2 loopback plugin install drops SSH on some hosts (#1656)](https://github.com/Datura-ai/lium-io/commit/7822e3a65c69926f7fd8f427b568d410166cdfa0) — Datura-ai/lium-io
- 2026-10-16 · commit · [DAH-4362 - \[P3\] Validator: remove settings unchanged for more than 3 …](https://github.com/Datura-ai/lium-io/commit/e330417741e65783f4ccb0bddf03032ad35aeb0b) — Datura-ai/lium-io
- 2026-10-16 · commit · [DAH-3980 - Validator: new loopback volumes on a v2 plugin installed b…](https://github.com/Datura-ai/lium-io/commit/e92272001c261250efd0d9a5038f94a8c407c07b) — Datura-ai/lium-io
- 2026-10-16 · commit · [DAH-4223 - \[P1\] validator: rented Spot nodes earn rental incentive at…](https://github.com/Datura-ai/lium-io/commit/991543da093228a29b0c3463feb24fc8584fd776) — Datura-ai/lium-io
- 2026-10-15 · release · [executor-v1.141](https://github.com/Datura-ai/lium-io/releases/tag/executor-v1.141) — Datura-ai/lium-io
- 2026-10-15 · commit · [DAH-4484 - \[P0\] Executor: send restore/backup progress at most every …](https://github.com/Datura-ai/lium-io/commit/5456d2a128f616bae177fc60a42acd3486686915) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
