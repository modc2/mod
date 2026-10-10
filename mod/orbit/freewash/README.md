# freewash 🛹

A free-roam **WebGL** game set in **Washington Square Park**, rendered as
**pixel art**. Stroll or skate around the marble arch, the central fountain
plaza, the tree-lined diagonal paths, the chess tables and the Greenwich Village
skyline — all in the browser with [three.js](https://threejs.org) / WebGL. No
install, no build step.

The park underneath is built for realism — golden-hour sun with a sky-and-bounce
fill, flagstone laid at true 1 m scale, London planes swaying on the breeze, the
arch with its dentils, archivolt and both Washingtons — and then the whole frame
is squeezed through a **low-resolution buffer, upscaled with nearest-neighbour,
and quantised to a small palette with a 4×4 ordered dither**. Real light, real
detail, hard pixels.

The entire game is one self-contained file: [`web/index.html`](web/index.html).
The Python module just serves it over a tiny stdlib HTTP server and opens your
browser. It depends on **no other mod**.

## Play

```python
import mod as m
m.mod('freewash')().play()        # serves on http://127.0.0.1:8799 and opens it
```

```bash
m freewash play                   # serve + open browser
m freewash serve port=8799        # serve only (background)
m freewash stop                   # stop the server
```

Or just open `web/index.html` directly in any modern browser — it loads three.js
from a CDN via an ES module import map, so it runs as a plain static page too.

## Controls

| Action            | Keys                        |
|-------------------|-----------------------------|
| Move              | `W` `A` `S` `D` / arrows    |
| Look              | Mouse (click to capture)    |
| Jump / Ollie      | `Space`                     |
| Sprint / Push     | `Shift`                     |
| **Talk to anyone**| `F`                         |
| Pick what you say | `1` `2` `3` `4` · `Q` leave |
| Contacts (phone)  | `P`                         |
| Sidequest log     | `J`                         |
| Toggle skate/walk | `E`                         |
| Reset to arch     | `R`                         |
| Free camera       | `C`                         |
| Time of day       | `T`                         |
| Mute sound        | `M`                         |
| Pixel size        | `[` chunkier · `]` finer    |
| Toggle help       | `H`                         |

**Walking** is direct and snappy. Press **E** to drop a skateboard — then
**skating** becomes momentum-based: hold **W** (and **Shift**) to push, steer
with the mouse to carve, and tap **Space** for an ollie. The speed bar and
compass live in the HUD.

## What's in the park

The park is laid out to plan, not scattered. Every walk is a polyline ribboned into paving with
arclength UVs, and the same segment list then decides where the trees, the benches and the lamps go —
so the planting can never drift away from the plan.

- The **Washington Arch**, hard against the north edge — Tuckahoe marble, procedural voussoirs and
  keystone, a barrel-vaulted soffit, dentil course, the carved frieze (*"let us raise a standard to
  which the wise and honest can repair"*), spandrel medallions and both Washingtons in their niches
- The **Fifth Avenue promenade** running dead straight from the arch to the fountain — the only
  straight walk in the park, and the widest. Everything else bows around a lawn.
- The round **fountain**: rippling water with a sky-reflecting sheen, a particle jet, drifting mist,
  stone darkened where the spray lands, and a rim with people sat all the way round it
- A **perimeter loop**, four bowed corner diagonals, and a plaza paved in **radial rings of pavers**
- **Allées of London planes** down every walk — limbed trunks you walk *under*, vase-profile crowns,
  each its own shade of green, all of it swaying in the wind — plus the **Hangman's Elm** in the
  north-west corner
- **Garibaldi** drawing his sword east of the fountain, the **Holley bust** west
- The **chess corner** in the south-west, boards inlaid in the stone, players sat facing each other
- The **dog run** on the west side (chain link, gravel, owners on the rail) and the concrete
  **mounds** and swings of the south-east playground
- **The Row** along Washington Square North — Greek Revival brick, marble stoops, iron rails and one
  cornice line the length of the block — **Judson Memorial Church** and its campanile to the south,
  and the red sandstone cube of **Bobst Library** on the south-east corner
- **Benches**, cast-iron **lampposts**, wire **bins**, **bike racks**, striped **hot-dog carts**, and
  a ring of **Greenwich Village walk-ups** with water tanks on the roofs

### Time of day
Press **`T`**. One number — the hour — drives the sun's position and colour, the sky gradient, the
fog, the stars, whether the lamps are burning and which windows are lit. Dawn, morning, noon, golden
hour, dusk, night. Or `?time=night`, `?time=19.5`, or `?clock=1` to let it run (a minute of play is
an hour of daylight). Lamps are lit from a **pool of eight point lights** handed to whichever posts
are nearest you, because a hundred real ones would cost more than the rest of the frame.

### Sound
All of it synthesised at runtime from one noise buffer and a few oscillators — nothing to download.
The fountain gets louder as you approach it, the djembe groove is scheduled against the audio clock
so it never stutters, footfalls fire off the same phase the legs are posed from (and sound different
on stone and on grass), the board hums and clacks over the paver seams, and the city hums outside the
fence. **`M`** mutes.

### The people
The crowd is built to human measurements, not cartoon ones: the hip joint sits at 0.53 of stature and
the head is a seventh of it, so a body reads as an adult rather than a bobblehead. **The shoulder is
a joint, not a shelf** — it hangs at 0.78 of stature, six centimetres below the top of the shoulder,
and the trapezius *slopes* from beside the neck down to the deltoid over it. Hung the other way (the
arms off the acromion, under a flat plank of yoke) every body wears shoulder pads, loses its neck and
loses five centimetres of arm, which is the whole difference between a person and an action figure.
Above the collar there are seven centimetres of throat; the ribcage is a sixth wider than the waist;
the pelvis stops at the crotch with the glutes behind it. The skull is a
squashed ellipsoid — taller than it is deep, much narrower than either — with a jaw, cheekbones, a
brow ridge, a nose with a bridge and ears; up close the eyes are balls set in sockets with lids over
them, brows in hair colour and two-part lips. Hair has a **hairline**: the crown is tipped back so it
crosses high on the forehead, just above the ears, and down the nape.

Clothes are a wardrobe, not a paint job — tee / long-sleeve / tank, shorts with a hem partway down
the thigh, skirts, collars, belts, jackets, hats. And people do something with their hands: a third
of the park stands with them in their pockets, arms folded, or a phone up (they put it away when you
talk to them), and everybody strides a little differently.

Nobody stands to attention, including you. Standing still is a weight shift from hip to hip, a
breath, a soft knee, hands resting against the thighs and feet turned out — and each body is crooked
its own way, with its own few degrees of stoop. Stop walking and the player settles into that over
about a fifth of a second, instead of freezing mid-stride the way it used to.

### The vibe — the park is *alive*
~170 cel-shaded people (jointed, two-segment limbs — real knees, elbows, walk/skate gaits), varying
in height and build, in jackets and backpacks and skirts, doing what people do in WSP:

- half the park **sat on the fountain rim** and on the benches, which is the pose everyone remembers
- **conversation knots** standing in circles, one person holding the floor at a time while the rest
  nod; **pairs** walking together, the follower shadowing their friend rather than pathing alone
- heads that **turn to look** at you, at the drums, at the fountain
- bodies that **push each other apart** instead of walking through one another
- **two drum circles** with dancers 🥁, **buskers** with the ring of watchers they always draw
- **skaters** carving the plaza (watch the back foot plant + push off), **joggers**, strollers
- folks **chilling on the grass**, people **holding it down by the gates** 🌿
- **dogs** in the run with owners on the rail, and **flocks of pigeons** that peck and shuffle and go
  up in a clatter when you run through them

### Talking to people 💬
Walk up to anybody and press **F**. Everyone in the park has a name, a job and a taste in
conversation — generated the first time you speak to them, and kept for the session.

The whole social game is **tone**. Every line you can say is stamped `FUNNY`, `SMOOTH`, `REAL`,
`CHILL` or `BOLD`; every person secretly loves one of those and can't stand another. Land their
tone and rapport jumps (the hearts in the dialogue header fill); hit the one they hate and it
drops. Tank it and they check their phone and leave.

Get rapport high enough and a new line unlocks: **"Can I get your number?"** — a roll weighted by
how well the conversation actually went. Numbers land in your **phone** (`P`) with the name, what
they do, where in the park you met and what tone they liked. They survive a reload.

The camera swings into a two-shot for the length of the conversation, the NPC stops what they were
doing and turns to face you — unless they're sat on a bench, because nobody gets up off a bench for
a stranger.

### Sidequests 📋
Nine people in the park stand under a **❗**. Talk to them and they'll ask you for something:

| Quest | Who | What |
|---|---|---|
| **Wingman** | Rico, by the fountain | get somebody's number and prove it's possible |
| **One dog short** | Nadia, at the dog run | find the loose dog, walk it home |
| **Pull a crowd** | Otis, busking | talk three people into coming over to watch |
| **Hold the circle** | Kwame, drumming | stand in the east drum circle for 20 seconds |
| **Fountain lap** | Sasha, skating | a full lap of the plaza on the board, rolling the whole way |
| **Put them up** | Gus, on a bench | scatter six flocks of pigeons |
| **The whole square** | Greta, first day here | arch → Garibaldi → chess corner → dog run |
| **Golden hour** | Anaïs, photographer | stand under the arch when the light goes gold (`T`) |
| **Cold coffee** | Sol, at the cart | run a coffee to the chess tables |

A gold beacon marks whatever the top quest wants next, the tracker sits bottom-right, and the log
is on `J`. Finishing one pays **rep** — and rep is charisma, so every quest you clear makes the
next conversation land a little softer. Quests feed flirting, flirting feeds quests.

### Multiplayer
A best-effort WebSocket relay runs alongside the server (auto-discovered via `mp.json`). Other real
players show up as teal avatars. If it can't connect (e.g. behind a gateway with no WS proxy), you still
get the full NPC crowd. For LAN/direct play just serve on a reachable host:

```python
m.mod('freewash')().serve(host='0.0.0.0')   # others on your network can join
```

### Look + performance knobs (URL params)
- `?px=4` — pixel size (bigger = chunkier + faster; `0`/omitted auto-picks from window width)
- `?levels=8` — colours per channel in the palette quantiser (lower = more retro banding)
- `?dither=0` — turn the ordered dither off
- `?raw=1` — skip the pixel pass entirely and render smooth at full resolution
- `?density=0.5` — scale the crowd down (or `2` for more chaos) on weaker / stronger machines
- `?lite=1` — disable shadows + bloom for low-end GPUs
- `?time=night` — start at a preset (`dawn`/`morning`/`noon`/`golden`/`dusk`/`night`) or an hour like `19.5`
- `?clock=1` — run the clock: one minute of play is an hour of daylight
- `?mute=1` — start silent
- `?ws=ws://host:port` — point multiplayer at a specific relay

Rendering at ~426×240 instead of full resolution is roughly a 9× cut in fragment work, and that
headroom is what pays for the extra scene detail.

## Deploy / gateway

```python
m.mod('freewash')().deploy()        # serve on 0.0.0.0 + register the /freewash route
m.mod('freewash')().register()      # just (re)register with the gateway
```
Registered at **https://modc2.com/freewash** via the `server.namespace` registry (the game itself stays
dependency-free; registration is deploy plumbing).

## Tech notes

- Pure WebGL via three.js `0.160` (ESM import map, CDN). No bundler.
- The pixel pipeline: `renderer.setSize(low, low, false)` + CSS `image-rendering: pixelated`, then
  bloom → ACES/sRGB output → a `ShaderPass` that grades, bins each channel to `levels`, and offsets
  with a 4×4 Bayer matrix built by recursing the 2×2 one.
- Textures are authored as pixel art at ~32 texels/metre, `NearestFilter` for magnification and
  mipmapped for minification, so they crunch up close without boiling in the distance.
- Shadow mapping, procedural canvas textures, a sky shader with sun disc + fbm clouds, wind sway
  injected into the foliage shader via `onBeforeCompile`.
- Instanced meshes for trees (trunks, limbs and foliage in three draws) and fence posts; per-tree
  crown and bark colour via `setColorAt`.
- Walks are quad-strip ribbons over polylines with arclength UVs, so flagstones hold their true 1 m
  scale round a bend. `onPath()` answers "is this paved?" against the same segment list.
- Bodies are one shared kit: every part is built once, merged down to a single mesh per material and
  reused by all ~180 people, so a body with a jaw, cheekbones, a nose, ears, lids, lips, a collar, a
  belt and sleeves still costs about what a stack of cylinders did.
- Performance is what buys the crowd and the clock: the static park is frozen with
  `matrixAutoUpdate = false` after it is built, colliders are bucketed into a 10 m grid instead of
  scanned linearly, the crowd drops to a two-mesh face and half the hair at range, ink outlines
  switch off past 60 m and limbs are only re-posed on one frame in three past 45 m.
- Built for realism *within the tech budget of a browser*, then deliberately pixelated on the way out.
