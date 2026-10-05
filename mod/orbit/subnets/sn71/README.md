# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003725 | +0.04% | -1.34% | -4.45% | 21,831 | 4,860 | 469.64 |

## Last 24h flow

73 trades by 37 coldkeys · 33 buys (218.47 τ) / 40 sells (249.64 τ) · net -31.17 τ

## News

- 2026-10-04 · commit · [Recover exact Oct 4 shadow Scrapingdog charges](https://github.com/leadpoet/leadpoet/commit/55e1be5ac508eb5672fe5db23ed84742b4be884d) — leadpoet/leadpoet
- 2026-10-04 · commit · [Settle known Scrapingdog charge after storage 403](https://github.com/leadpoet/leadpoet/commit/3da6a86eaff185e1c9be988f27727f910f56803f) — leadpoet/leadpoet
- 2026-10-04 · commit · [Bind local verifier fix to protected workflow manifest](https://github.com/leadpoet/leadpoet/commit/3f7051d89ec69c888c5d65235faef3febbcde102) — leadpoet/leadpoet
- 2026-10-04 · commit · [Keep completed unproven company reviews local after typed source refusal](https://github.com/leadpoet/leadpoet/commit/3cb476b6c8f288be0f634ccbee37e0d077f90058) — leadpoet/leadpoet
- 2026-10-04 · commit · [Merge pull request #210 from leadpoet/codex/arena-untransitioned-host…](https://github.com/leadpoet/leadpoet/commit/7fc86ae0c0a5bb2b0b5deff6b4fba3f23733e0de) — leadpoet/leadpoet
- 2026-10-04 · commit · [Merge pull request #207 from leadpoet/codex/arena-execute-host-cooldo…](https://github.com/leadpoet/leadpoet/commit/98e2f6291c48dc01bb5708d776b8f3cf4ef7e536) — leadpoet/leadpoet
- 2026-10-04 · commit · [Cool down repeated zero-call execute host failures](https://github.com/leadpoet/leadpoet/commit/215eb9f90da56b199e45449f9473a120253364b2) — leadpoet/leadpoet
- 2026-10-04 · commit · [Merge pull request #205 from leadpoet/codex/daily-miner-admission](https://github.com/leadpoet/leadpoet/commit/7f2d9f3b738ea1b2bf26bbfb21136baad38f6fc2) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
