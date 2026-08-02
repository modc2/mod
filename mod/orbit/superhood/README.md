# superhood — SUPER HOOD BROS.: WASHINGTON PARK

An 8-bit side-scrolling platformer set in Brooklyn. Three courses: Fifth Avenue
in Park Slope, the G train tunnel, and Washington Park itself — where the
landlord is waiting with a rent increase.

It runs entirely in the browser. No build step, no framework, no assets on
disk: every sprite is drawn from a pixel grid at load time and every sound is
synthesised in WebAudio, so the module's only job is to serve `web/`.

```
App   http://localhost:50341/superhood      (gateway route: /superhood)
```

---

## Usage

```bash
m superhood                 # null call → info()
m superhood/play            # serve it here and open a browser
m superhood/serve           # run under pm2, then register the gateway route
m superhood/url             # where it is
m superhood/levels          # the course list
m superhood/files           # what makes up the game
m superhood/test            # files + JS syntax + every level builds
m superhood/health          # liveness
m superhood/kill            # stop it
m superhood/deregister      # drop the route
```

From Python:

```python
import mod as m
sm = m.mod('superhood')()
sm.play()          # serves on 50341 and opens a browser
```

## Controls

| | |
| --- | --- |
| `←` `→` / `A` `D` | walk |
| hold `X` / `J` / `Shift` | run — and throw a bottle cap when you're armed |
| `Z` / `K` / `Space` | jump (hold to jump higher) |
| `↓` / `S` | duck, and enter a subway staircase you're standing on |
| `Enter` start · `P` pause · `M` mute | |

On a phone the on-screen pad appears automatically. On a desktop the key hints
under the screen are live buttons — click one and it presses that key.

The controls are meant to be forgiving rather than literal, so what you asked
for is what you get:

- a press lands on contact, not on click or on `touchend`;
- a tap shorter than one frame still counts — the key is held until a frame
  has read it, so nothing vanishes between updates;
- a jump asked for up to 7 frames before landing fires on landing, and one
  asked for up to 6 frames after walking off an edge still jumps;
- a finger sliding from one button to the next hands the key over, two fingers
  hold two keys, and a pointer lost to a swipe or a phone call releases
  instead of sticking down.

## The block

Sal starts small. What he picks up decides how the rest of the level goes.

| pickup | what it does |
| --- | --- |
| slice of pizza | Sal gets big — one free hit, and he can smash brownstone brick |
| egg cream | arms him: hold run to throw bottle caps that bounce off the sidewalk |
| MetroCard | ten seconds untouchable, and the music knows it |
| egg sandwich | 1-UP, hidden in bricks that look like every other brick |
| subway token | 100 of them is another life |

Who's out here:

- **Pigeons** — they walk, they do not care about you, they flatten when stomped.
- **Rats** — faster. Stomp one and it curls into a trash-can lid; kick the lid
  and it clears the whole lane, including whatever you didn't mean to clear.
- **Gulls** — they ride a sine wave over the park and ignore the ground.
- **The Landlord** — 1-3. Hops after you, lobs eviction notices, takes five
  hits. Stomps count, bottle caps count, a MetroCard counts double. Beating him
  opens the gate to the Old Stone House.

Courses:

- **1-1 PARK SLOPE** — stoops, scaffolding, bodega crates, two open manholes.
  One subway entrance is a real staircase: stand on it, press down, and you're
  in the **token cellar** — 48 tokens and a hidden 1-UP, with a pipe at the far
  end that puts you back out halfway up the avenue.
- **1-2 G TRAIN** — tight ceiling, moving scaffold planks over the trackbed,
  white tile on the walls, a rat between every pillar.
- **1-3 WASHINGTON PARK** — green, gulls, the Williamsburgh Bank tower on the
  horizon, and the boss yard at the end.

Each course ends at a lamppost: touch it, ride the pennant down, walk into the
brownstone. Time left is worth 50 points a second.

## Layout

```
superhood/
├── config.json       manifest — port 50341, gateway route /superhood
├── mod.py            the anchor; class Mod is the module surface
├── serve.py          stdlib static server; strips the /superhood prefix
├── web/
│   ├── index.html    page shell, CSS, touch pad
│   └── js/
│       ├── sprites.js  every sprite as a pixel grid + palettes, baked to canvas
│       ├── audio.js    chiptune engine: 2 pulse voices, triangle bass, noise
│       ├── levels.js   a small level-builder DSL and the four maps
│       └── game.js     physics, collision, entities, renderer, HUD, bitmap font
└── tests/smoke.py    headless Chromium playthrough
```

Nothing here is lifted from any commercial game: the art, the level design and
all six pieces of music are original to this module.

## Config

| Field | Value | Meaning |
| --- | --- | --- |
| `anchor` | `mod.py` | file the loader imports |
| `app_port` / `gateway_port` | `50341` | the port the game is served on |
| `base_path` | `/superhood` | path prefix the gateway mounts it under |
| `route` | `true` | orbit/caddy generates a public route from this |
| `fns` | see file | the public surface, for discovery |

There is no API process — the game is entirely client side. `serve_api()`
stays on the surface and says so rather than throwing. Private state (keys,
ACLs, secrets) belongs in `~/.mod/superhood/`, never in `config.json`.

## Testing

```bash
m superhood/test              # files + JS syntax + every level builds (node)
python3 tests/smoke.py         # headless playthrough
python3 tests/smoke.py --shots # ...and write PNGs to tests/shots/
```

`tests/smoke.py` boots the game in Chromium, plays it with synthetic key
presses, and asserts the things that break silently: every level builds, Sal
moves and stomps, crates pay out, bottle caps fly, the subway warp round-trips,
the boss spawns and dies, the gate opens, touching the pole advances the world,
the controls hold onto sub-frame taps and buffered jumps, a click on a key hint
walks him, and 1800 frames of 1-2 run without a console error.

Debug hooks live on `window.__sm` — `__sm.state()`, `__sm.goto('1-3')`,
`__sm.press('right')`, `__sm.clear()` to drop every held key, and `__sm.game`
for everything else.

## Adding to it

A new course is a function in `web/js/levels.js` using the builder
(`ground`, `crate`, `secret`, `pipe`, `stair`, `coins`, `foe`, `plat`, `goal`)
plus an entry in `MAKERS`/`ORDER`. A new sprite is a pixel grid in
`web/js/sprites.js`; a new sound is a note list in `web/js/audio.js`. A new
module function is a public method on `Mod` in `mod.py`, listed in `fns` in
`config.json`.
