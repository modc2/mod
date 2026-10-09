# sn90 — KubeTEE テ

KubeTEE AI Factory: Confidential Computing TEE Multi-Cluster K8s

Bittensor subnet **90** · generated daily by `orbit/subnets` from the local `orbit/bt` index · updated 2026-10-09 (block 9243466).

Links: [github](https://github.com/KubeTEE-AI/kubetee-subnet) · [url](https://kubetee.ai) · [discord](https://discord.gg/KUeXm9XQG4)

## Market

| price (τ) | 1h | 24h | 7d | mcap (τ) | pool τ | 24h vol (τ) |
|---|---|---|---|---|---|---|
| 0.021456 | +0.28% | +0.84% | -5.19% | 13,749 | 4,114 | 1,840 |

## Last 24h flow

258 trades by 106 coldkeys · 150 buys (926.50 τ) / 108 sells (895.91 τ) · net 30.59 τ

## News

- 2026-10-08 · commit · [docs(nemotron-omni): 256k context verified + NIM 2.0.13 + gateway mod…](https://github.com/KubeTEE-AI/kubetee-subnet/commit/feeb7495bb93a900a76f63af170bd0404187f871) — KubeTEE-AI/kubetee-subnet
- 2026-10-08 · commit · [docs(nemotron-omni): sync with NVIDIA build.nvidia.com model card](https://github.com/KubeTEE-AI/kubetee-subnet/commit/d114ca972a815dfde1211e36e0af2c018940e568) — KubeTEE-AI/kubetee-subnet
- 2026-10-08 · commit · [docs(nemotron-omni): remove the License section from the API doc](https://github.com/KubeTEE-AI/kubetee-subnet/commit/cc502f2a0ce0af867294315f3168b186ed78ee03) — KubeTEE-AI/kubetee-subnet
- 2026-10-08 · commit · [docs(nemotron-omni): attribute license to NVIDIA, not the HF checkpoint](https://github.com/KubeTEE-AI/kubetee-subnet/commit/2915f202da525de1e9be6a4ab9944fce5489fb50) — KubeTEE-AI/kubetee-subnet
- 2026-10-08 · commit · [docs(subnet): drop COMPUTE-ACCESS.md; Nemotron-Omni doc gets runnable…](https://github.com/KubeTEE-AI/kubetee-subnet/commit/f4ec9f53f27684bafd1d88c3f5130a110a3efd11) — KubeTEE-AI/kubetee-subnet
- 2026-10-06 · commit · [docs(h3): note /v1/videos/sync exists in the serving stack but is not…](https://github.com/KubeTEE-AI/kubetee-subnet/commit/d96942841926eb294c12a626736fd7a4333230fe) — KubeTEE-AI/kubetee-subnet
- 2026-10-06 · commit · [docs(h3): publish the H3 video generation API reference](https://github.com/KubeTEE-AI/kubetee-subnet/commit/6ddd2a01b3108524c0c2cfab5f4e78a6df4ebafb) — KubeTEE-AI/kubetee-subnet
- 2026-09-24 · commit · [docs(attestation): TLS-possession proof on client-facing attestation](https://github.com/KubeTEE-AI/kubetee-subnet/commit/c9cb76085cdbf8171a8d507a85965e5f931ad352) — KubeTEE-AI/kubetee-subnet

## Use

```bash
m subnets.sn90/info        # live identity + market (snapshot if bt is down)
m subnets.sn90/news        # scraped news
m subnets.sn90/trades      # 24h alpha tape
m subnets.sn90/daily       # daily candles
python3 orbit/subnets/sn90/mod.py  # or import it: Mod().info()
```

_config.json, README.md and data.json are regenerated every day — put hand-written code in a new file next to mod.py._
