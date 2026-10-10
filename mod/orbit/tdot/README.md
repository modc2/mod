# tdot — an open-source browser GIS for Toronto

A slippy-map GIS of Toronto in the browser: **police-reported crime by
neighbourhood**, the TTC subway and streetcar networks, the cycling network,
green space, apartment-building scores, serious traffic collisions and
administrative boundaries — as toggleable layers with a choropleth engine,
legends and a feature inspector.

Everything is built on public, key-free open data. No API keys, no accounts, no
proprietary tiles, no vendor SDK. Every layer names the dataset it came from and
links to it.

```
API   http://localhost:50320       (FastAPI, GeoJSON + gzip)
App   http://localhost:50321/tdot  (Next.js + MapLibre GL)
```

---

## Layers

| Layer | Kind | Source |
| --- | --- | --- |
| **Major crime** | choropleth | TPS Major Crime Indicators (`major-crime-indicators`) |
| **Individual incidents** | points, coloured by crime type | same |
| **Subway lines** | lines in official TTC colours | TTC GTFS static feed |
| **Subway stations** | points, with the lines that serve them | same |
| **Streetcar network** | lines | same |
| **Cycling network** | lines, weighted by protection | Cycling Network (`cycling-network`) |
| **Parks & green space** | polygons | Green Spaces (`green-spaces`) |
| **Apartment building scores** | graduated circles | Apartment Building Evaluation (`apartment-building-evaluation`) |
| **Predicted vs actual score** | diverging circles | the score model (see below) |
| **What homes cost** | choropleth, 16 metrics | Neighbourhood Profiles, 2021 Census |
| **Lots for affordable housing** | full lot polygons, highlighted and tiered | Real Estate Asset Inventory (land) |
| **Registered rental buildings** | circles sized by units | Apartment Building Registration |
| **Toronto Community Housing** | circles sized by units | `toronto-community-housing-data` |
| **Subsidized housing** | points by provider type | `subsidized-housing-listings` |
| **Highrise health hazards** | points by case status | `residential-health-hazards` |
| **Fire-code violations** | points by enforcement | `residential-fire-inspection-results` |
| **Rooming houses / rental demolitions** | per-ward choropleths | `multi-tenant-house-licences`, `demolition-and-replacement-of-rental-housing-units` |
| **Retirement homes** | circles sized by capacity | `retirement-homes` |
| **Development pipeline / applications / permits** | points | Planning + Building Permits |
| **Serious traffic collisions** | heatmap + points | Motor Vehicle Collisions (KSI) |
| **Former municipalities / neighbourhoods / wards** | outlines | `former-municipality-boundaries`, `neighbourhoods`, `city-wards` |

Basemaps are OpenFreeMap's key-free hosted vector styles (`dark`, `positron`,
`liberty`); geocoding is OpenStreetMap Nominatim; the renderer is MapLibre GL
(BSD-3). A **3D** toggle in the header tilts the camera, extrudes real
building heights from the basemap's vector tiles, and raises each candidate
housing lot to the massing it could carry (right-drag rotates).

### Lots for affordable housing

The `housing_lots` layer screens the city's own Real Estate Asset Inventory —
every parcel the city holds, with full lot polygons — down to the land
affordable housing actually gets built on: city-owned vacant land, surface
parking (the Housing Now model) and anything declared surplus, at least
450 m², never parks or linear scraps. Each lot is scored 0–100 (35 size,
30 rapid-transit distance, 20 current use, 15 surplus status) and tiered
PRIME / STRONG / POSSIBLE; the full lot boundary is drawn with a glow so the
land under discussion is unmistakable, and each lot carries a rough massing
estimate (45% coverage, 90 m² per home) that the 3-D view extrudes. The
`tdot_housing_lots` agent tool answers "where could housing go?" and turns
the layer on.

`m tdot/housing` lists every open housing dataset the city publishes and what
this module does with each — drawn as a layer, fed to the score model, open but
with no geography to draw, or not open at all.

