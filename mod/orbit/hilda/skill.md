# hilda — HILDA+ global land use change, 1960–2019

Annual land use / land cover for the whole planet at 1 km, in six classes,
reduced to a 0.5° cube and served three ways: as curves over time, as a
classified map, and as a cellular automaton. Public CC-BY-4.0 data from
PANGAEA, no keys.

**Ports:** API `50550`, app `50551` at `/hilda`. Start with `m hilda/serve`.

**First run:** `m hilda/bootstrap` fetches and reduces the record — about
15 minutes and 2.5 GB of transfer, once. Everything else fails with a 409 and
a hint until it has. `m hilda/status` shows what is on disk.

## Quick reference

```sh
m hilda/summary                        # what grew, what shrank, gross vs net
m hilda/series region=amazon           # area per class per year
m hilda/areas year=2019                # one year's totals
m hilda/net y0=1960 y1=2019            # net change per class
m hilda/transitions                    # gross class-to-class flows, km2
m hilda/hotspots n=10                  # cells that churned most
m hilda/cell lon=-60 lat=-3            # one cell, every year

m hilda/rates                          # observed annual transition matrix
m hilda/ca weight=0.6                  # run the automaton
m hilda/ca end=2050 scenario=urban=2   # project, with a thumb on the scale
m hilda/ca protect=amazon              # freeze a region
m hilda/calibrate                      # sweep the neighbourhood weight
m hilda/validate start=1990            # hindcast scorecard

m hilda/map year=2019 out=/tmp/m.png   # classified world
m hilda/window bbox=-63,-10,-58,-5     # full 1 km detail for a box
m hilda/change                         # gross turnover map
```

## Classes

Six land use classes move; water and ice do not.

| key | HILDA+ code | notes |
|---|---|---|
| `urban` | 11 | grew 55% over the record, the largest relative change |
| `cropland` | 22 | |
| `pasture` | 33 | pasture / rangeland |
| `forest` | 44 | |
| `grassland` | 55 | unmanaged grass / shrubland |
| `sparse` | 66 | sparse or no vegetation |
| `water` | 77 | stored, not modelled |
| `ice` | 99 | HILDA+ "no data" — in practice permanent ice |

Any `class=` argument takes a key, a short code (`FOR`), a pixel code (`44`)
or an index (`3`).

## Regions

`global africa europe north_america south_america asia oceania amazon congo
sea_asia india china us_midwest sahel boreal tropics`

Bounding boxes, not borders — blunt for a continent, exact about what they
include. Anything taking `region=` also takes `bbox=w,s,e,n`.

## Reading the numbers

- **Areas are km², cos-latitude weighted on the sphere.** A 0.5° cell at 60°N
  is half the area of one at the equator; unweighted sums put most of the
  world's forest in Siberia.
- **Net is not gross.** State layers give net change; transition layers give
  gross. HILDA+'s headline finding is that gross is several times net —
  cropland abandoned here and cleared there cancels on paper, not on the
  ground. Every response labels which one it is reporting.
- **`/transitions` is global only.** The cube stores one 6×6 matrix per year
  plus a per-cell turnover intensity; regional gross matrices would need the
  source rasters.

## The automaton

`flow[i,j] = S[i] · rate[i,j] · suit · pressure[j] · scenario[j]`, then each
`i→j` plane is rescaled so its global total equals `rate[i,j]` × the area
under *i*. Suitability decides where; the observed transition record decides
how much.

- `weight` (0–1) — how much conversion prefers cells whose neighbours are
  already that class. 0 is spatially blind. `m hilda/calibrate` picks it.
- `scenario` — per-class multipliers, e.g. `urban=2,forest=0.5`.
- `protect` — a region or bbox that neither gains nor loses.
- `end` past 2019 is a projection and is labelled `projection: true`.

**Skill** comes in two numbers, and you need both:

- `area_skill` — the global trajectory. On a 1990→2019 hindcast trained on
  1960–1990: urban +93%, cropland +91%. Negative in aggregate because pasture
  and grassland reverse sign between the halves of the record, which no
  stationary rate matrix can capture.
- `allocation_skill` — cell-by-cell placement. About −12%. The automaton does
  **not** beat assuming nothing moved. Reporting this is the point; a
  land-use CA that hides its persistence baseline always looks good.

Rates and suitability are always fitted on years strictly before `start`, and
`out_of_sample` says whether that was possible — a run from 1960 has no prior
years, so its skill is an upper bound rather than a forecast.

Do not "fix" the negative allocation skill by tuning against the validation
period. Trend extrapolation was tried and scores about −110%.

## Sanity anchor

Gross change 1961–2019 is 42.44 M km², 32% of global land — matching the
published ~43 M km² / 32%. If a change makes that number move, the change is
wrong.

## Known data defects

- **2015 states is a base map**, not an annual state: it codes permanent ice
  as sparse vegetation and adds 12.4 M km² of phantom land. Excluded from
  ranges; `years=2015` still fetches it deliberately. The state series has a
  documented one-year hole (59 years, not 60).
- **The 2015–2014 transition layer has corrupt LZW** from row 10 to 1808
  (89.90°N–71.91°N); the reader zero-fills it and carries on. That band holds
  1.75% of global land but ~0.2% of annual turnover.

## Gotchas

- `deg` must divide the 0.01° source grid evenly. 0.5 (default), 0.25, 0.2 and
  0.1 work; 0.7 does not.
- `/window.png` is capped at 2500 square degrees — it reads real 1 km pixels.
- Cached source rasters are pruned to the four most recent after a window
  read. `m hilda/clear` drops them; the cubes are never touched.
