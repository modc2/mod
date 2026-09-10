# grokbot

Grok (xAI) behind one mod, with a per-address account: sign in with a wallet
from the console, keep your own xAI key and your own named bots, chat with live
search over X and the web.

API `:50890` (`/api/grokbot`) · console `/grokbot` · MCP `POST /mcp` (10 tools)

BYOK: every call spends the **caller's** xAI credits. No house key.

## When to reach for it

- anything that wants Grok specifically — its voice, or its live view of X
- "what is X saying about …" — `search=auto` reads X and the web, with citations
- a reusable persona: save a bot once (`grok_bot_save`), run it by name
- giving a human a place to bring their own xAI key without touching a config

Not for: model shopping across vendors (`openrouter`), Claude jobs (`claude`,
`agent`), local models (`liquidai`), embeddings (`embed`), or reading X's API
directly — that is `x`.

## The order that matters

1. `grok_whoami` — did a token resolve, did a key resolve, and from where. Most
   failures here are one of those two being absent, and this says which.
2. `grok_models` — what this key can actually see. xAI needs a key even to list
   them, so an empty list means the key, not the catalog.
3. `grok_chat` — the call. `search='auto'` for anything time-sensitive;
   `bot=<name>` to run a saved persona.
4. `grok_bots` / `grok_bot_save` — only after step 1 shows an address. Bots are
   per account and there is no anonymous shelf to put one on.

## Two headers, two different things

    Authorization: Bearer <mod-protocol token>   who you are
    x-xai-key: xai-…                             whose credits get spent

Signing in is what gives you somewhere to *keep* a key. Sending the key per
request works and stores nothing. In MCP those are the `token` and `key`
arguments on every tool.

## Gotchas

- **A missing key is a 401 from this module; a bad key is a 400 from xAI.** The
  upstream message comes through verbatim — read it before retrying.
- **`search` costs money per source.** It is off unless asked for, on the call
  or on the bot.
- **Bots need an address.** `grok_bots` without a token is a 401, not an empty
  list — that distinction is the whole point of the sign-in.
- **Prices are USD per million tokens.** xAI quotes cents per 100M; the model
  card converts, and keeps the original in `raw`.
