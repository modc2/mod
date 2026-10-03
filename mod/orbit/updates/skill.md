---
name: updates
description: GitHub commit-feed monitor and daily update digest. Tracks the mod repo (modc2/mod) and shows its dev-branch commit history by default, and attaches any other GitHub repo into one aggregated updates feed that flags commits new since you last looked. Rolls each calendar day up into ONE paste-ready update for X/Twitter or Discord — commits, files, and which modules were touched — marked posted so it goes out once per day. GitHub REST API (optional $GITHUB_TOKEN) with a local `git log` fallback. Use to see what changed, watch repos, write the daily update post, or poll for new commits on a schedule.
---

# updates

A commit-feed monitor over GitHub repos. Pre-attached to **`modc2/mod`** and shows
its **`dev`** branch by default; `track` other repos into one merged feed.

## The daily update
```bash
m updates/post                              # THE update for the newest day, X/Twitter text
m updates/post style=discord                # …as Discord markdown
m updates/post date=yesterday               # 'today' | 'yesterday' | YYYY-MM-DD | 'latest'
m updates/daily days=14                     # one entry per UTC day + its post, 3 styles
m updates/mark_posted 2026-09-10 posted=False
m updates/paste                             # ONLY the text, raw — copy it out of the terminal
```
Commit messages on `modc2/mod`@`dev` are all bot pushes (`root push · N files`),
so a day is summarised by **which modules its files landed in**, not by subjects.
Copying a day in the app (or running `post`) marks it posted; a repeat run the
same day returns `skip: True`, which is what makes a daily cron post exactly once:
`0 9 * * * m updates/post style=discord`.

## Common usage
```bash
m updates                                   # feed (mod dev history by default)
m updates/commits repo=owner/repo branch=main n=50
m updates/track https://github.com/o/r      # attach another GitHub repo
m updates/untrack owner/repo
m updates/repos                             # watchlist + latest commit per repo
m updates/poll                             # only NEW commits since last poll
```

## Key functions
- `updates`/`show` — aggregated feed across tracked repos (or one via `repo=`),
  newest-first, NEW-flagged; advances per-repo last-seen markers.
- `commits`/`history` — commit list for a repo+branch (default `modc2/mod`@`dev`);
  `prefer_local=True` forces local `git log`.
- `poll` — returns only commits new since the last poll (for cron/`loop`).
- `daily`/`digest` — per-UTC-day rollup: `{date, commits, files, modules[], authors,
  highlights, posted, post{twitter,discord,markdown}, chars{}}`. Twitter text is
  held under 280 (links weighted at 23) by trimming modules to `+N more`.
- `post`/`tweet` — one day as paste-ready text; marks it posted, `force=True` re-emits.
- `mark_posted` — set/clear a day's posted flag.
- `paste` — the text alone as a bare string (pipes cleanly; doesn't mark posted).
  `daily_cron.py` is the cron entrypoint (installed here: 09:05 UTC →
  `/tmp/updates-daily.log`; env `UPDATES_STYLE`/`UPDATES_DATE`).
- `track`/`attach`, `untrack`/`detach`, `set_branch`, `repos`, `info`.

## Web app
Zero-dep UI + JSON API on one port: `m updates/serve` → http://localhost:50180
Three tabs: **Feed** (NEW badges, repo filter pills, branch chip on every commit,
track box, mark-read; auto-refresh 60s), **Daily** (one card per day with the
rendered post, X/Twitter · Discord · Plain toggle, a copy button that marks the
day posted, and an amber TO POST badge for days still owed), **Modules**.
`m updates/kill` to stop. API: `/api/daily?days=`, `/api/post?date=&style=`,
`POST /api/mark_posted`, `/api/updates`, `/api/commits`, `/api/repos`,
`/api/poll`, `POST /api/track|untrack|set_branch`.

## Notes
- `repo` = `owner/repo`, a GitHub URL, or a bare name (assumed `modc2/…`).
- Data: GitHub REST API (set `$GITHUB_TOKEN`/`$GH_TOKEN` for higher limits / private
  repos), with a local `git log` fallback for the checked-out repo (works offline).
- State (watchlist + markers): `~/.mod/updates/state.json`.
- Each commit: `{repo, branch, sha, full_sha, author, date, message, url, new}`.
- For a rolling monitor: `/loop 10m m updates/poll`.

Tests: `pytest mod/orbit/updates/tests/test_updates.py` (25). Related: `git`,
`gitsearch`, `gitbot`.
