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
m git/search "mcp server" sort=stars   # any repo on github → track or fork it
m git/push            # agent writes the message, git commits + pushes
m git/serve           # app+api on :50330 (m git/worker for pm2)
```

The app opens on **Changes** with the commit log already in the left rail —
filter it, click a commit for its diff, click the count to load more.

## The log

```bash
m git/commits n=40                   # newest 40, with per-commit +/−
m git/commits stat=0                 # …without: instant, see below
m git/commits search=polymarket      # grep the messages
m git/commits author=alice skip=40   # page further back
```

`--shortstat` makes git diff every commit it prints — on this repo that is five
seconds versus twenty milliseconds. The sidebar asks for `stat=0` first, paints,
then re-asks with the counts, so the list is never a blank column.

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

## Search GitHub

Find repos you don't own yet — the **Search** tab (and `m git/search`) queries
GitHub's search API, then every hit is one click from being tracked here or
forked to your account.

```bash
m git/search "mcp server" sort=stars      # sort: stars | forks | updated
m git/search agent language=rust n=50
m git/search "" user=anthropics           # everything an org publishes
m git/search "topic:mcp stars:>500 pushed:>2026-01-01"
```

GitHub's own qualifiers work inside the query. Searching works signed-out and
without a GitHub account — anonymously GitHub allows 10 searches a minute, 30
with an account connected (which also searches your private repos). When you
do hit the limit the API says so in one line instead of leaking a stack trace.

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
m git/session days=30                # …trade it for a session that outlives the hour
m git/sessions                       # who's signed in, from where, until when
m git/sign_out id=…                  # end one (no id: all of that key's)
m git/grant 0xADDR role=write        # track/untrack/pull/push + connect their own GitHub
m git/grant 0xADDR role=admin        # + grant/revoke, the OAuth app, other keys' GitHub
m git/revoke 0xADDR
m git/access                         # owners + grants
m git/set_owner 0xADDR               # CLI-only, not exposed over HTTP
m git/set_owner 0xADDR host=1        # …or pin who owns this host, for git
```

The owner (default: this box's key) is always admin. ACL lives off-chain in
`~/.mod/git/access.json`. `GIT_ACCESS_OPEN=1` bypasses auth for local dev.

**Own the host, own the module.** Whoever owns this mod host is an owner here
too — no grant to give yourself. The address comes from `$GIT_OWNER`, else
`~/.mod/git/owner.json`, else the host's owner of record
(`~/.mod/claude/owner.json`), the same file every module on the box reads.
Press **⬡ sign in with wallet** on the ACCESS tab and MetaMask signs a token for
your own address (EIP-191 `personal_sign` over the compact `{"data":…,"time":…}`
— exactly what `m git/token` signs with the box key), so you can commit and push
your changes straight from the app.

**Sign once, push all month.** A signature is only good for an hour, which used
to mean re-signing before every push. Signing in now trades it for a *session*:
`POST /api/session` hands back an opaque `gits.<id>.<secret>` the app keeps in
`localStorage`, and pushing next week opens no wallet. Only the SHA-256 of the
secret is stored (`~/.mod/git/sessions.json`, 0600), and the role is never baked
in — every call re-reads the ACL, so `m git/revoke` ends that key's sessions on
the spot. A session dies on `m git/sign_out`, on **end** in the ACCESS tab, or
after 30 days (`days=` up to a year). If one is revoked mid-use, the app
re-signs once by itself and finishes the push.

## API

| method | endpoint | auth |
|---|---|---|
| GET | `/api/info` `/api/changes` `/api/diff` `/api/commits` `/api/show` `/api/repos` `/api/branches` `/api/search` `/api/github` `/api/github/repos` `/api/oauth` `/api/access` `/api/whoami` `/api/sessions` | open (`/api/sessions` answers for the caller's key only) |
| GET | `/api/oauth/callback?code&state` | state |
| POST | `/api/track` `/api/untrack` `/api/pull` `/api/message` `/api/commit` `/api/push` `/api/connect` `/api/disconnect` `/api/oauth/start` `/api/oauth/poll` `/api/oauth/url` `/api/session` `/api/signout` | write |
| POST | `/api/oauth/app` `/api/grant` `/api/revoke` | admin |

`/api/commits` takes `n`, `skip`, `stat=0`, `search`, `author`, `branch`, `repo`;
`/api/search` takes `q`, `n`, `sort`, `language`, `user`. Bad arguments answer
400 with the reason, GitHub's errors come back as GitHub worded them.

Reads (`/api/github`, `/api/github/repos`, `/api/search`) resolve against the caller's own key
when a Bearer token is attached, or `?key=0x…`. Write calls act as the caller's
key; only an admin may pass `address` to act as another.
