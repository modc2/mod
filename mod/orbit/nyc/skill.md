# nyc — browser GIS for New York City, and an MCP server

Map of NYC with toggleable data layers: housing prices, transit, parks, flood
zones, traffic injuries, boundaries. All public key-free open data. The same
engine is an MCP server: 31 read-only tools — housing, the listing market
(StreetEasy/Zillow asking data), crime and shootings (NYPD), live NYC news,
311, restaurants, trees, air, evictions, permits — plus SoQL access to every
dataset NYC and NY State publish.

**Ports:** API `50310`, app `50311` at `/nyc`. Start with `m nyc/serve`.
**ASK agent drives the map:** `nyc_map` / `nyc_infographic` (`nycgis/scene.py`)
return validated directives; `/chat` emits them as `display` SSE events;
`app/src/lib/scene.ts` applies them; `/overlay` draws any dataset.
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
m nyc/crime                       # complaints + shootings vs last year (part=precincts|offenses|trend)
m nyc/news topic=housing          # NYC headlines (Gothamist/THE CITY/NYT Metro); q= searches GDELT
m nyc/market area=astoria         # asking rent/price + YoY (StreetEasy/Zillow public data)
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

## Population & the brief (`nycgis/demographics.py`, `nycgis/report.py`)

- Tool `nyc_population` (geography=borough|nta|tract, sort=<field>, limit).
  HTTP: `/layers/population`, `/stats`, `/report` (self-contained HTML), `/report.csv`.
- Census ACS comes from the **bulk table files**, not the API (keyless API calls
  are redirected). Add a table by adding it to `ACS_TABLES`; bump the cache key.
- City/borough medians are exact Census values; NTA medians are approximate.
- Home-sale medians drop multi-unit bulk deeds; keep that if you touch it.

## Crime, news, listing market (`nycgis/crime.py`, `news.py`, `realestate.py`)

- Tools `nyc_crime` / `nyc_news` / `nyc_market`; HTTP `/crime`, `/news`,
  `/market`; layers `crime` (precinct choropleth, breaks travel in the
  payload) and `shootings` (heat). The report gained Public-safety, Market
  and News sections; the app rail gained CITY PULSE.
- **NYPD current-year complaints refresh quarterly** and carry typo dates
  (`1016-…`). `crime.data_through()` reads real coverage; every YoY compare
  uses the same Jan-1→that-date window on BOTH sides, or crime "drops 50%".
- **The shooting file's lat/lng columns are swapped** on all but ~300 rows —
  normalize per row, never wholesale.
- **GDELT throttles as text with HTTP 200** (1 req/5s); a failed JSON parse
  is the throttle. `news.search()` falls back to filtering the RSS cache.
- **StreetEasy CSVs are wide** (area × month, zipped, public CDN; keyless).
  Thin neighborhoods whipsaw their medians — movers lists require 20+
  active listings. Keep the StreetEasy/Zillow attribution in payloads.

## Tenant housing data (`nycgis/housing.py`)

- Tools `nyc_lotteries` / `nyc_building_check` / `nyc_violations` /
  `nyc_nycha`; HTTP `/lotteries`, `/building`, `/violations`, `/nycha`;
  CLI `m nyc/lotteries` etc. Sources: Housing Connect lotteries by lottery
  (vy5i-a666) and by building (nibs-na6y), HPD violations (wvxf-dwi5),
  HPD complaints/problems (ygpa-z7cr), HPD litigation (59kj-x8nc), the
  NYCHA Development Data Book (evjd-dqpz).
- **The lottery file keeps stale `Active` rows with deadlines years past**
  — "open" additionally requires `lottery_end_date >= today OR NULL`, and
  the cache key carries the day. Boroughs there are two-letter codes
  (`BK`/`QN`/…), mapped both ways.
- **The old HPD complaints file (uwyv-629c) now requires a login** — use
  ygpa-z7cr, which is problem-level: count "problems", not complaints,
  and skip `problem_duplicate_flag = 'Y'` (null-safe, SoQL drops nulls
  on `!=`).
