# sound2text

**Speech to text as five steps, four of which are not the model.**

Every speech-to-text library is a wrapper around somebody's recogniser, and the
wrapper is where the cost is decided. A recogniser charges by the second — in
GPU time, in API cents, in a laptop fan — and most recordings are mostly not
speech. What you do before the model runs decides what the transcript costs.

```
decode    read the samples, without a media stack where possible
detect    find the speech, so silence never reaches the model
recall    anything transcribed before is not transcribed again
route     the engine that is fastest or cheapest here — measured, not advertised
run       only what is left, packed to fill the model's window
```

```bash
m sound2text/transcribe file=voice.wav    # the transcript, and what it cost
m sound2text/compare                      # whole file vs trimmed vs packed
m sound2text/engines                      # what can run on this machine
m sound2text/serve                        # API :50640, console :50641/sound2text
```

## The result this module exists to produce

`openai/whisper-tiny.en`, 24-core CPU, no GPU, best of three runs, model loading
excluded. `harvard-8k.wav` is a real recording of the IEEE Harvard sentences, so
the words that were said are known and the transcript can be **scored** rather
than admired.

```
harvard-8k.wav — 33.6s, 86% speech

   run              windows   audio sent   model time      WER
   ---------------------------------------------------------------
   whole file             2       33.6 s       1.77 s    0.111
   trimmed                8       28.9 s       2.53 s    0.284
   trimmed + packed       2       29.7 s       1.72 s    0.086

sparse-45pct-speech.wav — 64.3s, 51% speech (constructed, see below)

   run              windows   audio sent   model time      WER
   ---------------------------------------------------------------
   whole file             3       64.3 s       1.44 s    0.148
   trimmed                8       32.6 s       2.17 s    0.296
   trimmed + packed       2       33.3 s       1.59 s    0.099
```

Three things fall out of that table, and the middle row is the reason this
module is not fifty lines.

**1. Cutting the silence out, on its own, makes everything worse.** It is the
obvious optimisation and it is a trap. Whisper's encoder always sees thirty
seconds: a two-second clip is padded to thirty and costs what thirty costs. Eight
short segments are eight full windows where the untouched file was two — 43%
*slower* here — and the WER goes from 0.111 to 0.284, because a model given
two seconds of audio has no context and guesses. Trimming silence is not a
saving. It is a bill for the same work in smaller pieces.

**2. Packing is what makes the trimming pay.** The segments go back together, in
order, up to just under the window, with a beat of silence between them. Same
speech, a third of the windows, and the model has a paragraph to work with
instead of a phrase. On both files the packed run is the *most accurate of the
three* — better than sending the original file — because it removed the silence
Whisper would otherwise have hallucinated words into.

**3. On a local model, the saving is accuracy and money, not wall-clock.** Whisper
spends most of its time generating tokens, and the same words are the same
tokens whether or not there is silence between them. Trimming a local run saves
7% of the time here. Trimming a *metered* run saves 48% of the bill, because
every one of these APIs charges by the second of audio uploaded.

So: for a local engine this module buys you accuracy. For a hosted one it halves
the invoice. It does not claim both.

## The five steps

### decode — no media stack

`ffmpeg` is 80 MB of dependency for a job that, for WAV, is a header and a memory
copy. This module reads RIFF itself: PCM 8/16/24/32, IEEE float 32/64, and the
a-law/mu-law that comes off a phone line and is exactly the audio people most
want transcribed. `audioop` handled G.711 until Python 3.13 removed it, so both
tables are unpacked in numpy here — and checked byte for byte against the module
they replace, in a test that skips itself once the stdlib is gone.

Resampling is a Kaiser-windowed sinc, applied polyphase. Linear interpolation
would be four lines and would fold everything above the new Nyquist back into
the band a recogniser listens to.

ffmpeg is still used when it is installed and the input is something only it can
open. Whichever decoder ran is reported in the result, because "which decoder
ran" is the first question when a transcript comes back as nonsense.

### detect — where the speech is, without a model

Frames of 30 ms at a 10 ms hop, and three tests: energy against an adaptive
noise floor, spectral flatness, and a Schmitt trigger so a run of speech does not
flicker. About 80 ms per minute of audio, no weights, no network.

Two details that took measuring rather than guessing:

- **Voicing is required to start a run, not to continue one.** `/s/`, `/f/` and
  `/sh/` are as spectrally flat as noise, and they are the ends of words.
- **A recording with no dynamic range has no floor to measure against.** Hiss all
  the way through, a held tone, digital silence. The obvious fallback — put the
  threshold at the median — turns half of any hiss into speech. Measured here,
  white noise has a flatness of 0.56 and speech has 0.00, so the fallback is the
  voicing test and an absolute gate, which is the difference between "this is
  quiet" and "this is not a voice". There is a test for exactly this.

Analysis runs on a peak-normalised copy, so the thresholds mean the same thing
whether the recording came off a headset or a phone in a pocket. The audio
itself is not touched.

