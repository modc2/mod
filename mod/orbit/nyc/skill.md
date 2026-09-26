# nyc — browser GIS for New York City, and an MCP server

Map of NYC with toggleable data layers: housing prices, transit, parks, flood
zones, traffic injuries, boundaries. All public key-free open data. The same
engine is an MCP server: 17 read-only tools, plus SoQL access to every dataset
NYC and NY State publish.

**Ports:** API `50310`, app `50311` at `/nyc`. Start with `m nyc/serve`.
**Docs:** `/nyc/docs` (generated from `GET /tools`).

## MCP

```sh
claude mcp add --transport http nyc https://modc2.com/nyc/api/mcp
claude mcp add nyc -- python3 -m nycgis.mcp_server
```

Protocol `2025-06-18` (negotiates down to `2025-03-26`, `2024-11-05`). No auth.
Capabilities: tools, prompts, resources.

- **One dispatch, two transports.** `nycgis/mcp_server.py:handle_message()` is
  the whole JSON-RPC engine; `POST /mcp` in `api/api.py` is only HTTP framing
  around it, and `main()` is only stdio framing. They each carried their own
  copy once and drifted apart — do not reintroduce a second dispatch.
- **`nycgis/tools.py` is the single registry.** Adding a `Tool` there puts it on
  both transports, `GET /tools`, `POST /tools/{name}`, the in-app agent and the
  docs page at once. Titles come from `TITLES` or are derived from the name.
- **Tool failures are results, not JSON-RPC errors** — `isError: true` with the
  message in the content block, so the model can see and correct a bad SoQL
  clause instead of the client swallowing it.
- **Prompts/resources live in `mcp_server.py`**, not in the tool registry.
  `nyc://atlas/caveats` is the housing-exclusions doc; point clients at it
  before they quote a price.

## Quick reference

```sh
m nyc/layers                      # layer catalogue
m nyc/layer subway_lines          # one layer as GeoJSON
m nyc/housing metric=median_ppsf geography=nta property_type=condo
m nyc/prices                      # citywide summary, top/bottom neighborhoods
m nyc/trend area=BK0101           # a neighborhood's yearly price history
m nyc/where "Prospect Park"       # geocode
m nyc/traffic street="cross bronx" hour=8    # when to drive; live speeds too
m nyc/warm                        # pre-fetch all layers (~19MB, <1min)
```

## Housing choropleth parameters

- `metric`: median_price, median_ppsf, avg_price, sales, total_value, price_change
- `geography`: nta (262 neighborhoods), community_district (59), zip (178), borough
- `property_type`: all, residential, houses, one_family, condo, coop, rental, commercial
- `since` / `until`: ISO dates; data runs 2016 → present

## Things to know before changing this module

- **Source of truth is DOF rolling sales** (`w2pb-icbu`, ~845k deeds). Numeric
  columns are TEXT: `gross_square_feet` arrives as `"1,430"` and must go through
  `SQFT_EXPR` (`replace(...,",","")::number`) before any cast.
- **$/ft² needs the plausibility band.** For condos/co-ops the file often gives
  the *whole building's* square footage, producing $4/ft² medians. `ppsf_clause()`
  filters rows to $50–$5,000 and areas need ≥5 usable rows.
- **Sales under $50k are excluded** — they're nominal deed transfers, not prices.
- **Aggregate server-side.** Use SoQL `$group`; never download the row set.
  `trend_all()` gets every area's history in ONE query — per-area queries cost
  ~13s each on a cold cache.
- **Geometry must be simplified.** Raw portal GeoJSON is 3 MB per borough;
  `simplify_geojson()` (pure-Python RDP + rounding) cuts 10–30x.
- **Property bags dominate large layers.** The 29,679-segment bike network is
  mostly properties, not coordinates — trim `keep=[...]` aggressively.
- **Gzip is doing heavy lifting** (`GZipMiddleware`); 6.4 MB → 568 KB.
- **`import mod` shadowing**: run CLI/tests from outside the module directory,
  and note the package is named `nycgis`, not `src`, to avoid colliding in
  `sys.modules` with other orbit modules' `src`.
- **Cache is stale-tolerant** (`~/.mod/nyc/cache`): upstream failure serves the
  last good copy rather than erroring.
- **The map must be allowed to fail alone.** MapLibre throws without a WebGL 2
  context, from inside an effect, which React propagates to the root — that
  blanked the entire console on any GPU-less browser. `MapFrame.tsx` is an
  error boundary around `MapView` only. Keep it there.
- **Never build in place.** `next start` is serving `app/.next`; build with
  `NYC_DIST_DIR=.next-build npx next build`, then swap and restart pm2.

## Traffic data (`nycgis/traffic.py`)

Two datasets, two different questions — live speeds (where it's slow *now*) and
volume by hour (when to leave). Each has a trap that silently returns a wrong
number:

- **The speed feed `i4gi-tjb9` is an archive, not current state** — 110M rows.
  Unordered queries return arbitrary history. `max(data_as_of)` is *not*
  reliable (it returned two different values seconds apart); order
  `data_as_of DESC`, take ~20k rows, keep the newest row per `link_id`.
- **`status = -101` means the sensor is dark.** Those rows still carry a speed
  and it is always garbage. Count them, never draw them — ~40% of links.
- **Volume rows are 15-minute bins** (a few locations use 10-minute bins), not
  hourly totals. `avg(vol)` per hour is the average *bin* and understates by
  4-6x. Group by `hh,mm` and **sum the per-bin averages** — exact, and assumes
  nothing about bin width.
- **Count locations are EPSG:2263 WKT** (state-plane feet), not lat/lng.
  `sources.state_plane_to_wgs84()` is a stdlib Lambert Conformal Conic inverse;
  don't add pyproj for a few hundred points. Verified against known corners
  (South Ozone Park, Times Square, Staten Island).
- **A profile needs all 24 hours** or its "calmest hour" is a lie — a location
  counted 09:00–17:00 would name 9AM as its quietest. 20 partial locations are
  dropped; 498 survive.
- **Live layers need a per-layer cache header.** A layer with
  `refresh_seconds` in its `LAYERS` entry gets a matching `max-age` via
  `layers.cache_control()` and is re-polled by `page.tsx`. Served with the
  catalogue's default hour, a "live" layer sits frozen on screen.

## Adding a layer

Add a loader + a `LAYERS` entry in `nycgis/layers.py`. The frontend builds its
panel, legend and inspector from the catalogue, so a layer using an existing
mark form needs no frontend change. Give it a distinct hue only if it shares a
mark form with another layer (see the colour notes in `app/src/lib/palette.ts`).

## Tests

`python3 -m pytest tests -m "not network"` (offline) or without the marker to
include live open-data schema checks.
