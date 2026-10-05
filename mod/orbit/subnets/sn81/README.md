# sn81 — Reliquary ᚠ

The RL layer of Bittensor

Bittensor subnet **81** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-05 (block 9214666).

Links: [github](https://github.com/reliquadotai/reliquary) · [url](https://www.reliqua.ai/) · [discord](https://discord.com/channels/799672011265015819/1493247592551678012)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.006087 | +0.00% | -0.17% | +11.08% | 33,965 | 17,900 | 436.78 |

## Last 24h flow

54 trades by 32 coldkeys · 16 buys (210.91 τ) / 38 sells (223.17 τ) · net -12.25 τ

## News

- 2026-10-04 · commit · [Merge pull request #314 from reliquadotai/fix/weight-only-period-time](https://github.com/reliquadotai/reliquary/commit/3ac492c9123036334756fc55bee84149e7f95073) — reliquadotai/reliquary
- 2026-10-04 · commit · [fix(weights): import time for the period-settled replay](https://github.com/reliquadotai/reliquary/commit/17860c728bcc9c768340d7237b0b3c7cb67eb2fb) — reliquadotai/reliquary
- 2026-10-04 · commit · [Merge pull request #313 from reliquadotai/feat/agentic-corpus-split](https://github.com/reliquadotai/reliquary/commit/8c33bbce69708ed282dab6885d2bfa4219ad597d) — reliquadotai/reliquary
- 2026-10-04 · commit · [fix(corpus): check the replay lease on every hot add, refuse a bad pi…](https://github.com/reliquadotai/reliquary/commit/92e3a80c9f4b3181ba1f593faa8a5497113bb2cc) — reliquadotai/reliquary
- 2026-10-04 · commit · [fix(corpus): review minors for the split episode front](https://github.com/reliquadotai/reliquary/commit/eedd75c23e3069930fe14a3b1c8cfb928628ed22) — reliquadotai/reliquary
- 2026-10-04 · commit · [Merge pull request #312 from reliquadotai/feat/agentic-corpus-v1](https://github.com/reliquadotai/reliquary/commit/178590ae86f969e6c308d3f578953920bc193f38) — reliquadotai/reliquary
- 2026-10-04 · commit · [test(miner): check the mine-agentic option list, not ANSI-styled help](https://github.com/reliquadotai/reliquary/commit/35247d7d8c8a781350bf02437cd01ddf55e6884d) — reliquadotai/reliquary
- 2026-10-04 · commit · [fix(corpus): keep a voteless replay out of attempts unjudged](https://github.com/reliquadotai/reliquary/commit/40ef55b341bb8f7861e69b37894be3c545388d4d) — reliquadotai/reliquary

## Use

```bash
m subnets.sn81/info        # live identity + market (snapshot if bt is down)
m subnets.sn81/news        # scraped news
m subnets.sn81/trades      # 24h alpha tape
m subnets.sn81/daily       # daily candles
python3 orbit/subnets/sn81/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
