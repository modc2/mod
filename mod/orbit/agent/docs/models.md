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

## What to expect from a small LFM

These are 230M–8B models. They run the loop, but they are not Opus: expect
malformed step anchors, skipped tool calls, and answers that restate the
question. Sensible uses are cheap local work — a summary, a rewrite, a first
pass — and demonstrating that the whole agent loop can run with no key and no
cloud at all.

Measured on this box: `LFM2.5-1.2B-Instruct` on the `liquidai` (CPU) runtime
answers a 3-step run in ~28s. In a tab, `LFM2.5-230M-ONNX` loads as
`webgpu/q4` (211 MB, cached by the browser after the first load).

**WebGPU matters.** transformers.js picks a quant from what the repo ships;
the LFM q4 builds use `GatherBlockQuantized`, which onnxruntime-web implements
on WebGPU but not on the wasm backend. A browser without WebGPU will load some
repos and fail on others — the console's `⌁` pill reports the device it got.
