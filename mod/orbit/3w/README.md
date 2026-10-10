# 3w — 3world

**Free 3D models of any city, starting with NYC and Toronto — growing into a
free, open marketplace of 3D assets.**

Every source here is public and keyless. You name a place, 3w geocodes it,
pulls building footprints and heights from OpenStreetMap, and extrudes them
into an OBJ file you can drop into Blender, three.js, Unity, or anything else
that reads a mesh. For NYC and Toronto it also points you at the official,
city-published 3D massing models when you want the real surveyed geometry.

The marketplace half ("3world") starts as a browser over Poly Haven's CC0
model catalog and is meant to grow into a free exchange of 3D assets.

## Quick start

```sh
m 3w                                   # status + what this is
m 3w/cities                            # city shortcuts + official datasets
m 3w/model "Financial District, NYC"   # → ~/.mod/3w/models/financial_district_nyc.obj
m 3w/model "Kensington Market, Toronto"
m 3w/model bbox=-74.016,40.702,-74.004,40.712   # any bbox [w,s,e,n]
m 3w/buildings "Shibuya, Tokyo"        # footprints+heights as GeoJSON (any city)
m 3w/sources toronto                   # official free 3D downloads
m 3w/assets "chair"                    # CC0 models from Poly Haven
m 3w/asset ArmChair_01                 # one asset's download files
m 3w/test                              # offline geometry self-test
```

## How the city models are built

1. **Geocode** — Nominatim turns a place name into a bounding box
   (`where`). A raw `bbox=[w,s,e,n]` skips this step.
2. **Footprints** — Overpass fetches every `building` way/relation in the
   box. The box is clamped to ~2 km a side (`cap_km=`) so "Toronto" doesn't
   try to download a metro area; responses are cached a day under
   `~/.mod/3w/cache/`.
3. **Heights** — per building: the OSM `height` tag, else
   `building:levels × 3 m`, else 8 m.
4. **Extrude** — footprints are projected to local meters (Z-up, ground at
   0), walls become quads, roofs are ear-clip triangulated, and the mesh is
   written as OBJ to `~/.mod/3w/models/`.

OSM data is © OpenStreetMap contributors (ODbL) — the attribution line is
written into every exported file.

## Free city 3D sources

| City | Official dataset | Formats |
| --- | --- | --- |
| NYC | [NYC 3D Building Massing Model](https://www.nyc.gov/site/planning/data-maps/open-data/dwn-nyc-3d-model-download.page) (DCP) | CityGML, Multipatch |
| Toronto | [3D Massing](https://open.toronto.ca/dataset/3d-massing/) (Open Government Licence) | OBJ, FBX, CityGML, SKP, 3DS |
| Any city | OpenStreetMap via Overpass (what `model()` extrudes) · [Overture Maps](https://overturemaps.org) buildings (GeoParquet, with heights) | — |

Related modules: `orbit/nyc` (full NYC GIS) and `orbit/tdot` (Toronto GIS,
already has 3D notes) cover the 2D/data side of these cities.

## Roadmap (3world)

- **glTF/GLB export** alongside OBJ, and terrain under the buildings.
- **Official-model fetchers** — download + convert the NYC and Toronto
  massing tiles directly instead of just linking them.
- **More CC0/CC-BY sources** — Smithsonian 3D, Sketchfab's CC search,
  Kenney/Quaternius packs — one unified `assets` search.
- **The marketplace** — publish your own models through the store protocol,
  free to list and free to take; city extracts become just another asset.
- **Viewer app** — a three.js browser app on the `app_port` with an
  in-browser preview of any model or city extract.

## Files & state

```
mod.py        anchor — all functions, stdlib-only, no server required
config.json   module manifest (port 51220 reserved; no server yet)
~/.mod/3w/    models/ (exported OBJ) · cache/ (Overpass + geocode, 1-day TTL)
```
