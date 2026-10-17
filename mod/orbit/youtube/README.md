# youtube

YouTube in a frame, signed in as any Google account, with an MCP server that acts as the **user** and as the **creator**. One Python process, stdlib only, one port (`:51400`): the app, a REST API and `POST /mcp`.

**The first page is YouTube.** A full-width embed plays the home feed (your subscriptions when an account is active, trending with an API key, a keyless channel feed otherwise), with a search bar and a grid that loads anything into the frame. Paste any YouTube link, id or `@handle` into the search bar and it opens in the frame.

```
m youtube/serve                                   # app + REST + MCP on 127.0.0.1:51400 (pm2 `youtube`)
open http://localhost:51400/
```

## Why a frame of /embed/ and not youtube.com

`youtube.com` answers every page with `X-Frame-Options: SAMEORIGIN`, so no site can iframe it, signed in or not. YouTube's embed surfaces (`/embed/<video>`, `/embed/videoseries?list=<playlist>`) carry no such header. This module therefore rewrites **every** YouTube address into its framable form:

| you give | the frame gets |
|---|---|
| `youtu.be/ID?t=1m30s`, `/watch?v=`, `/shorts/`, `/live/` | `/embed/ID?start=90` |
| `/playlist?list=PL…` or a bare `PL…` id | `/embed/videoseries?list=PL…` |
| `/channel/UC…`, `@handle`, `/c/name` | `/embed/videoseries?list=UU…` (the channel's uploads playlist, no API needed) |
| a home / search / subscriptions feed | `/embed/FIRST?playlist=ID2,ID3,…` (one frame, next/prev inside it) |

`m youtube/open <anything>` or `GET /open?target=` returns `{kind, id, embed, watch}`; `GET /go?target=` 302s straight to the embed. The "open on youtube ↗" pill is always there for the real site in a new tab.

## Accounts

Google OAuth with your own Google Cloud client, so the module never sees a password and every account is revocable from Google's side. Any number of accounts (and brand channels) can be signed in; one is **active** and every tool takes `account=` to act as another.

1. Google Cloud console → APIs & Services → enable **YouTube Data API v3** and **YouTube Analytics API** → Credentials → OAuth client (Web application) with redirect URI `http://localhost:51400/auth/callback` (and `https://<host>/youtube/api/auth/callback` behind the gateway). Optionally an **API key**, which lets search and trending work with no account.
2. `m youtube/set_client client_id=… client_secret=… api_key=…` (or the ACCOUNTS pane). Stored in `~/.mod/youtube/client.json`, mode 0600.
3. ACCOUNTS → **+ add Google account** (or `m youtube/login`, which prints the consent URL). The callback stores the refresh token in `~/.mod/youtube/accounts.json` and makes the account active.

Nothing private is in `config.json`.

## Who may do what

| ring | tools | who |
|---|---|---|
| open | `yt_open` `yt_search` `yt_video` `yt_channel` `yt_playlist` `yt_trending` `yt_rss` `yt_home` `yt_comments` `yt_status` | anyone (keyless, or the API key) |
| user | `yt_accounts` `yt_use_account` `yt_remove_account` `yt_subscriptions` `yt_feed` `yt_my_playlists` `yt_create_playlist` `yt_playlist_add` `yt_playlist_remove` `yt_rate` `yt_rating` `yt_subscribe` `yt_unsubscribe` `yt_comment` `yt_reply` | box or owner |
| creator | `yt_me` `yt_my_videos` `yt_upload` `yt_update_video` `yt_set_thumbnail` `yt_delete_video` `yt_analytics` `yt_my_comments` `yt_moderate` | box or owner |

"Box" = a loopback client with no proxy header (the `m` CLI, stdio MCP, curl on the machine). "Owner" = a mod-protocol token (`Authorization: Bearer`) whose address is the box key; the app mints one with **local key** on the box or **browser wallet** anywhere. Anyone else gets `401` on account tools, and never touches the API key for anything but open reads. `yt_delete_video` and `yt_moderate(ban=true)` are flagged destructive in the MCP annotations; uploads are private unless told otherwise.

## MCP

```
claude mcp add --transport http youtube http://localhost:51400/mcp
claude mcp add youtube -- python3 -m ytmod.mcp_server          # stdio, run from orbit/youtube (owner)
```

Both transports share `ytmod/mcp_server.py:handle_message`, so they cannot drift. Every tool also has a REST route: `GET|POST /tools/<name>` plus friendly aliases (`/search`, `/home`, `/feed`, `/me`, `/upload`, …; `GET /tools` lists them).

## CLI

```
m youtube/open https://youtu.be/dQw4w9WgXcQ      m youtube/home        m youtube/rss @nasa
m youtube/search "lofi" n=5                       m youtube/trending region=GB
m youtube/accounts · m youtube/use @me · m youtube/logout @me
m youtube/feed · m youtube/subscriptions · m youtube/like <video> · m youtube/subscribe @x · m youtube/comment <video> "text"
m youtube/me · m youtube/my_videos · m youtube/upload path=clip.mp4 title="…" privacy=private
m youtube/update_video <video> title="…" privacy=public · m youtube/thumbnail <video> cover.jpg
m youtube/analytics start=2026-10-01 end=2026-10-15 · m youtube/my_comments moderation=heldForReview
m youtube/status · m youtube/kill · m youtube/test
```

## Layout

```
web/index.html         the app: frame + search + home grid, subscriptions, your channel (studio), accounts, api
ytmod/api.py           resolve()/rss()/home() keyless · Session: Data API v3 as key or account, uploads, Analytics
ytmod/auth.py          Google OAuth: consent url, callback exchange, refresh, revoke
ytmod/store.py         ~/.mod/youtube/{client,accounts,home}.json
ytmod/identity.py      mod-protocol token verification (box / owner)
ytmod/mcp_server.py    TOOLS table (three rings) + JSON-RPC dispatch + stdio main
ytmod/server.py        HTTP: app, REST aliases, /tools, /mcp, /auth/*
test/                  offline: resolve shapes, rss parsing, store, rails, mcp gating, http routes
```

Quota notes: the Data API gives 10 000 units/day per project; `search` costs 100, most reads 1, an upload 1 600. The home feed and channel pages use YouTube's public Atom feeds and cost nothing; with no key and no account, `/video` falls back to YouTube's public oEmbed (title, channel, thumbnail).

Two things YouTube does that the module works around: the feed server answers `404` to a whole IP for a while after a burst of requests (feeds are cached 10 min in-process, served stale on error, fanned out four at a time, and when every feed fails the first page still frames a channel's uploads playlist); and an unembeddable video in a queue is skipped by the player, so the title bar follows what the player reports it is playing, not what was asked for.
