# grow — a new tool and a new agent every minute

The console grows itself. Every `interval` seconds (default **60**) a background
thread adds one **custom tool** and one **agent built to wield it**:
`py-todos` and `py-todo-hunter`, then `py-largest` and `py-slimmer`, and so on.

**Only the module owner can change it.** Anyone can watch.

## Engines — where a pair comes from

| engine | tool | agent | cost |
|---|---|---|---|
| `local` | hand-written read-only recipe (find/grep/wc/git log) | template agent that runs it, reads, reports | nothing: no model, key or network |
| `model` | `tool-builder` agent drafts it, then `safe_command` checks it | `vibe-builder` designs one around the tool | 2 model runs |
| `scout` | as `model` | `agent_scout`: an idea from HN/GitHub/arXiv/web | 3 model runs + web |
| **`auto`** (default) | `local` until the 177-recipe catalog is used up, then `model` | | |

Local-first: on a box with no provider at all, `auto` still grows for ~3 hours
on the catalog alone, and every local tool is safe by construction.

## Guards

- **`model_daily_cap`** (100 per UTC day). A model tick that would exceed it
  falls back to `local`, or waits for tomorrow.
- **Cooldown.** A 429 or quota error parks the model engines for an hour, or
  until the reset time the provider gives. Local ticks keep going meanwhile.
- **`max_tools` / `max_agents`** (500 each). Growth stops at the cap. The
  grower never deletes anything on its own.
- **No overlap.** A tick that lands while another is running is skipped. The
  cadence is measured from each tick's start.
- **Drafted commands are allowlisted.** A pipeline of read-only programs only:
  no `; && || $( ) > <`, no `sed`, `find -delete`, `sort -o`, `rg --pre`,
  `git push`, and so on. A refused tool is logged, and that tick's agent grows
  without it.

## Who can do what

| action | who |
|---|---|
| `GET /grow`, MCP `agent_grow op=status` | anyone |
| `POST /grow/config` (`enabled`, `interval` ≥30, `engine`, caps, `free`, `model`, `provider`, `theme`, `reset:[...]`) | **owner** |
| `POST /grow/tick` (one pair now, even while disabled) | **owner** |
| `POST /grow/prune` (`kind` = tool / agent / all, `count` = oldest N; only grown items) | **owner** |

The gate is `Mod.require_owner`, not `require_allowed`, so an ACL grant (even
`*`) does not reach it. Over HTTP a request without a key gets 401: inside
`forward()` a missing key means "the process itself".

Grown items are created with the module's own server key. Tools record the
owner as their owner, and the owner, as host, manages every grown agent.

## Files

- `src/grow/recipes.py` — the local catalog (19 languages × 10 operations).
- `src/grow/mod.py` — `Grow` (config, state, guards, one tick), `Scheduler`,
  `safe_command`, and `ModHost`, the adapter onto `Mod`.
- `src/agents/tool-builder/` — the tool drafter for the model engines.
- State lives off-tree in `~/.mod/agent/grow/state.json`.
- `GROW_SCHEDULER=0` keeps the thread down. It also stays down under pytest.
- Tests: `tests/test_grow.py`.

```bash
curl -s localhost:50117/grow                                   # watch
curl -s -XPOST localhost:50117/grow/config -H 'content-type: application/json' \
     -d '{"interval": 300, "engine": "local", "key": "<owner token>"}'
```
