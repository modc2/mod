# sn92 — REAX र

Verified decision-model inference on Bittensor.

Bittensor subnet **92** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-15 (block 9286668).

Links: [github](https://github.com/reaxlabs/reax-subnet) · [url](https://reax.co/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.039437 | +3.92% | +773.07% | +2786.52% | 7,956 | 2,614 | 48,530 |

## Last 24h flow

5848 trades by 330 coldkeys · 3547 buys (25,121 τ) / 2301 sells (23,383 τ) · net 1,739 τ

## News

- 2026-10-11 · commit · [docs: run reax-selfcheck on every miner and validator host; single-ho…](https://github.com/reaxlabs/reax-subnet/commit/e5fbb76ffee6b147cf931ca0be824e34e7c9b1a0) — reaxlabs/reax-subnet
- 2026-10-11 · release · [v1.1.0-rc2](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.1.0-rc2) — reaxlabs/reax-subnet
- 2026-10-11 · commit · [docs: count distinct items for the per-item tail bound](https://github.com/reaxlabs/reax-subnet/commit/815987372074b1fd81846501141fdd7e738183d0) — reaxlabs/reax-subnet
- 2026-10-11 · commit · [docs: precise outlier value; create the wallet directory before the c…](https://github.com/reaxlabs/reax-subnet/commit/763112868714f92af362507a4a9dfa911de3af18) — reaxlabs/reax-subnet
- 2026-10-11 · commit · [docs: tail statistics over all 50,000 cross-card item pairs](https://github.com/reaxlabs/reax-subnet/commit/952a123cff1e530bcaebfa0954cf9095f579bc3f) — reaxlabs/reax-subnet
- 2026-10-11 · commit · [docs: RTX 3090 measured on the 5,000-item check](https://github.com/reaxlabs/reax-subnet/commit/78c4b4bee098969cfdd29ddf7593d429c67d11ce) — reaxlabs/reax-subnet

## Use

```bash
m subnets.sn92/info        # live identity + market (snapshot if bt is down)
m subnets.sn92/news        # scraped news
m subnets.sn92/trades      # 24h alpha tape
m subnets.sn92/daily       # daily candles
python3 orbit/subnets/sn92/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
