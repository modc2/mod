# sound2text

Speech to text as five steps rather than one call: decode the audio without a
media stack, find the speech, recall what was transcribed before, route to the
recogniser that is fastest or cheapest *on this machine*, and run only what is
left — packed to fill the model's window.

Dependencies: numpy (plus fastapi/uvicorn for the API). Any one recogniser is
optional: faster-whisper, transformers+torch, whisper.cpp, or an API key.

API `:50640` · console `:50641/sound2text` · state `~/.mod/sound2text`

## When to reach for it

- transcribing anything — a voice note, a meeting, a call, a podcast
- "how much of this recording is actually speech" — the detector runs with no model
- cutting a transcription bill: it sends segments, not files, to metered APIs
- picking a recogniser: `m sound2text/bench` times every one that runs here
- reading a WAV, resampling it, or decoding a-law/mu-law without ffmpeg
- scoring a transcript against a known reference (WER, with the error kinds split)

Not for: diarisation, word-level timestamps, live streaming, or training.

## The one idea

Removing silence is not the optimisation. **Packing what is left back into full
model windows is.** Whisper's encoder always sees thirty seconds, so eight
two-second segments cost eight full windows — slower than the untouched file, and
much less accurate, because a model given two seconds has no context.

Measured on `whisper-tiny.en`, CPU, model loading excluded:

```
harvard-8k.wav (33.6s, 86% speech)   windows   sent   model    WER
   whole file                              2  33.6s   1.77s  0.111
   trimmed                                 8  28.9s   2.53s  0.284   ← the trap
   trimmed + packed                        2  29.7s   1.72s  0.086   ← best of the three
```

On a local model the win is accuracy. On a metered API it is the bill: 48% less
audio uploaded on a recording that is half silence.

## CLI

```bash
m sound2text/info                              # the card
m sound2text/engines                           # every engine, and which run here
m sound2text/route policy=cheap                # what it would pick, and why
m sound2text/transcribe file=voice.wav         # transcript + full accounting
m sound2text/transcribe file=v.wav engine=groq policy=cheap text_only=true
m sound2text/vad file=voice.wav                # speech map, no model, ~80ms/min
m sound2text/compare file=voice.wav            # whole vs trimmed vs packed, scored
m sound2text/bench                             # every engine, timed → fills the ledger
m sound2text/speed                             # measured rtf per engine
m sound2text/samples                           # audio to try, real and constructed
m sound2text/cache clear=true                  # the transcript cache
m sound2text/pull model=base.en                # download weights up front
m sound2text/set_key vendor=groq key=gsk_…     # 0600 in ~/.mod/sound2text
m sound2text/serve                             # API :50640 + console :50641
```

## HTTP

```bash
curl -F file=@meeting.wav localhost:50640/transcribe
curl "localhost:50640/transcribe?path=/tmp/a.wav&engine=faster-whisper&policy=fast"
curl "localhost:50640/vad?path=/tmp/a.wav"       # includes a waveform for drawing
curl "localhost:50640/compare?path=/tmp/a.wav"
curl localhost:50640/engines
```

## What a result contains

```json
{"text": "…",
 "engine": "whisper-torch", "model": "tiny.en", "transcript": true,
 "routing": {"why": "fastest available here (measured rtf 0.059)"},
 "segments": [{"start": 0.0, "end": 28.95, "parts": 7, "text": "…", "cached": false}],
 "stats": {"audio_s": 33.6, "sent_to_model_s": 29.7, "saved_pct": 11.8,
           "windows": 2, "speech_spans": 8, "load_s": 0.9, "model_s": 1.72,
           "from_cache_s": 0.0, "rtf": 0.058, "cost_usd": 0.0}}
```

`transcript: false` means the engine was the `stub`, which describes sound rather
than recognising it. The router will never choose it on its own; ask for it by
name with `engine=stub` to exercise the pipeline without weights.

## Traps this module already fell into

- **Trimming without packing** — slower than doing nothing, and 2.6× the WER.
- **Counting model load as recognition time** — makes every first run look like a
  slow engine and teaches the router the wrong lesson. `load_s` is separate.
- **A flatness threshold of 0.55** — white noise measures 0.56, so hiss came back
  as speech. Speech measures 0.00; the threshold is 0.35.
- **A file with no dynamic range** — putting the threshold at the median turns
  half of any hiss into a transcript. Falls back to voicing + an absolute gate.
- **`audioop` is gone in Python 3.13** — G.711 is decoded in numpy here, verified
  against the stdlib byte for byte while a version still has it.

## Related

`agent`, `claude` — for what to *do* with a transcript. `embed` — for the same
"measure what the shortcut cost" treatment applied to model compression.
