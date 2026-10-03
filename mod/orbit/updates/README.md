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

## The daily update

The mod repo's `dev` branch is pushed by a bot, so every commit message reads
`root push · N files · <ts>` — useless as a changelog. What carries the signal is
**which modules the day's files landed in**, so `daily` buckets commits by UTC
calendar day, attributes every touched path to its module, and renders the day as
one post you can paste straight into X or Discord.

```bash
m updates/daily                       # last 7 days of modc2/mod@dev, each with a post
m updates/post                        # THE update for the newest day (X/Twitter text)
m updates/post style=discord          # …as Discord markdown
m updates/post date=yesterday         # a specific day ('today', 'yesterday', YYYY-MM-DD)
m updates/post force=True             # re-emit a day already marked posted
m updates/mark_posted 2026-09-10 posted=False     # un-post a day
m updates/paste                       # ONLY the text, printed raw — select it out of the terminal
```

```
modc2/mod · dev · Thu Sep 10

13 commits · 152 files · 11 modules

▸ polymarket 58
▸ hyperliquid 25
▸ wingman 16
+8 more

https://github.com/modc2/mod/commits/dev
```

**Once per day.** Each day is marked posted the moment you copy it (in the app)
or run `post` (on the CLI), and later runs of the same day come back with
`skip: True` — so a daily cron emits exactly one update:

```bash
0 9 * * *  m updates/post style=discord      # one post a day, 09:00, nothing on a repeat run
```

A cron entrypoint ships with the module and is **already installed** on this box
(`crontab -l`), writing the day's Discord-shaped update to `/tmp/updates-daily.log`
every morning at 09:05 UTC:

```
5 9 * * * /usr/bin/python3 /root/mod/mod/orbit/updates/daily_cron.py >> /tmp/updates-daily.log 2>&1
```

It does **not** mark the day posted — that flag means "a human sent this", and the
app's TO POST badge would start lying if a cron flipped it. Override with
`UPDATES_STYLE` / `UPDATES_DATE` / `UPDATES_REPO` / `UPDATES_BRANCH`.

File attribution uses local `git` when the repo is checked out here (free,
offline, exact); otherwise one GitHub `compare` call per day. `twitter` text is
kept under 280 with links weighted at 23 chars the way X counts them, trimming
the module list to `+N more`; `discord` is capped at 2000.

## Functions

| fn | what it does |
| --- | --- |
| `updates` / `show` | aggregated feed across tracked repos (or one, via `repo=`), newest first, NEW flagged; advances markers |
| `commits` / `history` | raw commit list for a repo+branch (default `modc2/mod`@`dev`); `prefer_local=True` forces `git log` |
| `poll` | returns only commits new since the last poll — for a cron/loop monitor |
| `daily` / `digest` | one entry per UTC day: commits, files, modules touched, authors, plus `post{twitter,discord,markdown}` and a `posted` flag |
| `post` / `tweet` | a single day's update as paste-ready text; marks the day posted and returns `skip: True` if it already went out |
| `mark_posted` | set/clear a day's posted flag (what makes "once per day" hold) |
| `paste` | the post text alone, as a bare string — for piping and terminal copy |
| `track` / `attach` | add a repo to the watchlist (branch defaults to the repo's default; `dev` for the mod repo) |
| `untrack` / `detach` | remove a repo |
| `set_branch` | change which branch a tracked repo follows |
| `repos` | the watchlist with each repo's branch + latest commit |
| `modules` | modules known to the **registrar** (`core/registry`), each with its live URL — `{name, key, registered, cid, updated, app, api, path, desc}`; cached ~90s, `refresh=True` to re-scan |
| `register` | register `updates` itself with the registrar |
| `info` | module + watchlist + auth status + registrar |

Each commit comes back as
`{repo, branch, sha, full_sha, parent, author, date, message, url, new}`
(`parent` is the first parent — it's what lets a whole day be diffed in one call).

## Web app

A zero-dependency web UI (no npm/build step) serves the feed at a single port,
with a JSON API alongside it:

```bash
m updates/serve                 # background; → http://localhost:50180
m updates/serve port=50180 background=False   # run in the foreground
m updates/kill                  # stop it
```

The UI has three views, switched with the segmented control in the header:

- **Daily** — one card per day: the date, the repo and its **branch chip**, the
  day's stats, module chips, and the rendered post with a **copy** button.
  Switch the format with the X / Twitter · Discord · Plain control (the choice
  sticks), pick a 7/14/30-day window, and watch the amber **TO POST** badge —
  copying a day marks it **POSTED**, so the tab count is "days still owed".
- **Feed** — the merged commit feed (mod `dev` by default), repo filter pills, a
  **NEW** badge on unseen commits, a "track owner/repo" box, and a "mark read"
  button; auto-refreshes every 60s.
- **Modules** — a launcher backed by the **registrar** (`core/registry`): every
  module it knows about as a card with a registered/local chip, last-updated time,
  and an **open ↗** link to the module's live app (gateway path `modc2.com/<name>`).
  Filter as you type; "rescan" forces a fresh registry walk.

JSON API (same port): `GET /api/daily?days=&repo=&branch=`,
`GET /api/post?date=&style=`, `POST /api/mark_posted {date,repo,branch,posted}`,
`GET /api/updates?n=&repo=`, `GET /api/commits?repo=&branch=&n=`,
`GET /api/repos`, `GET /api/modules?search=&refresh=`, `GET /api/poll`,
`GET /api/info`, `POST /api/track {repo,branch}`, `POST /api/untrack {repo}`,
`POST /api/set_branch {repo,branch}`.

## Continuous monitoring

`poll` is designed to be run on a schedule — pair it with the `loop` skill, e.g.
`/loop 10m m updates/poll`, to get a rolling notification of new commits across
every repo you track.

## Tests

`pytest mod/orbit/updates/tests/test_updates.py` (25 cases; feed/watchlist logic
runs with the API stubbed, plus a real local-`git log` smoke test — no network
required).
