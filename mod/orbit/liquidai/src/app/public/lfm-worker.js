// BROWSER runtime — an LFM running in the visitor's own tab.
//
// This file is deliberately not part of the Next bundle. It is a plain module
// worker that pulls transformers.js off a CDN and the ONNX weights off
// HuggingFace, so the whole inference path is browser ↔ CDN: our server never
// sees a prompt, a token, or a byte of the weights. That is the entire point
// of the browser runtime, and bundling it would quietly put the app server
// back in the middle of it.
//
// Protocol (postMessage both ways):
//   in   {type:'load', repo, dtype, device, modality}
//        {type:'generate', messages, max_tokens, temperature, top_p}
//        {type:'embed', texts}
//   out  {type:'status'|'progress'|'ready'|'token'|'done'|'embedding'|'error', …}
//
// Three of the four modalities live here. Text and vision both generate — a
// vision turn is the same loop with a processor in front of it — and embedding
// models return vectors instead of a stream. Audio is server-only: the ONNX
// speech builds aren't in the catalog, and a runtime that pretends otherwise
// wastes a 300 MB download to find out.

const CDN = [
  "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0",
  "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0/+esm",
  "https://unpkg.com/@huggingface/transformers@4.2.0/dist/transformers.min.js",
];

let lib = null;
let loaded = {
  repo: null, dtype: null, device: null, modality: "text",
  tokenizer: null, model: null, processor: null, extractor: null,
};

async function library() {
  if (lib) return lib;
  const errors = [];
  for (const url of CDN) {
    try {
      lib = await import(/* webpackIgnore: true */ url);
      lib.env.allowLocalModels = false;   // weights come from the Hub, always
      return lib;
    } catch (e) {
      errors.push(`${url}: ${e}`);
    }
  }
  throw new Error(`transformers.js unreachable — ${errors.join(" | ")}`);
}

function post(msg) {
  self.postMessage(msg);
}

// One progress line per file, in bytes, so the UI can show a real bar rather
// than a spinner over a 300 MB download.
function progress(p) {
  if (p.status === "progress" && p.file && p.total) {
    post({
      type: "progress", file: p.file, loaded: p.loaded, total: p.total,
      pct: Math.round((100 * p.loaded) / p.total),
    });
  } else if (p.status === "download" || p.status === "initiate") {
    post({ type: "status", state: "downloading", file: p.file });
  }
}

// transformers.js picks a weight file by appending a suffix to `model`, so a
// dtype it accepts is not the same as a dtype the repo ships: LFM2.5-350M-ONNX
// has model_q4f16.onnx and LFM2.5-230M-ONNX does not. Guessing q4f16 for both
//404s on the second and hangs the tab. So: read the repo's onnx/ listing and
// choose from what is actually there.
const SUFFIX_DTYPE = {
  "": "fp32", "_fp16": "fp16", "_q4": "q4", "_q4f16": "q4f16", "_quantized": "q8",
};
const DTYPE_ORDER = {
  webgpu: ["q4f16", "q4", "q8", "fp16", "fp32"],
  wasm: ["q4", "q8", "fp16", "fp32"],   // q4f16 wants fp16 shaders; wasm has none
};

async function availableDtypes(repo) {
  const r = await fetch(`https://huggingface.co/api/models/${repo}/tree/main/onnx`);
  if (!r.ok) throw new Error(`no onnx/ listing for ${repo} (HTTP ${r.status})`);
  const found = new Set();
  for (const f of await r.json()) {
    const name = (f.path || "").split("/").pop();
    const m = /^model(.*)\.onnx$/.exec(name || "");
    if (m && SUFFIX_DTYPE[m[1]] !== undefined) found.add(SUFFIX_DTYPE[m[1]]);
  }
  return found;
}

async function pickDtype(repo, device) {
  const have = await availableDtypes(repo);
  for (const d of DTYPE_ORDER[device] ?? DTYPE_ORDER.wasm) if (have.has(d)) return d;
  throw new Error(`${repo} ships no dtype this runtime can load`);
}

async function load({ repo, dtype, device, modality = "text" }) {
  const wanted = device || (navigator.gpu ? "webgpu" : "wasm");
  // Embedding repos ship one encoder file rather than a dtype family, so the
  // onnx/ listing that picks a quant for a decoder doesn't apply to them.
  const q = dtype || (modality === "embed" ? undefined : await pickDtype(repo, wanted));
  if (loaded.repo === repo && loaded.dtype === q && loaded.device === wanted
      && loaded.modality === modality) {
    post({ type: "ready", repo, dtype: q, device: wanted, modality, ms: 0, cached: true });
    return;
  }
  const transformers = await library();
  post({ type: "status", state: "loading", repo, dtype: q, device: wanted });
  const started = performance.now();
  const opts = { dtype: q, device: wanted, progress_callback: progress };

  let tokenizer = null, model = null, processor = null, extractor = null;
  if (modality === "embed") {
    extractor = await transformers.pipeline("feature-extraction", repo, opts);
  } else if (modality === "vision") {
    processor = await transformers.AutoProcessor.from_pretrained(repo, {
      progress_callback: progress,
    });
    tokenizer = processor.tokenizer ?? await transformers.AutoTokenizer.from_pretrained(repo);
    model = await transformers.AutoModelForImageTextToText.from_pretrained(repo, opts);
  } else {
    tokenizer = await transformers.AutoTokenizer.from_pretrained(repo, {
      progress_callback: progress,
    });
    model = await transformers.AutoModelForCausalLM.from_pretrained(repo, opts);
  }

  loaded = { repo, dtype: q, device: wanted, modality, tokenizer, model,
             processor, extractor };
  post({
    type: "ready", repo, dtype: q, device: wanted, modality,
    ms: Math.round(performance.now() - started),
  });
}

