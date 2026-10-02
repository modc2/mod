# sn71 — Leadpoet ㄴ

Intent-driven AI for modern sales teams.

Bittensor subnet **71** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/leadpoet/leadpoet) · [url](https://leadpoet.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003820 | +0.02% | -0.54% | -1.02% | 22,310 | 4,921 | 837.22 |

## Last 24h flow

70 trades by 45 coldkeys · 34 buys (411.19 τ) / 36 sells (424.24 τ) · net -13.05 τ

## News

- 2026-10-02 · commit · [Verify extended recovery schedule on migration replay](https://github.com/leadpoet/leadpoet/commit/a3ab6f6a3aae30566a21a77c8d123cafc805d5f3) — leadpoet/leadpoet
- 2026-10-02 · commit · [Rejudge October 1 with preserved event-state scope](https://github.com/leadpoet/leadpoet/commit/6041199e2f07e44aba30fc046b453664e9a250b9) — leadpoet/leadpoet
- 2026-10-02 · commit · [Preserve requested availability scope in event review](https://github.com/leadpoet/leadpoet/commit/2564ef035ecfa3a32695d4815b84f6336b01cabd) — leadpoet/leadpoet
- 2026-10-02 · commit · [Preserve buyer event state in Arena intent verification](https://github.com/leadpoet/leadpoet/commit/cbdcb3120a1c2f8c179e050b91d195203b6b0608) — leadpoet/leadpoet
- 2026-10-02 · commit · [Hold October 1 scoring for intent event-state correction](https://github.com/leadpoet/leadpoet/commit/469dd08d76a6bb18324d335548125eeddf558e41) — leadpoet/leadpoet
- 2026-10-02 · commit · [Refresh protected workflow manifest for industry review](https://github.com/leadpoet/leadpoet/commit/983f1d195b136cc81e0f373a8453d19d5c196505) — leadpoet/leadpoet
- 2026-10-02 · commit · [Separate industry review from required attribute proof](https://github.com/leadpoet/leadpoet/commit/53bf35e5d9de8e6800cb3cdef58f9ca209133470) — leadpoet/leadpoet
- 2026-10-02 · commit · [Merge pull request #200 from leadpoet/codex/progressive-model-scores](https://github.com/leadpoet/leadpoet/commit/fb9b2a264aac5dd9b69dc7a480198d06b93ad0d8) — leadpoet/leadpoet

## Use

```bash
m subnets.sn71/info        # live identity + market (snapshot if bt is down)
m subnets.sn71/news        # scraped news
m subnets.sn71/trades      # 24h alpha tape
m subnets.sn71/daily       # daily candles
python3 orbit/subnets/sn71/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
