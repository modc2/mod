# memes

A meme search engine that scrapes the meme sites. One query fans out to four
sources in parallel and comes back as one deduplicated, score-ranked list:

| source | what it answers | how |
|---|---|---|
| **reddit** | fresh memes with real velocity | public JSON — search + hot across r/memes, r/dankmemes, r/me_irl, r/wholesomememes, r/AdviceAnimals, r/ProgrammerHumor, r/HistoryMemes, r/terriblefacebookmemes |
| **imgflip** | the blank templates everything is captioned from | `api.imgflip.com/get_memes`, cached an hour |
| **knowyourmeme** | what a meme *is* — the encyclopedia | search page HTML, scraped |
| **ninegag** | 9GAG's wall | the undocumented `search-posts` JSON |

No API keys anywhere. A source that is down or blocking this box reports
itself in `errors` and the others still answer — the engine degrades, it
never breaks.

## Console

A search box and a wall of memes at `/memes/` — source chips to narrow to one
site, a TRENDING chip for the hot feeds, lazy-loaded masonry grid, every card
linking back to the source page. Loads the trending wall before you type.

## CLI

```
m memes                          # null call → info()
m memes/search q="this is fine"  # every site at once
m memes/search q=doge source=knowyourmeme
m memes/trending                 # what is hot right now
m memes/random                   # one, off the top of the hot feeds
m memes/templates q=drake        # imgflip's blank canvases
m memes/serve                    # console + API on :50900
m memes/test                     # offline tests
m memes/kill                     # stop it
```

## API

One process on **:50900** answers the protocol's URL rule: `/memes/` is the
console, and the API answers at `/memes/api/{fn}`, `/api/memes/{fn}` and bare
`/{fn}` alike.

```
GET /search?q=doge&source=all&limit=24&nsfw=0
GET /trending?limit=24
GET /random
GET /templates?q=drake
GET /sources · /info · /health · /readme
```

The normalized meme:

```json
{"source": "reddit", "id": "abc123", "title": "…", "url": "page a human opens",
 "image": "direct file an <img> loads", "score": 4200, "author": "r/memes",
 "nsfw": false, "ts": 1756800000}
```

NSFW posts are dropped unless `nsfw=1`. Results are deduplicated by image URL
and sorted by score (upvotes on Reddit/9GAG, caption-volume rank on Imgflip).

## Files

```
mod.py       the anchor — the orbit loader instantiates Mod from here
sites.py     the four adapters + the parallel fan-out
serve.py     console + API on one port (pm2 runs this directly)
web/         the console, a single index.html
tests/       offline — parsing and wiring, no source is hit
```
