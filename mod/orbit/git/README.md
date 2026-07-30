# git — repo change tracker for the mod protocol

Tracks **every change in the mod repo** — staged, modified, deleted and
untracked files with per-file `+/−` line counts, branch, HEAD, ahead/behind and
full unified diffs — plus any other repo you point it at: a local checkout or a
GitHub repo (cloned on `track`, private ones included once GitHub is connected).

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
m git/serve           # app+api on :50330 (m git/worker for pm2)
```

## Other repos

```bash
m git/track ~/some/checkout          # local repo
m git/track owner/repo               # cloned into ~/.mod/git/repos
m git/track owner/repo branch=dev
m git/untrack name
m git/repos                          # per-repo change summary
```

The mod repo is always tracked and cannot be untracked.

## GitHub

```bash
m git/connect <personal_access_token>
m git/github                          # who's connected, scopes, rate limit
m git/github_repos                    # your repos (private too) — trackable 1-click in the app
m git/disconnect
```

The token is validated against `api.github.com`, stored **off-chain** in
`~/.mod/git/github.json` (mode 0600), never returned by any endpoint (only a
`…tail`), and used for private clones, authenticated push/pull and higher API
rate limits. `$GITHUB_TOKEN` works as a fallback.

## Access

Reads are open. Writes require `Authorization: Bearer <signed token>` from the
shared auth module (`m.mod('auth')`, 1h TTL):

```bash
m git/token                          # mint a token (paste into the app's ACCESS tab)
m git/grant 0xADDR role=write        # track/untrack/pull/push
m git/grant 0xADDR role=admin        # + connect/disconnect/grant/revoke
m git/revoke 0xADDR
m git/access                         # owner + grants
m git/set_owner 0xADDR               # CLI-only, not exposed over HTTP
```

The owner (default: this box's key) is always admin. ACL lives off-chain in
`~/.mod/git/access.json`. `GIT_ACCESS_OPEN=1` bypasses auth for local dev.

## API

| method | endpoint | auth |
|---|---|---|
| GET | `/api/info` `/api/changes` `/api/diff` `/api/commits` `/api/repos` `/api/branches` `/api/github` `/api/github/repos` `/api/access` `/api/whoami` | open |
| POST | `/api/track` `/api/untrack` `/api/pull` `/api/push` | write |
| POST | `/api/connect` `/api/disconnect` `/api/grant` `/api/revoke` | admin |
