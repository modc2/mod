# sn120 — Affine ⴷ

Reason Mining

Bittensor subnet **120** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-07 (block 9229089).

Links: [github](https://github.com/AffineFoundation/affine) · [url](https://www.affine.io) · discord `consttt`

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.046520 | +0.03% | -0.07% | +0.39% | 205,233 | 77,380 | 918.86 |

## Last 24h flow

237 trades by 113 coldkeys · 69 buys (247.89 τ) / 168 sells (464.53 τ) · net -216.64 τ

## News

- 2026-10-07 · commit · [docs: record qualified evaluation staging and latest heldout result](https://github.com/AffineFoundation/affine/commit/49610268a5270a1277a256cf5d2725a7aab66401) — AffineFoundation/affine
- 2026-10-07 · commit · [docs: align live native training and eight-worker status](https://github.com/AffineFoundation/affine/commit/3a07fb469df339ae97ae8428387f8f43e7b9cd36) — AffineFoundation/affine
- 2026-10-07 · commit · [docs: report first live native grading result](https://github.com/AffineFoundation/affine/commit/3b2a2b770ae7c4ee287643af717edf7710dbf385) — AffineFoundation/affine
- 2026-10-07 · commit · [docs: distinguish native admission and wider evaluation repair status](https://github.com/AffineFoundation/affine/commit/7f376bf61dca3bb35ef8a280ae2180705121732d) — AffineFoundation/affine
- 2026-10-07 · commit · [Reserve fresh original bytecode prefixes before requests and suppress…](https://github.com/AffineFoundation/affine/commit/1c40aa36a1f3fbcf08f8df8a3d27fcba918ea3db) — AffineFoundation/affine
- 2026-10-06 · commit · [Add signed bounded parallel learner capture without changing historic…](https://github.com/AffineFoundation/affine/commit/2062935849892691db7ecbeac407dce8e0791f3f) — AffineFoundation/affine
- 2026-10-06 · commit · [Distinguish proof population from bounded training capture coverage](https://github.com/AffineFoundation/affine/commit/23e8314e482f81c56d021d962c2e220c2cd80491) — AffineFoundation/affine
- 2026-10-06 · commit · [Record observed training updates and trajectory length confound](https://github.com/AffineFoundation/affine/commit/bb60703ff9dc03326c635c0005069b3a3e3fdbc8) — AffineFoundation/affine

## Use

```bash
m subnets.sn120/info        # live identity + market (snapshot if bt is down)
m subnets.sn120/news        # scraped news
m subnets.sn120/trades      # 24h alpha tape
m subnets.sn120/daily       # daily candles
python3 orbit/subnets/sn120/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
