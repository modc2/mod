# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-03 (block 9200279).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006037 | +0.00% | +0.32% | +1.07% | 33,597 | 17,826 | 1,703 |

## Last 24h flow

103 trades by 55 coldkeys · 41 buys (865.67 τ) / 62 sells (835.93 τ) · net 29.73 τ

## News

- 2026-10-03 · commit · [Merge pull request #306 from reliquadotai/fix/corpus-judge-pass-speed](https://github.com/reliquadotai/reliquary/commit/423cefe751e7360aa4529ba6339059a3918df36b) — reliquadotai/reliquary
- 2026-10-03 · commit · [perf(corpus): a math pass in ~75 s: no listing per pass, one re-audit…](https://github.com/reliquadotai/reliquary/commit/af69280e0ef115fedc6d3ae7840815a6c6ca7d72) — reliquadotai/reliquary
- 2026-10-03 · commit · [Merge pull request #305 from reliquadotai/fix/corpus-judge-drand-bound](https://github.com/reliquadotai/reliquary/commit/782e5906643c16fb3a8cb83d6224acc6880c2cc8) — reliquadotai/reliquary
- 2026-10-02 · commit · [fix(corpus): bound a pass's siblings, race each drand round once, pac…](https://github.com/reliquadotai/reliquary/commit/a27e17743663eef7eb404185c2178fdbd90347d0) — reliquadotai/reliquary
- 2026-10-02 · commit · [Merge pull request #303 from reliquadotai/fix/corpus-judge-seed-metadata](https://github.com/reliquadotai/reliquary/commit/124e9241b56b8624eff1d86494427152ae8603e8) — reliquadotai/reliquary
- 2026-10-02 · commit · [Merge pull request #300 from reliquadotai/feat/corpus-split-processes](https://github.com/reliquadotai/reliquary/commit/674e0a2b0f580a71f610c93721f51aad55d43297) — reliquadotai/reliquary
- 2026-10-02 · commit · [test(corpus): check pass counts while the judges run; the final stop …](https://github.com/reliquadotai/reliquary/commit/0f84bd4bf5a0ce3f64b4d3c15efed30a3f5c5a26) — reliquadotai/reliquary
- 2026-10-02 · commit · [test(corpus): give the crash tests' first verdicts 360 s on a loaded box](https://github.com/reliquadotai/reliquary/commit/5d3e4a1115331658742327b389106d61e8d68096) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
