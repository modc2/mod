# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003701 | -0.08% | -0.65% | -5.22% | 21,716 | 4,844 | 336.60 |

## Last 24h flow

63 trades by 36 coldkeys · 34 buys (160.45 τ) / 29 sells (174.46 τ) · net -14.01 τ

## News

- 2026-10-06 · commit · [Merge pull request #232 from leadpoet/codex/arena-idle-recovery-resta…](https://github.com/leadpoet/leadpoet/commit/fa8796375a76b8fa75867236d8fef516b5f8fa59) — leadpoet/leadpoet
- 2026-10-06 · commit · [Number restart guard migration after concurrent billing recovery](https://github.com/leadpoet/leadpoet/commit/2179dcec80c6ff41184898efe136daa2bd640c07) — leadpoet/leadpoet
- 2026-10-06 · commit · [Merge remote-tracking branch 'origin/main' into codex/arena-idle-reco…](https://github.com/leadpoet/leadpoet/commit/1eb64d379486446be9ca52d6765499a1b8485ceb) — leadpoet/leadpoet
- 2026-10-06 · commit · [Keep Arena worker and billing recovery progressing independently (#231)](https://github.com/leadpoet/leadpoet/commit/1abfa02cf5859d199f4855d5ed0d5b3bf0763154) — leadpoet/leadpoet
- 2026-10-06 · commit · [Preserve restart drain receipts during abandoned host recovery](https://github.com/leadpoet/leadpoet/commit/eba88ce52a14e851d44ca85a81e17fdce797b23e) — leadpoet/leadpoet
- 2026-10-05 · commit · [Merge pull request #225 from leadpoet/op/V7jOJiWPpT/ci-name-failures](https://github.com/leadpoet/leadpoet/commit/ca0faa53256a1a3b9a747d99ce36c1eb739f3c4b) — leadpoet/leadpoet
- 2026-10-05 · commit · [Make the pytest lane name the tests that fail](https://github.com/leadpoet/leadpoet/commit/d7ce5a51f1ac4d2742f3ecb7471df915ddf8f259) — leadpoet/leadpoet
- 2026-10-05 · commit · [Merge pull request #224 from leadpoet/codex/arena-delay-recovery-oct05](https://github.com/leadpoet/leadpoet/commit/e8f7bd73568f27652248d79e255c9f9cbed6b30d) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
