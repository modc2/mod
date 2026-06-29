# updates — a GitHub commit-feed monitor

Watches one or more GitHub repositories and shows their recent commits as an
**updates feed**, flagging which commits are NEW since you last looked.

It ships pre-attached to the **mod repo** (`modc2/mod`) and shows its **`dev`**
branch history by default. `track` any other GitHub repo to fold it into the same
feed.

## Data source

- **GitHub REST API** — anonymous by default; set `$GITHUB_TOKEN` (or `$GH_TOKEN`)
  for higher rate limits and private repos.
- **Local `git log` fallback** — for the checked-out repo the feed works even
  offline or when the API is rate-limited (and shows local commits not yet pushed).

State (the watchlist + per-repo last-seen markers) lives in
`~/.mod/updates/state.json`.

## CLI

```bash
m updates                                  # the feed (mod dev history by default)
m updates/commits                          # mod repo, dev branch, recent commits
m updates/commits repo=foo/bar branch=main n=50
m updates/track owner/repo                 # attach another GitHub repo
m updates/track https://github.com/o/r branch=release
m updates/untrack owner/repo
m updates/set_branch modc2/mod branch=main # follow a different branch
m updates/repos                            # what's being watched + latest commit
m updates/poll                             # only NEW commits since the last poll
```

`repo` accepts `owner/repo`, a full GitHub URL (`https://…`, `git@…`, with or
without `.git`), or a bare name (assumed under `modc2`).

## Functions

| fn | what it does |
| --- | --- |
| `updates` / `show` | aggregated feed across tracked repos (or one, via `repo=`), newest first, NEW flagged; advances markers |
| `commits` / `history` | raw commit list for a repo+branch (default `modc2/mod`@`dev`); `prefer_local=True` forces `git log` |
| `poll` | returns only commits new since the last poll — for a cron/loop monitor |
| `track` / `attach` | add a repo to the watchlist (branch defaults to the repo's default; `dev` for the mod repo) |
| `untrack` / `detach` | remove a repo |
| `set_branch` | change which branch a tracked repo follows |
| `repos` | the watchlist with each repo's branch + latest commit |
| `info` | module + watchlist + auth status |

Each commit comes back as
`{repo, branch, sha, full_sha, author, date, message, url, new}`.

## Web app

A zero-dependency web UI (no npm/build step) serves the feed at a single port,
with a JSON API alongside it:

```bash
m updates/serve                 # background; → http://localhost:50180
m updates/serve port=50180 background=False   # run in the foreground
m updates/kill                  # stop it
```

The page shows the merged commit feed (mod `dev` by default), repo filter pills,
an **NEW** badge on unseen commits, a "track owner/repo" box, and a "mark read"
button; it auto-refreshes every 60s.

JSON API (same port): `GET /api/updates?n=&repo=`, `GET /api/commits?repo=&branch=&n=`,
`GET /api/repos`, `GET /api/poll`, `GET /api/info`, `POST /api/track {repo,branch}`,
`POST /api/untrack {repo}`, `POST /api/set_branch {repo,branch}`.

## Continuous monitoring

`poll` is designed to be run on a schedule — pair it with the `loop` skill, e.g.
`/loop 10m m updates/poll`, to get a rolling notification of new commits across
every repo you track.

## Tests

`pytest mod/orbit/updates/tests/test_updates.py` (13 cases; feed/watchlist logic
runs with the API stubbed, plus a real local-`git log` smoke test — no network
required).
