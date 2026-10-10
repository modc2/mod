# tdot — open-source browser GIS for Toronto

Map of Toronto with toggleable data layers: police-reported major crime by
neighbourhood, the full open housing record (rental buildings, community and
subsidized housing, health and fire violations, the development pipeline, census
dwelling values), TTC subway/streetcar, cycling, green space, serious traffic
collisions, boundaries. All public key-free open data. Also carries a model that
predicts a building's RentSafeTO inspection score from open data, a
`housing_lots` layer that screens the city's land inventory for lots affordable
housing could be built on (city-owned vacant land, surface parking, surplus
property — full lot polygons, highlighted and tiered PRIME/STRONG/POSSIBLE),
and a 3-D map mode that extrudes buildings and each lot's potential massing.

**Ports:** API `50320`, app `50321` at `/tdot`. Start with `m tdot/serve`.

## Quick reference

```sh
m tdot/layers                      # layer catalogue
m tdot/layer ttc_lines             # one layer as GeoJSON
m tdot/crime metric=per_km2 geography=neighbourhood category=auto_theft
m tdot/summary                     # city-wide totals, top/bottom neighbourhoods
m tdot/trend area=087              # a neighbourhood's yearly incident history
m tdot/incidents since=2026-01-01  # individual incidents as points
m tdot/where "Christie Pits"       # geocode
m tdot/warm                        # pre-fetch all layers (~18MB)

m tdot/housing                     # every open housing dataset + what tdot does with it
m tdot/housing role=closed         # the ones that are NOT open data (MLS prices)
m tdot/score                       # score-model accuracy, drivers, caveats
m tdot/outliers limit=10           # buildings scoring worst vs comparable ones
m tdot/building 4153587            # one building: prediction + what drives it

m tdot/layer housing_lots          # scored lots for affordable housing (612 lots)
```

The agent tool `tdot_housing_lots` (MCP + Ask panel) lists the best lots —
optionally filtered by ward or minimum score — and switches the layer on.

## The score model (`tdotgis/score.py`)

Predicts a building's RentSafeTO evaluation score (out of 100) from the
registration filing, health-hazard and fire-code violations at the address, and
the neighbourhood's census + crime profile. Out-of-fold: **MAE 5.4 pts, R² 0.36**,
against 7.1 / 0.00 for guessing the mean. Top drivers: ward, property management
company, year evaluated, year built, fire-code violations.

Report it with its error. It is a ranking tool, not a verdict on a building.

## Crime choropleth parameters

- `metric`: incidents, per_km2, per_month, change
- `geography`: neighbourhood (158, current), neighbourhood140 (historical)
- `category`: all, assault, auto_theft, break_enter, robbery, theft_over
- `since` / `until`: ISO dates; the record runs 2014 → present

## Things to know before changing this module

- **Source of truth is the TPS Major Crime Indicators dump** (`crime.RESOURCE`,
  ~490k incidents). Toronto's CKAN blocks `datastore_search_sql`, so **there is
  no server-side GROUP BY**: `warehouse()` pulls the CSV dump once and reduces
  it to a *month cube* keyed by (neighbourhood, category, month), plus a ~30
  month slice kept at incident grain for the point layer. Every query after
  that is local and instant.
- **Neighbourhood codes are zero-padded to 3.** The boundary file gives `"7"`,
  the crime file gives `"007"`. `choropleth()` pads both sides; skip that and
  the map silently joins to nothing.
- **`NEIGHBOURHOOD_158` carries its code in the name** — `"West Hill (136)"`.
  Run it through `_clean_hood_name()` before showing it.
- **`HOOD_*` is sometimes `"NSA"`** (no neighbourhood assigned). Those rows are
  excluded from area aggregates, not from the city-wide record.
- **Change needs 10+ prior incidents** (`MIN_CHANGE_SAMPLE`) or it reports
  `null`. Two incidents becoming twenty is not a 900% surge.
- **Zero is a real value.** Areas with no incidents keep their geometry and
  report `0`; only `per_km2` goes null (when the geometry reports no area).
- **Geometry must be simplified.** Raw portal GeoJSON is authoritative
  precision; `simplify_geojson()` (pure-Python RDP + rounding) cuts 10–30x.
- **Gzip is doing heavy lifting** (`GZipMiddleware`).
- **`import mod` shadowing**: run CLI/tests from outside the module directory,
  and note the package is named `tdotgis`, not `src`, to avoid colliding in
  `sys.modules` with other orbit modules' `src`.
