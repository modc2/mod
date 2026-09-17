# A repo of choice, as a coding game

Most games here are written. A coding game is **harvested**: you name a
repository, and the arena reads its Python, keeps the functions it can grade,
runs each one to find out what it answers, and stores a game whose rounds are
that repo's functions with their bodies taken out.

```bash
m arena/codegame repo=TheAlgorithms/Python
m arena/codegame repo=https://github.com/psf/requests rounds=5
m arena/codegame repo=/root/mod/mod/orbit/hyperliquid name=hl-recon
```

The same thing is `POST /codegame {repo, name?, tasks?, rounds?}`, the MCP
tool `harvest_repo`, and **harvest a repo** in the console's `+ add` panel.
Cloning and running a large repository takes minutes and the call waits, so a
big one is happiest from the command line.

`repo` is a path on this box, a git URL, or a GitHub `owner/name`. A URL is
cloned shallow into `~/.mod/arena/repos/` and reused; `refresh=1` pulls it
again. What comes back is an ordinary stored game — same registry, same id,
same leaderboard as a game somebody wrote by hand, and its own mod and MCP
server like any other module.

## What a round looks like

Each round, every seat is shown one function: the signature, its docstring,
whatever the file had in scope, and three worked calls.

```
Python — round 1 of 3.
Reconstruct `get_set_bits_count_using_modulo_operator` from
bit_manipulation/count_number_of_one_bits.py:35.

WRITE THIS:
def get_set_bits_count_using_modulo_operator(number: int) -> int:
    """Count the number of set bits in a 32 bit integer"""
    pass

WORKED CALLS (12 are graded, 3 shown):
  get_set_bits_count_using_modulo_operator(1) -> 1
  get_set_bits_count_using_modulo_operator(15) -> 4
  get_set_bits_count_using_modulo_operator(8) -> 1
```

The seat answers with a whole function. The arena runs it against all twelve
vectors — nine of which it never saw — and the round score is the fraction it
reproduces. A function that uses randomness is scored on the *spread* of
answers it gives over eight seeds, against the spread the original gave.

**Nobody writes these tests.** They are what the repo already does, recorded by
running it. That is also the filter: a function the sandbox will not run never
becomes a task, so no round is unwinnable by construction.

## Who plays

Anything that can fill a seat, but the reason this exists is agents:

```bash
m arena/codeplay game=python-recon agents=builder,dev
m arena/codeplay game=python-recon agents=builder matches=3
```

Each named agent is entered as an `agent_mod` player — an agent of this
fleet's `agent` module, reached over the agent protocol — and all of them sit
at the same table and answer the same functions at the same time. Nobody waits
for anyone, and no seat sees another's code before writing its own. See
[filling a seat](#docs/player) for the other six kinds; a model seat, an MCP
module or your own endpoint plays the same game the same way.

## Asking for code

A game says what a move is. A coding game sets

```python
answer = 'code'
```

and the arena changes what it asks for: the brief tells a seat to reply with
one fenced block, and the **whole block** is read back as the move rather than
its last line. Nothing else has to know — the match loop carries the game's
`answer` to whatever is driving each seat.

## Running what a player wrote

`exec` is not in a class's builtins, on purpose — it is how a restricted
namespace gets talked around. So a game that has to run a submission asks the
host instead:

```python
out = judge(code, 'my_fn', [{'args': [1, 2]}], context='import math')
out['ok']                    # did the submission load
out['results'][0]['value']   # what the call returned
```

The host compiles it into a **child namespace with the same cage** — guarded
imports, the same denied builtins, no `open`, a seeded `random`, a deadline per
call and a cap per match — and hands back one result per call. The game holds
"run this and tell me what came back", never `exec` itself. See
[the sandbox](#docs/sandbox).

## What it will not harvest

A function is skipped unless it is plainly gradeable: no methods, no
zero-argument functions, no private names, nothing under three lines, nothing
that prints, opens files or touches `os`/`sys`, and nothing from a module that
imports the network, a database or a numeric stack. Inputs are guessed from
parameter names and annotations, so most guesses are wrong and are simply
dropped — what survives is a set of calls the real function answered. A repo
whose Python is all methods and all IO will harvest nothing, and says so.

Two more filters worth knowing: a vector whose answer is longer than ~1200
characters is dropped (nobody can read it, and the game file would be
enormous), and a function whose vectors all give the same answer is dropped
too — a constant is reconstructed by returning it, which measures nothing.
