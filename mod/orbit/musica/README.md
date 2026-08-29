# musica

A DJ booth and a pattern studio in one browser tab. Two decks with beatmatch,
EQ, filters, beat loops and hot cues; a step sequencer and piano roll in the FL
Studio idiom running off the same clock; and Spotify as the crate you plan a set
from. Every sample is decoded and mixed client-side through Web Audio — the
module serves the console and answers the Spotify half from your own API keys.

```
m musica                      # null call → info()
m musica/play                 # serve the console and open a browser
m musica/serve                # run it under pm2, then register the route
m musica/decks                # the signal chain, deck by deck
m musica/kit                  # the sequencer's voices
m musica/set_key client_id=… client_secret=…
m musica/search q="four tet"
m musica/test                 # files + JS syntax + the engine harness
m musica/kill
```

App `/musica` · API `:50780` (`/api/musica`) · one process serves both

## The problem it is shaped around

Every browser DJ tool has to answer one question first: where does the audio
come from? Streaming services are the obvious answer and the wrong one —
Spotify's audio is DRM-protected and cannot be routed through Web Audio at all,
so anything built on it can play a track but never EQ, loop or scratch it.

So musica splits the two jobs honestly. **Files you drop in are the sound**:
decoded in the tab, analysed in the tab, mixed in the tab, never uploaded.
**Spotify is the crate**: search it to plan the set, find the track, then load
your own copy onto a deck to actually mix it. The CRATE tab shows both columns
side by side and labels which is which.

The second question is tempo. Spotify closed `/audio-features` and
`/audio-analysis` to apps registered after 2024-11-27, so the BPM and key on a
deck cannot be looked up — they are worked out from the samples, in a worker,
here. See *Tempo, honestly* below for what that does and does not get right.

## The three surfaces

**BOOTH** — two decks either side of a mixer. Each deck has an overview
waveform, a zoomed view with the detected beat grid drawn over it, four hot
cues, beat loops from ½ to 8 bars, a ±16% pitch fader with nudge, and SYNC.
The mixer is a channel strip per deck (gain, 3-band EQ, one bipolar filter
knob, fader, pre-fader cue) around a constant-power crossfader, with a global
echo whose delay time follows the sequencer's BPM.

**STUDIO** — eight patterns of 16, 32 or 64 steps across eleven channels, with
swing on the 16ths. Every voice is synthesised at play time (`m musica/kit`
lists them), so the module ships with no audio assets. Drop a file on a channel
name and that channel becomes a sampler, pitched by the piano roll. Patterns
are kept in `localStorage` and restored next time.

**CRATE** — Spotify search over tracks, albums, artists and playlists, next to
the local files you have decoded. Local rows show duration, detected BPM and
Camelot key, and load straight onto a deck.

## Keys

`space` sequencer · `q`/`p` deck A/B · `a`/`s`/`d` crossfader left/centre/right
· `1`/`2`/`3` tabs. On a knob: drag, `shift`-drag for fine, double-click to
reset, or scroll.

## Tempo, honestly

The detector runs in two passes over an onset envelope: an autocorrelation with
a harmonic comb to find the periodicity, then a separate decision about which
metrical level to call the beat.

That second pass exists because autocorrelation genuinely cannot tell 90 from
180 — a track with an offbeat hat is periodic at both, and a hypothesis at half
the true period collects the true period's own peak as its second harmonic. So
the octave is chosen on a different measurement: mean onset energy *per beat* on
each candidate's own grid, which a double-time hypothesis has to spend on the
weak events in between. Where two levels really are equal — 174 against 87, in
drum and bass — a log-normal prior around 125 BPM decides, the way a person
tapping along would.

It still gets some tracks wrong, which is why **clicking the BPM readout on a
deck cycles ×2 and ÷2**, and why the confidence is shown. Every DJ tool has this
button for the same reason.

Key detection is a chroma vector from Goertzel bins correlated against the
Krumhansl-Schmuckler profiles, reported as both a name and a Camelot code.
`tests/engine.mjs` asserts the whole wheel: relatives share a number, fifths are
adjacent, all 24 codes are reachable.

## What it will not do

- **Mix Spotify audio.** DRM. Load the file.
- **Give you a real headphone cue.** A booth sends the cue bus to a second
  output device; a browser only offers that behind `setSinkId` with a device the
  user has picked. CUE here is a pre-fader solo that ducks the program on the
  one output you have.
- **Key-lock the pitch fader.** Pitch is `playbackRate`, so a pitched track
  changes key. Time-stretching without artefacts is a much larger piece of DSP
  than this module wants to be.
- **Persist your audio.** Dropped files are never uploaded and never stored;
  patterns are, in `localStorage`.

## The API

Reads only — `serve`, `kill` and `set_key` stay on the CLI, because the API
answers from the public gateway.

| endpoint | what it answers |
| --- | --- |
| `/info` | null call — what this module is and what it exposes |
| `/health` | liveness |
| `/decks` | the mixer's signal chain, deck by deck |
| `/kit` | the synthesised voices the sequencer ships with |
| `/keys`, `/spotify_status` | credential status (masked) and what Spotify will answer |
| `/search` | `q`, `kind=track\|album\|artist\|playlist`, `limit` |
| `/track`, `/playlist` | one track's metadata; a public playlist's tracks |

`Mod.CHAIN` and `Mod.KIT` in `mod.py` are the same lists the console builds
from, so `m musica/decks` and the audio graph cannot drift apart silently.

## Spotify

Optional — the decks and the studio work without it. Register an app at
[developer.spotify.com/dashboard](https://developer.spotify.com/dashboard); the
client-credentials grant used here needs no redirect URI and no user login.

```
m musica/set_key client_id=… client_secret=…
```

Credentials go to `~/.mod/musica/keys.json` at mode 0600, or come from
`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`. They are never committed and
never echoed back — `keys()` masks them.

## Layout

```
mod.py          the anchor — CLI, API surface, CHAIN and KIT
serve.py        one process: static console + the mod protocol API
spotify.py      client-credentials Spotify client
web/index.html  the console
web/js/engine.js     AudioContext, the two decks, the master bus
web/js/analyze.js    tempo and key detection — pure, and node-testable
web/js/synth.js      the drum kit and synth voices
web/js/sequencer.js  the clock, patterns, the piano roll's data
web/js/spotify.js    the API client
web/js/ui.js         knobs, canvases, toasts
web/js/app.js        the wiring
tests/engine.mjs     the harness m musica/test runs
```

## Tests

```
m musica/test
```

Checks every file is present, runs `node --check` over each script, then runs
`tests/engine.mjs`, which loads the same files the browser loads and drives the
pure halves against synthetic audio: click tracks at known tempos, chords built
from known key profiles, the Camelot wheel, the swing grid, the crossfader and
EQ curves, and pattern resizing. It prints a JSON summary and exits non-zero on
any failure.
