/* lfm-worker.js — LFM2.5-Audio-1.5B ASR, fully inside the tab.
 *
 * This is the liquidai catalog's browser-runnable audio model
 * (LiquidAI/LFM2.5-Audio-1.5B-ONNX) on onnxruntime-web/WebGPU. The repo is
 * not a transformers.js pipeline — the pieces are hand-wired here, and
 * every step mirrors Liquid4All/onnx-export's reference implementation
 * (src/liquidonnx/lfm2_audio/infer.py):
 *
 *   16 kHz PCM ─ pre-emphasis 0.97 ─ centered STFT (512 fft / 400 hann /
 *   160 hop) ─ slaney 128-mel power ─ log(x + 6e-8) ─ per-feature
 *   normalize (Bessel, valid frames only)
 *        └→ audio_encoder_q4  →  audio_embeddings [1, T', 2048]
 *   <|startoftext|><|im_start|>system\nPerform ASR.<|im_end|>\n<|im_start|>user\n
 *        [AUDIO EMBEDDINGS] <|im_end|>\n<|im_start|>assistant\n
 *        └→ decoder_q4 (greedy, KV+conv cache) until <|im_end|>
 *
 * Downloads (~1.9 GB, once — kept in the browser Cache API):
 *   audio_encoder_q4  ~140 MB · decoder_q4 ~1218 MB · embed_tokens ~537 MB
 */

import { AutoTokenizer } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0";
import * as ort from "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/ort.webgpu.min.mjs";

ort.env.wasm.wasmPaths = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/";

const REPO = "LiquidAI/LFM2.5-Audio-1.5B-ONNX";
const BASE = `https://huggingface.co/${REPO}/resolve/main`;
const CACHE = "voice-lfm-v1";
const HIDDEN = 2048;
const MAX_NEW_TOKENS = 448;

const post = (msg) => self.postMessage(msg);

// ── cached, progress-reporting fetch ────────────────────────────────

