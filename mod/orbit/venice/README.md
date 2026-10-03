# venice

Venice AI gateway with **one multimodal chat** — text, image generation/editing,
upscaling and video all happen in the same thread. A tool-calling text model
orchestrates Venice's media endpoints and the results render inline. Two ways
for a user to pay, both gated by **wallet (mod-protocol) auth**:

1. **Bring your own key (BYOK)** — the user pastes their own Venice API key. It
   is encrypted at rest and only ever used to serve that user's own requests.
   Free to us.
2. **Pay per message (x402)** — the user has no key, pays a small USDC amount
   per request over the [x402](https://x402.org) protocol, and the backend
   funds the actual Venice call with **our** key.

The console is **8-bit**: hard 3px frames, offset drop shadows, Press Start 2P
chrome over a VT323 body, a pixel lattice and CRT scanlines. Eleven display
modes ship with it — eight dark, three light — see *Display modes* below.

```
venice/
├── venice/mod.py          # original Python SDK (forward/models/pricing/keys…)
├── src/
│   ├── api/               # Rust (axum) gateway  → :50880
│   │   └── src/
│   │       ├── auth.rs        # verify wallet-signed mod-protocol tokens (k256/EIP-191)
│   │       ├── keystore.rs     # per-user BYOK keys, AES-256-GCM at rest
│   │       ├── venice.rs       # proxy to api.venice.ai (models + streamed chat)
│   │       ├── media.rs        # store/serve generated & uploaded media (/media/:id)
│   │       ├── tools.rs        # image generate/edit/upscale + video tool executors
│   │       ├── agent.rs        # tool-calling loop, streams SSE (status/media/message)
│   │       ├── x402.rs         # 402 challenge + facilitator verify/settle
│   │       └── routes.rs       # /health /models /me /key /chat /agent /media
│   └── app/               # Next.js frontend → :3880, served under /venice
├── ecosystem.config.js    # pm2: venice-api + venice-app
└── start.sh
```

## Run

```bash
./start.sh                 # build + pm2 start (dev)
# or
pm2 start ecosystem.config.js
```

API on `:50880`, app on `:3880/venice`.

## Display modes

Every mode is pure CSS. `data-theme` on `<html>` swaps a block of custom
properties and `data-base` records whether that palette is light or dark; no
component hard-codes a colour, so a light mode is a palette swap and nothing
else. Structure (frame width, radius, shadow, fonts, scanline strength) is a
variable too — that's how one soft mode coexists with ten pixel ones.

| mode | base | |
| --- | --- | --- |
| Arcade | dark | NES cabinet — red, gold, midnight blue *(default)* |
| Atelier | dark | Venetian gold leaf on obsidian, in 8 bits |
| Noir | dark | monochrome ink and brushed steel |
| Lagoon | dark | the water at dawn — aqua on deep teal |
| Commodore | dark | C64 boot screen |
| Vapor | dark | neon dusk — magenta, cyan, chrome |
| Phosphor | dark | green CRT terminal, heavier scanlines |
| Velvet | dark | the original obsidian-glass atelier — no pixels |
| Paper | light | ink on warm newsprint |
| Game Boy | light | DMG-01, four shades of pea soup |
| Bloom | light | rose, cream and ripe plum |

The picker sits top-right (swatch + a one-tap light/dark flip). The choice is
stored under `venice:theme`, re-painted before first paint by the inline script
in `app/src/app/layout.tsx`, and with nothing stored the OS
`prefers-color-scheme` decides — a light-mode machine never gets a black screen.
Ids live in `app/src/lib/theme.ts`; the layout script and `globals.css` mirror
them.

Icons are pixel SVGs (`app/src/components/Pix.tsx`) drawn on a 7×7 grid rather
than emoji or an icon font: a host with no emoji font renders 📎 as tofu, and
pixel fonts carry no symbol range.

### Layout

Signed in, the screen is two columns. The left rail is the *conversation*:
new-conversation, the stored thread list, and the orchestrator model at its
foot. Everything about the *account* lives in the top-right corner
(`app/src/components/Account.tsx`) — a pixel identicon
(`app/src/components/Ident.tsx`, a 5×5 sprite mirrored from a hash of the
address, stable across every display mode) plus the short address, opening a
menu with the full address, the BYOK key, the pay-per-turn toggle where the
deployment offers one, and sign-out / forget-identity. A lit pixel on the chip
means this identity can actually send a turn — a key on file, or the paid path
standing by.

```bash
python3 scripts/shots.py            # screenshot every mode → /tmp/venice-shots
python3 scripts/shots.py arcade     # …or just one
```

## Enabling the paid path

The paid (backend-funded) path advertises itself only when **both** are set in
the environment that launches the processes:

| env | meaning |
| --- | --- |
| `VENICE_API_KEY` | our Venice key, used to fund paid requests |
| `VENICE_X402_RECEIVER` | 0x address that receives the USDC payments |

Optional knobs: `VENICE_X402_NETWORK` (`base` \| `base-sepolia`),
`VENICE_X402_PRICE` (e.g. `0.01`), `VENICE_X402_FACILITATOR`,
`VENICE_X402_ASSET`, `VENICE_MASTER_KEY` (pin the BYOK at-rest key; 64 hex
chars), `VENICE_DATA_DIR` (persist the keystore), `VENICE_SESSION_TTL`.

With only `VENICE_API_KEY` (no receiver) or neither, the gateway runs **BYOK
only** — users must add their own key.

## Auth model

Clients sign a time-bounded `{data, time, key, signature}` envelope and send it
as `Authorization: Bearer <token>`. The gateway recovers the signer address
(secp256k1) and uses it as the per-user identity — no server-side sessions. This
matches `mod/core/server/auth`; the Rust verifier in `auth.rs` is cross-checked
against the Python implementation.

The signer can be **either** identity, and the gateway can't tell them apart —
it only ever sees the recovered address:

- **Wallet** — MetaMask `personal_sign`. The identity *is* your real on-chain
  address: convenient, but every edit is linked to your wallet.
- **Local derivation (anonymous)** — the browser mints a random secp256k1
  keypair (viem `generatePrivateKey`), keeps it in `localStorage`, and signs the
  same EIP-191 envelope itself (`buildLocalModToken` in `app/src/lib/wallet.ts`).
  The address is a throwaway pseudonym with no link to any wallet or chain; the
  private key never leaves the device. Each anonymous identity stores its own
  Venice key under its pseudonym. *Forget identity* wipes the key and orphans the
  server-side BYOK entry. For the privacy-maximalist: edit photos under an
  identity that reveals nothing about you. Because the browser holds the key, an
  anonymous session re-authenticates silently on reload (no signing prompt).

## HTTP API

| method | path | auth | body | description |
| --- | --- | --- | --- | --- |
| GET | `/health` | — | — | liveness |
| GET | `/models` | — | — | Venice model list (public) |
| GET | `/me` | Bearer | — | address, `has_key`, `paid_available`, price |
| POST | `/key` | Bearer | `{key}` | store the caller's BYOK key |
| DELETE | `/key` | Bearer | — | forget the caller's BYOK key |
| POST | `/chat` | Bearer | OpenAI-style | raw chat completion passthrough; BYOK or x402-paid |
| POST | `/agent` | Bearer | `{model, messages, attachments?}` | **multimodal turn** — tool-calling loop, streams SSE (`status`/`media`/`message`/`done`); BYOK or x402-paid |
| POST | `/media` | Bearer | `{data}` | upload an image (base64/data-URL) → `media_id` for the agent to edit/animate |
| GET | `/media/:id` | — | — | serve a stored image/video (capability URL) |

### The agent loop

`/agent` runs a bounded tool-calling loop: the orchestrator model may call
`generate_image`, `edit_image`, `upscale_image`, or `generate_video` (Venice's
`/image/*` and `/video/queue`+`/video/retrieve` endpoints). Each tool stores its
output in the media store and feeds the `media_id` back so the model can chain
steps (generate → upscale → animate). One resolved key (BYOK or one x402
payment) funds **all** Venice calls in the turn. Video is async; the loop polls
and streams `rendering video… Ns` status until the mp4 is ready
(`VENICE_VIDEO_POLL_SECS`, default 300).
