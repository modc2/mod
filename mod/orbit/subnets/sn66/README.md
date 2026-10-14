# sn66 — conjectures ض

Incentivizing breakthroughs on decades-old open mathematical conjectures

Bittensor subnet **66** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-14 (block 9279460).

Links: [github](https://github.com/conjectures-io/conjectures-validator) · [url](https://conjectures.io) · discord `wejh`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002990 | -0.00% | +2.16% | -2.24% | 17,920 | 6,731 | 1,260 |

## Last 24h flow

170 trades by 46 coldkeys · 66 buys (665.68 τ) / 104 sells (593.37 τ) · net 72.31 τ

## News

- 2026-10-13 · commit · [Merge pull request #125 from conjectures-io/feat/chat-logs-all-open-t…](https://github.com/conjectures-io/conjectures-validator/commit/cec320f8a2f4e595f5052da39edb9d0a1b41dd7b) — conjectures-io/conjectures-validator
- 2026-10-13 · commit · [Require chat logs for every open target, including from the CLI](https://github.com/conjectures-io/conjectures-validator/commit/fcdb62696dae17d1dcc46b317426ec324aa713fb) — conjectures-io/conjectures-validator
- 2026-10-13 · commit · [Merge pull request #124 from conjectures-io/release/2026-10-13](https://github.com/conjectures-io/conjectures-validator/commit/b92d506ade3d7af6d22f295c78996ba1d0575943) — conjectures-io/conjectures-validator
- 2026-10-13 · commit · [Sum co-solver payout shares on public results](https://github.com/conjectures-io/conjectures-validator/commit/6daa21fc9b1aab0a82ceafc035437e4a96163390) — conjectures-io/conjectures-validator
- 2026-10-13 · commit · [Use the unique main database hostname for the API](https://github.com/conjectures-io/conjectures-validator/commit/915c97a2b2a010e40700e40cb1e58c2eb0715c04) — conjectures-io/conjectures-validator
- 2026-10-13 · commit · [Add owner PDF attachments with approval-gated public access](https://github.com/conjectures-io/conjectures-validator/commit/7ebcf7272227fd32d6eb3b095fb9ce3e78681e27) — conjectures-io/conjectures-validator
- 2026-10-13 · commit · [Release bonus transfer matching after finalized settlement](https://github.com/conjectures-io/conjectures-validator/commit/2cd08a6205584cd2bdaab9519ca9ec4705007a33) — conjectures-io/conjectures-validator
- 2026-10-09 · commit · [Raise proof limits to five million tokens and 30 MiB (#117)](https://github.com/conjectures-io/conjectures-validator/commit/bc4e1f7d5354fbb20d09636399ac24df2b6b63d8) — conjectures-io/conjectures-validator

## Use

```bash
m subnets.sn66/info        # live identity + market (snapshot if bt is down)
m subnets.sn66/news        # scraped news
m subnets.sn66/trades      # 24h alpha tape
m subnets.sn66/daily       # daily candles
python3 orbit/subnets/sn66/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
