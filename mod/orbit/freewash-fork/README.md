# freewash-fork 🍅

A free-roam **WebGL** game set in **Kensington Market, Toronto**, rendered as
**pixel art**. Walk or skate Augusta Avenue, Baldwin Street and Kensington
Avenue — a hundred and fifty Victorian houses painted every colour there is,
fruit stacked to head height in the doorways, vintage on the rails out under the
awnings, murals on every blank wall, and the whole sky cut into pieces by the
hydro wires. All in the browser with [three.js](https://threejs.org) / WebGL. No
install, no build step.

This is a fork of [`orbit/freewash`](../freewash), which is the same engine set
in Washington Square Park. Everything underneath — the pixel pipeline, the body
kit, the day/night clock, the synthesised sound, the social layer — is that
game's. What changed is the **place**: the park was replaced with a street grid,
the arch and the fountain with a market, and the cast with the people who work
it.

The market underneath is built for realism — the real street plan, storefronts
at real 6-metre frontages, sidewalks with a 14 cm kerb, wires strung pole to
pole in real catenaries — and then the whole frame is squeezed through a
**low-resolution buffer, upscaled with nearest-neighbour, and quantised to a
small palette with a 4×4 ordered dither**. Real light, real detail, hard pixels.

The entire game is one self-contained file: [`web/index.html`](web/index.html).
The Python module just serves it over a tiny stdlib HTTP server and opens your
browser. It depends on **no other mod**.

## Play

```python
import mod as m
m.mod('freewash-fork')().play()   # serves on http://127.0.0.1:8809 and opens it
```

```bash
m freewash-fork play              # serve + open browser
m freewash-fork serve port=8809   # serve only (background)
m freewash-fork stop              # stop the server
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
| Wardrobe          | `K`                         |
| Toggle skate/walk | `E`                         |
| Reset to Augusta  | `R`                         |
| Free camera       | `C`                         |
| Time of day       | `T`                         |
| Mute sound        | `M`                         |
| Pixel size        | `[` chunkier · `]` finer    |
| Toggle help       | `H`                         |

**Walking** is direct and snappy. Press **E** to drop a skateboard — then
**skating** becomes momentum-based: hold **W** (and **Shift**) to push, steer
with the mouse to carve, and tap **Space** for an ollie.

## The market

You start in the middle of Augusta Avenue, just south of Baldwin, looking north
at the sign. Everything is laid off two tables of street centrelines, so the
sidewalks, the shopfronts, the poles, the trees, the parked cars and the crowd
all follow the plan and none of them can drift off it.

- **Augusta Avenue** — the spine, and the widest thing here. Painted storefronts
  both sides the whole length, breaking only for Bellevue Square
- **Baldwin Street** — where you eat. Tacos, patties, empanadas, a fish counter,
  four kinds of bakery
- **Kensington Avenue** — the vintage block, wall to wall, with the rails out on
  the pavement under the awnings
- **Nassau**, **Oxford** and **St Andrew** — quieter, more house than shop
- **Spadina Avenue** on the east edge: four lanes, a concrete track bed, rails,
  a trolley wire and a **510 streetcar** sat on it
- **College Street** closing the north end

### What's in it

- **Bellevue Square** — the one piece of grass: paths cut across it, a **wading
  pool** running all summer, a playground, the **off-leash corner**, benches,
  and a **drum circle** going in the corner
- **Al Waxman**, the King of Kensington, cast in bronze in the middle of the
  square with his hands on his hips, which is exactly how he stands in real life
- The **garden car** on Augusta — an abandoned sedan somebody filled with soil in
  1994 and has been planting every spring since. Parked, legally, at the kerb
- The **Kiever** on Denison Square — twin ribbed onion domes, yellow brick,
  round-arched windows, older than everything around it
- The **KENSINGTON MARKET** sign over Augusta, with the bulbs that come on at dusk
- **St Stephen-in-the-Fields**, the spire you see over the rooftops from anywhere
- **Murals** on every wall a laneway or an intersection exposes — flat blocks of
  colour, a face somewhere in it, and a tag over the top by the weekend
- The **hydro poles**: creosote, three crossarms, glass insulators, transformer
  cans, and a cat's cradle of service drops. Look up anywhere and the sky is in
  pieces. This is the roof of the market
- **Produce crates** three tiers high, **clothes rails**, market **canopies**,
  patio tables with umbrellas, sandwich boards, planters, post-and-ring stands
  with bikes locked to them, and more parked cars than the street can hold
- The **CN Tower** and downtown over the rooftops to the south-east, hazy

### Storefronts

Everything here is a Victorian house that stopped being a house. Two or three
storeys of painted brick with a bay window and a gable on top — the Toronto
bay-and-gable — and then somebody knocked the parlour out, put a window across
the whole front, hung an awning off it and painted the name on in whatever
colour was left. That is the entire building type, repeated a hundred and fifty
times, and the only reason the street looks the way it does is that no two of
them agreed on the colour.

Each one is generated to that recipe: painted facade, glass shopfront with
mullions and a lit interior you can see into, a recessed door with a step, a
hand-lettered **sign band** with the shop's name on it, a striped awning, a bay
window, a gable with sawn bargeboard, a chimney, sometimes a fire escape,
sometimes a shutter box for the neighbourhood to tag. The names are the
market's — Portuguese fish and bacalhau, Jamaican patties, Salvadoran pupusas,
Chilean empanadas, a Hungarian butcher, four cheese shops, a Vietnamese
grocery, a synagogue's worth of bagels, and eleven vintage places on one block.

### Time of day
Press **`T`**. One number — the hour — drives the sun's position and colour, the
sky gradient, the fog, the stars, whether the streetlights and the sign bulbs
are burning, and which shop interiors and upstairs windows are lit. Dawn,
morning, noon, golden hour, dusk, night. Or `?time=night`, `?time=19.5`, or
`?clock=1` to let it run. Streetlights are lit from a **pool of eight point
lights** handed to whichever heads are nearest you.

### Sound
All of it synthesised at runtime from one noise buffer and a few oscillators —
nothing to download. The wading pool gets louder as you cross the square, the
market murmur thickens on Augusta and Baldwin, Spadina and College keep up a
four-lane rumble from the edges, the djembe groove is scheduled against the audio
clock so it never stutters, footfalls fire off the same phase the legs are posed
from, and the board hums and clacks over the paving. **`M`** mutes.

### The people
~190 cel-shaded people built to human measurements, not cartoon ones: the hip
joint sits at 0.53 of stature and the head is a seventh of it, skulls have a jaw
and a nose and lids over the eyes, hair has a hairline, and clothes are a
wardrobe rather than a paint job.

- **Vendors** stood at the crates and the rails all day, which is what makes the
  street feel staffed rather than merely populated
- **Knots** of people stopped dead in the middle of the sidewalk, talking, which
  is the market's most common formation and its main traffic problem
- **Pairs** shopping together, the follower shadowing their friend
- **Buskers** on four corners with the ring of watchers they always draw
- Half of Bellevue Square **sat on the benches** or sprawled on the grass; the
  patios full; skaters and couriers cutting down Augusta the wrong way
- **Dogs** in the off-leash corner with owners on the rail, and **pigeons** that
  work the fruit crates and go up in a clatter when you run at them

### Talking to people 💬
Walk up to anybody and press **F**. Everyone has a name, a job and a taste in
conversation — generated the first time you speak to them, kept for the session.

The whole social game is **tone**. Every line you can say is stamped `FUNNY`,
`SMOOTH`, `REAL`, `CHILL` or `BOLD`; every person secretly loves one and can't
stand another. Land their tone and rapport jumps; hit the one they hate and it
drops. Get rapport high enough and **"Can I get your number?"** unlocks. Numbers
land in your **phone** (`P`) with the name, the job, and which street you met on.

### Sidequests 📋
Nine people stand under a **❗**:

| Quest | Who | What |
|---|---|---|
| **Wingman** | Marco, outside the coffee place | get somebody's number and prove it's possible |
| **One dog short** | Nadia, at the off-leash corner | find the loose dog, walk it home |
| **Pull a crowd** | Ozzie, busking at Augusta & Baldwin | talk three people into coming over |
| **Hold the circle** | Kwame, drumming in Bellevue Square | stand in the circle for 20 seconds |
| **Round the square** | Sasha, skating | a full lap of Bellevue Square, rolling the whole way |
| **Put them up** | Gus, on a bench | scatter six flocks of pigeons |
| **The whole market** | Greta, first day here | the sign → the garden car → the Kiever → the square |
| **Golden hour** | Anaïs, photographer | stand under the market sign when the light goes gold (`T`) |
| **Cold coffee** | Sol, at the coffee window | run a coffee over to Kensington Ave |

Finishing one pays **rep** — and rep is charisma, so every quest you clear makes
the next conversation land a little softer.

### Multiplayer
A best-effort WebSocket relay runs alongside the server (auto-discovered via
`mp.json`). Other real players show up as teal avatars. For LAN/direct play:

```python
m.mod('freewash-fork')().serve(host='0.0.0.0')
```

### Look + performance knobs (URL params)
- `?px=4` — pixel size (bigger = chunkier + faster; `0`/omitted auto-picks)
- `?levels=8` — colours per channel in the palette quantiser
- `?dither=0` — turn the ordered dither off
- `?raw=1` — skip the pixel pass and render smooth at full resolution
- `?density=0.5` — scale the crowd down (or `2` for a Pedestrian Sunday)
- `?lite=1` — disable shadows + bloom for low-end GPUs
- `?time=night` — start at a preset (`dawn`/`morning`/`noon`/`golden`/`dusk`/`night`) or an hour
- `?clock=1` — run the clock: a minute of play is an hour of daylight
- `?mute=1` — start silent

## Deploy / gateway

```python
m.mod('freewash-fork')().deploy()     # serve on 0.0.0.0 + register /freewash-fork
m.mod('freewash-fork')().register()   # just (re)register with the gateway
```

## Tech notes

- Pure WebGL via three.js `0.160` (ESM import map, CDN). No bundler.
- The pixel pipeline: `renderer.setSize(low, low, false)` + CSS
  `image-rendering: pixelated`, then bloom → ACES/sRGB output → a `ShaderPass`
  that grades, bins each channel to `levels`, and offsets with a 4×4 Bayer
  matrix. **Quantise after OutputPass.**
- **The plan is two tables.** `NS` and `EW` hold the street centrelines, roadway
  half-widths and sidewalk widths. `buildStreet()` reads them for the asphalt,
  the raised sidewalk slabs, the kerb faces, the lane paint and the crosswalks;
  `shopRow()` reads them to find each frontage line and to skip every
  intersection; the poles, the street trees, the parked cars, the bike rings and
  the crowd hotspots all read them too. Move a street and the market moves.
- **Buildings are collidable.** `wallColliders()` buries a run of circles just
  inside each frontage so the pushed-out boundary lands on the wall plane rather
  than bulging into the sidewalk — which is what makes these actual streets you
  walk down rather than a texture with props on it.
- Wires are one `LineSegments` of sagged catenaries — six conductors per span
  plus service drops — in a single draw call.
- Façade materials are shared (fourteen painted-brick variants); sign textures
  are not, because the name is the whole point of a sign.
- Instanced meshes for trees (trunks, limbs and foliage in three draws), per-tree
  crown and bark colour via `setColorAt`.
- Performance: the static market is frozen with `matrixAutoUpdate = false` after
  it is built, colliders are bucketed into a 10 m grid, the crowd drops to a
  two-mesh face and half the hair at range, ink outlines switch off past 60 m,
  and limbs are re-posed on one frame in three past 45 m.
- Built for realism *within the tech budget of a browser*, then deliberately
  pixelated on the way out.

## Tests

```bash
m freewash-fork serve port=8809     # the suites drive a live server
python3 tests/test_social.py        # talk, flirt, get a number, phone, persistence
python3 tests/test_quests.py        # invite, the square lap, the lost dog, rejection
```

Both need Playwright + Chromium and run under swiftshader at about 1 fps — trust
the assertions, never the frame rate.