### recall — keyed on the audio, not the filename

A sha256 of the samples, the engine, the model, the language and the task. The
same voice note under two names is transcribed once; a job that crashed and
restarted costs nothing the second time. The key is per *segment*, so appending
thirty seconds to a recording and re-running costs thirty seconds, not the whole
file. Writes are atomic — a torn cache file is a wrong transcript, and a wrong
transcript that never expires is worse than no cache.

### route — measured here, not advertised elsewhere

```bash
m sound2text/route policy=cheap
```

Three policies, because there are only three questions anyone asks: `fast` (what
finishes soonest *on this machine*), `cheap` (what costs least — local is free,
so local wins), `best` (the largest model available, whatever it costs). Every
run writes one number back to a ledger — real-time factor, model seconds per
audio second — and `fast` routes on what has actually happened here rather than
on someone's benchmark.

The rule the router will not break: it never picks an engine that cannot run, and
it never quietly substitutes the stub for a recogniser. With nothing installed
it fails with the four commands that would fix it.

### run — one interface, five recognisers

| engine | kind | needs | notes |
|---|---|---|---|
| `faster-whisper` | local | `pip install faster-whisper` | CTranslate2 int8/fp16 — the fastest local option |
| `whisper-torch` | local | `pip install transformers torch` | CPU or GPU, batched, fp16 on CUDA |
| `whisper.cpp` | local | a binary and a `ggml-*.bin` | no python dependencies at all |
| `openai` | remote | `OPENAI_API_KEY` | $0.006/min |
| `groq` | remote | `GROQ_API_KEY` | $0.00067/min, `whisper-large-v3-turbo` |
| `deepinfra` | remote | `DEEPINFRA_API_KEY` | $0.0002/min |
| `stub` | — | nothing | describes the sound; never claims to transcribe it |

Adding one is a file in `src/engines/` and a line in the registry. Nothing else
in the module names an engine.

The `stub` deserves a word. It exists so that the four steps that are not a model
can be tested on a machine with no weights and no network. It returns things like
`[ordinary sound, 3.13s, peak near 220 Hz]`, every result carries
`transcript: false`, and the pipeline propagates that flag to the top of the
response. A test asserts it. A fake transcript that looks real is the one bug in
a speech pipeline that nobody catches.

## Using it

```bash
m sound2text/transcribe file=meeting.wav              # route for me
m sound2text/transcribe file=call.wav engine=groq     # or don't
m sound2text/transcribe file=note.wav policy=cheap
m sound2text/transcribe url=https://example.com/a.wav text_only=true
m sound2text/transcribe file=x.wav vad=false          # send everything
m sound2text/vad file=meeting.wav                     # just the map, no model
m sound2text/compare file=meeting.wav                 # the table above, on yours
m sound2text/bench                                    # every engine, timed
m sound2text/speed                                    # what the ledger has learnt
m sound2text/pull model=base.en                       # download before you need it
m sound2text/set_key vendor=groq key=gsk_…            # 0600, off the tree
```

Every function is the same code the API calls:

```bash
curl -F file=@meeting.wav localhost:50640/transcribe
curl "localhost:50640/vad?path=meeting.wav"
curl "localhost:50640/compare?path=meeting.wav"
```

The console at `:50641/sound2text` draws the waveform with the speech in green
and the skipped silence in grey, which is the fastest way to see whether the
detector is set right for your audio. It records from the microphone too, and
encodes to a 16 kHz WAV in the page — which is how a host with no ffmpeg on it
takes a recording.

## The samples

`harvard-8k.wav` is real: the IEEE Harvard sentences, read aloud, 8 kHz, 33.6
seconds, 86% speech. It is this module's worst case — there is almost nothing to
skip — and that is why it is the default. A saving that only appears on
favourable audio is not a saving.

`sparse-45pct-speech.wav` is **constructed**, by this module, from that same real
speech with room tone between the sentences — faint noise and a little mains hum,
not digital silence, because digital silence would make the detector's job
unrealistically easy. It is labelled as constructed in `m sound2text/samples`, in
the console, and in the API. A test asserts that the label is there.

## What it costs to run

`numpy` for everything below the recogniser; `fastapi` and `uvicorn` for the API;
one recogniser of your choosing, or none if all you want is the detector. State
lives in `~/.mod/sound2text` — keys at 0600, the transcript cache, the speed
ledger. Nothing private is ever written into this directory, and the two
endpoints that store a secret refuse any request that did not come from the
loopback interface.

```bash
m sound2text/test        # 28 tests, no model and no network needed
```

## What it does not do

Diarisation (who spoke), word-level timestamps, live streaming, or punctuation
restoration beyond what the model gives you. It does not train anything. It will
not tell you a transcript is good — it will tell you the WER when the words are
known, and otherwise it reports what it sent, what it skipped, and what it cost,
and leaves the judgement to you.
