# grokbot

Grok (xAI) as one mod, with an account behind it.

The xAI API is one endpoint and a bearer token; the interesting question is
*whose* token. This module answers it the way the rest of the fleet does: you
sign in from the website with a wallet, that mints a mod-protocol token, and
the address inside it is the account your xAI key and your saved bots hang off.

API `:50890` · console `/grokbot` · MCP `POST /mcp` (11 tools) · stdlib only,
no dependencies.

**Sign-in is required.** Every route that touches Grok — chat, models, images,
raw, the run ledger — demands a signed mod-protocol token, over REST, MCP and
the console alike. The console shows nothing but the CONNECT WALLET gate until
a wallet has signed. A BYOK key still decides whose credits are spent; it never
substitutes for signing in.

## What a grokbot is

A name, a model and a system prompt — optionally with live search turned on.
Saved against your address, run by name:

```
m grokbot/save_bot name=skeptic system="doubt everything; cite or say you can't"
m grokbot/ask "did SpaceX launch today?" bot=skeptic search=auto
```

`search=auto` lets Grok read X and the web before answering; the citations come
back beside the text.

## Signing in

One door, the same one every module in this fleet verifies: a mod-protocol
token — base64url of `{data, time, key, signature}`, where the signature is an
EIP-191 `personal_sign` over `JSON.stringify({data, time})`.

* **From the website.** Open `/grokbot`, press CONNECT WALLET. MetaMask (or any
  injected wallet) signs, the token goes in `localStorage`, and every request
  carries it as `Authorization: Bearer …`. No wallet extension? The console
  takes a pasted token instead.
* **From a shell or another module.** `m.mod('auth')().token({})` mints the
  same envelope from a local key.
* **From a box with no wallet in front of it.** `GROKBOT_OPEN=1` collapses every
  caller into one local identity. `GET /me` always says when it is on.

Standings: **owner** (the first signed caller claims the deployment; sees
`/stats`) · **signed** (owns their key, their bots and their run ledger,
nothing else) · **anon** (reads the description and health — nothing more;
sign-in is required for anything that touches Grok).

## Keys — BYOK, always

Every call spends the **caller's** xAI credits. This module holds no house key.
Resolution order, per request:

1. the key on the request — `x-xai-key: xai-…` (stored nowhere)
2. the signed-in caller's stored key — `~/.mod/grokbot/users/<address>.json`,
   0600, off-tree, never in this repo
3. `XAI_API_KEY` / `GROK_API_KEY` in the environment
4. the operator's fallback key — `~/.mod/grokbot/key.json`

`GET /me` reports which one resolved and its fingerprint. Nothing echoes a key
back in full, ever — not `/me`, not `/stats`, not `/`.

Get a key at [console.x.ai](https://console.x.ai). xAI requires one even to
*list* models, which is why the console shows an empty model dropdown until you
save one.

## Routes

| route | what |
| --- | --- |
| `GET /` | what this module is, and every route it serves |
| `GET /health` | liveness and tool count |
| `GET /me` | who this token is, whether a key resolved, your bots |
| `POST /key` `DELETE /key` | store / forget your xAI key (signed in) |
| `GET /models` `GET /model?id=` | what your key can see, priced per million tokens |
| `GET /keyinfo` | what xAI says about the key itself |
| `POST /chat` | `{prompt\|messages, model, system, bot, temperature, max_tokens, search, stream}` |
| `GET/POST/DELETE /bots` | your saved bots |
| `GET /runs` `DELETE /runs` | your run ledger — every chat/stream/image as ALL · LIVE · DONE · ERROR |
| `POST /images` | `{prompt, model, n}` |
| `POST /raw` | any xAI route, with the resolved key attached |
| `GET /stats` | accounts and bots on this deployment (owner only) |
| `POST /mcp` | MCP JSON-RPC 2.0 |
| `GET /grokbot` | the browser console |

`stream: true` on `/chat` is an SSE passthrough — xAI's frames, forwarded byte
for byte, which is what the console renders.

## The run ledger

Every spend — a chat, a stream, an image, from the console, the REST API, MCP
or the CLI — opens a LIVE row in your ledger and closes it DONE or ERROR with
the duration, the token usage and the error text if there was one. The console
renders it as the RUNS board: **ALL · LIVE · DONE · ERROR** pills that filter
the list. Per account, newest 200 kept, prompt truncated to a head, the key
never written. A row still LIVE past every timeout is reaped as
`lost — the server went away mid-call`.

## MCP

Eleven tools: `grok_chat`, `grok_models`, `grok_model`, `grok_key_info`,
`grok_whoami`, `grok_set_key`, `grok_bots`, `grok_bot_save`, `grok_bot_delete`,
`grok_runs`, `grok_raw`. Each takes `token` (who you are — required for
anything that touches Grok) and optionally `key` (whose credits). Over HTTP the
server reads both from the request headers instead.

```
m grokbot/mcp_config     # drop-in config for Claude Code / Desktop
python3 mcp.py           # stdio
python3 mcp.py --http    # Streamable HTTP on :50890/mcp
```

## Running it

```
m grokbot/serve          # api + console + mcp on :50890, under pm2
m grokbot/kill
m grokbot/test
```

## From the CLI

```
m grokbot/me                                   # who you are here
m grokbot/set_key key=xai-…                    # operator key; mine=1 for yours
m grokbot/models                               # what your key can see
m grokbot/ask "what is the mod protocol" search=auto
m grokbot/bots
m grokbot/raw path=/chat/completions method=POST body='{…}'
```

## Environment

| var | meaning |
| --- | --- |
| `PORT` | API/console/MCP port (default 50890) |
| `GROKBOT_DIR` | state directory (default `~/.mod/grokbot`) |
| `GROKBOT_MODEL` | default model (default `grok-4-fast`) |
| `XAI_API_KEY` / `GROK_API_KEY` | operator fallback key |
| `GROKBOT_OPEN` | `1` — no wallet in front of this box |
| `GROKBOT_TOKEN_MAX_AGE` | seconds a signed token stays valid (default 604800) |
| `GROKBOT_CACHE_TTL` | seconds the model list is cached (default 600) |
| `GROKBOT_UPSTREAM` | xAI base url (default `https://api.x.ai/v1`) |

## Layout

```
mod.py         the module surface — every fn the protocol exposes
client.py      the xAI client, and the per-address key/bot store
identity.py    sign-in: token → address → standing
api.py         REST + console + MCP on one port (stdlib http.server)
mcp.py         the ten tools, JSON-RPC 2.0, stdio or HTTP
runs.py        the run ledger — ALL · LIVE · DONE · ERROR, per account
console.html   the website: the sign-in gate, key, bots, chat, the runs board
tests/         36 tests; none of them can spend a credit
```
