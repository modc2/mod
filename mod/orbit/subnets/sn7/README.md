# sn7 — Allways η

universal transaction layer

Bittensor subnet **7** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/entrius/allways) · [url](https://all-ways.io/) · discord ` `

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002204 | -0.00% | +0.15% | -4.86% | 12,118 | 5,290 | 74.97 |

## Last 24h flow

13 trades by 8 coldkeys · 4 buys (38.61 τ) / 9 sells (34.55 τ) · net 4.06 τ

## News

- 2026-10-19 · commit · [Land alpha deliveries on a hotkey the recipient accepts and record on…](https://github.com/entrius/allways/commit/24b5187dedb9ba7ccb0a10820a76fb52ec5df9d0) — entrius/allways
- 2026-10-19 · release · [release-20261019-165322](https://github.com/entrius/allways/releases/tag/release-20261019-165322) — entrius/allways
- 2026-10-19 · commit · [Apply the token gas floor to every ERC-20 payout (#797)](https://github.com/entrius/allways/commit/9e29f8385c89b298bfb5a23e170d8c6ed5042fe2) — entrius/allways
- 2026-10-19 · commit · [Use one gas limit for every native EVM payout (#796)](https://github.com/entrius/allways/commit/d5510cf8f414f56e4b2bf825ae467e9e14a5bba5) — entrius/allways
- 2026-10-16 · release · [release-20261017-152034: Bound event ingest per pass and cap its lookback at 6h (#792)](https://github.com/entrius/allways/releases/tag/release-20261017-152034) — entrius/allways
- 2026-10-16 · commit · [Bound event ingest per pass and cap its lookback at 6h (#792)](https://github.com/entrius/allways/commit/c34aac903b0340f6b830974fa7250f7a429ec497) — entrius/allways
- 2026-10-16 · commit · [Emit enough in program events to follow pool, quote and config state …](https://github.com/entrius/allways/commit/3b5f8618756eba6dd94364947614713aa7f25e68) — entrius/allways
- 2026-10-16 · commit · [Key the source lock by deposit wallet and source address (#782)](https://github.com/entrius/allways/commit/ac367cc0c87bbff8bf8a9e753d42faf41cfa1aeb) — entrius/allways

## Use

```bash
m subnets.sn7/info        # live identity + market (snapshot if bt is down)
m subnets.sn7/news        # scraped news
m subnets.sn7/trades      # 24h alpha tape
m subnets.sn7/daily       # daily candles
python3 orbit/subnets/sn7/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
