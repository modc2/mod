# The sandbox

**The server never runs a module.** Execution happens in `src/runtime/`, and
that directory is the same code in both places it runs: the console imports it
from this server over `/runtime/*.mjs`, the node runner imports it off disk. A
match played in a tab and a match played from the CLI are the same computation,
which is the only reason they can share a leaderboard.

## Wasm

A module gets memory, a seeded PRNG, a clock that starts at zero, and somewhere
to write text. It does not get a filesystem, a network, or the real time of
day. Three shims stack so that instantiation is never what fails:

- `wasi_snapshot_preview1` — a real preview1 subset: argv, environ, stdin,
  stdout/stderr, `random_get`, `clock_time_get`, `proc_exit`. Enough that a
  program compiled for WASI by someone who never heard of this arena runs
  unmodified.
- `arena` — `log`, `random`, `now`, for modules that want them.
- everything else — synthesised from the module's own import list, with the
  right return type read out of the recovered signature, and logged. An
  unsupported module still loads, and tells you what it wanted.

In the browser it all runs inside a Worker. That *is* the sandbox: a wasm call
that never returns cannot be interrupted from inside its own thread, and
uploaded modules are other people's code, so the page must be able to
`terminate()` one without stopping itself.

## A Python class

A class runs in a python subprocess started by the runner (`runtime/host.py`,
driven over JSON lines by `runtime/pyhost.mjs`). It wears the same face the
wasm host does, so the match loop never learns which kind it got.

| | |
|---|---|
| no filesystem | `open` is not in builtins, `RLIMIT_FSIZE` is 0 |
| no network | `socket`, `urllib`, `http`, `subprocess` are not importable |
| no clock | `time` and `datetime` are not importable, so replays cannot drift |
| seeded `random` | seeded from the match seed before `__init__` runs |
| bounded | 512 MiB, 30 CPU seconds, and a per-move timeout that kills the process |

Anything the class prints goes into the match transcript, the way `arena.log`
does for wasm.

**This is not the wasm sandbox, and the difference is not a detail.** Wasm
cannot reach anything the host does not hand it. CPython can be talked out of a
restricted namespace by someone who knows the language well enough. The limits
here stop accidents, casual mischief and runaways. Run classes the way you
would run any code you have decided to trust; upload wasm for the rest.

A Rust class is not this case: it is compiled to wasm on upload and runs in the
wasm sandbox like anything else.

## The door out

A class has no network. What it has is a request: `arena::mcp(server, tool,
args)` in Rust, `self.mcp(...)` in Python, and the host — not the sandbox —
makes the call.

```
the class says      arena::mcp("weather", "forecast", "{\"city\":\"Oslo\"}")
the sandbox does    nothing; it hands the host a string
the host asks       the arena, over one HTTP call it did not compose
the arena calls     the MCP server, if that server is on the list
```

Three things follow from routing it this way rather than opening a socket. A
class names a **server, never a URL**, so it cannot be talked into calling
somewhere else. Credentials live in `~/.mod/arena/mcp_servers.json` and are
never handed to the code that uses them. And every call is one place to see,
count and cut off.

Say the last part plainly to anyone reading a leaderboard: a class with MCP
access is not sandboxed *from the world*, only from this machine. It is **off
by default** for that reason, a match that used it is marked, and every call is
counted onto the seat that made it.

## Where this stops

- **The sandbox is the engine's.** A wasm module gets no filesystem and no
  network, and in the browser it can be terminated. It is still running in the
  same process as the page. Treat a public arena's modules as untrusted code
  you have chosen to run.
- **A class is sandboxed by convention, not by construction.** See above.
- **A class cannot play in a browser tab.** A tab cannot start a python
  process, so those matches go through the runner.
- **Results are reported by the runner.** Rated, recorded, replayable — but not
  independently re-executed on submission. The transcript is what makes a
  disputed match checkable.
