# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-04 (block 9207487).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006097 | -0.05% | +1.00% | +2.58% | 33,978 | 17,915 | 523.21 |

## Last 24h flow

70 trades by 34 coldkeys · 31 buys (306.29 τ) / 39 sells (216.31 τ) · net 89.98 τ

## News

- 2026-10-03 · commit · [Merge pull request #310 from reliquadotai/design/sft-period-clock](https://github.com/reliquadotai/reliquary/commit/31371e19fd09be11a58eae1367d101ffc2b356cd) — reliquadotai/reliquary
- 2026-10-03 · commit · [Merge pull request #311 from reliquadotai/feat/register-reliquary-sci…](https://github.com/reliquadotai/reliquary/commit/5734d639f57289300592f46c2753e2f6dbe8d101) — reliquadotai/reliquary
- 2026-10-03 · commit · [feat(env): register reliquary-science and declare it in the Teutonic …](https://github.com/reliquadotai/reliquary/commit/421513a486a3e9074ae77777604631976d76c246) — reliquadotai/reliquary
- 2026-10-03 · commit · [fix(corpus): period pay conserves under backlogs, crashes and late wr…](https://github.com/reliquadotai/reliquary/commit/f804c4e8609582677de3f54f405506b6d3d7bd39) — reliquadotai/reliquary
- 2026-10-03 · commit · [docs(design): the entry period is the one after settlement](https://github.com/reliquadotai/reliquary/commit/fcf88118b18d0cf557d316c59205428610d57201) — reliquadotai/reliquary
- 2026-10-03 · commit · [Merge pull request #309 from reliquadotai/feat/agentic-corpus-swe](https://github.com/reliquadotai/reliquary/commit/e701dbee90e2db38b666576dfa441da3d4d74fc4) — reliquadotai/reliquary
- 2026-10-03 · commit · [Merge pull request #308 from reliquadotai/feat/eval-validator-mode](https://github.com/reliquadotai/reliquary/commit/09d68fe84106af3b8f0591e687dd119684d9af73) — reliquadotai/reliquary
- 2026-10-03 · commit · [docs(corpus): replayed actions come from the proven tokens in production](https://github.com/reliquadotai/reliquary/commit/810aac3af9957ff7fff816401d5f3fc07db17de5) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
