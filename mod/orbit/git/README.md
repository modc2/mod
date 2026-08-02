# git — repo change tracker for the mod protocol

Tracks **every change in the mod repo** — staged, modified, deleted and
untracked files with per-file `+/−` line counts, branch, HEAD, ahead/behind and
full unified diffs — plus any other repo you point it at: a local checkout or a
GitHub repo (cloned on `track`, private ones included once GitHub is connected).

Then commits them for you: the **agent** module reads the diff and writes the
message, this module stages, commits and pushes it over your GitHub account.

One zero-dependency server on **:50330** serves the app (`/`) and the JSON API
(`/api/*`), tolerating the gateway prefix either way, so caddy auto-routes
`modc2.com/git` (app) and `modc2.com/api/git` (API) straight from
`config.json` (`route: true`).

## Quick start

```bash
m git                 # info: what's tracked, github status, owner, grants
m git/changes         # ALL changes in the mod repo
m git/changes repo=agent diff=1
m git/commits n=20
m git/push            # agent writes the message, git commits + pushes
m git/serve           # app+api on :50330 (m git/worker for pm2)
```

## Commits the agent writes

The **agent** module is a dependency: it reads the pending diff and proposes
the message. Everything that touches the repo stays here — staging, the commit,
and the push over your GitHub account, behind this module's ACL.

```bash
m git/message                     # just the proposal — nothing is staged
m git/message hint="split the ACL out"   # steer it
m git/message free=1              # run it on the agent's free models
m git/commit                      # stage + commit with that message
m git/commit msg="fix the parser"        # …or write your own; the agent is skipped
m git/commit files='["mod.py"]'   # stage only these
m git/push                        # commit + push
```

In the app the **Changes** tab has a commit box: leave it empty and *✦ let the
agent write it* fills it in (the model that wrote it is named next to the
button), edit anything you like, then **commit** or **commit + push**.

`message` returns `by: agent` with the model, or `by: fallback` with the error
when the agent can't be reached — a missing API key or a flat provider never
blocks a commit, it just falls back to a plain `update N files in …` summary.
Prompt gets at most 60k chars of diff. `/api/message` spends model credits, so
it is write-gated like the rest.

## Other repos

```bash
m git/track ~/some/checkout          # local repo
m git/track owner/repo               # cloned into ~/.mod/git/repos
m git/track owner/repo branch=dev
m git/untrack name
m git/repos                          # per-repo change summary
```

The mod repo is always tracked and cannot be untracked.

## GitHub — OAuth, attached to a key

Every mod key keeps its **own** GitHub connection: clone, pull and push run as
whoever is signed in, and the owner's account is the fallback for keys without
one.

Register one OAuth app for the box (github.com → Settings → Developer settings →
**OAuth Apps**; tick *Enable Device Flow*), callback URL
`https://modc2.com/git/oauth/callback`:

```bash
m git/oauth_app <client_id> [client_secret]   # secret only for the redirect flow
m git/oauth                                   # → user code + verification URL
m git/oauth_poll <session> wait=120           # blocks until you approve
m git/github                                  # account, how it was connected, rate limit
m git/github_repos                            # your repos (private too) — 1-click track
m git/disconnect
```

In the app: **GitHub → Connect with GitHub**. With a client secret that's a
normal redirect (`/oauth/callback` → token → attached to your key); without one
it shows a device code and polls until you approve. `state`/`device_code` are
held server-side and bound to the key that started the flow, so a stray callback
can't graft an account onto someone else's key.

A personal access token still works — `m git/connect <pat>` or *use a token
instead* in the app. Either way the token is validated against `api.github.com`,
stored **off-chain** in `~/.mod/git/github.json` (0600), never returned by any
endpoint (only a `…tail`), and used for private clones, authenticated push/pull
and higher API rate limits. `$GITHUB_TOKEN` is the last fallback;
`$GITHUB_CLIENT_ID`/`$GITHUB_CLIENT_SECRET` configure the OAuth app from the
environment.

## Access

Reads are open. Writes require `Authorization: Bearer <signed token>` from the
shared auth module (`m.mod('auth')`, 1h TTL):

```bash
m git/token                          # mint a token (paste into the app's ACCESS tab)
m git/grant 0xADDR role=write        # track/untrack/pull/push + connect their own GitHub
m git/grant 0xADDR role=admin        # + grant/revoke, the OAuth app, other keys' GitHub
m git/revoke 0xADDR
m git/access                         # owner + grants
m git/set_owner 0xADDR               # CLI-only, not exposed over HTTP
```

The owner (default: this box's key) is always admin. ACL lives off-chain in
`~/.mod/git/access.json`. `GIT_ACCESS_OPEN=1` bypasses auth for local dev.

## API

| method | endpoint | auth |
|---|---|---|
| GET | `/api/info` `/api/changes` `/api/diff` `/api/commits` `/api/repos` `/api/branches` `/api/github` `/api/github/repos` `/api/oauth` `/api/access` `/api/whoami` | open |
| GET | `/api/oauth/callback?code&state` | state |
| POST | `/api/track` `/api/untrack` `/api/pull` `/api/message` `/api/commit` `/api/push` `/api/connect` `/api/disconnect` `/api/oauth/start` `/api/oauth/poll` `/api/oauth/url` | write |
| POST | `/api/oauth/app` `/api/grant` `/api/revoke` | admin |

Reads (`/api/github`, `/api/github/repos`) resolve against the caller's own key
when a Bearer token is attached, or `?key=0x…`. Write calls act as the caller's
key; only an admin may pass `address` to act as another.
