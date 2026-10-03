# vidz

Make a film of any form from a prompt: a **60-second short by default**, any length you ask for.

```
prompt ──► planner ──► shots (≤ provider max clip, e.g. 6 × 10s)
                           │  each shot: POST → 402 → sign USDC → retry → poll → clip
                           ▼
               ~/.vidz/projects/<id>/shot_00.mp4 …  ──► film.mp4 (ffmpeg) | playlist.m3u
```

## Why x402

Every model is bought **per request with USDC on Base** over [x402](https://x402.org):
no accounts, no API keys, no subscriptions. The wallet on this machine signs a
gasless EIP-3009 `transferWithAuthorization` (it needs USDC, not ETH). Both wire
versions are spoken: v1 (`X-PAYMENT`, requirements in the body) and v2
(`PAYMENT-SIGNATURE`, `PAYMENT-REQUIRED` header, CAIP-2 networks).

## Quick start

```bash
m vidz/wallet create=1          # local wallet in ~/.vidz/wallet.json (0600); fund it with USDC on Base
m vidz/quote "a fox crossing a neon city at night"          # price the 60s film, pays nothing
m vidz/make  "a fox crossing a neon city at night"          # dry run: saves a project, plan + quote
m vidz/render <project_id> confirm=1                        # pay, render, stitch
```

One step: `m vidz/make "<prompt>" confirm=1`. Options:

| arg | default | meaning |
|---|---|---|
| `seconds` | 60 | film length (split into clips ≤ the provider's max) |
| `model` | provider default | e.g. `veo-3.1-lite`, `veo-3.1`, `kling-3.0`, `wan-2.7`, `seedance-2.5` |
| `provider` | `treza` | any enabled entry in `config.json → providers` |
| `aspect` | `16:9` | `9:16` for vertical shorts |
| `shots` | auto | your own storyboard: `shots='["wide city","close on the fox","dawn"]'` |
| `style` | – | appended to every shot for continuity ("35mm, teal-orange grade") |
| `max_usd` | 8 | hard budget for the whole film; over-quote films are refused, each payment is capped by what is left |
| `confirm` | false | nothing is paid without it |

A run that stops (failed shot, budget hit, timeout) is resumable:
`m vidz/render <id> confirm=1` skips clips already on disk.

## Prices seen 2026-10-02 (treza, 10s clip)

| model | $/10s | 60s film |
|---|---|---|
| veo-3.1-lite (default) | 0.45 | ~2.70 |
| minimax-h3 | 0.84 | ~5.04 |
| wan-2.7 | 1.40 | ~8.40 |
| kling-3.0 | 1.77 | ~10.62 |

Always `m vidz/quote` first; prices are read live from the 402, never hard-coded.

## Providers are config, not code

```json
"myprovider": {
  "enabled": true,
  "url": "https://…/x402/video",
  "max_clip_seconds": 10,
  "models": ["…"], "default_model": "…",
  "body": {"prompt": "{prompt}", "duration": "{seconds}", "aspect_ratio": "{aspect}", "model": "{model}"}
}
```

Responses are read loosely: the video URL is any `video`/`video_url`/`url`/`output` (or an
`.mp4` anywhere), and async jobs are polled through `X-Status-Url`, `Location` or a
`status_url` field. Shipped: **treza** (live), **render402** (disabled: domain down),
**x402gate** (WaveSpeed: Wan/Kling/Sora; set the model path, then enable).

## Local-first

- Projects, clips, receipts and the key live in `~/.vidz` (override with `VIDZ_HOME`); the key can instead come from `VIDZ_PRIVATE_KEY`. It is never written to config.
- No ffmpeg? You still get the ordered clips and a `playlist.m3u`. Install ffmpeg (`nix profile install nixpkgs#ffmpeg`) and run `m vidz/assemble <id>` to get one `film.mp4`.
- Dependencies: `requests`, `eth_account`.

## Layout

```
mod.py              Mod: wallet · plan · quote · make · render · assemble · projects
vidzkit/x402.py     402 parsing (v1+v2), EIP-3009 signing, quote/pay
vidzkit/providers.py config-driven provider adapter + async polling
vidzkit/planner.py  seconds → shots, story arc beats, custom storyboards
vidzkit/assemble.py ffmpeg concat (copy, re-encode fallback) / playlist
test/test_vidz.py   offline e2e: fake x402 seller, real signatures, v1 + v2
```

`m vidz/test` or `pytest mod/orbit/vidz/test` runs the offline end-to-end check.

## Next

- An LLM pass that turns a one-line idea into a real storyboard (via the local `model` module).
- Narration and music tracks from x402 audio providers, mixed in at assemble time.
- Image-to-video continuity: feed each shot's last frame into the next shot.
- A small console app on :51100.