// LFM2.5's chat template wraps the assistant turn in `{% generation %}` —
// a Jinja block transformers (python) uses to mark which tokens to train on.
// @huggingface/jinja doesn't implement it and dies with "Unknown statement
// type: generation". The block only marks a span; deleting the two tags
// leaves the rendered prompt byte-identical, so we strip them and re-render.
const GENERATION_TAGS = /\{%-?\s*(end)?generation\s*-?%\}/g;
// Separate, non-global copy: `.test()` on a /g regex advances lastIndex and
// would answer false on the next call.
const HAS_GENERATION_TAG = /\{%-?\s*(end)?generation\s*-?%\}/;

function chatInputs(tokenizer, messages) {
  const opts = { add_generation_prompt: true, return_dict: true };
  try {
    return tokenizer.apply_chat_template(messages, opts);
  } catch (e) {
    const tpl = tokenizer.chat_template;
    if (typeof tpl !== "string" || !HAS_GENERATION_TAG.test(tpl)) throw e;
    return tokenizer.apply_chat_template(messages, {
      ...opts, chat_template: tpl.replace(GENERATION_TAGS, ""),
    });
  }
}

// A turn's content is a string, or parts when it carries an image. The
// processor wants the images separately from the text, same as the server.
async function visionInputs(processor, messages) {
  const { RawImage } = await library();
  const images = [];
  const turns = [];
  for (const m of messages) {
    if (typeof m.content === "string") {
      turns.push({ role: m.role, content: [{ type: "text", text: m.content }] });
      continue;
    }
    const parts = [];
    for (const p of m.content || []) {
      if (p.type === "image" && p.image) {
        images.push(await RawImage.fromURL(p.image));
        parts.push({ type: "image" });
      } else if (p.type === "text") {
        parts.push({ type: "text", text: p.text });
      }
    }
    turns.push({ role: m.role, content: parts });
  }
  const text = processor.apply_chat_template(turns, { add_generation_prompt: true });
  return processor(text, images.length ? images : null);
}

// Text models can't see, so an image in the history has to be flattened out
// rather than crashing the template.
function flatten(messages) {
  return messages.map((m) => ({
    role: m.role,
    content: typeof m.content === "string" ? m.content
      : (m.content || []).filter((p) => p.type === "text").map((p) => p.text).join("\n"),
  }));
}

async function embed({ texts }) {
  if (!loaded.extractor) throw new Error("no embedding model loaded");
  const started = performance.now();
  const out = await loaded.extractor(texts, { pooling: "mean", normalize: true });
  const vectors = out.tolist();
  const similarity = vectors.map((a) =>
    vectors.map((b) => Math.round(a.reduce((s, v, i) => s + v * b[i], 0) * 1e4) / 1e4));
  post({
    type: "embedding", runtime: "browser", repo: loaded.repo,
    dim: vectors[0]?.length ?? 0, count: vectors.length, vectors, similarity,
    elapsed_sec: Math.round((performance.now() - started) / 10) / 100,
  });
}

async function generate({ messages, max_tokens = 256, temperature = 0.3, top_p = 0.95 }) {
  if (!loaded.model) throw new Error("no model loaded");
  const { TextStreamer } = await library();
  const { tokenizer, model, processor, modality } = loaded;

  const inputs = modality === "vision" && processor
    ? await visionInputs(processor, messages)
    : chatInputs(tokenizer, flatten(messages));
  const started = performance.now();
  let first = null;
  let chunks = 0;

  const streamer = new TextStreamer(tokenizer, {
    skip_prompt: true,
    skip_special_tokens: true,
    callback_function: (text) => {
      if (!text) return;
      first = first ?? performance.now();
      chunks += 1;
      post({ type: "token", text });
    },
  });

  await model.generate({
    ...inputs, max_new_tokens: max_tokens, do_sample: temperature > 0,
    temperature: Math.max(temperature, 1e-5), top_p, streamer,
  });

  const elapsed = (performance.now() - started) / 1000;
  post({
    type: "done", runtime: "browser", repo: loaded.repo, device: loaded.device,
    dtype: loaded.dtype, modality: loaded.modality, chunks,
    elapsed_sec: Math.round(elapsed * 100) / 100,
    ttft_sec: first ? Math.round(((first - started) / 1000) * 100) / 100 : null,
    chunks_per_sec: elapsed ? Math.round((chunks / elapsed) * 100) / 100 : null,
  });
}

// A failure inside transformers.js often surfaces as an unhandled rejection on
// a task we never awaited — without these two the UI just sits on "loading
// weights…" forever, which is the worst way to report a 404.
self.addEventListener("unhandledrejection", (e) => {
  post({ type: "error", error: String(e.reason?.message || e.reason) });
});
self.addEventListener("error", (e) => {
  post({ type: "error", error: String(e.message || e) });
});

self.onmessage = async (e) => {
  const msg = e.data || {};
  try {
    if (msg.type === "load") await load(msg);
    else if (msg.type === "generate") await generate(msg);
    else if (msg.type === "embed") await embed(msg);
    else if (msg.type === "probe") post({ type: "probe", webgpu: !!navigator.gpu });
    else throw new Error(`unknown message ${msg.type}`);
  } catch (err) {
    post({ type: "error", error: String(err?.message || err) });
  }
};
