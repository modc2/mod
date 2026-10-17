# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.004696 | -0.51% | -3.56% | -3.05% | 27,920 | 5,456 | 909.11 |

## Last 24h flow

133 trades by 77 coldkeys · 56 buys (404.75 τ) / 77 sells (503.02 τ) · net -98.27 τ

## News

- 2026-10-17 · commit · [Merge pull request #512 from leadpoet/codex/arena-training-input-sche…](https://github.com/leadpoet/leadpoet/commit/6ab0dc94feddfd9220d4c7b611ab9b94284c1909) — leadpoet/leadpoet
- 2026-10-17 · commit · [fix(arena): preserve JSON schema integers in training input](https://github.com/leadpoet/leadpoet/commit/f95ae4ea8191b1aa6698db76125e97ce4381cfe9) — leadpoet/leadpoet
- 2026-10-17 · commit · [Merge pull request #511 from leadpoet/fix/reviewer-runtime-completion](https://github.com/leadpoet/leadpoet/commit/9413a02ad9d666ef3ff0c6248fe6562fdfc97347) — leadpoet/leadpoet
- 2026-10-17 · commit · [Compose only the approved frozen negative-finality correction](https://github.com/leadpoet/leadpoet/commit/ad38ff9607bbdcbe54ea50c85aaaa9ba1493a470) — leadpoet/leadpoet
- 2026-10-17 · commit · [Admit the existing final reviewer correction after late settlement](https://github.com/leadpoet/leadpoet/commit/27449b8481adeb1176a8279f17f9b0b536f9edf8) — leadpoet/leadpoet
- 2026-10-16 · commit · [Merge pull request #491 from leadpoet/fix/arena-store-error-diagnosti…](https://github.com/leadpoet/leadpoet/commit/7a1d61bb4be7d9e29d75be33bbdd4a2659f3f0ed) — leadpoet/leadpoet
- 2026-10-16 · commit · [Record safe store diagnostics in provider errors](https://github.com/leadpoet/leadpoet/commit/fd1da8e9271c7585e88e30c398fd48da8d0a26a9) — leadpoet/leadpoet
- 2026-10-16 · commit · [Recover keyed Deepline edge locators after missing exact bills (#490)](https://github.com/leadpoet/leadpoet/commit/5fc5b5d11c22cecba74ff0a614bcf098d0d12ef5) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
