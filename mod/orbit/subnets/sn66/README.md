# sn66 — conjectures ض

Incentivizing breakthroughs on decades-old open mathematical conjectures

Bittensor subnet **66** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-19 (block 9314686).

Links: [github](https://github.com/conjectures-io/conjectures-validator) · [url](https://conjectures.io) · discord `wejh`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.002521 | — | -3.59% | -15.74% | 15,204 | 6,184 | 495.42 |

## Last 24h flow

52 trades by 32 coldkeys · 25 buys (190.75 τ) / 27 sells (304.08 τ) · net -113.33 τ

## News

- 2026-10-18 · commit · [Merge pull request #130 from conjectures-io/codex/mathlib-headers-202…](https://github.com/conjectures-io/conjectures-validator/commit/c50b5d55b0025f7fc164a8e92c4be9beefd75f64) — conjectures-io/conjectures-validator
- 2026-10-18 · commit · [Activate the Mathlib header task release](https://github.com/conjectures-io/conjectures-validator/commit/21673f28ecafc3cd5aeac4c28fc4c6cd649c8303) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Merge pull request #129 from conjectures-io/fix/chat-log-reasoning](https://github.com/conjectures-io/conjectures-validator/commit/e890a393d90c768ca627a3465fd88435c185136a) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Keep the model's reasoning in chat-log packages wherever it is readable](https://github.com/conjectures-io/conjectures-validator/commit/8abcfa303fa9823fef1bc0355372984b3c7d7b79) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Merge pull request #128 from conjectures-io/fix/chat-log-revisions](https://github.com/conjectures-io/conjectures-validator/commit/e15556488544b48db1a44d76befd63ab572c313a) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Let reviewers ask for more of a bounty's required chat logs](https://github.com/conjectures-io/conjectures-validator/commit/6374db6efe492320e4435d9b292ebdc34918a365) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Merge pull request #127 from conjectures-io/dev](https://github.com/conjectures-io/conjectures-validator/commit/e9c2839e9b100a2813c515e2fdc175a851028397) — conjectures-io/conjectures-validator
- 2026-10-14 · commit · [Merge pull request #126 from conjectures-io/feat/floatlib-past-leader…](https://github.com/conjectures-io/conjectures-validator/commit/3c44b2e403202fb29ffc29c722fc3ae6ea1cd68f) — conjectures-io/conjectures-validator

## Use

```bash
m subnets.sn66/info        # live identity + market (snapshot if bt is down)
m subnets.sn66/news        # scraped news
m subnets.sn66/trades      # 24h alpha tape
m subnets.sn66/daily       # daily candles
python3 orbit/subnets/sn66/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
