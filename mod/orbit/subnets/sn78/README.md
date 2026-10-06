# sn78 — Umi و

Universal motion to meaning

Bittensor subnet **78** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/Umi-BitSign/umi) · [url](https://www.umi.vision) · [discord](https://discord.gg/8pexneWef)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004219 | +3.73% | -6.42% | +23.95% | 6,347 | 1,546 | 805.58 |

## Last 24h flow

431 trades by 205 coldkeys · 138 buys (376.78 τ) / 293 sells (427.41 τ) · net -50.63 τ

## News

- 2026-10-06 · commit · [Restore C5 request delivery and retained-work recovery (#223)](https://github.com/Umi-BitSign/umi/commit/b8819bfccdcbde9afd423cf069491918e6f69fb9) — Umi-BitSign/umi
- 2026-10-04 · commit · [Merge pull request #222 from Umi-BitSign/codex/c5-request-start-timeo…](https://github.com/Umi-BitSign/umi/commit/5c0cadf5533e76d8886b0171537288766fe611bb) — Umi-BitSign/umi
- 2026-10-04 · commit · [Allow verified forward chain runtime succession](https://github.com/Umi-BitSign/umi/commit/72bec89daec2f50ec6cc5f370e86f0b438f73e3a) — Umi-BitSign/umi
- 2026-10-04 · commit · [Preserve request rests across timeout growth](https://github.com/Umi-BitSign/umi/commit/5e6e251a09f69f0f369e879e022bf9d55fc89153) — Umi-BitSign/umi
- 2026-10-04 · commit · [Merge pull request #221 from Umi-BitSign/codex/c5-timeout-state-compat](https://github.com/Umi-BitSign/umi/commit/b3a219f1cb452b4adbde5841d075b78ecfceb326) — Umi-BitSign/umi
- 2026-10-04 · commit · [Allow reviewed endpoint cache rollback](https://github.com/Umi-BitSign/umi/commit/812adfa4e08c69716750867467881adc15a62b90) — Umi-BitSign/umi
- 2026-10-04 · commit · [Merge pull request #217 from Umi-BitSign/codex/c5-automatic-service-c…](https://github.com/Umi-BitSign/umi/commit/82f874b37f3e1e2f2097f5f5ea8bfe32b72f597f) — Umi-BitSign/umi
- 2026-10-04 · commit · [Complete endpoint enrollment with service claims](https://github.com/Umi-BitSign/umi/commit/6e3ed63e019ebdbf05ad665a37552c308c12dab5) — Umi-BitSign/umi

## Use

```bash
m subnets.sn78/info        # live identity + market (snapshot if bt is down)
m subnets.sn78/news        # scraped news
m subnets.sn78/trades      # 24h alpha tape
m subnets.sn78/daily       # daily candles
python3 orbit/subnets/sn78/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