## Predicting a building's inspection score

RentSafeTO scores an apartment building out of 100 on the state of its common
areas. About 3,500 buildings carry one. :mod:`tdotgis.score` asks whether the
*rest* of the open record can predict it — the building's own registration
filing, health-hazard and fire-code violations at its address, and its
neighbourhood's census and crime profile.

It can, partly. Out-of-fold across 5 folds:

| | typical miss | within 10 pts | variance explained |
| --- | --- | --- | --- |
| model | **5.4 pts** | 85.8% | **36%** |
| guessing the city mean | 7.1 pts | 72.7% | 0% |

The strongest inputs are the ward, **who manages the building**, the year it was
evaluated, its age, and the fire-code violations recorded at its address. The
evaluation's own per-area sub-scores are excluded by design — they sum to the
target, so a model fed them would score ~1.0 and know nothing.

The useful output is not the prediction but the **residual**. A building
predicted at 82 and evaluated at 41 is doing 41 points worse than buildings with
the same age, size, systems and neighbourhood — a shortlist worth an
inspector's morning, and what the `predicted_scores` layer colours by. Anything
said about a specific evaluated building goes through the one fold that never
saw it, so the map, the outlier list and the per-building explanation all quote
the same number.

It explains about a third of the variance. That is useful for ranking and no
substitute for an inspection, and `GET /score/model` says so in the response.

## The crime choropleth

Colour any of four metrics, over two geographies, six crime types and four
time windows:

| Control | Options |
| --- | --- |
| **Metric** | incidents · incidents per km² · incidents per month · change vs. prior period |
| **Geography** | neighbourhood (158, the 2021 model) · neighbourhood (the historical 140) |
| **Crime type** | all major crime · assault · auto theft · break & enter · robbery · theft over $5,000 |
| **Window** | 2026 · 2025– · 2022– · everything from 2014 |

The underlying record is **every major-crime occurrence** the Toronto Police
Service has published since 2014 — roughly 490,000 incidents, each carrying an
occurrence date, offence, premises type, neighbourhood and an offset lat/lng.

Click any area for its stats, its **crime mix**, and a **yearly incident
history**; click any point feature for its own detail card.

### How the engine differs from a Socrata one

Toronto's CKAN portal blocks `datastore_search_sql`, so there is **no
server-side GROUP BY**. Aggregating in the client's request path would be
hopeless, so the module builds a warehouse instead:

1. The full table is pulled **once** through the column-filtered CSV dump
   (~25 MB) and reduced to a **month cube** — counts keyed by (neighbourhood,
   category, month) — plus a slice of the last ~30 months kept at incident
   grain for the point layer.
2. The cube is ~2 MB and answers every choropleth, trend and summary query
   locally in milliseconds. Only the weekly refresh touches the network.

### What the numbers exclude, and why

1. **Incidents with no neighbourhood assigned ("NSA") are excluded from area
   aggregates.** They have nowhere to land on a choropleth. They still count in
   the city-wide record.

2. **Percent change needs 10+ incidents in the prior window.** Below that, a
   change is noise: two incidents becoming twenty is a 900% "surge" that means
   nothing. Those areas report `null` and are drawn in a neutral grey, never in
   the ramp's lowest class — "no comparison" and "least crime" must not look the
   same.

3. **The diverging scale is clipped to the middle 90% and kept symmetric about
   zero** — otherwise one thin-volume area swinging −80% stretches the domain
   until every other area reads neutral.

4. **Locations are the police service's own offset geocodes**, moved to the
   nearest road intersection for privacy. A point marks the block, not the
   address.

Areas with **zero** recorded incidents keep their geometry and report `0`. On a
crime map zero is a real — and good — value, not missing data.

## CLI

