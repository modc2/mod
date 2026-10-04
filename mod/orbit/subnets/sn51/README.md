# sn51 — lium.io ת

revolutionizing the democratization of compute

Bittensor subnet **51** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/Datura-ai/lium-io) · [url](https://lium.io) · discord `p383_54249`

Fleet mods for this subnet: `lium`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.095036 | +0.02% | +2.09% | +5.05% | 574,864 | 165,277 | 5,545 |

## Last 24h flow

500 trades by 275 coldkeys · 281 buys (3,258 τ) / 219 sells (1,901 τ) · net 1,357 τ

## News

- 2026-10-02 · commit · [DAH-3980 - validator: check a present Docker Hub image's tag from the…](https://github.com/Datura-ai/lium-io/commit/5ace8c148ccc47a796622c92670354aa3894d843) — Datura-ai/lium-io
- 2026-10-02 · release · [validator-v2026.10.02.2](https://github.com/Datura-ai/lium-io/releases/tag/validator-v2026.10.02.2) — Datura-ai/lium-io
- 2026-10-02 · commit · [DAH-3964 - [P1] validator: rented node inactive on host-confirmed GPU…](https://github.com/Datura-ai/lium-io/commit/eec20430b7a15255704773e213a61c2f7adf4b6a) — Datura-ai/lium-io
- 2026-10-02 · release · [validator-v2026.10.02](https://github.com/Datura-ai/lium-io/releases/tag/validator-v2026.10.02) — Datura-ai/lium-io
- 2026-10-02 · commit · [NO-TICKET - [P1] validator: a shell lost mid-check sends its reason c…](https://github.com/Datura-ai/lium-io/commit/010bd5f86d0b0310b883d965f02340068687574a) — Datura-ai/lium-io
- 2026-10-02 · commit · [NO-TICKET - [P1] validator: report a rented node that lost a GPU on t…](https://github.com/Datura-ai/lium-io/commit/58f36cea3516d13cff5f45429d58493446a8c28f) — Datura-ai/lium-io
- 2026-10-01 · commit · [NO-TICKET - [P2] validator: remove INSPECTOR_ENFORCE_ENABLED, finding…](https://github.com/Datura-ai/lium-io/commit/a425a36af8a7ce975c4f70f863037c6e50de06e3) — Datura-ai/lium-io
- 2026-10-01 · commit · [validator: library fetch default tracks main, like the verifyx defaul…](https://github.com/Datura-ai/lium-io/commit/ad5f633b4a32ac1a1c3c0753c77b97698f779fcc) — Datura-ai/lium-io

## Use

```bash
m subnets.sn51/info        # live identity + market (snapshot if bt is down)
m subnets.sn51/news        # scraped news
m subnets.sn51/trades      # 24h alpha tape
m subnets.sn51/daily       # daily candles
python3 orbit/subnets/sn51/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