async function fetchBytes(url, label, sizeHint) {
  const cache = await caches.open(CACHE).catch(() => null);
  if (cache) {
    const hit = await cache.match(url);
    if (hit) return new Uint8Array(await hit.arrayBuffer());
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} fetching ${url}`);
  const total = Number(res.headers.get("Content-Length")) || sizeHint || 0;
  const reader = res.body.getReader();
  const parts = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    got += value.length;
    if (total) {
      post({ type: "progress", pct: (got / total) * 100 });
      post({ type: "status", text: `${label}: ${(got / 1e6).toFixed(0)}/${(total / 1e6).toFixed(0)} MB` });
    }
  }
  const buf = new Uint8Array(got);
  let off = 0;
  for (const p of parts) { buf.set(p, off); off += p.length; }
  if (cache) await cache.put(url, new Response(buf)).catch(() => {});
  return buf;
}

// ── mel frontend (matches compute_mel_spectrogram_numpy) ────────────

function hann(n) {
  // np.hanning: symmetric window
  const w = new Float32Array(n);
  for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1));
  return w;
}

function melFilterbank(sr, nFft, nMels, fmin, fmax) {
  // librosa.filters.mel with htk=False, norm="slaney"
  const hzToMel = (f) => f < 1000 ? f / (200 / 3)
    : 15 + Math.log(f / 1000) / (Math.log(6.4) / 27);
  const melToHz = (m) => m < 15 ? m * (200 / 3)
    : 1000 * Math.exp((m - 15) * (Math.log(6.4) / 27));
  const nBins = Math.floor(nFft / 2) + 1;
  const fftFreqs = new Float64Array(nBins);
  for (let i = 0; i < nBins; i++) fftFreqs[i] = (i * sr) / nFft;
  const melPts = new Float64Array(nMels + 2);
  const mLo = hzToMel(fmin), mHi = hzToMel(fmax);
  for (let i = 0; i < nMels + 2; i++) melPts[i] = melToHz(mLo + ((mHi - mLo) * i) / (nMels + 1));
  const weights = new Float32Array(nMels * nBins);
  for (let m = 0; m < nMels; m++) {
    const lower = melPts[m], center = melPts[m + 1], upper = melPts[m + 2];
    const enorm = 2 / (upper - lower); // slaney norm
    for (let k = 0; k < nBins; k++) {
      const f = fftFreqs[k];
      const w = Math.min((f - lower) / (center - lower), (upper - f) / (upper - center));
      if (w > 0) weights[m * nBins + k] = w * enorm;
    }
  }
  return weights;
}

// iterative radix-2 FFT, real input, n a power of two
function rfftPower(frame, n, re, im) {
  re.set(frame); im.fill(0);
  for (let i = 1, j = 0; i < n; i++) {  // bit-reverse
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      const tr = re[i]; re[i] = re[j]; re[j] = tr;
      const ti = im[i]; im[i] = im[j]; im[j] = ti;
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cwr = 1, cwi = 0;
      for (let k = 0; k < len / 2; k++) {
        const ur = re[i + k], ui = im[i + k];
        const vr = re[i + k + len / 2] * cwr - im[i + k + len / 2] * cwi;
        const vi = re[i + k + len / 2] * cwi + im[i + k + len / 2] * cwr;
        re[i + k] = ur + vr; im[i + k] = ui + vi;
        re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
        const nwr = cwr * wr - cwi * wi;
        cwi = cwr * wi + cwi * wr; cwr = nwr;
      }
    }
  }
}

function melSpectrogram(audio, cfg) {
  const nFft = cfg.n_fft ?? 512, win = cfg.win_length ?? 400,
    hop = cfg.hop_length ?? 160, nMels = cfg.n_mels ?? 128,
    preemph = cfg.preemph ?? 0.97,
    guard = cfg.log_zero_guard ?? 5.960464477539063e-8,
    sr = cfg.sample_rate ?? 16000,
    fmin = cfg.fmin ?? 0, fmax = cfg.fmax ?? sr / 2;
  const nBins = nFft / 2 + 1;
  const fb = melFilterbank(sr, nFft, nMels, fmin, fmax);

  // pre-emphasis
  const x = new Float32Array(audio.length);
  x[0] = audio[0];
  for (let i = 1; i < audio.length; i++) x[i] = audio[i] - preemph * audio[i - 1];

  // center pad + framed windowed FFT (window centered in the fft frame)
  const pad = nFft / 2;
  const padded = new Float32Array(x.length + 2 * pad);
  padded.set(x, pad);
  const nFrames = 1 + Math.floor((padded.length - nFft) / hop);
  const window = hann(win);
  const winPad = (nFft - win) >> 1;

  const mel = new Float32Array(nMels * nFrames); // [mel, time]
  const frame = new Float32Array(nFft);
  const re = new Float32Array(nFft), im = new Float32Array(nFft);
  const power = new Float32Array(nBins);
  for (let t = 0; t < nFrames; t++) {
    frame.fill(0);
    const start = t * hop;
    for (let i = 0; i < win; i++) frame[winPad + i] = padded[start + winPad + i] * window[i];
    rfftPower(frame, nFft, re, im);
    for (let k = 0; k < nBins; k++) power[k] = re[k] * re[k] + im[k] * im[k];
    for (let m = 0; m < nMels; m++) {
      let acc = 0;
      const row = m * nBins;
      for (let k = 0; k < nBins; k++) acc += fb[row + k] * power[k];
      mel[m * nFrames + t] = Math.log(acc + guard);
    }
  }

  // per-feature normalize over valid frames, Bessel's correction
  const validLen = Math.min(nFrames, Math.floor(audio.length / hop));
  if (validLen > 1) {
    for (let m = 0; m < nMels; m++) {
      const row = m * nFrames;
      let mean = 0;
      for (let t = 0; t < validLen; t++) mean += mel[row + t];
      mean /= validLen;
      let ss = 0;
      for (let t = 0; t < validLen; t++) { const d = mel[row + t] - mean; ss += d * d; }
      const std = Math.sqrt(ss / (validLen - 1)) + 1e-5;
      for (let t = 0; t < validLen; t++) mel[row + t] = (mel[row + t] - mean) / std;
      for (let t = validLen; t < nFrames; t++) mel[row + t] = 0;
    }
  }

  // [mel, time] → [1, time, mel]
  const out = new Float32Array(nFrames * nMels);
  for (let t = 0; t < nFrames; t++)
    for (let m = 0; m < nMels; m++) out[t * nMels + m] = mel[m * nFrames + t];
  return { data: out, frames: nFrames, nMels };
}

// ── model state ─────────────────────────────────────────────────────

let state = null; // { tokenizer, encoder, decoder, embed, melCfg, imEnd }

async function load() {
  if (state) return state;
  if (!navigator.gpu) throw new Error("WebGPU is required for this engine");

  post({ type: "status", text: "loading tokenizer + mel config…" });
  const [tokenizer, melCfg, embedMeta] = await Promise.all([
    AutoTokenizer.from_pretrained(REPO),
    fetch(`${BASE}/onnx/mel_config.json`).then((r) => r.ok ? r.json() : ({})),
    fetch(`${BASE}/onnx/embed_tokens.json`).then((r) => r.json()),
  ]);

  const encGraph = await fetchBytes(`${BASE}/onnx/audio_encoder_q4.onnx`, "encoder graph");
  const encData = await fetchBytes(`${BASE}/onnx/audio_encoder_q4.onnx_data`, "encoder weights", 139e6);
  post({ type: "status", text: "compiling audio encoder (WebGPU)…" });
  const encoder = await ort.InferenceSession.create(encGraph, {
    executionProviders: ["webgpu"],
    externalData: [{ path: "audio_encoder_q4.onnx_data", data: encData }],
  });

  const decGraph = await fetchBytes(`${BASE}/onnx/decoder_q4.onnx`, "decoder graph");
  const decData = await fetchBytes(`${BASE}/onnx/decoder_q4.onnx_data`, "decoder weights", 1218e6);
  post({ type: "status", text: "compiling decoder (WebGPU)…" });
  const decoder = await ort.InferenceSession.create(decGraph, {
    executionProviders: ["webgpu"],
    externalData: [{ path: "decoder_q4.onnx_data", data: decData }],
  });

  const embedRaw = await fetchBytes(`${BASE}/onnx/embed_tokens.bin`, "embedding table", 537e6);
  const embed = new Float32Array(embedRaw.buffer, embedRaw.byteOffset, embedRaw.byteLength / 4);
  const hidden = embedMeta.hidden_size ?? HIDDEN;

  const imEnd = tokenizer.encode("<|im_end|>", null, { add_special_tokens: false })[0];
  state = { tokenizer, encoder, decoder, embed, melCfg, hidden, imEnd };
  post({ type: "progress", pct: null });
  return state;
}

function textEmbeds(ids, embed, hidden) {
  const out = new Float32Array(ids.length * hidden);
  for (let i = 0; i < ids.length; i++)
    out.set(embed.subarray(ids[i] * hidden, (ids[i] + 1) * hidden), i * hidden);
  return new ort.Tensor("float32", out, [1, ids.length, hidden]);
}

function initCache(decoder, hidden) {
  const cache = {};
  for (const name of decoder.inputNames) {
    if (name.startsWith("past_conv")) {
      cache[name] = new ort.Tensor("float32", new Float32Array(hidden * 3), [1, hidden, 3]);
    } else if (name.startsWith("past_key_values")) {
      cache[name] = new ort.Tensor("float32", new Float32Array(0), [1, 8, 0, 64]);
    }
  }
  return cache;
}

function rollCache(cache, outputs) {
  for (const [name, tensor] of Object.entries(outputs)) {
    if (name.startsWith("present_conv")) cache[name.replace("present_conv", "past_conv")] = tensor;
    else if (name.startsWith("present.")) cache[name.replace("present.", "past_key_values.")] = tensor;
  }
}

function argmaxLast(logits) {
  // logits: [1, seq, vocab] — argmax over the final position
  const [, seq, vocab] = logits.dims;
  const data = logits.data;
  const base = (seq - 1) * vocab;
  let best = 0, bestV = -Infinity;
  for (let i = 0; i < vocab; i++) {
    const v = data[base + i];
    if (v > bestV) { bestV = v; best = i; }
  }
  return best;
}

async function transcribe(audio) {
  const s = await load();
  const { tokenizer, encoder, decoder, embed, melCfg, hidden, imEnd } = s;

  post({ type: "status", text: "computing mel spectrogram…" });
  const mel = melSpectrogram(audio, melCfg);
  const melT = new ort.Tensor("float32", mel.data, [1, mel.frames, mel.nMels]);
  const melLen = new ort.Tensor("int64", BigInt64Array.from([BigInt(mel.frames)]), [1]);

  post({ type: "status", text: "encoding audio…" });
  const enc = await encoder.run({ mel_spectrogram: melT, mel_lengths: melLen });
  const audioEmb = enc.audio_embeddings; // [1, T', 2048]

  // ChatML prompt around the audio embeddings — exactly the reference's.
  const prefixIds = tokenizer.encode(
    "<|startoftext|><|im_start|>system\nPerform ASR.<|im_end|>\n<|im_start|>user\n",
    null, { add_special_tokens: false });
  const suffixIds = tokenizer.encode(
    "<|im_end|>\n<|im_start|>assistant\n", null, { add_special_tokens: false });
  const pre = textEmbeds(prefixIds, embed, hidden);
  const suf = textEmbeds(suffixIds, embed, hidden);

  const audioLen = audioEmb.dims[1];
  const seqLen = prefixIds.length + audioLen + suffixIds.length;
  const all = new Float32Array(seqLen * hidden);
  all.set(pre.data, 0);
  all.set(await audioEmb.getData(), prefixIds.length * hidden);
  all.set(suf.data, (prefixIds.length + audioLen) * hidden);
  let embeds = new ort.Tensor("float32", all, [1, seqLen, hidden]);

  const cache = initCache(decoder, hidden);
  let totalLen = seqLen;
  const generated = [];
  post({ type: "status", text: "decoding…" });

  for (let step = 0; step < MAX_NEW_TOKENS; step++) {
    const mask = new ort.Tensor("int64", new BigInt64Array(totalLen).fill(1n), [1, totalLen]);
    const outputs = await decoder.run({ inputs_embeds: embeds, attention_mask: mask, ...cache });
    rollCache(cache, outputs);
    const next = argmaxLast(outputs.logits);
    if (next === imEnd) break;
    generated.push(next);
    totalLen += 1;
    embeds = textEmbeds([next], embed, hidden);
    if (step % 4 === 0) {
      post({ type: "partial", text: tokenizer.decode(generated, { skip_special_tokens: true }) });
    }
  }

  post({ type: "result", text: tokenizer.decode(generated, { skip_special_tokens: true }).trim() });
}

self.onmessage = async ({ data }) => {
  try {
    if (data.type === "transcribe") await transcribe(data.audio);
  } catch (e) {
    post({ type: "error", text: "LFM engine: " + String(e?.message || e) });
  }
};