- The violations file is ~11M rows: `$group` server-side only. Address
  lookups match house number exactly and the street as a prefix with the
  suffix word dropped (`ELDERT ST` ≡ `ELDERT STREET`).
- NYCHA Data Book is a spreadsheet upload: rents arrive as `$513`,
  numbers with commas (`_n` strips both), and TOTAL roll-up rows are
  skipped by name.

## City data + the full catalog (`nycgis/citydata.py`, `catalog.py`)

- Curated tools: `nyc_311` (erm2-nwe9), `nyc_collisions` (h9gi-nx95),
  `nyc_restaurants` (43nn-pn8j), `nyc_trees` (uvpi-gqnh / hn5i-inap),
  `nyc_evictions` (6z8x-wfk4), `nyc_permits` (rbx6-tga4), `nyc_air`
  (c3uy-2p5r); `nyc_crime` also takes offense/borough/severity/date filters
  (citydata.crime stitches historic + YTD). All aggregate server-side with
  `$group` — never pull raw row sets — and cite dataset ids.
- **Borough columns differ per dataset** (`borough`/`boro`/`boro_nm`/
  `boroname`, mixed case). Use `citydata.borough_norm()` + `upper(col)=` —
  it accepts "bk", "richmond", "the bronx". Garbage values ('Unspecified',
  blank) become honest buckets, never crashes.
- **Permits use DOB NOW (rbx6-tga4)**, not legacy BIS (ipu4-2q9a): BIS
  stores dates as MM/DD/YYYY TEXT (needs `::floating_timestamp` casts) and
  dwindled to ~1/10 the volume after the agency moved systems.
- **Shootings live file is 5ucz-vwe8**; 833y-fsy8 is ARCHIVED (frozen 2025).
- **`nyc_catalog` harvests the ENTIRE portal catalog** (2,404 NYC + 1,033
  NYS datasets) into `catalog-full-<domain>` cache entries (TTL 7d);
  `nyc_find_datasets` then searches it offline (exact-name > name-token >
  category > description, freshness tiebreak) and falls back to the live
  Discovery API when no harvest exists. **Page Discovery by `offset`, not
  `scroll_id`** — the scroll cursor silently dropped ~650 datasets. The
  Discovery API returns no row counts (`rows_size` absent).

## Adding a layer

Add a loader + a `LAYERS` entry in `nycgis/layers.py`. The frontend builds its
panel, legend and inspector from the catalogue, so a layer using an existing
mark form needs no frontend change. Give it a distinct hue only if it shares a
mark form with another layer (see the colour notes in `app/src/lib/palette.ts`).

## The owner's data (`nycgis/userdata.py`)

The owner can save datasets as permanent layers (rail category "Your data"),
three ways: inline GeoJSON, any Socrata dataset shaped like an agent overlay
(points/heat/areas + SoQL where — re-fetched on the 6h overlay cadence), or a
remote GeoJSON URL. Records live in `~/.mod/nyc/data` (NOT the cache —
`clear_cache` must never touch them); `layers.catalog()/get()` fall through to
it, so the layer appears everywhere a built-in does.

Writes are owner-gated on the fleet's mod-protocol token:

- HTTP: `POST /data`, `DELETE /data/{slug}`, `POST /data/{slug}/refresh` need
  `Authorization: Bearer <token>` verifying to the owner (box key, or
  `NYC_OWNER`); the same header unlocks `nyc_add_data`/`nyc_remove_data` over
  `/tools` and `/mcp`.
- Chat: the app sends the owner's wallet token with `/chat`; the agent's MCP
  subprocess is then launched with `NYC_DATA_WRITE=1`, which is the whole
  write bit. An unauthenticated chat gets a read-only agent.
- CLI: `m nyc add_data title="..." dataset=...` (the box holds the key, so no
  token).

`userdata._auth()` only uses an ALREADY-imported `mod` package — a fresh
import from inside nycgis is fragile in test/stdio contexts; where the
protocol isn't loaded, identity reports unknown and the env grant gates.

## Tests

`python3 -m pytest tests -m "not network"` (offline) or without the marker to
include live open-data schema checks.
