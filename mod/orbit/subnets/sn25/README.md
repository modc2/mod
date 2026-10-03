# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006737 | +0.00% | +0.89% | -1.33% | 41,027 | 13,270 | 155.08 |

## Last 24h flow

27 trades by 20 coldkeys · 10 buys (107.01 τ) / 17 sells (47.52 τ) · net 59.49 τ

## News

- 2026-10-03 · commit · [Verify combined published Connect recovery dependency](https://github.com/urfoundation/sn/commit/265807c109d7df12a0efda67cf16562be5a57e88) — urfoundation/sn
- 2026-10-03 · commit · [Track complete-owner restore scope and preserve current dependency floor](https://github.com/urfoundation/sn/commit/055c0a0fb092623f704a93123ea10a84bcfd6e27) — urfoundation/sn
- 2026-10-03 · commit · [Complete current migration candidate gate and preserve upstream publi…](https://github.com/urfoundation/sn/commit/67a160aa2efc32750f0025aa54a6080c346e3781) — urfoundation/sn
- 2026-10-03 · commit · [Record current migration race qualification](https://github.com/urfoundation/sn/commit/59ced028b7254231bc5052e79948a8f5db92366d) — urfoundation/sn
- 2026-10-03 · commit · [Record current payout migration qualification and recovery test contr…](https://github.com/urfoundation/sn/commit/bdbd42f9fe7834cb5f21506f9544b8a6e7660969) — urfoundation/sn
- 2026-10-02 · commit · [Record private-root preparation source qualification](https://github.com/urfoundation/sn/commit/404262054f41144af3fceeff64d4d3d33de9ed2a) — urfoundation/sn
- 2026-10-02 · commit · [Record guarded inactive cache reclaim for ongoing qualification](https://github.com/urfoundation/sn/commit/11ca89776005c5026dabc06dc9c27b6f8549fff2) — urfoundation/sn
- 2026-10-02 · commit · [Record integrated provider monitoring qualification and scope](https://github.com/urfoundation/sn/commit/a692bbbae43327b76af0161a2bc2e6a21ea29fe0) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
