# sn5 — Hone ε

Hone (SN5) is an RLVR subnet: reinforcement learning from verifiable rewards. Miners are paid to solve real software-engineering tasks- repository patches submitted as git diffs and terminal tasks submitted as scripts with full trajectories. Validators lease tasks from a problem server and grade every response deterministically in isolated Docker verifiers, so rewards reflect verified outcomes only. Each scored solution becomes high-quality, verifiable RL training data for post-training code and agent models. Docs: https://github.com/hone-subnet-org/hone-subnet · Dashboard: https://honedashboard.com

Bittensor subnet **5** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-18 (block 9308119).

Links: [github](https://github.com/hone-subnet-org/hone-subnet) · [url](https://honedashboard.com)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.012607 | +0.00% | +0.19% | -1.00% | 75,961 | 33,613 | 126.84 |

## Last 24h flow

180 trades by 35 coldkeys · 24 buys (34.69 τ) / 156 sells (76.03 τ) · net -41.34 τ

## News

- 2026-10-12 · commit · [Merge pull request #19 from hone-subnet-org/no-speed-penalty](https://github.com/hone-subnet-org/hone-subnet/commit/0f578e19764b6be096650d8740d7161a7f960b5a) — hone-subnet-org/hone-subnet
- 2026-10-12 · commit · [Switch the speed factor off: every pass pays in full](https://github.com/hone-subnet-org/hone-subnet/commit/21fb20f77403f4b7318657422cb8c6d97b9bcfd4) — hone-subnet-org/hone-subnet
- 2026-10-06 · commit · [Merge pull request #18 from hone-subnet-org/score-window-100](https://github.com/hone-subnet-org/hone-subnet/commit/78cd4b888f7a69b3c1410a5afa7c6afb12e3ef6a) — hone-subnet-org/hone-subnet
- 2026-10-06 · commit · [Shrink the score window to the latest 100 observations](https://github.com/hone-subnet-org/hone-subnet/commit/5a6546d80b8ce12287943a8c461bda518f623c45) — hone-subnet-org/hone-subnet
- 2026-10-02 · commit · [Merge pull request #16 from hone-subnet-org/owner-by-hotkey](https://github.com/hone-subnet-org/hone-subnet/commit/587c9fd498c13767b528989b867a5ad8b24cb973) — hone-subnet-org/hone-subnet
- 2026-10-02 · commit · [Exclude the subnet owner by hotkey, not by assuming it is UID 0](https://github.com/hone-subnet-org/hone-subnet/commit/e75e8e4b0757afc85b23c337b8bee61d6c053850) — hone-subnet-org/hone-subnet
- 2026-09-30 · commit · [Merge pull request #15 from hone-subnet-org/dedup-and-wider-sampling](https://github.com/hone-subnet-org/hone-subnet/commit/5f254a994ccc04773597b8ad9a393c31f2f5173f) — hone-subnet-org/hone-subnet
- 2026-09-30 · commit · [Name the pool size in the lease, two CPUs per grading, 32 notices in …](https://github.com/hone-subnet-org/hone-subnet/commit/3a500d48cda47f189a393faa5f9ff5b6bb11eb84) — hone-subnet-org/hone-subnet

## Use

```bash
m subnets.sn5/info        # live identity + market (snapshot if bt is down)
m subnets.sn5/news        # scraped news
m subnets.sn5/trades      # 24h alpha tape
m subnets.sn5/daily       # daily candles
python3 orbit/subnets/sn5/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
