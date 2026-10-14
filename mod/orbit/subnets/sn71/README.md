# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005419 | +0.79% | +7.28% | +31.83% | 32,108 | 5,862 | 3,617 |

## Last 24h flow

382 trades by 150 coldkeys · 206 buys (1,910 τ) / 176 sells (1,700 τ) · net 209.98 τ

## News

- 2026-10-14 · commit · [Merge pull request #419 from leadpoet/codex/oct14-generic-http-401-pr…](https://github.com/leadpoet/leadpoet/commit/f42015438a0d0348fbf9f840f2d58a31edc48dcb) — leadpoet/leadpoet
- 2026-10-14 · commit · [Classify typed generic HTTP 401 as a request refusal](https://github.com/leadpoet/leadpoet/commit/584b670019713994c2ee8b078094c4dff47cfb70) — leadpoet/leadpoet
- 2026-10-14 · commit · [Merge pull request #418: Preserve verified headcount ranges](https://github.com/leadpoet/leadpoet/commit/db9d8c67169a587e9ce1b1bd203d7529b9e16fda) — leadpoet/leadpoet
- 2026-10-14 · commit · [Normalize verified investigator headcount before fit decision](https://github.com/leadpoet/leadpoet/commit/65806e1e4bff557a9ea57e20b759a1ab6b54066f) — leadpoet/leadpoet
- 2026-10-13 · commit · [Merge pull request #417: Restore runtime478 confirmation and frozen-f…](https://github.com/leadpoet/leadpoet/commit/ce7b0949991ce2c9f628fdfd61f54054da3a9da5) — leadpoet/leadpoet
- 2026-10-13 · commit · [Merge pull request #416: Avoid repeated polling in delayed Deepline b…](https://github.com/leadpoet/leadpoet/commit/771ac8a2214ed7cf0e2b2ce1174a7fa64a4cc8df) — leadpoet/leadpoet
- 2026-10-13 · commit · [Remove trailing blank line from billing regression test](https://github.com/leadpoet/leadpoet/commit/d6898472ef45e5be7033622730ad2b674949bd7a) — leadpoet/leadpoet
- 2026-10-13 · commit · [Read retained Deepline bills once per driver pass](https://github.com/leadpoet/leadpoet/commit/901f541b970144a3f46a4c0b9f75894824620680) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
