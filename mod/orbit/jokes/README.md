# jokes

A jokebook for other people's jokes. Save a joke credited to the comedian who
told it and where it is from, tag it, search it, pull a random one, and share
it — one joke as a link, or a whole comedian as a pack someone else imports.

Local-first: one SQLite file at `~/.mod/jokes/jokes.db` (set `JOKES_DIR` to
move it), Python stdlib only, no accounts, no keys, no network calls.

## Use it

```bash
m jokes/serve                     # console + API on :51130 → http://localhost:51130/jokes/

m jokes/add text="I used to do drugs. I still do, but I used to, too." \
            comedian="Mitch Hedberg" source="Mitch All Together" year=2003 tags=oneliner
m jokes/search q=drugs
m jokes/search comedian="Mitch Hedberg" sort=top
m jokes/random comedian="Mitch Hedberg"
m jokes/comedians
m jokes/share id=<id>             # link + plain text + one-joke pack
m jokes/export comedian="Mitch Hedberg" > hedberg.jokes.json
m jokes/import_pack pack="$(cat hedberg.jokes.json)"
m jokes/test
```

## Sharing

- **One joke** — `share` returns `…/jokes/?j=<id>`, which opens the console
  on that joke, plus the joke as plain text with its credit line.
- **A collection** — `export` returns a *pack*: `{"format": "jokes.pack/1",
  "jokes": [...]}`. Send the file any way you like; `import_pack` merges it.

A joke's id is `sha256(text + comedian)` (case- and whitespace-insensitive),
recomputed on import, never trusted from the pack. So the same joke saved on
two machines has one id, packs merge without duplicates, and a pack can't
overwrite a different joke. Votes are this book's opinion and never travel.

## API

Same routes at `/{fn}`, `/jokes/api/{fn}` and `/api/jokes/{fn}`.

| method | fn | args |
|---|---|---|
| GET | `search` | `q, comedian, tag, sort=new\|top\|old\|random, limit, offset` |
| GET | `get` · `share` | `id` |
| GET | `random` | `comedian, tag` |
| GET | `comedians` · `tags` · `info` · `health` | |
| GET | `export` | `ids, comedian, tag, name` |
| POST | `add` | `{text, comedian, source, year, tags, by}` |
| POST | `vote` | `{id, delta: 1\|-1}` |
| POST | `import_pack` | `{pack}` (or the pack itself as the body) |
| POST | `remove` | `{id}` — loopback callers only |

Anyone you share the console with can add and vote; only this box can delete.

## Files

```
mod.py        anchor — the Mod class the orbit loader instantiates
jokebook.py   the store: SQLite, content-hash ids, packs
serve.py      console + API on one port (stdlib http.server)
web/          the console, one HTML file
tests/        offline tests in a throwaway book
```
