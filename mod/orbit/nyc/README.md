# nyc — an open-source browser GIS for New York City, and an MCP server

A slippy-map GIS of New York in the browser: **housing prices**, **transit**,
parks, flood zones, traffic injuries and administrative boundaries, as
toggleable layers with a choropleth engine, legends and a feature inspector.

The same data engine is also an **MCP server**, so an AI assistant can ask New
York a question directly — 17 read-only tools over housing prices, the transit
and bike networks, live traffic, and SoQL access to every dataset the city and
state publish.

Everything is built on public, key-free open data. No API keys, no accounts, no
proprietary tiles, no vendor SDK. Every layer names the dataset it came from and
links to it.

```
API   http://localhost:50310       (FastAPI, GeoJSON + gzip)
App   http://localhost:50311/nyc   (Next.js + MapLibre GL)
Docs  http://localhost:50311/nyc/docs
MCP   http://localhost:50310/mcp   (streamable HTTP) · python3 -m nycgis.mcp_server (stdio)
```

---

## MCP server

```sh
claude mcp add --transport http nyc https://modc2.com/nyc/api/mcp   # hosted
claude mcp add nyc -- python3 -m nycgis.mcp_server                  # local, stdio
```

Both transports are served by one JSON-RPC engine in `nycgis/mcp_server.py`;
the HTTP endpoint in `api/api.py` is transport framing around the same
`handle_message()`. They carried separate copies once and drifted — a tool that
worked over stdio 404'd over HTTP — so the dispatch is deliberately shared.

| | |
| --- | --- |
| **Protocol** | `2025-06-18`, negotiating down to `2025-03-26` / `2024-11-05` |
| **Capabilities** | tools · prompts · resources |
| **Auth** | none — every source is public open data |
| **Writes** | nothing; all 17 tools are annotated `readOnlyHint` |

**Tools** — `nyc_info`, `nyc_boroughs`, `nyc_borough`, `nyc_where` ·
`nyc_layers`, `nyc_layer` · `nyc_housing`, `nyc_prices`, `nyc_trend`,
`nyc_sales`, `nyc_rents`, `nyc_homes`, `nyc_affordable` · `nyc_traffic` ·
`nyc_find_datasets`, `nyc_dataset`, `nyc_query`.

The last three are the important ones: they reach *every* dataset on NYC Open
Data and NY State Open Data via Socrata Discovery + SoQL — 311, crime, schools,
health, budgets, permits — not just the layers the map draws.

**Prompts** — `neighborhood_report`, `compare_areas`, `explore_open_data`:
recipes that tell a model which tools to reach for and in what order.

**Resources** — `nyc://atlas/layers`, `nyc://atlas/housing-options`,
`nyc://atlas/boroughs`, and `nyc://atlas/caveats`, which is the exclusions list
below. A client should read the caveats before quoting a price.

`GET /tools` returns the whole surface as plain JSON; the docs page at
`/nyc/docs` renders itself from it, so a tool added to `nycgis/tools.py`
documents itself everywhere at once.

---

## Layers

| Layer | Kind | Source |
| --- | --- | --- |
| **Housing prices** | choropleth | DOF Citywide Rolling Sales (`w2pb-icbu`) |
| **Individual sales** | points, coloured by price | same |
| **Affordable housing built** | graduated circles | HPD Affordable Housing Production (`hg8x-zxpr`) |
| **Subway lines** | lines in official MTA colours | MTA GTFS static feed |
| **Subway stations** | points, routes + ADA | MTA Subway Stations (`39hk-dx4f`) |
| **Station ridership** | graduated circles | MTA Subway Hourly Ridership (`ak4z-sape`) |
| **Bike network** | lines, weighted by protection | NYC Bike Routes (`mzxg-pwib`) |
| **Parks & open space** | polygons | Parks Properties (`enfh-gkve`) |
| **Hurricane evacuation zones** | ordinal polygons | Hurricane Evacuation Zones (`epne-qv9x`) |
| **Live traffic speeds** | lines banded by speed | DOT Real-Time Traffic Speeds (`i4gi-tjb9`) |
| **Traffic volume by hour** | graduated circles + 24h profile | DOT Automated Traffic Volume Counts (`7ym2-wayt`) |
| **Traffic injuries** | heatmap + points | Motor Vehicle Collisions (`h9gi-nx95`) |
| **Borough / Neighborhood boundaries** | outlines | `gthc-hcne`, `9nt8-h7nd` |

