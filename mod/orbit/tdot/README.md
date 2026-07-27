# tdot — an open-source browser GIS for New York City

A slippy-map GIS of New York in the browser: **housing prices**, **transit**,
parks, flood zones, traffic injuries and administrative boundaries, as
toggleable layers with a choropleth engine, legends and a feature inspector.

Everything is built on public, key-free open data. No API keys, no accounts, no
proprietary tiles, no vendor SDK. Every layer names the dataset it came from and
links to it.

```
API   http://localhost:50320      (FastAPI, GeoJSON + gzip)
App   http://localhost:50321/tdot  (Next.js + MapLibre GL)
```

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
| **Traffic injuries** | heatmap + points | Motor Vehicle Collisions (`h9gi-nx95`) |
| **Borough / Neighborhood boundaries** | outlines | `gthc-hcne`, `9nt8-h7nd` |

Basemaps are CARTO's free raster tiles (dark/light) and OpenStreetMap's own
tiles; geocoding is OpenStreetMap Nominatim; the renderer is MapLibre GL
(BSD-3).

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
m tdot                                  # null call → info()
m tdot/layers                           # the layer catalogue
m tdot/layer subway_lines               # one layer as GeoJSON
m tdot/housing metric=median_ppsf       # a housing choropleth
m tdot/housing geography=zip since=2022-01-01 property_type=condo
m tdot/prices                           # city-wide summary + top/bottom areas
m tdot/trend area=BK0101                # one neighborhood's price history
m tdot/sales since=2025-06-01           # individual sales as points
m tdot/where "Prospect Park"            # geocode a place
m tdot/boroughs                         # the five boroughs
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
| `GET /layers/housing_prices?metric=&geography=&since=&property_type=` | choropleth + quantile breaks |
| `GET /layers/sales?since=&property_type=&limit=` | individual sales as points |
| `GET /boundary/{borough\|nta\|zip}` | boundary geometry |
| `GET /prices` | city-wide summary |
| `GET /trend?area=` | yearly history |
| `GET /where?q=` | geocode |
| `GET /options`, `/view`, `/health`, `/cache` | UI metadata |

Responses are gzipped — the 29,679-segment bike network goes out at 568 KB
instead of 6.4 MB.

## Caching

Open-data responses are cached under `~/.mod/tdot/cache`, and **a stale entry
beats an error**: if a portal is slow or down, the last good copy is served.
`m tdot/warm` pre-fetches everything (~19 MB, under a minute).

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

## Tests

```sh
python3 -m pytest tests -m "not network"   # 27 offline: geometry, joins, breaks
python3 -m pytest tests                    # + 17 live open-data checks
```

The network tests are deliberately unmocked — they are what catches an upstream
schema change (a renamed column, a dataset that moved portals, the day DOF
finally stores square footage as a number).

## Layout

```
tdot/
├── config.json          # module manifest (ports, fns, sources)
├── mod.py               # the Mod class (anchor)
├── tdotgis/
│   ├── sources.py       # fetch + disk cache + RDP simplification + GTFS
│   ├── prices.py        # the housing-price engine (SoQL aggregation)
│   └── layers.py        # the layer catalogue and loaders
├── api/api.py           # FastAPI, :50320
├── app/                 # Next.js + MapLibre GL, :50321, basePath /tdot
└── tests/test_tdot.py
```

Adding a layer means adding a loader and a catalogue entry in `tdotgis/layers.py`
— the UI builds its panel and legend from the catalogue, so no frontend change
is needed for a layer that fits an existing mark form.

## Attribution

Data © the City of New York, New York State / MTA, and OpenStreetMap
contributors, used under their respective open-data terms. This module is a
viewer; it is not affiliated with or endorsed by any of them.