- **Cache is stale-tolerant** (`~/.mod/tdot/cache`): upstream failure serves the
  last good copy rather than erroring.
- **Line 3 (Scarborough RT) closed in 2023** and is gone from the GTFS feed, so
  it is not a key in `TTC_LINE_COLOR` either.
- **Use `S.ckan_datastore()`, never `S.ckan_resource()`, to get rows.** A package
  publishes the same table as a live datastore *and* as .csv/.xml/.json/.geojson
  exports with near-identical names. Only the datastore answers
  `datastore_search`, and it is usually not first in the list — matching on name
  alone silently picks a file resource and 404s.
- **Columns the city ships null.** `retirement-homes` has LATITUDE/LONGITUDE
  columns that are entirely null; the populated one is `geometry`. Check values,
  not just field names, before choosing a `locate` mode.
- **Never feed the evaluation's per-area sub-scores to the model.** They sum to
  `SCORE`; a model given them scores ~1.0 and knows nothing. `score.py` reads
  features only from the *registration* table — the evaluation is only ever the
  target. There is a test pinning this.
- **Anything said about one evaluated building uses its held-out fold.** A
  400-iteration booster largely memorises its training rows: the full model puts
  383 Sherbourne at 53, the fold that never saw it puts it at 82 (its actual
  score is 41). `model()` keeps the fold estimators and `fold_of` for exactly
  this; using `fit['estimator']` on a training row would make the map and the
  inspector disagree.
- **`score-*` caches carry the fit.** `report()` and `predictions()` are on
  disk (14-day TTL), so a cold API answers instantly; only `building()` needs
  the fitted estimators, which the API warms in a background thread at startup.
- **scikit-learn is an optional dependency.** It is imported lazily inside
  `score.py`; every other layer works without it, and the API returns 501 rather
  than failing to boot.

## Adding a layer

Add a loader + a `LAYERS` entry in `tdotgis/layers.py`. The frontend builds its
panel, legend and inspector from the catalogue, so a layer using an existing
mark form needs no frontend change. Give it a distinct hue only if it shares a
mark form with another layer (see the colour notes in `app/src/lib/palette.ts`).
A layer that deserves a hand-written inspector body gets a case in
`app/src/app/components/Inspector.tsx` and an entry in its `KNOWN` list.

Give it a colour in **both** tables in `palette.ts` — `mapPalette(base, theme)`
starts from the dark or the light one, and a layer missing from the light table
falls back to the generic point colour on every light theme.

## Themes

Ten themes, picked from the header and saved to `localStorage` as `tdot_theme`.
A theme is an entry in `THEMES` (`app/src/lib/theme.ts`), a `[data-theme="id"]`
token block in `globals.css`, a `THEME_RAMPS` pair in `palette.ts`, and its id in
the blocking script in `layout.tsx` that applies the saved theme before first
paint. Adding one means all four.

- Chrome holds **no literal colours** — components use the token classes from
  `tailwind.config.ts` (`text-ink`, `bg-fill`, `border-line`, `bg-accent`) and
  the `.panel`/`.chip`/`.field` classes. A new hex in a component is a bug.
- A theme's `base` (dark/light) lands on the document as `data-base` and picks
  the *map* palette, not just the chrome: sequential and heat ramps have to run
  away from the surface, so each surface has its own table in `palette.ts`.
- A theme also owns its **magnitude ramps**, so it repaints the map and not only
  the panels. What it may recolour is scoped: sequential ramps yes; diverging,
  status, category and TTC line colours no — those carry meaning or identity,
  and a skin must not change what the map says.
- Take the palette from `usePalette()`, never `mapPalette(base)` — the surface
  alone can't say which of the ten themes is on, and two themes can share a
  surface without sharing ramps. Anything keyed on `base` will fail to repaint
  when you switch GLASS → MATRIX.
- A theme's `basemap` is applied on every theme change; the Dark/Light/Streets
  buttons override by hand until the next change.

## MCP

`tdotgis/mcp_server.py` is the whole protocol, and `rpc(msg) -> reply | None` is
transport-free. The stdio loop and the `POST /mcp` route in `api/api.py` are both
thin wrappers over it — add a transport by wrapping `rpc`, never by
reimplementing the dispatch.

Tools come from `tdotgis/tools.py`; adding one there publishes it to the agent,
the MCP server and the console's **MCP** panel at once, because all three read
the same registry. A tool that moves the map sets `drives_map` and returns its
action under `__map__`.

## Tests

`python3 -m pytest tests -m "not network"` (33 offline) or without the marker to
include the 16 live open-data schema checks.
