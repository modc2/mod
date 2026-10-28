/* voice-worker.js — transformers.js engines, entirely inside the tab.
 *
 *   transcribe: whisper tiny/base/small (onnx-community), task=transcribe
 *               for the source language or task=translate for English out.
 *   translate:  LFM2.5-350M-ONNX rewrites a transcript into a target
 *               language — the liquidai catalog's smallest browser model.
 *
 * transformers.js comes from a CDN on purpose (same reasoning as the
 * liquidai module's worker): bundling it would put our server back into an
 * inference path whose whole point is that it isn't there.
 *
 * LFM trap, inherited knowledge: LFM2.5's chat template uses a
 * python-transformers-only `{% generation %}` Jinja block that breaks
 * @huggingface/jinja. We never call apply_chat_template — the ChatML string
 * is built by hand below, which sidesteps the template entirely.
 */

import {
  pipeline,
  TextStreamer,
} from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0";

const WHISPER_REPOS = {
  "whisper-tiny": "onnx-community/whisper-tiny",
  "whisper-base": "onnx-community/whisper-base",
  "whisper-small": "onnx-community/whisper-small",
};

const post = (msg) => self.postMessage(msg);
const asrCache = {};   // engine id -> pipeline
let lfmGen = null;     // LFM2.5-350M text-generation pipeline

async function gpuAdapter() {
  if (!navigator.gpu) return null;
  try {
    return await navigator.gpu.requestAdapter();
  } catch {
    return null;
  }
}

async function device() {
  return (await gpuAdapter()) ? "webgpu" : "wasm";
}

function progressToUi(label) {
  // transformers.js progress_callback fires per file; surface the big one.
  return (p) => {
    if (p.status === "progress" && p.total > 1e6) {
      post({ type: "progress", pct: (p.loaded / p.total) * 100 });
      post({ type: "status", text: `${label}: ${p.file} ${(p.loaded / 1e6).toFixed(0)}/${(p.total / 1e6).toFixed(0)} MB` });
    }
  };
}

async function getAsr(engine) {
  if (asrCache[engine]) return asrCache[engine];
  const repo = WHISPER_REPOS[engine];
  const dev = await device();
  post({ type: "status", text: `loading ${engine} (${dev})…` });
  asrCache[engine] = await pipeline("automatic-speech-recognition", repo, {
    device: dev,
    dtype: dev === "webgpu" ? { encoder_model: "fp32", decoder_model_merged: "q4" } : "q8",
    progress_callback: progressToUi(engine),
  });
  return asrCache[engine];
}

async function getLfm() {
  if (lfmGen) return lfmGen;
  // Every quant of LFM2.5-350M-ONNX quantizes the embedding gather with
  // GatherBlockQuantized, an op onnxruntime-web only implements on the
  // WebGPU EP — so this engine is WebGPU-only, verified, not assumed.
  const adapter = await gpuAdapter();
  if (!adapter) {
    throw new Error(
      "the LFM2.5-350M translation pass needs WebGPU, which this browser " +
      "does not offer — pick ENGLISH (whisper translate) instead");
  }
  // q4f16 is smaller and faster but its shaders need the f16 feature.
  const dtype = adapter.features.has("shader-f16") ? "q4f16" : "q4";
  post({ type: "status", text: `loading LFM2.5-350M (webgpu, ${dtype})…` });
  lfmGen = await pipeline("text-generation", "LiquidAI/LFM2.5-350M-ONNX", {
    device: "webgpu",
    dtype,
    progress_callback: progressToUi("LFM2.5-350M"),
  });
  return lfmGen;
}

async function doTranscribe({ engine, audio, task }) {
  const asr = await getAsr(engine);
  post({ type: "status", text: "transcribing…" });
  post({ type: "progress", pct: null });
  const result = await asr(audio, {
    task,                      // "transcribe" | "translate" (→ English)
    chunk_length_s: 30,        // long audio in 30s windows
    stride_length_s: 5,
    return_timestamps: false,
  });
  post({ type: "result", text: (result.text || "").trim() });
}

async function doTranslate({ text, target }) {
  const gen = await getLfm();
  // Hand-built ChatML — see the {% generation %} note at the top.
  const prompt =
    "<|startoftext|><|im_start|>system\n" +
    `You are a translator. Translate the user's text into ${target}. ` +
    "Reply with the translation only.<|im_end|>\n" +
    `<|im_start|>user\n${text}<|im_end|>\n` +
    "<|im_start|>assistant\n";
  let acc = "";
  const streamer = new TextStreamer(gen.tokenizer, {
    skip_prompt: true,
    skip_special_tokens: true,
    callback_function: (t) => {
      acc += t;
      post({ type: "translation-partial", text: acc });
    },
  });
  const out = await gen(prompt, {
    max_new_tokens: 512,
    do_sample: false,
    return_full_text: false,
    streamer,
  });
  const full = (out[0]?.generated_text ?? acc).trim();
  post({ type: "translation", text: full });
}

self.onmessage = async ({ data }) => {
  try {
    if (data.type === "transcribe") await doTranscribe(data);
    else if (data.type === "translate") await doTranslate(data);
  } catch (e) {
    post({ type: "error", text: String(e?.message || e) });
  }
};
