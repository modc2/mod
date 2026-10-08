# sn5 — Hone ε

Hone training

Bittensor subnet **5** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-08 (block 9236282).

Links: [github](https://github.com/hone-subnet-org/hone-subnet) · [url](https://honedashboard.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.012796 | +0.01% | +0.58% | -0.21% | 76,212 | 33,734 | 143.99 |

## Last 24h flow

141 trades by 23 coldkeys · 9 buys (62.17 τ) / 132 sells (20.37 τ) · net 41.79 τ

## News

- 2026-10-06 · commit · [Merge pull request #18 from hone-subnet-org/score-window-100](https://github.com/hone-subnet-org/hone-subnet/commit/78cd4b888f7a69b3c1410a5afa7c6afb12e3ef6a) — hone-subnet-org/hone-subnet
- 2026-10-06 · commit · [Shrink the score window to the latest 100 observations](https://github.com/hone-subnet-org/hone-subnet/commit/5a6546d80b8ce12287943a8c461bda518f623c45) — hone-subnet-org/hone-subnet
- 2026-10-02 · commit · [Merge pull request #16 from hone-subnet-org/owner-by-hotkey](https://github.com/hone-subnet-org/hone-subnet/commit/587c9fd498c13767b528989b867a5ad8b24cb973) — hone-subnet-org/hone-subnet
- 2026-10-02 · commit · [Exclude the subnet owner by hotkey, not by assuming it is UID 0](https://github.com/hone-subnet-org/hone-subnet/commit/e75e8e4b0757afc85b23c337b8bee61d6c053850) — hone-subnet-org/hone-subnet
- 2026-09-30 · commit · [Merge pull request #15 from hone-subnet-org/dedup-and-wider-sampling](https://github.com/hone-subnet-org/hone-subnet/commit/5f254a994ccc04773597b8ad9a393c31f2f5173f) — hone-subnet-org/hone-subnet
- 2026-09-30 · commit · [Name the pool size in the lease, two CPUs per grading, 32 notices in …](https://github.com/hone-subnet-org/hone-subnet/commit/3a500d48cda47f189a393faa5f9ff5b6bb11eb84) — hone-subnet-org/hone-subnet
- 2026-09-30 · commit · [Merge pull request #13 from hone-subnet-org/terminal-task-fixture](https://github.com/hone-subnet-org/hone-subnet/commit/44d3aadc10059a454de23560d96c3c7cbc28d72c) — hone-subnet-org/hone-subnet
- 2026-09-30 · commit · [Merge pull request #14 from hone-subnet-org/fix-relative-state-dir](https://github.com/hone-subnet-org/hone-subnet/commit/4238f551e67b365c61ed7afda6f06af69d7fab0c) — hone-subnet-org/hone-subnet

## Use

```bash
m subnets.sn5/info        # live identity + market (snapshot if bt is down)
m subnets.sn5/news        # scraped news
m subnets.sn5/trades      # 24h alpha tape
m subnets.sn5/daily       # daily candles
python3 orbit/subnets/sn5/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