Basemaps are CARTO's free raster tiles (dark/light) and OpenStreetMap's own
tiles; geocoding is OpenStreetMap Nominatim; the renderer is MapLibre GL
(BSD-3).

## Traffic: when to drive

Two datasets answer two different questions, and the module keeps them apart.

**Live speeds** say where traffic is moving *now*. DOT sensors on the highways
and major arterials report a speed and a travel time every few minutes; the
layer bands them stopped / crawling / moving / free flow and re-polls itself
every 3 minutes. Coverage is the highway network only — most local streets
have no detector, and the legend says so rather than letting an empty street
read as a clear one.

**Volume by hour** is the part you can plan around. Every DOT count location
carries a 24-hour profile of vehicles per hour, its busiest and calmest hour,
and the hours that sit at or under half its peak. Click a location for the
chart; ask `m nyc/traffic` for the same thing as data:

```bash
m nyc/traffic street="cross bronx" hour=8   # how bad is 8am, and when is better
m nyc/traffic borough=brooklyn              # the busiest counts in one borough
m nyc/traffic                               # city-wide curve + what's slow now
```

Three traps in these files, each of which silently produces a wrong number:

- The speed feed is an **archive**, not a current-state table — 110M rows going
  back years. Query it without an order and you get arbitrary history.
  `max(data_as_of)` is not a way out either; it returned two different values
  seconds apart. The snapshot orders newest-first and keeps the most recent
  reading per link.
- Rows with `status = -101` are **sensors that are not reporting**. They still
  carry a speed field and it is always garbage. They are counted (the layer
  reports how much of the network is dark) and never drawn.
- Volume rows are **15-minute bin counts**, not hourly totals, and a handful of
  locations use 10-minute bins. Averaging `vol` over an hour gives the average
  *bin* and understates traffic by 4-6x. Summing the per-bin averages across
  the hour is exact and assumes nothing about bin width.

Count locations are also unprojected in-process: DOT publishes them as WKT in
EPSG:2263 (state-plane feet), so `sources.state_plane_to_wgs84` does the
Lambert Conformal Conic inverse in the stdlib rather than making PROJ the
largest dependency in the module for the sake of a few hundred points.

Profiles average every count since 2022 — post-pandemic, so the commute peaks
are the current ones — and a location is dropped unless it has all 24 hours,
because a location counted only 09:00–17:00 would otherwise report 9AM as its
quietest hour purely because the night is missing.

## The housing choropleth

Colour any of six metrics, over four geographies, seven property types and four
time windows:

| Control | Options |
| --- | --- |
| **Metric** | median sale price · median $/ft² · average price · number of sales · total value · price change vs. prior period |
| **Geography** | neighborhood (262 NTAs) · community district (59) · ZIP (178 MODZCTAs) · borough |
| **Property type** | all · all residential · houses (1–3 family) · one-family · condos · co-ops · rentals · commercial |
| **Window** | 2025– · 2024– · 2022– · everything from 2016 |

The underlying record is **every recorded deed** in the five boroughs from 2016
to the present — about 845,000 sales. Aggregation happens server-side in SoQL,
so a 262-area choropleth costs one HTTP request rather than a million rows.

Click any area for its stats and a **yearly price history**; click any point
feature for its own detail card.

### What the numbers exclude, and why

Three decisions shape every figure on the map. They are in the code with
comments, and worth knowing before quoting a number:

1. **Sales under $50,000 are dropped.** A large share of rows in the DOF file
   are $0 or nominal deed transfers — family transfers, LLC restructurings,
   estate filings. They are not market prices and would drag every median down.

