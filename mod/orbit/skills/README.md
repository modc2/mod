# skills

**A tool is something an agent can call. A skill is something it can learn.**

A skill is a `SKILL.md` — YAML front matter, then instructions — that tells a
model *when* to reach for the tools it already has and exactly *how* to use
them for one job. Thousands of them are now scattered across GitHub, and the
fleet had nowhere to find them. This is that place: a marketplace that scrapes
the open web for skills, files them in a catalog on this box, and hands them to
an agent as context.

```bash
m skills/search "pdf forms"                      # scan the web
m skills/get gh:anthropics/skills:skills/pdf     # read the document
m skills/install gh:anthropics/skills:skills/pdf # file it in the catalog
m skills/install gh:anthropics/skills all=true   # the whole pack
m skills/installed                               # what is on this box
m skills/load names=pdf,deploy                   # bodies, for a run
m skills/serve                                   # api + mcp + console, one port
```

Console: <http://127.0.0.1:50860/skills>

## Six sources, one scan

| source      | what it finds                                                     |
|-------------|-------------------------------------------------------------------|
| `anthropic` | the official `anthropics/skills` catalog — listed whole, filtered locally, never spends a search call |
| `topics`    | repos that tag themselves `claude-skill` / `agent-skill` / `claude-code-skill` |
| `code`      | GitHub code search for `filename:SKILL.md` — the widest net, and the only one that needs a token |
| `github`    | repo search scoped to name + description (`in:readme` drags in every monorepo that says the word once) |
| `awesome`   | curated community indexes, parsed out of their READMEs and cached hard |
| `registry`  | every module on **this host** — the fleet already writes `skill.md` in exactly this format |

One query fans out to all six at once. Duplicates across sources collapse onto
one card that remembers all of them (`also: [awesome, topics]`), because the
same skill appearing in the official catalog *and* an awesome-list is evidence,
not noise. Ranking is **source trust × relevance × a capped nod to stars**, so a
40k-star framework that merely mentions your query cannot outrank the skill that
answers it.

A search returns cards and fetches nothing. The document is pulled only when a
card is opened or installed — a scan that fetched every hit would spend the
whole GitHub budget on cards nobody read.

## Nothing here is executed

Installing a skill writes markdown. It runs no script, installs no package, and
adds no executable tool. `tools:` in the front matter names tools the agent
*already has* — a skill teaches the use of a capability, it cannot grant one.

That is the whole security model, and it is why a marketplace of documents
scraped off the internet is a safe thing to own.

## The catalog

```
~/.mod/skills/
  catalog/<name>/SKILL.md    the document
  catalog/<name>/meta.json   where it came from, when, who installed it
  cache/                     search results, per-source TTL
  index.json                 every card ever scanned, by id
  github.token               optional, 0600, never in the module directory
  server.secret              optional bearer token for remote writes
```

One folder, one `SKILL.md` — so the catalog is portable in both directions:
copy a folder into `~/.claude/skills` and Claude Code reads it; copy one out of
there and this reads it. Same format, no converter.

## Write your own

```bash
m skills/write name=deploy-checklist description="Ship safely" body=@checklist.md
```

Or the `+ write` panel in the console. A hand-written skill and a scraped one
are the same kind of thing the moment they are saved.

## API

Reads are open. The catalog is shared state on this box, so **changing** it —
install, write, remove, token — wants a caller from this box that is not being
proxied, or the bearer token in `~/.mod/skills/server.secret`.

| route | what |
|---|---|
| `GET /search?q=&sources=&limit=&fresh=` | scan every source, merged and ranked |
| `GET /sources` | the sources, and which are ready |
| `GET /skill?id=&path=` | one card, with the `SKILL.md` behind it |
| `GET /installed?q=&tag=` | the catalog, as cards without bodies |
| `GET /doc?name=` · `GET /raw?name=` | the markdown an agent is handed (JSON / `text/markdown`) |
| `GET /load?names=a,b` | several skills **with** bodies — the start-of-run call |
| `POST /install` | `{id, path?, name?, all?}` (gated) |
| `POST /write` | `{name, body, description?, tools?, tags?}` (gated) |
| `DELETE /installed/<name>` | remove one (gated) |
| `POST /token` | `{token}` — a GitHub PAT for code search (gated) |
| `POST /mcp` | MCP JSON-RPC 2.0 — the same ten operations |
| `GET /skills` | the browser console |

## MCP

Ten tools — `skills_search`, `skills_sources`, `skills_get`, `skills_install`,
`skills_installed`, `skills_doc`, `skills_load`, `skills_write`,
`skills_remove`, `skills_token` — each one a method on `Market`. REST, MCP and
the console call the same implementation, so no surface can be told something
another one cannot do.

```bash
curl -s localhost:50860/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"skills_search","arguments":{"q":"excel"}}}'
```

## With the agent module

`orbit/agent` reads this catalog as its skill registry: skills appear in the
agent box beside its prompt, model, toolbox and memory, an agent can be built
with a set of them, and the `skill` tool lets a run pick one up mid-flight.
Without this module the agent still runs — it just has nothing to learn.

## Tests

```bash
python3 -m pytest tests/ -q          # offline: format, catalog, ranking, gates
python3 -m pytest tests/ -q -m live  # plus the actual scraping
```

The live tests skip themselves when GitHub is unreachable: a suite that goes red
because of a rate limit is a suite people stop running.

## Env

`SKILLS_PORT` (50860) · `SKILLS_BIND` (0.0.0.0) · `SKILLS_DIR` (`~/.mod/skills`) ·
`SKILLS_TIMEOUT` (14s) · `GITHUB_TOKEN` · `MOD_ROOT` (`/root/mod/mod`)