```sh
m tdot                                  # null call → info()
m tdot/layers                           # the layer catalogue
m tdot/layer ttc_lines                  # one layer as GeoJSON
m tdot/crime metric=per_km2             # a crime choropleth
m tdot/crime category=auto_theft since=2024-01-01
m tdot/summary                          # city-wide totals + top/bottom areas
m tdot/trend area=087                   # one neighbourhood's yearly history
m tdot/incidents since=2026-01-01       # individual incidents as points
m tdot/where "Christie Pits"            # geocode a place
m tdot/housing                          # every open housing dataset + its role
m tdot/housing role=closed              # the ones that are not open data
m tdot/score                            # model accuracy, drivers, caveats
m tdot/outliers limit=10                # buildings scoring worst vs their peers
m tdot/building 4153587                 # one building, and what drives it
m tdot/districts                        # the six former municipalities
m tdot/warm                             # pre-fetch every layer into the cache
m tdot/cache                            # cache size/location
m tdot/serve                            # run API + map app under pm2
m tdot/kill                             # stop both
```

## HTTP API

| Route | Returns |
| --- | --- |
| `GET /layers` | the layer catalogue (drives the UI) |
| `GET /layers/{id}` | any layer as GeoJSON |
| `GET /layers/crime?metric=&geography=&since=&until=&category=` | choropleth + quantile breaks |
| `GET /layers/incidents?since=&category=&limit=` | individual incidents as points |
| `GET /boundary/{neighbourhood\|neighbourhood140\|ward\|municipality}` | boundary geometry |
| `GET /summary` | city-wide summary |
| `GET /trend?area=` | yearly history |
| `GET /where?q=` | geocode |
| `GET /housing?role=` | every open housing dataset and what tdot does with it |
| `GET /housing/search?q=` | housing datasets the catalogue does not cover yet |
| `GET /score/model` | out-of-fold accuracy, drivers, sources, caveats |
| `GET /score/outliers?direction=under\|over&limit=` | buildings furthest from their peers |
| `GET /score/building/{rsn}` | one building: prediction and what moves it |
| `GET /options`, `/view`, `/health`, `/cache` | UI metadata |
| `POST /mcp` | MCP over streamable HTTP (JSON-RPC 2.0) |
| `GET /mcp`, `/mcp/schema`, `/mcp/config` | what the MCP server is, its tools, a client config |

Responses are gzipped — the 3,250-polygon green-space layer goes out at a
fraction of its raw size.

## MCP

The 18 tools the **Ask** panel plays are also published over the Model Context
Protocol, so any MCP client can drive the map. Both transports dispatch through
the same `mcp_server.rpc`, so they cannot drift apart:

```sh
claude mcp add --transport http tdot http://localhost:50320/mcp   # streamable HTTP
claude mcp add tdot -- python3 -m tdotgis.mcp_server              # stdio
m tdot/mcp                                                        # print both configs
```

The tools that move the map — `tdot_show_layers`, `tdot_hide_layers`,
`tdot_fly_to`, `tdot_set_crime_view` — return their action under a `__map__` key
in the result, which the browser applies to the live map. So an MCP client is
not just querying the data; it is steering the console someone is looking at.

The **MCP** panel in the header is the server's front door: status, connection
snippets for both transports, every tool with its JSON Schema, and a runner that
calls those tools over the real `/mcp` endpoint — so what you try there is
exactly what an outside client gets.

## Caching

Open-data responses are cached under `~/.mod/tdot/cache`, and **a stale entry
beats an error**: if a portal is slow or down, the last good copy is served.
`m tdot/warm` pre-fetches everything (~18 MB).

Geometry from the city's portal is authoritative-precision. A pure-Python
Ramer–Douglas–Peucker pass plus coordinate rounding cuts it 10–30× with no
visible difference at city zoom.

## Colour

The choropleth ramps were checked with a validator, not chosen by eye:

- **sequential** — one blue hue, monotonic in lightness, running *away* from the
  basemap so magnitude increases away from the surface: dark→light on the dark
  basemap, light→dark on the light one;
- **diverging** (change vs. prior period) — blue ↔ red with a neutral grey
  midpoint that recedes; the *red* arm is the positive one, because on a crime
  map "up" is the bad direction;
