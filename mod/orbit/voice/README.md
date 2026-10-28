# voice

Fully local audio → text, **in the visitor's own browser tab**. The page
records (or accepts) audio, transcribes it with a model running on
WebGPU/WASM inside the tab, and optionally translates the transcript there
too. No audio, no transcript, nothing leaves the page — the server's only
jobs are handing out the static app, answering "what can a tab run", and
speaking MCP.

    API + app   :50980        (one uvicorn process)
    gateway     /voice/  ·  /api/voice
    MCP         POST /mcp (streamable HTTP) or stdio

## Engines — all in-browser

| engine | runtime | size | does |
|---|---|---|---|
| WHISPER tiny/base/small | transformers.js — WebGPU, WASM fallback | 40–250 MB | transcribe ~100 languages; native `translate` task → English |
| **LFM2.5-Audio-1.5B** | onnxruntime-web — WebGPU required | ~1.9 GB | transcribe (English). The liquidai catalog's browser-runnable audio model |
| LFM2.5-350M | transformers.js — WebGPU required | ~300 MB | translation post-pass: transcript → any target language |

(The 350M translation pass is WebGPU-only in practice: every quant the repo
ships quantizes the embedding gather with `GatherBlockQuantized`, an op
onnxruntime-web only implements on the WebGPU EP. On WebGPU the worker picks
`q4f16` when the adapter reports `shader-f16`, `q4` otherwise.)

Why two ASR engines: Whisper is the pragmatic default — small, runs
everywhere, and `task=translate` gives audio→English in one pass. The LFM
engine is the point of the exercise: `LiquidAI/LFM2.5-Audio-1.5B-ONNX` ships
WebGPU-ready Q4 graphs (tagged `asr` upstream), but it is **not** a
transformers.js pipeline — `src/app/lfm-worker.js` hand-wires the pieces,
mirroring Liquid's reference (`Liquid4All/onnx-export`,
`lfm2_audio/infer.py`) step for step:

    16 kHz PCM → pre-emphasis 0.97 → centered STFT (512 fft / 400 hann / 160 hop)
      → slaney 128-mel power → log(x + 6e-8) → per-feature normalize
      → audio_encoder_q4 → audio embeddings [1, T', 2048]
    <|im_start|>system\nPerform ASR.<|im_end|> … [AUDIO EMBEDDINGS] …
      → decoder_q4, greedy with KV+conv cache, until <|im_end|>

Weights are cached in the browser Cache API after the first download.

## The liquidai dependency

`/models` here is the [liquidai](../liquidai/) catalog filtered to
`runtime=browser` — that catalog is derived from HuggingFace at runtime and
folds repos by weights, so when Liquid ships a new browser-capable model it
shows up here without a code change. The LFM2.5 chat-template trap
(`{% generation %}` breaks @huggingface/jinja) is dodged by never calling
`apply_chat_template`: both workers build their ChatML strings by hand.

## Run

    m voice/serve            # :50980
    m voice/status
    m voice/test             # health + catalog + MCP round-trip
    m voice/models kind=audio
    m voice/kill

Open http://localhost:50980/ (or /voice/ on the gateway — keep the trailing
slash, the app uses relative asset paths).

## MCP

    claude mcp add --transport http voice http://localhost:50980/mcp
    claude mcp add voice -- python3 -m src.api.mcp_server     # stdio, cwd here

Tools: `voice_engines`, `voice_models`, `voice_status`, `voice_app` (all
read-only) and `voice_transcribe` — the one server-side path, which forwards
a file on this box to liquidai's `/transcribe` for when there is no browser
in the loop. It is labelled as the not-fully-local path on purpose.

## Layout

    config.json
    src/
      mod.py               # anchor: lifecycle + mod-protocol surface
      api/
        server.py          # FastAPI: /health /engines /models /transcribe /mcp + static app
        mcp_server.py      # one JSON-RPC dispatcher, stdio + HTTP transports
      app/
        index.html         # the app shell
        voice.js           # capture, decode to 16 kHz PCM, worker orchestration
        voice-worker.js    # transformers.js: whisper ASR + LFM2.5-350M translate
        lfm-worker.js      # onnxruntime-web: LFM2.5-Audio-1.5B ASR (WebGPU)