2. **$/ft² is filtered to a plausible band ($50–$5,000).** For condo and co-op
   units the city's file often reports the *whole building's* square footage
   rather than the unit's, so a $1M apartment in a 250,000 ft² tower computes to
   $4/ft². Unfiltered, this put Financial District–Battery Park City at a
   $4/ft² median across 163 "samples". Rows are filtered individually, so the
   genuine ones still count; areas left with fewer than 5 usable rows report no
   $/ft² at all rather than a noisy one.

3. **Price change needs 5+ sales on both sides**, and its colour scale is
   clipped to the middle 90% and kept symmetric about zero — otherwise one
   thin-volume ZIP swinging −79% stretches the domain until every other area
   reads neutral.

Areas with no qualifying sales keep their geometry and are drawn in a neutral
grey, never in the ramp's lowest class: "no data" and "cheapest" must not look
the same.

## CLI

```sh
m nyc                                  # null call → info()
m nyc/layers                           # the layer catalogue
m nyc/layer subway_lines               # one layer as GeoJSON
m nyc/housing metric=median_ppsf       # a housing choropleth
m nyc/housing geography=zip since=2022-01-01 property_type=condo
m nyc/prices                           # city-wide summary + top/bottom areas
m nyc/trend area=BK0101                # one neighborhood's price history
m nyc/sales since=2025-06-01           # individual sales as points
m nyc/traffic street="cross bronx"     # when to drive it, and what's slow now
m nyc/where "Prospect Park"            # geocode a place
m nyc/boroughs                         # the five boroughs
m nyc/warm                             # pre-fetch every layer into the cache
m nyc/cache                            # cache size/location
m nyc/serve                            # run API + map app under pm2
m nyc/kill                             # stop both
```

## HTTP API

| Route | Returns |
| --- | --- |
| `GET /layers` | the layer catalogue (drives the UI) |
| `GET /layers/{id}` | any layer as GeoJSON |
| `GET /layers/housing_prices?metric=&geography=&since=&property_type=` | choropleth + quantile breaks |
| `GET /layers/sales?since=&property_type=&limit=` | individual sales as points |
| `GET /traffic?street=&borough=&hour=` | hourly volume profiles + the live speed picture |
| `GET /boundary/{borough\|nta\|zip}` | boundary geometry |
| `GET /prices` | city-wide summary |
| `GET /trend?area=` | yearly history |
| `GET /where?q=` | geocode |
| `GET /options`, `/view`, `/health`, `/cache` | UI metadata |
| `GET /tools` | the whole MCP surface (tools, prompts, resources) as JSON |
| `POST /tools/{name}` | call one tool with a JSON object of arguments |
| `POST /mcp` | MCP streamable HTTP (`DELETE` ends a session; `GET` is 405 — no server-initiated stream) |

Responses are gzipped — the 29,679-segment bike network goes out at 568 KB
instead of 6.4 MB.

## Caching

Open-data responses are cached under `~/.mod/nyc/cache`, and **a stale entry
beats an error**: if a portal is slow or down, the last good copy is served.
`m nyc/warm` pre-fetches everything (~19 MB, under a minute).

Layer responses also carry a browser lifetime, and it is **per layer**, not
global: a layer that declares `refresh_seconds` (live traffic speeds) is served
with a matching `max-age` and re-polled by the UI on the same cadence.
Everything else keeps the hour-long default. A live layer served with the
default would sit frozen on screen for an hour no matter how often the server
refreshed behind it.

Geometry from the city's portal is authoritative-precision — the borough file
alone is 3 MB of coastline. A pure-Python Ramer–Douglas–Peucker pass plus
coordinate rounding cuts that to 165 KB with no visible difference at city zoom.

## Colour

The choropleth ramps were checked with a validator, not chosen by eye:

- **sequential** — one blue hue, monotonic in lightness, running dark→light on
  the dark basemap so magnitude increases away from the surface;
- **diverging** (price change) — blue ↔ red with a neutral grey midpoint that
  recedes; both arms lighten away from the middle;
- **overlays** — layers that share a mark form never share a hue; the aqua and
  orange pair clears all-pairs colour-blind separation (ΔE 9.4 deutan) and
  normal-vision separation (ΔE 26.5) against the map surface;
- **subway lines** — the one layer that inherits its palette, because riders
  read the network by the MTA's own route colours.

