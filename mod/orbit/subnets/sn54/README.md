# sn54 — Yanez ت

Yanez SN54 generates synthetic identities for challenging Yanez humanhood, presence, and uniqueness detection models.

Bittensor subnet **54** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/yanez-compliance/MIID-subnet) · [url](https://www.yanez.ai) · [discord](https://discord.com/channels/799672011265015819/1351934165964296232)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.005920 | -0.02% | -1.82% | -4.51% | 35,815 | 9,753 | 1,741 |

## Last 24h flow

202 trades by 106 coldkeys · 99 buys (845.74 τ) / 103 sells (914.28 τ) · net -68.54 τ

## News

- 2026-10-07 · commit · [P5 c1 exec (#119)](https://github.com/yanez-compliance/MIID-subnet/commit/7018f075944447003b063379302ddac75db6867c) — yanez-compliance/MIID-subnet
- 2026-10-07 · commit · [fixing the time line](https://github.com/yanez-compliance/MIID-subnet/commit/16819c52172776ec9c50cbd84ca2c792edc7f8c9) — yanez-compliance/MIID-subnet
- 2026-10-07 · commit · [changing the wordings](https://github.com/yanez-compliance/MIID-subnet/commit/60426dcee0b145619f80f2c61a3bcc72fb8e6257) — yanez-compliance/MIID-subnet
- 2026-10-07 · commit · [adding in testnet/ sandbox voices](https://github.com/yanez-compliance/MIID-subnet/commit/262601d02465b2c6d1ac0087eb448e38533f7a71) — yanez-compliance/MIID-subnet
- 2026-10-06 · commit · [P5 c1 voice api (#117)](https://github.com/yanez-compliance/MIID-subnet/commit/97b9e16be0f012385e00b49ce57892d13d86e866) — yanez-compliance/MIID-subnet
- 2026-09-29 · blog · [Give Your Agent a Pulse](https://www.yanez.ai/post/give-your-agent-a-pulse) — www.yanez.ai
- 2026-09-25 · commit · [uav -> partner when empty (#114)](https://github.com/yanez-compliance/MIID-subnet/commit/a0218c09a7a9737071aa08cf19e934782196982c) — yanez-compliance/MIID-subnet
- 2026-09-25 · commit · [uav -> partner when empty](https://github.com/yanez-compliance/MIID-subnet/commit/e989f2ee36e9a2336c0d8859cb9c6d7adf305026) — yanez-compliance/MIID-subnet

## Use

```bash
m subnets.sn54/info        # live identity + market (snapshot if bt is down)
m subnets.sn54/news        # scraped news
m subnets.sn54/trades      # 24h alpha tape
m subnets.sn54/daily       # daily candles
python3 orbit/subnets/sn54/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
