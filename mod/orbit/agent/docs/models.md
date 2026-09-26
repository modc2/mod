# LFM providers: on this box, in the cloud, in your tab

Five providers now show up in the console's provider dropdown. Two of them
(`openrouter`, `venice`) are hosted APIs the module pays for. The other three
are Liquid AI's LFM models, reached through the [`liquidai`](../../liquidai)
module, and none of them spends a cent of the module's provider credit:

| provider         | where it runs                       | key           | billed |
|------------------|-------------------------------------|---------------|--------|
| `liquidai`       | this box (torch, CPU)               | none          | never  |
| `liquidai-cloud` | `inference.liquid.ai`               | liquidai BYOK | never  |
| `browser`        | the visitor's own tab (WebGPU/wasm) | none          | never  |

Because a run on them can't cost the module anything, they skip the credit
ceiling and the ledger entirely (`Mod.is_free_provider`) — a guest with an
empty balance can run one, and FREE MODE leaves their model choice alone
instead of swapping in a zero-cost hosted model.

## Local is the default

A run that names no provider gets `liquidai` — the weights on this box —
rather than a frontier model on somebody's key. `Mod.default_provider()`
picks it, `/providers` reports it as the console's default, and `/params`
hands it to sibling consoles; a hosted provider is the fallback for when
nothing local is actually serving, since a default that can't run is no
default. Everything that names a provider still wins: the console's picker,
an agent's own saved model, the `provider=` argument, an arena round.

The default model there is **`LFM2.5-1.2B-Instruct`** — the generalist, not
the `LFM2-1.2B-Tool` fine-tune sitting next to it in the same list. A run is
tool calls *and* the answer that ends it: measured on this box, the Tool
build makes the cleaner call (`read(file_path="a.txt")` on step one, where
the instruct build lists the directory first) and then hands back a shell
snippet where the answer should be, while the instruct build answers in
words every time. `LFM2.5-2.6B` beats both — it reasons in `<think>` blocks
and reads its own trail properly — and costs minutes per step on CPU. All
three are one pick away in the console's model dropdown.

The model lists are the live liquidai catalog, one entry per **repo** (which
is what actually loads) rather than per catalogued model: torch repos for the
server, ONNX repos for the tab, Liquid's hosted ids for the cloud. If the
liquidai module isn't serving, the console falls back to a short curated list
in `Mod.MODELS` — and a repo id typed by hand is as valid as one picked.

## The browser bridge

The agent loop is server-side and synchronous: it builds a context, calls the
model, parses one step, repeats. A browser model breaks that shape — the
weights are in a tab this process cannot call into. So the call is turned
inside out:

```
run thread                  SSE stream                    the tab
    │                            │                           │
 client.forward(context) ──▶ model_request {id, model, …} ──▶ worker.generate()
    │  (blocked)                 │                           │
    ▼                            │                    POST /browser/completion
 text ◀───────────────────────────────────────────────  {id, text}
```

- `src/liquid.py` holds the bridge. A session is opened per run stream and
  **bound to the run's thread** — one run, one thread, one tab — the same way
  the meter keeps its tally.
- The tab's half is `src/app/app/lib/browserModel.ts` driving
  `public/lfm-worker.js`, a verbatim copy of the liquidai module's worker: it
  pulls transformers.js off a CDN and the ONNX weights off HuggingFace, so a
  browser run's prompt, tokens and weights never touch this server.
- If the tab goes away mid-run, the stream's teardown closes the session and
  the blocked run fails with "the console tab went away" instead of parking
  until the 15-minute timeout.
- `POST /run` (the blocking endpoint) refuses `provider=browser`: there is no
  stream to carry the request. Use `/run/stream`.

A caller that isn't the console can use the same protocol: send
`browser_session` with the run, watch for `model_request` events on the
stream, and POST each answer to `/browser/completion` with the id it carried.

## Making the loop legible to a small model

A 1.2B model does not fail at agent work the way a frontier model does. It
fails at *format*: it answers the shape it recognises. Four things in this
module exist because of that, and all four make the hosted providers cheaper
and steadier too.

**The prompt is written, not dumped** (`src/prompt.py`). Working memory used
to reach the model as `str(dict)` — 12 kB of Python repr, `<class 'str'>`
annotations and all. It is now sections: the task, the directory, the tools
as one signature each, the trail so far with old results trimmed, and the
step format last. Small and local models get the compact variant, with a
worked example of a real call (`Agent.compact_prompt`).

**A call is read in any dialect** (`src/steps.py`). `<STEP>` stays the asked-
for format, but `<tool_call>`, a fenced JSON block, `<|tool_call_start|>`,
`[bash(command="ls")]`, `{"function": {"name": …}}` and `TOOL: bash` all
parse to the same step. Names and parameters are then mapped onto this
registry's — `read_file(path=…)` is `read(file_path=…)` — so a correct
decision typed in another harness's dialect runs instead of failing. A
thinking model's `<think>` block is read past, and never shown to the user.

**Relative paths mean the run's directory.** The model is told which
directory it is working in, so `read(file_path="a.txt")` means the file in
it — not one relative to wherever this server was started, which is what a
tool would otherwise resolve. An omitted `path` is filled in the same way.

**A circling run is stopped.** A read-only call the run already made is
answered from the first result with a note saying so, the step after a repeat
is sampled rather than greedily decoded, and three repeats in a row end tool
use and go straight to writing the answer. Nothing is lost: a repeat produces
no new information by definition.

## What to expect from a small LFM

These are 230M–8B models. They run the loop, but they are not Opus: expect
shallower plans, and answers that restate the question rather than answering
it. Sensible uses are cheap local work — a summary, a rewrite, a first pass —
and demonstrating that the whole agent loop can run with no key and no cloud
at all.

Measured on this box (24 CPU cores, no GPU): `LFM2-1.2B-Tool` answers a
two-step file question in ~40s from a warm model, `LFM2.5-1.2B-Instruct` in
about the same but with wasted steps, `LFM2.5-2.6B` in minutes. In a tab,
`LFM2.5-230M-ONNX` loads as `webgpu/q4` (211 MB, cached by the browser after
the first load).

**WebGPU matters.** transformers.js picks a quant from what the repo ships;
the LFM q4 builds use `GatherBlockQuantized`, which onnxruntime-web implements
on WebGPU but not on the wasm backend. A browser without WebGPU will load some
repos and fail on others — the console's `⌁` pill reports the device it got.
