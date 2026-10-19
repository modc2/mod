# sn7 — Allways η

universal transaction layer

Bittensor subnet **7** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/entrius/allways) · [url](https://all-ways.io/) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002201 | — | +0.55% | -9.87% | 12,092 | 5,286 | 57.23 |

## Last 24h flow

13 trades by 8 coldkeys · 6 buys (35.59 τ) / 7 sells (21.09 τ) · net 14.50 τ

## News

- 2026-10-16 · release · [release-20261017-152034: Bound event ingest per pass and cap its lookback at 6h (#792)](https://github.com/entrius/allways/releases/tag/release-20261017-152034) — entrius/allways
- 2026-10-16 · commit · [Bound event ingest per pass and cap its lookback at 6h (#792)](https://github.com/entrius/allways/commit/c34aac903b0340f6b830974fa7250f7a429ec497) — entrius/allways
- 2026-10-16 · commit · [Emit enough in program events to follow pool, quote and config state …](https://github.com/entrius/allways/commit/3b5f8618756eba6dd94364947614713aa7f25e68) — entrius/allways
- 2026-10-16 · commit · [Key the source lock by deposit wallet and source address (#782)](https://github.com/entrius/allways/commit/ac367cc0c87bbff8bf8a9e753d42faf41cfa1aeb) — entrius/allways
- 2026-10-16 · commit · [Drop only the rejected routed request, not the whole queue behind it …](https://github.com/entrius/allways/commit/3fd4917ffececfdc0df6db0d4cd7449d5e75e4d0) — entrius/allways
- 2026-10-16 · commit · [Require an EVM address to be exactly 0x plus 40 hex characters (#784)](https://github.com/entrius/allways/commit/63ac69834a7cba08be9a92755d70e4b190237ee5) — entrius/allways
- 2026-10-16 · commit · [Create the destination ATA only when missing, so an existing ATA is a…](https://github.com/entrius/allways/commit/72b028defbb3ff08906ced292d2517d289cfe4d7) — entrius/allways
- 2026-10-16 · commit · [Spend only confirmed or own-change UTXOs for BTC payouts (#788)](https://github.com/entrius/allways/commit/ef365e985302fa0d2af3feed14c747b45d314c32) — entrius/allways

## Use

```bash
m subnets.sn7/info        # live identity + market (snapshot if bt is down)
m subnets.sn7/news        # scraped news
m subnets.sn7/trades      # 24h alpha tape
m subnets.sn7/daily       # daily candles
python3 orbit/subnets/sn7/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
