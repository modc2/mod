# sn67 — Harnyx ط

Deep research as a commodity. Faster, cheaper, traceable research — produced by a competitive swarm of miners on Bittensor SN67.

Bittensor subnet **67** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-06 (block 9221879).

Links: [github](https://github.com/harnyx/harnyx) · [url](https://harnyx.ai/) · [discord](https://discord.com/channels/799672011265015819/1457737666316472351)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.003480 | +0.00% | -0.41% | -5.97% | 6,124 | 2,666 | 9.94 |

## Last 24h flow

80 trades by 18 coldkeys · 3 buys (1.22 τ) / 77 sells (5.32 τ) · net -4.10 τ

## News

- 2026-10-05 · commit · [chore(validator): bump repo-owned validator version to 20261005.post0](https://github.com/harnyx/harnyx/commit/2bfe5fef2798de53f196494ac074ec5cf3692eb7) — harnyx/harnyx
- 2026-10-05 · commit · [Retry incomplete Flash search-worker responses (#1648)](https://github.com/harnyx/harnyx/commit/7efa628e93a1b774b5a2936b76406896537ba58a) — harnyx/harnyx
- 2026-10-04 · commit · [chore(validator): bump repo-owned validator version to 20261004.post1](https://github.com/harnyx/harnyx/commit/3db2e50fea9c098dc16108e597583a45184861ac) — harnyx/harnyx
- 2026-10-04 · commit · [Retry Gemini solver streams that end without a finish reason (#1647)](https://github.com/harnyx/harnyx/commit/5560452205c8a88af0f248cf60c28c9ec5ac1795) — harnyx/harnyx
- 2026-10-04 · commit · [chore(validator): bump repo-owned validator version to 20261004.post0](https://github.com/harnyx/harnyx/commit/d3f529eca0f48c9add03c17cdb221c1d5e69331f) — harnyx/harnyx
- 2026-10-03 · commit · [Publish public Harnyx API docs and LLM-friendly references (#1643)](https://github.com/harnyx/harnyx/commit/886f030103e33c0b4bec73397116952b971eee6b) — harnyx/harnyx
- 2026-10-02 · commit · [chore(validator): bump repo-owned validator version to 20261002.post1](https://github.com/harnyx/harnyx/commit/f015c036a3986b1080a66067e641b825ba6d6559) — harnyx/harnyx
- 2026-10-02 · commit · [chore(validator): bump repo-owned validator version to 20261002.post0](https://github.com/harnyx/harnyx/commit/03366abd6f64ec156d3dc583e840f6bf1e31fd8b) — harnyx/harnyx

## Use

```bash
m subnets.sn67/info        # live identity + market (snapshot if bt is down)
m subnets.sn67/news        # scraped news
m subnets.sn67/trades      # 24h alpha tape
m subnets.sn67/daily       # daily candles
python3 orbit/subnets/sn67/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
