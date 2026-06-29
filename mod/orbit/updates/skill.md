---
name: updates
description: GitHub commit-feed monitor. Tracks the mod repo (modc2/mod) and shows its dev-branch commit history by default, and attaches any other GitHub repo into one aggregated updates feed that flags commits new since you last looked. GitHub REST API (optional $GITHUB_TOKEN) with a local `git log` fallback. Use to see what changed, watch repos, or poll for new commits on a schedule.
---

# updates

A commit-feed monitor over GitHub repos. Pre-attached to **`modc2/mod`** and shows
its **`dev`** branch by default; `track` other repos into one merged feed.

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
- `track`/`attach`, `untrack`/`detach`, `set_branch`, `repos`, `info`.

## Notes
- `repo` = `owner/repo`, a GitHub URL, or a bare name (assumed `modc2/…`).
- Data: GitHub REST API (set `$GITHUB_TOKEN`/`$GH_TOKEN` for higher limits / private
  repos), with a local `git log` fallback for the checked-out repo (works offline).
- State (watchlist + markers): `~/.mod/updates/state.json`.
- Each commit: `{repo, branch, sha, full_sha, author, date, message, url, new}`.
- For a rolling monitor: `/loop 10m m updates/poll`.

Tests: `pytest mod/orbit/updates/tests/test_updates.py` (13). Related: `git`,
`gitsearch`, `gitbot`.
