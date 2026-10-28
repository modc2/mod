# reviewer

Code review for the fleet. Point it at a module, a path, a pasted file or a
unified diff and get a scored review. Every finding is anchored to
`file:line` and comes with a fix.

Three families of rules:

- **protocol** — does the folder look like a mod? `config.json` present and
  parseable, name matches the folder, the anchor exists and holds a `Mod`
  class, every declared fn is a method (and the other way round), `api_fns`
  within `fns`, ports do not collide with a sibling module, README and tests
  exist.
- **fleet** — the house rules this box has learned the hard way: no pattern
  kills (`pkill`/`killall` take the fleet down), no `git stash`, no native
  browser dialogs in consoles, no secrets in source, answer failures with 4xx
  (Cloudflare swaps 5xx bodies), append the module dir to `sys.path`, never
  prepend it.
- **hygiene** — syntax errors, bare `except:`, `eval`/`exec`, `shell=True`,
  `innerHTML` from data, leftover debugger hooks and TODOs, oversized files.

Two engines:

- **static** — stdlib only, offline, instant. Runs everywhere.
- **agent** — the static pass plus a written, read-only review from the
  claudecode agent (`:51260`, the orbit agent protocol). Costs money, so over
  HTTP it answers loopback callers only (`REVIEWER_OPEN_AGENT=1` lifts that).
  If the agent is down the review still completes and says so.

Reviews live in one SQLite file at `~/.mod/reviewer/reviews.db` (set
`REVIEWER_DIR` to move it). Findings can be marked fixed or ignored, and the
score follows.

## Use it

```bash
m reviewer/serve                     # console + API + MCP on :51430 → http://localhost:51430/reviewer/

m reviewer/targets                   # every module this box can review
m reviewer/review target=jokes       # static review of a module
m reviewer/review path=/root/mod/mod/orbit/jokes/serve.py
m reviewer/review target=jokes engine=agent focus="the API auth"
m reviewer/list                      # past reviews, newest first
m reviewer/get id=<id>               # one review, findings + verdicts
m reviewer/verdict id=<id> index=3 verdict=fixed note="moved to ~/.mod"
m reviewer/rules                     # every rule the static engine runs
m reviewer/test                      # offline tests
m reviewer/kill
```

Scoring: start at 100, subtract 15 per high, 6 per medium, 2 per low; info
is free. A ≥ 90, B ≥ 75, C ≥ 60, D ≥ 40, else F. Fixed and ignored findings
do not count.

## Pragmas

A file can document a dangerous pattern without being flagged for it:

```python
# reviewer: off
DANGEROUS = re.compile(r'\bpkill\b')
# reviewer: on
os.system(cmd)  # reviewer: ignore
```

Docstrings, comments and data/doc files (`.json`, `.md`, `.yaml`, `.toml`)
are prose: only the secret and TODO rules look at them.

## API

Same routes at `/{fn}`, `/reviewer/api/{fn}` and `/api/reviewer/{fn}`.

| method | fn | args |
|---|---|---|
| GET | `info` · `health` · `readme` · `rules` | |
| GET | `targets` | `q` |
| GET | `resolve` | `target` → path |
| GET | `list` | `target, engine, limit, offset` |
| GET | `get` | `id` |
| POST | `review` | `{target \| path \| code+filename \| diff, engine=static\|agent, focus, model, timeout, save}` |
| POST | `verdict` | `{id, index, verdict=open\|fixed\|ignored, note}` |
| POST | `delete` | `{id}` — loopback callers only |
| POST | `mcp` | MCP JSON-RPC (also at `/mcp`) |

A review answers:

```json
{"id": "…", "target": "jokes", "kind": "module", "engine": "static",
 "score": 90, "grade": "A", "counts": {"high": 0, "medium": 0, "low": 5, "info": 0},
 "findings": [{"rule": "js.innerhtml_dynamic", "severity": "low",
               "file": "web/index.html", "line": 113,
               "message": "…", "fix": "…", "verdict": "open", "index": 0}],
 "summary": null, "meta": {"files": 6, "path": "…"}}
```

With `engine=agent`, `summary` holds the agent's markdown and `meta.agent`
the cost; `meta.agent_score` is the number it gave.

## MCP

```bash
claude mcp add --transport http reviewer http://localhost:51430/mcp
claude mcp add reviewer -- python3 /root/mod/mod/orbit/reviewer/mcp_server.py
```

Tools: `reviewer_review`, `reviewer_get`, `reviewer_list`, `reviewer_verdict`,
`reviewer_rules`, `reviewer_targets`, `reviewer_status`.

## Files

```
mod.py          anchor — the Mod class the orbit loader instantiates
rules.py        the static engine: rules, scanner, diff parser, score
store.py        SQLite reviews + verdicts
agent.py        the deep engine: claudecode agent protocol on loopback
serve.py        console + API + MCP on one port (stdlib http.server)
mcp_server.py   MCP JSON-RPC dispatch (stdio and HTTP)
web/            the console, one HTML file
tests/          offline tests in a throwaway store
```
