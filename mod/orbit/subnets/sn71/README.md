# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004782 | — | +9.00% | -1.85% | 28,503 | 5,506 | 1,879 |

## Last 24h flow

147 trades by 63 coldkeys · 80 buys (1,056 τ) / 67 sells (822.16 τ) · net 233.90 τ

## News

- 2026-10-18 · commit · [obs(arena): record canonical round IDs on HTTP spans (#536)](https://github.com/leadpoet/leadpoet/commit/618071fa8c9bf1bd8363585d8d070fe7634c602a) — leadpoet/leadpoet
- 2026-10-18 · commit · [Preserve bounded store diagnostics on arena advance failures (#535)](https://github.com/leadpoet/leadpoet/commit/1211af84922802fc804ffb36313bf3a3386c7f47) — leadpoet/leadpoet
- 2026-10-18 · commit · [Merge pull request #534 from leadpoet/fix/arena-reviewer-register-no-…](https://github.com/leadpoet/leadpoet/commit/34543d5bb0f8d041cacee2f592e8f90f7a594482) — leadpoet/leadpoet
- 2026-10-18 · commit · [Prevent bytecode writes during reviewer runtime registration](https://github.com/leadpoet/leadpoet/commit/201c5b9740cc68216ca88538fdc5b2730b48d593) — leadpoet/leadpoet
- 2026-10-18 · commit · [Merge pull request #533 from leadpoet/fix/arena-host-compat-capabilit…](https://github.com/leadpoet/leadpoet/commit/ab08dac67dc3dd1bfd286e6e15e18090c53ce937) — leadpoet/leadpoet
- 2026-10-18 · commit · [fix(arena): classify unavailable frozen score routes before dispatch](https://github.com/leadpoet/leadpoet/commit/7dca2bb46d4ee5d9890eae6a591582bb6dc288d4) — leadpoet/leadpoet
- 2026-10-18 · commit · [Prune superseded Deepline score billing heads before V3 candidate wor…](https://github.com/leadpoet/leadpoet/commit/2ae20ac465ffb4467a56ea88f9372082998cc4f9) — leadpoet/leadpoet
- 2026-10-18 · commit · [Capture runsc child fatal logs through bounded stderr (#527)](https://github.com/leadpoet/leadpoet/commit/c6e6fd314bb53f2471db185e5e3cd84e011ce498) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
