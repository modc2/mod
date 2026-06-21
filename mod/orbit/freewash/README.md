# freewash 🛹

A free-roam **WebGL** game set in **Washington Square Park**. Stroll or skate
around the marble arch, the central fountain plaza, the tree-lined diagonal
paths, the chess tables and the Greenwich Village skyline — all rendered in the
browser with [three.js](https://threejs.org) / WebGL. No install, no build step.

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
| Toggle help       | `H`                         |

**Walking** is direct and snappy. Press **E** to drop a skateboard — then
**skating** becomes momentum-based: hold **W** (and **Shift**) to push, steer
with the mouse to carve, and tap **Space** for an ollie. The speed bar and
compass live in the HUD.

## What's in the park

- The **Washington Arch** (marble, ~23 m, built from procedural voussoirs) at the north plaza
- The round **fountain** with animated water and a particle jet
- Radiating **paths** (N–S / E–W spines + diagonals) and a circular center plaza
- ~150 instanced **trees**, **benches lining every sidewalk**, glowing **lampposts**, **chess tables**
- A ring of low-poly **Greenwich Village buildings** with lit windows, golden-hour light, long shadows, soft bloom

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

### Performance knobs (URL params)
- `?density=0.5` — scale the crowd down (or `2` for more chaos) on weaker / stronger machines
- `?lite=1` — disable shadows + bloom for low-end GPUs
- `?ws=ws://host:port` — point multiplayer at a specific relay

## Deploy / gateway

```python
m.mod('freewash')().deploy()        # serve on 0.0.0.0 + register the /freewash route
m.mod('freewash')().register()      # just (re)register with the gateway
```
Registered at **https://modc2.com/freewash** via the `server.namespace` registry (the game itself stays
dependency-free; registration is deploy plumbing).

## Tech notes

- Pure WebGL via three.js `0.160` (ESM import map, CDN). No bundler.
- Soft shadow mapping, ACES tone mapping, gradient sky shader, procedural canvas textures.
- Instanced meshes for trees/fence posts to keep it light; runs at 60fps on integrated GPUs.
- Built for realism *within the tech budget of a browser* — stylized-realistic, not photoreal.