### Chrome vs. data

The interface is dressed as an 8-bit platformer — block panels with a black
outline and a hard bevel, a coin-counter HUD, brick section headers, a spinning
coin for "busy", Press Start 2P for labels. That styling stops at the edge of
the map. **The chrome palette (`tailwind.config.ts`, the `nes-*` tokens) and the
data palette (`src/lib/palette.ts`) are separate on purpose**: primaries are
chosen to be loud and mutually maximally distinct, which is the opposite of what
a sequential ramp needs, so nothing that encodes a value is reachable as a
Tailwind utility. If you extend the theme, keep the ramps out of it — a
choropleth in coin yellow and Mario red would look great and read as nothing.

Pixel-font labels are ASCII-only and upper case; Press Start 2P covers basic
Latin, so `·`, `–`, `²` and `↗` fall through to the fallback stack per glyph.
Values (`$1.45M`, `198/262 areas`) stay in the body sans — the display face is
for labels, never for data.

## Tests

```sh
python3 -m pytest tests -m "not network"   # 27 offline: geometry, joins, breaks
python3 -m pytest tests                    # + 17 live open-data checks
```

The network tests are deliberately unmocked — they are what catches an upstream
schema change (a renamed column, a dataset that moved portals, the day DOF
finally stores square footage as a number).

## Rebuilding the app

`next start` serves `app/.next` while it runs, so building straight over it
hands every open tab a chunk that no longer exists. Build into a scratch dist
and swap:

```sh
cd app
rm -rf .next-build
NYC_DIST_DIR=.next-build npx next build || exit 1   # gate the swap on this
test -f .next-build/BUILD_ID || exit 1              # and on a complete build
rm -rf .next.old && mv .next .next.old && mv .next-build .next
pm2 restart nyc.app
```

Both guards earn their keep. Piping the build to `tail` masks its exit code
behind `tail`'s, and `next build` can also fail *after* printing its route
table — "Collecting build traces" hit `ENOENT` here and left a `.next-build`
that looked finished and served nothing. Swapping that in takes the site down.
Keep `.next.old` until the new build answers 200; it is the rollback.

`next.config.js` reads `NYC_DIST_DIR` and falls back to `.next`, which is how
pm2 runs it. Never run `next dev` in this directory — it overwrites the
standalone build the live server is serving.

## Degrading without a GPU

MapLibre needs a WebGL 2 context and throws when it cannot get one — from
inside an effect, which React propagates to the root. Unguarded that took the
whole console down: a headless capture box or a machine with blocklisted
drivers got a bare "Application error: a client-side exception has occurred"
and nothing else, though the layer rail, the housing controls and the agent
need no GPU at all.

`app/src/app/components/MapFrame.tsx` is an error boundary around the map
alone. Without WebGL the map is replaced by a panel that says so and the rest
of the console keeps working. Worth checking after any change to `MapView`:

```sh
chromium --headless --disable-webgl --disable-gpu ...   # should NOT be blank
```

## Layout

```
nyc/
├── config.json          # module manifest (ports, fns, sources)
├── mod.py               # the Mod class (anchor)
├── nycgis/
│   ├── sources.py       # fetch + disk cache + RDP simplification + GTFS
│   ├── prices.py        # the housing-price engine (SoQL aggregation)
│   ├── layers.py        # the layer catalogue and loaders
│   ├── tools.py         # the ONE tool registry — every surface reads this
│   └── mcp_server.py    # MCP dispatch + stdio transport (HTTP shares it)
├── api/api.py           # FastAPI, :50310, incl. POST /mcp
├── app/                 # Next.js + MapLibre GL, :50311, basePath /nyc
│   └── src/app/docs/    # the docs page, generated from GET /tools
└── tests/test_nyc.py
```

Adding a layer means adding a loader and a catalogue entry in `nycgis/layers.py`
— the UI builds its panel and legend from the catalogue, so no frontend change
is needed for a layer that fits an existing mark form.

## Attribution

Data © the City of New York, New York State / MTA, and OpenStreetMap
contributors, used under their respective open-data terms. This module is a
viewer; it is not affiliated with or endorsed by any of them.
