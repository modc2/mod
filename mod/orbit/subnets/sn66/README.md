# sn66 — conjectures ض

Incentivizing breakthroughs on decades-old open mathematical conjectures

Bittensor subnet **66** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-17 (block 9300804).

Links: [github](https://github.com/conjectures-io/conjectures-validator) · [url](https://conjectures.io) · discord `wejh`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002621 | +0.00% | +0.29% | -11.84% | 15,770 | 6,305 | 145.30 |

## Last 24h flow

53 trades by 23 coldkeys · 11 buys (77.20 τ) / 42 sells (64.34 τ) · net 12.86 τ

## News

- 2026-10-14 · commit · [Merge pull request #129 from conjectures-io/fix/chat-log-reasoning](https://github.com/conjectures-io/conjectures-validator/commit/e890a393d90c768ca627a3465fd88435c185136a) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Keep the model's reasoning in chat-log packages wherever it is readable](https://github.com/conjectures-io/conjectures-validator/commit/8abcfa303fa9823fef1bc0355372984b3c7d7b79) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Merge pull request #128 from conjectures-io/fix/chat-log-revisions](https://github.com/conjectures-io/conjectures-validator/commit/e15556488544b48db1a44d76befd63ab572c313a) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Let reviewers ask for more of a bounty's required chat logs](https://github.com/conjectures-io/conjectures-validator/commit/6374db6efe492320e4435d9b292ebdc34918a365) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Merge pull request #127 from conjectures-io/dev](https://github.com/conjectures-io/conjectures-validator/commit/e9c2839e9b100a2813c515e2fdc175a851028397) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Merge pull request #126 from conjectures-io/feat/floatlib-past-leader…](https://github.com/conjectures-io/conjectures-validator/commit/3c44b2e403202fb29ffc29c722fc3ae6ea1cd68f) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [feat: extend leaderboard endpoint (add past champions challengers his…](https://github.com/conjectures-io/conjectures-validator/commit/15cd88a49afdbcdf75a972cf0b2e133d14e51c9e) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [fix the database name in url](https://github.com/conjectures-io/conjectures-validator/commit/ed3f48dd2f8957f6d06720b43834ad47a4f1153b) — conjectures-io/conjectures-validator

## Use

```bash
m subnets.sn66/info        # live identity + market (snapshot if bt is down)
m subnets.sn66/news        # scraped news
m subnets.sn66/trades      # 24h alpha tape
m subnets.sn66/daily       # daily candles
python3 orbit/subnets/sn66/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
