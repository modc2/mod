# sn25 — UR א

The peer to peer privacy network and encryption layer for the internet

Bittensor subnet **25** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-02 (block 9195968).

Links: [github](https://github.com/urfoundation/sn) · [url](https://ur.xyz/) · discord `xcolwell`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006673 | -0.05% | -0.42% | -2.46% | 40,610 | 13,207 | 203.84 |

## Last 24h flow

32 trades by 20 coldkeys · 8 buys (87.95 τ) / 24 sells (114.99 τ) · net -27.04 τ

## News

- 2026-10-02 · commit · [docs: map remaining mainnet implementation and qualification work](https://github.com/urfoundation/sn/commit/414a0863a2e7c4318f446095534bfc89e1ab7e81) — urfoundation/sn
- 2026-10-02 · commit · [docs: record independent inventory scopes and composition failures](https://github.com/urfoundation/sn/commit/3c00c3741d83a9d10a9a07dddb70330ae77ba8ef) — urfoundation/sn
- 2026-10-02 · commit · [docs(mainnet): bind independent durable owner qualification](https://github.com/urfoundation/sn/commit/4a62320d5ad8d7ef612677ccdd42e661c2d0fa04) — urfoundation/sn
- 2026-10-02 · commit · [Record bounded inventory CLI and preparation boundary](https://github.com/urfoundation/sn/commit/926988b7109ea9030b7b709487bf9e33e60a7f99) — urfoundation/sn
- 2026-10-02 · commit · [Record retained-owner qualification and remaining mainnet integration…](https://github.com/urfoundation/sn/commit/19a88008f66b07446f48c1f34e59fcf967987d33) — urfoundation/sn
- 2026-10-02 · commit · [Pin final fixture integration and keep journal provisioning gaps expl…](https://github.com/urfoundation/sn/commit/105e54c3acdc30ce37e32075342dc5f9421beb3f) — urfoundation/sn
- 2026-10-02 · commit · [Bind current statistics fixture integration and immutable runner corr…](https://github.com/urfoundation/sn/commit/4973dfd01e6e610e87e18babe08923e74836d0e5) — urfoundation/sn
- 2026-10-02 · commit · [Merge remote-tracking branch 'origin/main' into docs/server-model-fix…](https://github.com/urfoundation/sn/commit/421613e76ff4d7cc6ea285723938d6705cae1760) — urfoundation/sn

## Use

```bash
m subnets.sn25/info        # live identity + market (snapshot if bt is down)
m subnets.sn25/news        # scraped news
m subnets.sn25/trades      # 24h alpha tape
m subnets.sn25/daily       # daily candles
python3 orbit/subnets/sn25/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
