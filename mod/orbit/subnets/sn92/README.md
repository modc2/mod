# sn92 — REAX र

Verified decision-model inference on Bittensor.

Bittensor subnet **92** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-16 (block 9293868).

Links: [github](https://github.com/reaxlabs/reax-subnet) · [url](https://reax.co/)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.034085 | +3.35% | -14.02% | +2447.49% | 7,049 | 2,431 | 13,494 |

## Last 24h flow

2221 trades by 294 coldkeys · 1010 buys (6,584 τ) / 1211 sells (6,842 τ) · net -257.81 τ

## News

- 2026-10-15 · release · [v1.2.0-rc4](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc4) — reaxlabs/reax-subnet
- 2026-10-15 · commit · [release 1.2.0rc4: capacity probe timing (per-UID windows from the fir…](https://github.com/reaxlabs/reax-subnet/commit/de710addb126a8e642623454d23ec9d86db1a964) — reaxlabs/reax-subnet
- 2026-10-15 · commit · [Merge branch 'fusion/sn92-fast-launch-20261015-probetiming-own' into …](https://github.com/reaxlabs/reax-subnet/commit/6990ee94cfb96b064fcefb42e10f220b3d9fb5bd) — reaxlabs/reax-subnet
- 2026-10-15 · commit · [probe: open each UID's window at the validator's first send to it; la…](https://github.com/reaxlabs/reax-subnet/commit/489573fe2ebf6114d1bc3055a1a101a0d7758425) — reaxlabs/reax-subnet
- 2026-10-15 · commit · [bench_probe: internet-like probe bench (RTT, GPU service time, both p…](https://github.com/reaxlabs/reax-subnet/commit/db0ebda463f7336f0c333621a85f7eaca45310e8) — reaxlabs/reax-subnet
- 2026-10-15 · release · [v1.2.0-rc3](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc3) — reaxlabs/reax-subnet
- 2026-10-15 · commit · [spec-eval: read /dev.jsonl (published HF dataset layout); d…](https://github.com/reaxlabs/reax-subnet/commit/71ccfe0085b72947e092bf5d28a450221d865ebb) — reaxlabs/reax-subnet
- 2026-10-15 · release · [v1.2.0-rc2](https://github.com/reaxlabs/reax-subnet/releases/tag/v1.2.0-rc2) — reaxlabs/reax-subnet

## Use

```bash
m subnets.sn92/info        # live identity + market (snapshot if bt is down)
m subnets.sn92/news        # scraped news
m subnets.sn92/trades      # 24h alpha tape
m subnets.sn92/daily       # daily candles
python3 orbit/subnets/sn92/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
