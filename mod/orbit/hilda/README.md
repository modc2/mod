# hilda

**Sixty years of global land use change — longitudinally, spatially, and as a cellular automaton. In eight-bit.**

[HILDA+](https://ceos.org/gst/HILDAplus.html) (HIstoric Land Dynamics Assessment+) reconstructs annual land use and land cover for the entire planet from 1960 to 2019 at 1 km, in six classes: urban, cropland, pasture/rangeland, forest, unmanaged grass/shrubland and sparse vegetation. It is published on PANGAEA under CC-BY-4.0 as a 4.5 GB ZIP of 933-megapixel GeoTIFFs.

That is not a thing you can put on a screen. This module makes it one.

```
m hilda/bootstrap     # fetch and reduce the record (once, ~15 min)
m hilda/serve         # API on :50550, console on :50551
```

---

## What it does

**Longitudinal.** Area under each class, per year, for the globe, a named region or any bounding box — properly area-weighted on the sphere. Net change, gross class-to-class flows, turnover hotspots, and the full history of any single cell.

**Spatial.** The classified world for any year as a 720×360 grid you can scrub through, plus full 1 km windows fetched on demand for any box.

**Modelled.** A cellular automaton whose transition rates are counted from the HILDA+ transition layers, trained on years disjoint from the run, and scored against both the observed maps and a do-nothing baseline.

---

## Three problems, and how they are solved

### 1. The data is 4.5 GB and you want one year of it

PANGAEA's file server answers HTTP range requests, and a ZIP is random-access by design. So `remote.py` reads the archive without downloading it:

1. `GET` the last 64 KB → end-of-central-directory (this archive is ZIP64, so the real offsets live in the ZIP64 record, not the classic one)
2. `GET` the central directory → all 244 members, their sizes and local offsets
3. `GET` one member's bytes → raw deflate, inflated locally

One year costs ~19 MB and about two seconds. The member index is cached, so steps 1–2 happen once per machine. No GDAL, no rasterio — `zlib` and `urllib` from the standard library.

### 2. One year is 933 million pixels and you want a slice of it

Each raster is 36000×18000 uint8, LZW, and — critically — `RowsPerStrip = 1`. Every row is an independently compressed strip with its own byte offset. So `raster.py` reads a latitude band by assembling a *new, small* TIFF in memory that points at only those strips, and handing it to Pillow, which decodes them in C.

A 200-row window costs ~90 ms and is byte-identical to the same slice of a full decode (which costs 2.5 s and ~3 GB). This is what makes the 1 km zoom feasible at request time.

Two quirks the reader has to know about, both discovered the hard way:

- libtiff refuses a one-strip image built this way. Single-row reads therefore include the same strip **twice** and keep the first copy — pairing a row with itself rather than a neighbour, so a damaged strip cannot condemn the intact row next to it.
- The published `2015-2014` transition layer has corrupt LZW from row 10 to row 1808 (89.90°N–71.91°N). libtiff rejects the whole file over it. `rows_tolerant` falls back to row-at-a-time and zero-fills only what genuinely will not decode. That band holds 1.75% of global land but ~0.2% of annual turnover, so the 2015 transition year is very slightly incomplete and `m hilda/status` says so.

### 3. Sixty years is too much to hold, and a browser needs all of it

Each year is reduced once to **class fractions** on a 0.5° grid — 720×360 cells, each the exact count of its 50×50 source pixels per class, scaled to a byte. Fractions rather than a dominant class: at half a degree a cell is ~55 km across and almost never one land use, and keeping the mixture is what makes area totals correct and lets the automaton move small amounts of land per step.

The whole record is one 15 MB `.npz`. The console gets it as a single gzipped binary blob (~1 MB), so scrubbing the timeline and toggling classes never touch the network.

Ingest downloads **one year at a time** — PANGAEA is a shared public archive and answers parallel range requests with 429 — while the decode, which is the actual bottleneck, fans out across a process pool.

---

## The automaton

Every cell holds six fractions. Each year, the flow from class *i* to class *j* in a cell is

```
flow[i,j] = S[i] · rate[i,j] · suit · pressure[j] · scenario[j]
```

| term | where it comes from |
|---|---|
| `rate[i,j]` | annual conversion probability, **counted** from the HILDA+ transition layers — not fitted |
| `suit` | how change-prone the cell was during the training window, relative to average |
| `pressure[j]` | `1−w + w·(neighbourhood mean of j / neighbourhood land)` — the automaton part |
| `scenario[j]` | 1.0 unless you are asking a what-if question |

Then every `i→j` plane is **rescaled so its global total equals `rate[i,j]` × the area currently under *i***. Suitability decides *where*; the transition record decides *how much*.

That last step is not decoration. Without it the contagion term quietly rewrites the global budget: cells surrounded by forest attract the most forest, and forest cells have the most forest neighbours, so forest grows by feeding itself. An earlier version did exactly that and turned an observed loss of 0.75 M km² into a simulated *gain* of 1.1 M — while the spatial pattern was already right (+0.17 to +0.31 correlation with observed change). Right places, wrong quantities.

Two more things it gets right by construction:

- **Area weighting.** The transition matrix is accumulated in km², one row of latitude at a time. Counting pixels would weight a hectare in Siberia twice a hectare in Brazil, which on this dataset means boreal forest dominating a matrix meant to describe the world.
- **Honest validation.** If you learn where change happens from the record and then predict that record, you have proved nothing. `run` fits both the rates and the suitability on the years strictly *before* `start` and reports `out_of_sample`. A run starting at 1960 has nothing to train on and is labelled in-sample; its skill number is an upper bound, not a forecast.

### What it actually scores

**Skill** is the share of the no-change baseline's error the model removes, reported on two axes because the answer differs sharply between them. On a 1990→2019 hindcast trained only on 1960–1990:

| | skill | reading |
|---|---|---|
| **area** — the global trajectory | urban **+93%**, cropland **+91%**, forest +2%, sparse +21% | it tracks the classes whose regime holds |
| | pasture **−149%**, grassland **−29%** | it cannot track the two that reverse |
| **allocation** — which cells | **−12%** overall | it does **not** beat "nothing moved" |

Both numbers are printed everywhere, deliberately. Three things are worth saying plainly:

- **The negative aggregate area skill is a property of the data, not the model.** Pasture and grassland *flip sign* between the halves of the record — pasture +1.73 M km² in 1960–90, −0.83 M km² in 1990–2019. No stationary transition matrix gets both halves right. Given a matching rate window the model reproduces observed net change to within 1% on every class, which is the machinery check (`test_demand_allocation_pins_global_totals`).
- **The negative allocation skill is the honest state of the model.** Simulated change correlates with observed change per class at only +0.17 to +0.31, and the misallocation costs more than the captured change earns. It gets worse at *short* horizons, because a 4-year baseline error is tiny and any misplacement dominates it.
- **Making it look better was tried and failed.** Extrapolating each cell's training-window trend — concentrating conversion where a class grew before — scores about **−110%**. Where a class grew in 1960–1990 is simply not where it grows afterwards. That result is worth more than a tuned number would have been.

A land-use CA that never reports its persistence baseline can look excellent. This one reports it.

```
m hilda/calibrate                 # sweep the neighbourhood weight
m hilda/validate start=1990       # hindcast 1990→2019, trained on 1960–1990
m hilda/ca end=2050 scenario=urban=2
m hilda/ca protect=amazon         # freeze a region and see what moves instead
```

---

## Checks against the published result

Gross change 1961–2019 comes out at **42.44 M km²**, or 32% of global land. Winkler et al. report ~43 M km² and 32%. That number arrives by a completely independent path — range-read the archive, decode the transition rasters, aggregate in km² with latitude weighting — so agreeing with it validates the pipeline end to end. Net change over the same span is 3.4 M km² by two independent routes (the transition matrix and the state layers), a ratio of 12.5×.

## Two defects in the published archive

Both are handled, quantified and regression-tested rather than papered over.

- **The 2015 state layer is a base map, not an annual state.** It ships under a different filename and does not use the no-data class at all: all 60.5 M pixels that every other year codes 99 (permanent ice) are coded 66 (sparse vegetation). Included, it adds 12.4 M km² of phantom land in a single year — a 9% spike in global land area. It is excluded from ranges (`m hilda/ingest years=2015` still fetches it if you want it) and the series carries a documented one-year hole.
- **The 2015→2014 transition layer has corrupt LZW** from row 10 to row 1808. libtiff rejects the whole file over it. The reader zero-fills only the rows that genuinely will not decode; that band holds 1.75% of global land but ~0.2% of annual turnover.

---

## The console

One HTML file, no build, no dependencies. Everything is drawn on canvases with image smoothing off, which is what makes it look the way it looks.

- **MAP** — the classified world, year scrubber and play button, class solo/hide, hover inspector, click to pin a cell and get its 60-year stack, region jump, and a `1KM` button that pulls the current view at full source resolution
- **TIME** — stacked area by class, indexed view, net change bars, gross-vs-net, and the largest class-to-class transfers
- **AUTOMATA** — run controls, scenario dials per class, simulated vs observed side by side with its own scrubber, the skill scorecard, and the observed transition matrix as a heat grid
- **DATA** — dataset, citation, class table, cube state (including gaps), regions, and every API route

`app/server.py` also proxies `/api/*` to the API, so the page uses one relative URL shape whether it is served on `:50551` or behind the gateway at `/hilda/api` by fleet convention.

---

## CLI

```
m hilda                                  # null call → info()
m hilda/bootstrap                        # states + transitions, ~15 min
m hilda/ingest years=1990-2019           # or a subset; re-runnable, skips what it has
m hilda/status                           # cubes, years, gaps, cached rasters

m hilda/summary                          # what grew, what shrank, gross vs net
m hilda/series region=amazon             # a region's curve
m hilda/areas year=2019                  # one year's totals
m hilda/net y0=1960 y1=2019              # net change per class
m hilda/transitions                      # gross flows, from → to
m hilda/hotspots n=10                    # where the world churned most
m hilda/cell lon=-60 lat=-3              # one cell, every year

m hilda/rates                            # the observed transition matrix
m hilda/ca weight=0.6 scenario=urban=2   # run the automaton
m hilda/calibrate                        # pick the neighbourhood weight
m hilda/validate                         # hindcast scorecard

m hilda/map year=2019 out=/tmp/m.png     # classified world as a PNG
m hilda/window bbox=-63,-10,-58,-5       # 1 km detail for a box
m hilda/change                           # gross turnover map

m hilda/serve                            # API + console under pm2
```

## API

`GET /info /health /classes /regions /status`
`GET /series /areas /net /transitions /hotspots /summary /cell`
`GET /grid.bin /grid.png /layer.png /change.png /window.png`
`GET /ca/run /ca/validate /ca/calibrate /ca/rates /ca/frame.png`

Read-only and unauthenticated: every byte behind it is public CC-BY-4.0 data.

## Layout

```
config.json           ports 50550 (api) / 50551 (app), route /hilda
mod.py                the anchor — CLI surface, serve, register
hildaplus/
  sources.py          dataset constants, class table, regions, cache paths
  remote.py           HTTP-range ZIP reader
  raster.py           GeoTIFF reads: whole, windowed, reduced
  cube.py             every year on one grid, in one file
  series.py           the longitudinal half
  automata.py         the model
  render.py           classified grids, PNGs, the binary payload
api/api.py            FastAPI
app/index.html        the console
app/server.py         static files + API proxy
tests/test_hilda.py   synthetic fixtures throughout; real-data tests skip cleanly
```

State lives in `~/.mod/hilda/` — cubes, cached rasters and the ZIP member index. Nothing is committed.

## Requirements

`numpy`, `Pillow`, `fastapi`, `uvicorn`. Deliberately **not** GDAL or rasterio: the reader is ~200 lines of TIFF parsing and the module installs anywhere numpy does.

## Source

Winkler, K; Fuchs, R; Rounsevell, M D A; Herold, M (2020): *HILDA+ Global Land Use Change between 1960 and 2019* [dataset]. PANGAEA, <https://doi.org/10.1594/PANGAEA.921846> — CC-BY-4.0

Winkler, K; Fuchs, R; Rounsevell, M D A; Herold, M (2021): Global land use changes are four times greater than previously estimated. *Nature Communications* 12, 2501. <https://doi.org/10.1038/s41467-021-22702-2>
