# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-11 (block 9257875).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005690 | -0.00% | +1.47% | -6.68% | 31,995 | 17,307 | 456.56 |

## Last 24h flow

58 trades by 27 coldkeys · 14 buys (291.28 τ) / 44 sells (167.73 τ) · net 123.55 τ

## News

- 2026-10-10 · commit · [Merge pull request #346 from reliquadotai/feat/register-reliquary-gen…](https://github.com/reliquadotai/reliquary/commit/81a26b60ad7dac1bf3f05c58947f35053939ec85) — reliquadotai/reliquary
- 2026-10-10 · commit · [feat(env): register reliquary-general as a stage-A corpus source](https://github.com/reliquadotai/reliquary/commit/4c851b546d4aa1c2730467e85b5ecf2671fc2b55) — reliquadotai/reliquary
- 2026-10-09 · commit · [Merge pull request #345 from reliquadotai/feat/register-reliquary-com…](https://github.com/reliquadotai/reliquary/commit/2701f005d269db9351484dcbc88580d4a84006bb) — reliquadotai/reliquary
- 2026-10-09 · commit · [feat(env): register reliquary-competitive-code as an SFT-only corpus …](https://github.com/reliquadotai/reliquary/commit/75bff4c668f609979ee3e1a41a8d6725f45da2bc) — reliquadotai/reliquary
- 2026-10-09 · commit · [feat(env): score reliquary/stdio-program/v1 sources in the sandbox](https://github.com/reliquadotai/reliquary/commit/0a76dc42e2bf084944ca23395327ded8982d4f1f) — reliquadotai/reliquary
- 2026-10-09 · commit · [feat(grader): run a whole stdin program in the sandbox (stdio mode)](https://github.com/reliquadotai/reliquary/commit/7742b187851470c9a3b31f11ba00a21f1645e447) — reliquadotai/reliquary
- 2026-10-09 · commit · [Merge pull request #344 from reliquadotai/fix/expired-leases-revoke-n…](https://github.com/reliquadotai/reliquary/commit/458c6a690a0c27340bb406ee59e44da4cae0cf02) — reliquadotai/reliquary
- 2026-10-09 · commit · [fix(executors): expired leases bench an executor, never quarantine it](https://github.com/reliquadotai/reliquary/commit/8df9d2cc447778e88287cab5e2fe367e216b9e03) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
