# sn62 — Ridges ز

Software Engineering Agents

Bittensor subnet **62** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/ridgesai/ridges) · [url](https://www.ridges.ai/) · [discord](https://discord.gg/WeDvTnYDad)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.010390 | -0.03% | -0.16% | -2.71% | 58,601 | 29,659 | 162.74 |

## Last 24h flow

187 trades by 89 coldkeys · 130 buys (40.20 τ) / 57 sells (82.07 τ) · net -41.87 τ

## News

- 2026-10-02 · commit · [Merge pull request #517 from ridgesai/update/remove-time-limit-credits](https://github.com/ridgesai/ridges/commit/02a12dac06e3f60b47dd482e8791ccc46c56a257) — ridgesai/ridges
- 2026-10-02 · commit · [update tests](https://github.com/ridgesai/ridges/commit/e11c7e9f34802682809d9866c415b86088b1b9a3) — ridgesai/ridges
- 2026-10-02 · commit · [return remaining time properly](https://github.com/ridgesai/ridges/commit/df4b7fdce441ad39bbf0e66a82643bbbbe49d703) — ridgesai/ridges
- 2026-10-02 · commit · [allow credit uploads to skip time enforcement](https://github.com/ridgesai/ridges/commit/d45290e4e8e3a6343fd2966f1c3f32743768796a) — ridgesai/ridges
- 2026-10-01 · release · [v0.3.9](https://github.com/ridgesai/ridges/releases/tag/v0.3.9) — ridgesai/ridges
- 2026-10-01 · commit · [Merge pull request #516 from ridgesai/update/preference-to-inprocess-…](https://github.com/ridgesai/ridges/commit/f3f2107639caa356b45dd9efbdc0fce08b728049) — ridgesai/ridges
- 2026-10-01 · release · [v0.3.8](https://github.com/ridgesai/ridges/releases/tag/v0.3.8) — ridgesai/ridges
- 2026-10-01 · commit · [Merge branch 'main' into update/preference-to-inprocess-valis](https://github.com/ridgesai/ridges/commit/8eb058f2592ec9518e8e80c5dc5004626b7ad578) — ridgesai/ridges

## Use

```bash
m subnets.sn62/info        # live identity + market (snapshot if bt is down)
m subnets.sn62/news        # scraped news
m subnets.sn62/trades      # 24h alpha tape
m subnets.sn62/daily       # daily candles
python3 orbit/subnets/sn62/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
