/* voice.js — main thread: capture audio, hand Float32 PCM to a worker,
 * render transcript + translation. All inference lives in the workers;
 * this file never talks to the server about audio at all.
 *
 * Two workers, one message protocol ({type, ...}):
 *   voice-worker.js  transformers.js — whisper ASR + LFM2.5-350M translate
 *   lfm-worker.js    onnxruntime-web — LFM2.5-Audio-1.5B ASR (WebGPU)
 */

const $ = (id) => document.getElementById(id);
const engineSel = $("engine"), targetSel = $("target");
const recordBtn = $("record"), drop = $("drop"), fileInput = $("file");
const statusEl = $("status"), bar = $("bar"), barFill = bar.firstElementChild;
const out = $("out"), transPanel = $("transPanel"), trans = $("trans");

const SAMPLE_RATE = 16000;

let tjsWorker = null;   // transformers.js engines (whisper + translate)
let lfmWorker = null;   // onnxruntime-web LFM audio engine
let recorder = null, chunks = [], busy = false;

function status(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}
function progress(pct) {
  if (pct == null) { bar.className = ""; return; }
  bar.className = "on";
  barFill.style.width = Math.max(0, Math.min(100, pct)) + "%";
}

function worker(kind) {
  if (kind === "lfm") {
    if (!lfmWorker) {
      lfmWorker = new Worker("lfm-worker.js", { type: "module" });
      lfmWorker.onmessage = onWorkerMessage;
      lfmWorker.onerror = (e) => { status("lfm worker error: " + e.message, "err"); busy = false; };
    }
    return lfmWorker;
  }
  if (!tjsWorker) {
    tjsWorker = new Worker("voice-worker.js", { type: "module" });
    tjsWorker.onmessage = onWorkerMessage;
    tjsWorker.onerror = (e) => { status("worker error: " + e.message, "err"); busy = false; };
  }
  return tjsWorker;
}

function onWorkerMessage({ data }) {
  switch (data.type) {
    case "status":
      status(data.text, "busy");
      break;
    case "progress":
      progress(data.pct);
      break;
    case "partial":
      out.textContent = data.text || "…";
      break;
    case "result":
      progress(null);
      out.textContent = data.text || "(no speech found)";
      busy = false;
      maybeTranslate(data.text);
      break;
    case "translation":
      transPanel.hidden = false;
      trans.textContent = data.text;
      status("done", "");
      busy = false;
      break;
    case "translation-partial":
      transPanel.hidden = false;
      trans.textContent = data.text;
      break;
    case "error":
      progress(null);
      status(data.text, "err");
      busy = false;
      break;
  }
}

function maybeTranslate(text) {
  const target = targetSel.value;
  // "" = raw transcript; "en" is whisper's own translate task, already done.
  if (!target || target === "en" || !text || !text.trim()) {
    status("done", "");
    return;
  }
  busy = true;
  status("translating to " + target + " (LFM2.5-350M in-tab)…", "busy");
  worker("tjs").postMessage({ type: "translate", text, target });
}

// ── audio in: file or mic → mono Float32 @ 16 kHz ──────────────────

async function decodeToPcm(arrayBuffer) {
  // OfflineAudioContext resamples for us during decode.
  const probe = new AudioContext();
  const raw = await probe.decodeAudioData(arrayBuffer);
  probe.close();
  const frames = Math.ceil(raw.duration * SAMPLE_RATE);
  const off = new OfflineAudioContext(1, frames, SAMPLE_RATE);
  const src = off.createBufferSource();
  src.buffer = raw;
  src.connect(off.destination);
  src.start();
  const mixed = await off.startRendering();
  return mixed.getChannelData(0).slice();
}

async function transcribe(pcm) {
  if (busy) return;
  busy = true;
  transPanel.hidden = true;
  out.innerHTML = '<span class="ghost">…</span>';
  const engine = engineSel.value;
  const target = targetSel.value;
  const seconds = (pcm.length / SAMPLE_RATE).toFixed(1);
  status(`transcribing ${seconds}s with ${engine}…`, "busy");
  if (engine === "lfm-audio") {
    worker("lfm").postMessage({ type: "transcribe", audio: pcm }, [pcm.buffer]);
  } else {
    worker("tjs").postMessage({
      type: "transcribe",
      engine,
      audio: pcm,
      task: target === "en" ? "translate" : "transcribe",
    }, [pcm.buffer]);
  }
}

async function handleFile(file) {
  try {
    status("decoding " + file.name + "…", "busy");
    const pcm = await decodeToPcm(await file.arrayBuffer());
    await transcribe(pcm);
  } catch (e) {
    status("could not decode audio: " + e.message, "err");
  }
}

// mic
recordBtn.addEventListener("click", async () => {
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      recordBtn.textContent = "● RECORD";
      recordBtn.className = "hot";
      const blob = new Blob(chunks, { type: recorder.mimeType });
      const pcm = await decodeToPcm(await blob.arrayBuffer());
      await transcribe(pcm);
    };
    recorder.start();
    recordBtn.textContent = "■ STOP";
    recordBtn.className = "rec";
    status("recording — click STOP when done", "busy");
  } catch (e) {
    status("microphone unavailable: " + e.message, "err");
  }
});

// file drop / pick
drop.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});
["dragover", "dragenter"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => {
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

// The LFM engines need WebGPU; say so up front instead of failing a
// gigabyte into the download.
function webgpuGate() {
  if (navigator.gpu) { status("ready", ""); return; }
  if (engineSel.value === "lfm-audio") {
    status("LFM2.5 AUDIO needs WebGPU — this browser has none. " +
           "Whisper engines still work (WASM).", "err");
  } else if (targetSel.value && targetSel.value !== "en") {
    status("the LFM2.5-350M translation pass needs WebGPU — " +
           "ENGLISH (whisper translate) works everywhere.", "err");
  } else {
    status("ready", "");
  }
}
engineSel.addEventListener("change", webgpuGate);
targetSel.addEventListener("change", webgpuGate);
