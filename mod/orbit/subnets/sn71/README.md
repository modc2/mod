# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004874 | +0.78% | +17.55% | +27.30% | 28,702 | 5,559 | 5,725 |

## Last 24h flow

507 trades by 171 coldkeys · 293 buys (3,079 τ) / 214 sells (2,644 τ) · net 435.21 τ

## News

- 2026-10-09 · commit · [Merge pull request #296 from leadpoet/fix/judge-completion-reason](https://github.com/leadpoet/leadpoet/commit/cd16d8bd7d48f1c8aa758b3a9d5ece290775562e) — leadpoet/leadpoet
- 2026-10-09 · commit · [Accept fixed admission interruption reason at Arena completion](https://github.com/leadpoet/leadpoet/commit/339c0883bf8eb0fbd92605d214c23f055a0ae9c7) — leadpoet/leadpoet
- 2026-10-09 · commit · [Merge pull request #295 from leadpoet/fix/host-score-closed-billing](https://github.com/leadpoet/leadpoet/commit/e60478e9514879cbccd7f3f885babe923db10466) — leadpoet/leadpoet
- 2026-10-09 · commit · [Reconcile exact closed host judge Deepline bills](https://github.com/leadpoet/leadpoet/commit/a0e4227163b1db35d001c2f2971fee5c8d4a57b2) — leadpoet/leadpoet
- 2026-10-08 · commit · [Verify host payment refusal ledger transitions in PostgreSQL](https://github.com/leadpoet/leadpoet/commit/3a19a08e7e2a243faa38dd90e6d1bbf3ac4367e1) — leadpoet/leadpoet
- 2026-10-08 · commit · [Clear stale provider refusal guard after definitive response (#293)](https://github.com/leadpoet/leadpoet/commit/58260206f5c92a6d2b0f988f1cf12b536cac3b88) — leadpoet/leadpoet
- 2026-10-08 · commit · [Recover provider refusal guards and reject empty failed research (#292)](https://github.com/leadpoet/leadpoet/commit/258976a18edbb4b047cae1f77084b4c4edb65226) — leadpoet/leadpoet
- 2026-10-08 · commit · [Merge pull request #291 from leadpoet/codex/judge-deadline-return-oct08](https://github.com/leadpoet/leadpoet/commit/a65e45520335cf974db739d07daa16f59b89b15b) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
