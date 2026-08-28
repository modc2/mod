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
| Toggle skate/walk | `E`                         |
| Reset to arch     | `R`                         |
| Free camera       | `C`                         |
| Pixel size        | `[` chunkier · `]` finer    |
| Toggle help       | `H`                         |

**Walking** is direct and snappy. Press **E** to drop a skateboard — then
**skating** becomes momentum-based: hold **W** (and **Shift**) to push, steer
with the mouse to carve, and tap **Space** for an ollie. The speed bar and
compass live in the HUD.

## What's in the park

- The **Washington Arch** — Tuckahoe marble, procedural voussoirs and keystone, a barrel-vaulted
  soffit, dentil course, the carved frieze (*"let us raise a standard to which the wise and honest
  can repair"*), spandrel medallions and both Washingtons standing in their niches
- The round **fountain**: rippling water with a sky-reflecting sheen, a particle jet, drifting mist,
  and stone darkened where the spray lands
- Radiating **paths** with granite kerbs, and a center plaza paved in **radial rings of pavers**
- ~150 instanced **trees** — mottled plane-tree bark, five-layer crowns, each its own shade of green,
  all of it swaying in the wind
- **Benches**, glowing **lampposts**, **chess tables**, wire **bins**, **bike racks**, striped
  **hot-dog carts**
- A ring of **Greenwich Village walk-ups** with pressed-metal cornices, lit windows, storefront
  awnings and **wooden water tanks** on the roofs

### The vibe — the park is *alive*
~85 cel-shaded people (jointed, two-segment limbs — real knees, elbows, walk/skate gaits) doing what people do in WSP:

- **two drum circles** with dancers, the heartbeat of the park 🥁
- **skaters** carving the plaza (watch the back foot plant + push off)
- folks **chilling / sunbathing on the grass**, **joggers**, strollers
- people **holding it down by the gates** 🌿
- **buskers** near the fountain, **dogs** off-leash, scattering **pigeons**

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
- Instanced meshes for trees/fence posts; per-tree crown colour via `setColorAt`.
- Built for realism *within the tech budget of a browser*, then deliberately pixelated on the way out.
