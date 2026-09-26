# @ref — pose search over your reference images

You don't remember which folder a reference landed in; you remember the
**view**: "3/4 left, seen slightly from above, head tilted." @ref makes
that the query. Every indexed image carries the orientation of its
subject — yaw, pitch, roll, like a gimbal holding a mannequin — and
finding a reference is pointing the gimbal at the view you're drawing,
not spelunking through folders.

Local-first, self-sustaining by design:

- **Images never move.** `scan` indexes folders in place; the index holds
  paths and poses, nothing else. Delete `data/ref.db` and the module's
  entire state is gone — your images aren't.
- **No cloud, no keys, no big-tech API.** The whole thing is the Python
  standard library + SQLite. Nothing leaves the machine.
- **Models are optional plug-ins, never dependencies.** If mediapipe is
  installed locally, `scan` pre-fills head poses; if not, images queue in
  UNTAGGED and you orient them from the console's drag-gimbal in a couple
  of seconds each. Add your own estimator with one decorator in
  `estimate.py`.

## The pose model

| angle | meaning | range |
|---|---|---|
| `yaw` | heading. 0 = facing you, -45 = 3/4 left, ±180 = back | -180…180 |
| `pitch` | elevation. +30 = seen from above, -30 = from below | -90…90 |
| `roll` | tilt toward viewer-right | -180…180 |

Matching is the great-circle angle between facing directions plus a
discounted roll term (`roll_weight`, default 0.5) — where the subject
faces matters more than how the head is tilted. Yaw wraps correctly
across the back, so 175° and -175° are neighbours, not opposites.

## Use it

```sh
m @ref/scan path=~/art/refs tags="figure female"   # index a folder in place
m @ref/query yaw=-45 pitch=15                      # the gimbal query
m @ref/query yaw=90 tolerance=30 tags=hands        # narrow by tag
m @ref/untagged                                    # what still needs a pose
m @ref/set id=<id> yaw=-45 pitch=10 roll=5         # pin a pose
m @ref/similar id=<id>                             # refs posed like this one
m @ref/describe yaw=-135 pitch=20                  # → "rear 3/4 left, from above"
m @ref/serve                                       # console + API on :51040
m @ref/test                                        # offline tests
```

## The console — `http://localhost:51040/@ref/`

- **SEARCH**: drag the head to aim the gimbal (drag = yaw/pitch, slider =
  roll); the wall live-updates with everything within tolerance, nearest
  first, each card badged with its angular distance. Click a card to snap
  the gimbal to that image's pose.
- **UNTAGGED**: the queue of images without a pose. Click one, aim the
  gimbal at how the subject is oriented, hit PIN POSE. Tagging a scanned
  folder takes minutes, and you only ever do it once per image.
- **SCAN**: type a folder path, index it without moving anything.

## Layout

```
orientation.py   pure angle math — wrap, distance, artist-words naming (no I/O)
store.py         the index: one SQLite file in ./data
estimate.py      plug-in registry for optional local pose estimators
mod.py           the anchor — every fn the CLI/gateway sees
serve.py         static console + API on one port (pm2-runnable directly)
web/index.html   the gimbal console, vanilla JS, zero CDN deps
tests/           offline: math + store on a temp DB
```

Every layer is reusable on its own: `orientation.py` has no imports
beyond `math`, `store.py` works headless from any Python, and the
estimator registry accepts anything callable — a future body-pose model,
a CLIP head, your own heuristic — without touching the rest.