- **overlays** — layers that share a mark form never share a hue; the aqua and
  orange pair clears all-pairs colour-blind separation (ΔE 9.4 deutan) and
  normal-vision separation (ΔE 26.5) against the map surface;
- **subway lines** — the one layer that inherits its palette, because riders
  read the network by the TTC's own line colours.

## Themes

The picker in the top-right corner switches the whole console: GLASS (default),
DAYLIGHT, PAPER, TTC, MATRIX, NEON, EMBER, ABYSS, WIN95 and HI-CON. The choice
is saved in `localStorage` under `tdot_theme` and re-applied by a blocking
script in `app/src/app/layout.tsx`, so the first paint is already the right
palette — there is no flash of the wrong theme.

A theme is four things, all declared in `app/src/lib/theme.ts`:

- a **token block** in `globals.css` (`[data-theme="id"]`) that every piece of
  chrome draws from — the components hold no literal colours;
- a **base**, dark or light, which lands on the document as `data-base`. It
  picks the *map* palette, because a ramp is only valid on one surface: a light
  theme gets the light sequential, diverging and heat ramps and darker overlay
  hues, or the map would read inside-out (see **Colour** above);
- a **basemap**, applied on every theme change so picking a light theme moves
  the tiles under it too. The Dark/Light/Streets buttons still override by hand
  until the next theme change;
- a pair of **magnitude ramps** in `THEME_RAMPS` (`app/src/lib/palette.ts`), so
  the theme reaches the map and not just the panels around it. MATRIX paints the
  city green, EMBER amber, TTC red.

What a theme may and may not recolour is a deliberate split. The **sequential**
ramps are themed, because "how much" carries no meaning in its hue — only in its
position along the ramp. **Diverging** (up-is-bad stays red), **status** and
**category** colours, and the TTC line palette, are not: those encode meaning or
identity, and a skin that changed them would change what the map *says*.

Each themed ramp was generated in OKLCH against the lightness and chroma
schedule of the two validated base ramps, with chroma binary-searched to the
sRGB gamut boundary, then gated with the dataviz `validateOrdinal` check. All
twenty pass single-hue, monotone lightness and the ≥0.06 adjacent-step gap.

Adding one means an entry in `THEMES`, a matching token block, a `THEME_RAMPS`
pair, and the id in the blocking script's list in `layout.tsx`.

## Tests

```sh
python3 -m pytest tests -m "not network"   # 44 offline: geometry, cube, breaks, model wiring
python3 -m pytest tests                    # + 29 live open-data checks
```

The network tests are deliberately unmocked — they are what catches an upstream
schema change (a renamed column, a dataset that moved resources, the day TPS
relabels a CSI category and silently empties the map).

## Layout

```
tdot/
├── config.json          # module manifest (ports, fns, sources)
├── mod.py               # the Mod class (anchor)
├── tdotgis/
│   ├── sources.py       # fetch + disk cache + RDP simplification + GTFS
│   ├── crime.py         # the choropleth engine (month cube + points)
│   └── layers.py        # the layer catalogue and loaders
├── api/api.py           # FastAPI, :50320
├── app/                 # Next.js + MapLibre GL, :50321, basePath /tdot
│   ├── src/lib/theme.ts     # the theme registry (base, basemap, swatch)
│   ├── src/lib/palette.ts   # the map ramps, one table per surface
│   └── src/app/globals.css  # one token block per theme
└── tests/test_tdot.py
```

Adding a layer means adding a loader and a catalogue entry in `tdotgis/layers.py`
— the UI builds its panel and legend from the catalogue, so no frontend change
is needed for a layer that fits an existing mark form.

## Attribution

Data © the City of Toronto, the Toronto Police Service, the Toronto Transit
Commission and OpenStreetMap contributors, used under their respective
open-data terms. This module is a viewer; it is not affiliated with or endorsed
by any of them.
