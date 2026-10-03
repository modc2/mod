# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003829 | +0.00% | +0.01% | -0.58% | 22,385 | 4,927 | 882.15 |

## Last 24h flow

73 trades by 47 coldkeys · 40 buys (440.89 τ) / 33 sells (440.25 τ) · net 0.64 τ

## News

- 2026-10-03 · commit · [Merge remote-tracking branch 'origin/main' into codex/arena-supersede…](https://github.com/leadpoet/leadpoet/commit/1f9a934f5935a91243f016a307aba8db8bad8e40) — leadpoet/leadpoet
- 2026-10-03 · commit · [Reuse complete company-fit JSON judgments without response format](https://github.com/leadpoet/leadpoet/commit/2a5d96e4c170146f72676370170e676a85556429) — leadpoet/leadpoet
- 2026-10-03 · commit · [Align funding fixtures and HQ priority assertions with active contracts](https://github.com/leadpoet/leadpoet/commit/20fbde250e60abb26050dd86f7068d5b0b45d3a8) — leadpoet/leadpoet
- 2026-10-03 · commit · [Bind HQ and funding recovery to exact protected source](https://github.com/leadpoet/leadpoet/commit/929fb8285d3e9a11c640e9c630c4ea27a20a08d2) — leadpoet/leadpoet
- 2026-10-03 · commit · [Make current first-party HQ priority unambiguous](https://github.com/leadpoet/leadpoet/commit/a9a316ca8812ecaadf101c3a25997779fd1a25f8) — leadpoet/leadpoet
- 2026-10-02 · commit · [Bound zero-call score runner cooldown to current stage](https://github.com/leadpoet/leadpoet/commit/42bf55e613a70102c8e1080215d3f630549ca11e) — leadpoet/leadpoet
- 2026-10-02 · commit · [Keep zero-call expired scoring retries off failed runner](https://github.com/leadpoet/leadpoet/commit/73880cde3b7ce16460a9d92281b46977a1866fed) — leadpoet/leadpoet
- 2026-10-02 · commit · [Archive October 1 optional-signal scores for full rejudge](https://github.com/leadpoet/leadpoet/commit/c4cfd8b39d3e8c60591945c9da66b9758fdec4ea) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
