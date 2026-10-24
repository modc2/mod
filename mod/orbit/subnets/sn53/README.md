# sn53 — engy ب

Verified inference for frontier open models.

Bittensor subnet **53** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-20 (block 9322663).

Links: [github](https://github.com/hanlinai/engy) · [url](https://engy.ai)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.035557 | +0.91% | -6.21% | -9.42% | 222,987 | 39,383 | 14,570 |

## Last 24h flow

767 trades by 307 coldkeys · 394 buys (6,491 τ) / 373 sells (7,906 τ) · net -1,415 τ

## News

- 2026-10-19 · commit · [Merge pull request #56 from hanlinai/fix/tee-miner-prefix-continuation](https://github.com/hanlinai/engy/commit/ed84d0b43a9de19f8611a2321c45acc09e570b4c) — hanlinai/engy
- 2026-10-19 · commit · [tee_miner: forward prefix continuation to the serve](https://github.com/hanlinai/engy/commit/8d7ba516a3a3e0ecdcaf3d3a9a927d2b1516ba5e) — hanlinai/engy
- 2026-10-18 · commit · [Merge pull request #55 from hanlinai/feat/multi-entrance-legs](https://github.com/hanlinai/engy/commit/70a4f104d7fc02c0f29e9bdbbf0146c70921e353) — hanlinai/engy
- 2026-10-18 · commit · [tee_miner: default to half the legs on the LB, half on Cloudflare, sh…](https://github.com/hanlinai/engy/commit/1829b9874e1b90f000570b05654578ae94d5f895) — hanlinai/engy
- 2026-10-17 · commit · [tee_miner: Cloudflare is a backup door, not a second home](https://github.com/hanlinai/engy/commit/01ff05a8bb46d0d4dec44d29604bf067df3a92eb) — hanlinai/engy
- 2026-10-17 · commit · [tee_miner: spread the legs across several gateway doors](https://github.com/hanlinai/engy/commit/a4c161396491c40008dd0160a5aa8c1bae9abd65) — hanlinai/engy
- 2026-10-13 · commit · [Merge pull request #53 from hanlinai/fix/tee-miner-no-body-in-logs](https://github.com/hanlinai/engy/commit/29ad734b006442bbedd28d06ed06ff8dbe42a0aa) — hanlinai/engy
- 2026-10-13 · commit · [tee_miner: never write the request to the miner log](https://github.com/hanlinai/engy/commit/fe55344aba142cf9d8496df3cf3549bb80028715) — hanlinai/engy

## Use

```bash
m subnets.sn53/info        # live identity + market (snapshot if bt is down)
m subnets.sn53/news        # scraped news
m subnets.sn53/trades      # 24h alpha tape
m subnets.sn53/daily       # daily candles
python3 orbit/subnets/sn53/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
