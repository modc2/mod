# Memory: what the agent keeps, and who it keeps it for

The memory module (`src/memory/`) is its own mod — it has its own class, its
own store under `~/.mod/agent/memory/`, and it can be served as its own
process on `:50119`. Everything the agent remembers goes through it, in four
layers:

| layer      | what it holds                          | written by            | read back by |
|------------|----------------------------------------|-----------------------|--------------|
| working    | the per-run prompt state               | the run loop          | every step of that run |
| episodic   | every step the agent executed          | `_emit_step`          | the trail (`/memory/episodes`) |
| dialogue   | what the user asked, what it answered  | the end of a run      | the next run's prompt |
| semantic   | durable facts, with tags               | `remember` / the tab  | the next run's prompt |

The first two are a record of what happened. The last two are what makes a
new conversation not start from zero.

## Memory is built for retrieval

Anything remembered but unfindable is dead weight, so retrieval is a
component of its own (`src/memory/retrieval.py`) rather than a method bolted
onto each layer. One scorer ranks facts, past turns and steps alike:

- **idf** — a word in every document tells you nothing; a rare one is the
  signal. ("deploy" in a repo full of deploy scripts picks out nothing;
  "tzdata" picks out one line.)
- **saturation** — the tenth occurrence of a word means little more than the
  second.
- **length** — a long document matching one word is a weaker hit than a short
  one matching the same word.
- **recency** — a tie between two equally good matches goes to the newer.
- **stop words** — dropped, so "what is the port" doesn't match everything.

Scores come back normalised to 0–1: the fraction of the query's *findable*
information a document covers — a word nothing in the store has ever seen
("run" when the fact says "runs") is not evidence that the best match is a
weak one, so it doesn't count against it.

`retrieve()` ranks every layer in **one pass** rather than merging three
separate rankings, which is what makes the scores comparable: a word is worth
the same wherever it is found. Ranked per layer, a junk step that shares one
word with the question outranks the fact that answers it, because that word
is all its own layer has ever seen. `k` is still applied per layer, so one
chatty layer can't crowd the others out.

```python
mem.retrieve("what port does the relay use?", k=5, who="0x…")
# [{'layer': 'semantic', 'name': 'relay', 'text': '…8412', 'score': 0.82, …},
#  {'layer': 'dialogue', 'text': 'they asked: … you answered: …', 'score': 0.4},
#  {'layer': 'episodic', 'name': 'bash', 'text': 'systemctl start relay', …}]
```

`compile()` is this same ranking with a prompt shape, and `GET
/memory/retrieve?q=` is it over HTTP — the console's **RECALL** tab is that
endpoint, so what you read there is exactly what a run would be handed.

Hits below a small floor are dropped (`Memory.MIN_SCORE`, `min_score=0` for
the raw ranking): one shared word between a question and a file path is not a
memory of anything, and noise in a prompt is worse than silence.

Working memory is left out unless asked for by name (`layers=['working']`):
it is the prompt being written right now, so retrieving it would hand the
model back what it is already reading.

## Memory modules: a component, with a default

An agent is a prompt, a model, a toolbox and a memory module. The last one
comes from its own registry (`src/memory/registry.py`), the same shape as the
agent and tool registries — a directory per module, each with a `mod.py`
holding a `Memory` class that subclasses the base:

| module      | what it is                                                       |
|-------------|------------------------------------------------------------------|
| `default`   | everything above, persisted under `~/.mod/agent/memory/`          |
| `ephemeral` | the same layers and the same retrieval, in RAM, written nowhere   |

`ephemeral` is the right memory for a run that should leave no trace — an
arena match, a benchmark, a sandboxed portal run — where a durable trail is
contamination rather than context: the next match must meet the task cold.
Retrieval still works *inside* the run, which is the point.

An agent declares its module like any other component, and a run can override
it:

```python
class Agent:            # agents/<name>/mod.py
    memory = "ephemeral"
```
```bash
POST /run  {"query": "…", "memory": "ephemeral"}
GET  /memory/modules       # what an agent can be built with
GET  /parts                # the whole box: requires, model, memory, toolbox, tools, prompt
```

