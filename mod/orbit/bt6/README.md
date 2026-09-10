# bt6

A single interface for the **BT6 arsenal** — every public app in the Pliny /
BT6 red-team toolkit listed at [bt6.gg/#arsenal](https://bt6.gg/#arsenal),
gathered behind one launcher console.

BT6 is an independent frontier-AI red team stewarded by Pliny the Liberator.
This module is a **directory / launcher**: it catalogs each tool and links out
to it. It does not run any exploit — it's a front door to the open toolkit.

Made with ♥ pliny.

## Layout

```
bt6/
  config.json      module manifest (ports, routing)
  mod.py           catalog + API (ARSENAL is the single source of truth)
  app/
    server.py      zero-dep static + JSON server
    index.html     the arsenal console
  README.md
```

## API (mod.py)

| call | returns |
| --- | --- |
| `m.mod('bt6')()` / `.info()` | module info + arsenal count |
| `.arsenal(tag=None)` | full catalog, or filtered by tag |
| `.get(id)` | a single arsenal entry by id |
| `.tags()` | the distinct tags |
| `.readme()` | this file |

The app also serves `GET /bt6/arsenal.json` from the same `ARSENAL` list so the
frontend and the API never drift.

## Run

```bash
python app/server.py        # serves the console on :50591 (/bt6)
```

- API port: **50590**
- App port: **50591**  (base path `/bt6`, `route: true`)

## The arsenal

| app | what it is |
| --- | --- |
| **L1B3RT4S** | The liberation library — freedom prompts for every major model |
| **CL4R1T4S** | Leaked, verified system prompts (ChatGPT, Claude, Gemini, Grok…) |
| **G0DM0D3** | Demo of what models say once alignment scaffolding gives way |
| **0BL1T3R4TUS** | Refusal-direction ablation for open-weight LLMs |
| **T3MP3ST** | Multi-agent autonomous red-teaming / pentest platform |
| **ST3GG** | Steganography toolkit — multimodal covert-channel payloads |
| **P4RS3LT0NGV3** | Prompt-engineering & payload-crafting reference |
| **L34KHVB** | Community hub for leaked AI system prompts |
| **V3SP3R** | AI hardware-hacking companion (smart-glasses) |
| **GL0SS0P3TR43** | Constructed-language generator with steganographic encoding |
| **GL4SS** | Spatiotemporal image/video engine |
| **3NTH34** | Real-time psychedelic / music-reactive visuals |
| **PL1NY.TV** | AI-curated dispatches from latent space |
| **BT6** | The red-team collective itself |
| **BASI Community** | The BASI Discord for red-teamers & prompt engineers |

> URLs and the tool set are pulled from bt6.gg and pliny.gg; the site sits
> behind a bot challenge, so if a link 404s or moves, update `ARSENAL` in
> `mod.py` — everything else renders from that list.
