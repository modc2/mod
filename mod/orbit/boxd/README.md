# boxd

A Letterboxd lens with no API key.

Letterboxd's own API has been invite-only beta for years, and there is no key
to go and get. So this reads the three things the site serves anyone with a
browser and no account — a member's RSS feed, a film page's schema.org blob,
and the poster wall — and then does the arithmetic the site itself doesn't:

> what does this diary say about the person keeping it, and
> do these two people actually agree about films?

## The doors

Every row below was probed from this box, not assumed. The open ones answer
`200` when asked politely; the shut ones answer `403` no matter how slowly
they are asked. `m boxd/sources` prints this table at runtime, so a door that
closes later says so at call time rather than turning into an empty list.

| door | path | open | what it gives |
|---|---|:--:|---|
| **rss** | `/{member}/rss/` | ✅ | the spine — watches, reviews and lists, with rating, liked, rewatch and watched-date as real fields |
| **film** | `/film/{slug}/` | ✅ | one film out of its JSON-LD block: director, cast, genre, runtime, the weighted average rating |
| **grid** | `/{member}/films/` | ✅ | the poster wall of everything a member has logged (throttled first) |
| **search** | `/search/films/{q}/` | ❌ | nothing. Name the film instead — `film()` slugifies a title |
| **charts** | `/films/ajax/popular/` | ❌ | nothing, and neither do `/films/by/rating/` or film similars |
| **likes** | `/{member}/likes/films/` | ❌ | nothing, but the feed carries a per-entry `liked` flag anyway |

### Two things worth knowing before you trust a number

**The feed is a window, not a lifetime.** RSS carries roughly the last 50
logged films and 50 lists. A mean rating over 50 films that gets read as a
mean over 4,000 is a lie of framing, so every answer carries its own
`coverage` line saying what it was computed over, and `overlap`'s "only"
lists mean *not in the window*, not *never seen*.

**Being shut out is an answer.** Letterboxd 403s a chatty client within
seconds — measured here, back-to-back requests fail while the same URLs
return 200 when spaced. Every request therefore goes through one lock with a
**1.4 s minimum interval** and a disk cache under `~/.mod/boxd/cache`
(rss 15 min, film 24 h, grid 1 h). On a 403 the stale cache still answers,
flagged `stale: true`; only a cold cache raises `Blocked`. A lens that reports
"no films" when it means "I was shut out" is worse than no lens.

## CLI

```
m boxd                                  # null call → info()
m boxd/member user=dave                 # who they are + the headline numbers
m boxd/diary user=dave limit=20         # the feed, newest first
m boxd/diary user=dave kind=list        # just their lists
m boxd/reviews user=davidehrlich        # only what they wrote about
m boxd/taste user=dave                  # mean, median, histogram, decades
m boxd/overlap a=dave b=davidehrlich    # agreement, hardest splits, what to steal
m boxd/film title=parasite year=2019    # one film's card
m boxd/films user=dave                  # the poster wall
m boxd/sources                          # which doors are open right now
m boxd/serve                            # console + API on :50940
m boxd/test                             # offline tests
m boxd/kill                             # stop it
```

**Always pass `year=` for a common title.** Letterboxd gives the bare slug to
whichever film claimed it first, which is rarely the famous one:
`film(title=parasite)` is a 1982 Charles Band creature feature rated 2.4, and
Bong Joon Ho's is `parasite-2019`. Without the year you get a wrong answer
rather than an error, which is the worse failure.

## API

One process on **:50940** answers both halves of the protocol's URL rule:
`/boxd/` is the console, and the API answers at `/boxd/api/{fn}`,
`/api/boxd/{fn}` and bare `/{fn}` alike.

```
GET /member?user=dave
GET /diary?user=dave&limit=50&kind=watch|review|list|all
GET /reviews?user=davidehrlich&limit=20
GET /taste?user=dave
GET /overlap?a=dave&b=davidehrlich
GET /film?title=parasite&year=2019
GET /films?user=dave
GET /sources
GET /health          # no network call — answers while Letterboxd is blocking
```

Add `&fresh=1` to any of them to go around the cache. Errors come back as
`4xx` with the reason in the body — a missing `user` is a 400, an unknown
member or film a 404 — and never as a 5xx, whose body Cloudflare strips.

## What comes back

A diary entry is one watch:

```json
{ "kind": "watch", "film": "The President's Cake", "year": 2025,
  "rating": 4.0, "stars": "★★★★", "liked": true, "rewatch": false,
  "watched": "2026-09-14", "slug": "the-presidents-cake", "tmdb": "1464883",
  "poster": "https://a.ltrbxd.com/…", "url": "https://letterboxd.com/dave/film/…" }
```

`kind: "review"` adds the member's own words in `review`, with the poster and
the "Watched on …" boilerplate stripped, plus `spoilers`.

`taste` adds up the rated half of that window — `films`, `rated`, `mean`,
`mean_stars`, `median`, `histogram`, `liked` / `like_rate`, `rewatches` /
`rewatch_rate`, `reviews`, `decades`, `watched_from` / `watched_to`, and
`top`.

`overlap` holds two windows against each other: `seen_by_both`,
`rated_by_both`, `agreement`, `mean_gap`, the `shared` films, the ten hardest
`disagreements`, and a `for_{member}` list each way — what one rated highly
that the other hasn't logged, which is the recommendation you actually wanted.

## Layout

```
mod.py          the anchor — every public method is a CLI verb and an API route
letterboxd.py   the read layer: throttle, cache, and the RSS/JSON-LD/grid parsers
taste.py        the arithmetic — pure functions over already-parsed entries
serve.py        console + API on one port, dispatching straight at the anchor
web/index.html  the console
tests/          offline; Letterboxd is never hit
```

`taste.py` is kept out of the read layer on purpose: reading a page and
judging what it means are different jobs, and only the judging half is worth
arguing with. It also means the interesting half is testable with no network.

```
m boxd/test        # or: python3 -m pytest -q tests
```

22 tests, all offline, on hand-built fixtures rather than scraped copies of
anyone's diary — including the two failures that are silent rather than loud:
a bare slug resolving to the wrong film, and a 403 dressed up as an empty
result.