A dotted name (`agent.memory`, or another mod entirely) is resolved through
the framework instead, so memory can live outside this module. If that module
has moved, the run falls back to `default` — an agent should lose its memory,
not its ability to answer.

## Tools that reach back into the box

Three of the shipped tools act on the agent's own sub-components instead of
the world:

| tool       | what it does                                                    |
|------------|-----------------------------------------------------------------|
| `recall`   | retrieval over the run's own memory module, any layer            |
| `remember` | writes one durable fact future runs will retrieve                |
| `toolbox`  | lists the bundles and snaps one on mid-run                       |

They get the *live* agent handed to them (`Tools.bind`), so `recall` searches
the memory the run is actually using rather than a fresh empty one. `toolbox`
edits only the run's working schema, never the module's loadout — widening one
run must not widen every later one — and a sandboxed run still cannot pull a
fleet module in that way.

## The dialogue layer

Each run in the console is its own chat: the composer opens a fresh
conversation every time you ask something. So without this layer the agent
meets the same person as a stranger on every message — the notes you tick and
the facts it recalls were the only things crossing a run boundary, and both
have to be curated by hand.

A run that produced an answer files one exchange:

```python
memory.exchange(query, answer, session=..., who=..., agent=...)
```

`who` is the caller's verified address (`None` for an anonymous visitor),
`session` the console session — one id per browser, kept in localStorage and
sent along with every run as `session`. A run with **no** session is not
conversation and leaves this layer alone: an arena match, a tool call, a
scripted `Mod.run()` all stay out of it, which is what keeps the board's
thousands of matches from drowning the thing.

Both sides are clipped to `Memory.MAX_TURN` characters — enough to recall,
cheap to inject.

## Reading it back

Before the loop starts, `Memory.compile(query, session=, who=)` renders the
layers into one context block:

```
CONVERSATION SO FAR (earlier turns with this same user):
user: … / you: …            ← the last 3 turns, verbatim

RELATED PAST TURNS (older, matched to this question):
- they asked: … / you answered: …   ← keyword-scored, older than the window

RECALLED FACTS (from past runs):
- [name] content            ← the semantic layer
```

That block rides into the prompt as `recalled`. Nothing else changed about
the loop — the agent simply starts each run already knowing what was said.

## Scoping is the safety story

This module is served to the open internet, so "the conversation so far" has
to mean *yours*:

- **signed in** → every turn recorded under your address, across
  conversations, browsers and devices. Sign-in is the identity; the session is
  incidental.
- **anonymous** → only the session that made them, and never a turn a
  signed-in caller recorded. Two strangers on one host cannot be reminded of
  each other's chats, and guessing a session id gets you nothing that belongs
  to an account.

The same rule applies to what a client may list, so what the console shows is
exactly what the agent recalls:

```
GET /memory/exchanges?n=&session=&key=      scoped as above
GET /memory/retrieve?q=&k=&layers=&key=     every layer, ranked, same scoping
GET /memory/episodes?n=&session=            the step trail
GET /memory/facts · /memory/recall?q=       the semantic layer
GET /memory/state · /memory/modules         layer counts · pluggable modules
```

The console's MEMORY tab shows all of it: **NOTES** (library notes you attach
to a run deliberately), **FACTS** (recalled on their own), **RECALL**
(retrieval itself — ask a question, see what a run would be handed, ranked
and tagged by layer), and **CHATS** (the dialogue layer — read-only, because
a chat you can rewrite is not a memory).

## Where it lives

```
~/.mod/agent/memory/
  episodes.jsonl     the step trail, self-rotating past 2 MB
  exchanges.jsonl    the dialogue layer, same rotation
  facts.json         the semantic store
```

Nothing here is in the module directory, so nothing here is committed, pinned
or sealed with the code — see [privacy.md](privacy.md).

## CLI

```bash
m agent/memory/status
m agent/memory/exchanges n=10 who=0x…       # a caller's turns
m agent/memory/compile query="what port?" who=0x…
m agent/memory/remember name=style content="tabs, not spaces"
m agent/memory/recall query="what style?"
m agent/memory/retrieve query="the relay port"   # every layer at once
m agent/memories                             # the modules an agent can use
m agent/parts                                # the whole box, component by component
m agent/memory/serve                         # the layer as its own process
```
