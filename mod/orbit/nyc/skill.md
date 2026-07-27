# nyc — open-source browser GIS for New York City

Map of NYC with toggleable data layers: housing prices, transit, parks, flood
zones, traffic injuries, boundaries. All public key-free open data.

**Ports:** API `50310`, app `50311` at `/nyc`. Start with `m nyc/serve`.

## Quick reference

```sh
m nyc/layers                      # layer catalogue
m nyc/layer subway_lines          # one layer as GeoJSON
m nyc/housing metric=median_ppsf geography=nta property_type=condo
m nyc/prices                      # citywide summary, top/bottom neighborhoods
m nyc/trend area=BK0101           # a neighborhood's yearly price history
m nyc/where "Prospect Park"       # geocode
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

## Adding a layer

Add a loader + a `LAYERS` entry in `nycgis/layers.py`. The frontend builds its
panel, legend and inspector from the catalogue, so a layer using an existing
mark form needs no frontend change. Give it a distinct hue only if it shares a
mark form with another layer (see the colour notes in `app/src/lib/palette.ts`).

## Tests

`python3 -m pytest tests -m "not network"` (offline) or without the marker to
include live open-data schema checks.
