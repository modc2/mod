'use client'

import { useEffect, useRef } from 'react'
import maplibregl, { Map as MLMap } from 'maplibre-gl'
import type { Catalog, Choropleth, LayerDef } from '@/lib/api'
import {
  classExpression, divergingExpression, seriesColor, sizeExpression,
  stepExpression, type MapPalette,
} from '@/lib/palette'
import { usePalette } from './ThemeProvider'
import type { BasemapId, ThemeBase } from '@/lib/theme'

/** Bounding box of the amalgamated city, used to frame the opening view. */
const TORONTO_BOUNDS: [[number, number], [number, number]] = [[-79.65, 43.56], [-79.10, 43.87]]

/**
 * Basemap styles. All raster, all key-free: CARTO's free tiles for the muted
 * cartography a data map needs, and OpenStreetMap's own tiles for the "show me
 * the actual streets" case. Attribution is mandatory and is baked into each
 * source rather than left to the caller.
 */
const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
const CARTO_ATTR = `${OSM_ATTR} © <a href="https://carto.com/attributions">CARTO</a>`

const BASEMAPS: Record<BasemapId, { tiles: string[]; attribution: string }> = {
  dark: {
    tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
    attribution: CARTO_ATTR,
  },
  light: {
    tiles: ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png'],
    attribution: CARTO_ATTR,
  },
  streets: {
    tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    attribution: OSM_ATTR,
  },
}

function styleFor(basemap: BasemapId): any {
  const b = BASEMAPS[basemap]
  return {
    version: 8,
    // A raster-only style ships no glyphs, and any symbol layer (the station
    // labels) needs them. MapLibre's own free font endpoint serves Noto Sans.
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources: {
      base: { type: 'raster', tiles: b.tiles, tileSize: 256, attribution: b.attribution },
    },
    layers: [{ id: 'base', type: 'raster', source: 'base' }],
  }
}

type Props = {
  catalog: Catalog | null
  active: string[]
  opacity: Record<string, number>
  crime: Choropleth | null
  crimeMetric: string
  layerData: Record<string, GeoJSON.FeatureCollection>
  basemap: BasemapId
  /** The active theme's surface. Picks which ramps the overlays are drawn with. */
  base: ThemeBase
  flyTo: { lng: number; lat: number; zoom?: number; nonce: number } | null
  onFeatureClick: (payload: { layerId: string; props: Record<string, any> } | null) => void
  onMapReady: (map: MLMap) => void
}

export default function MapView({
  catalog, active, opacity, crime, crimeMetric, layerData,
  basemap, base, flyTo, onFeatureClick, onMapReady,
}: Props) {
  // Straight from the provider, not rebuilt from `base` — the surface alone
  // can't say which of the ten themes is on, and the magnitude ramps differ
  // per theme. Taking it here is what makes a theme change repaint the map.
  const pal = usePalette()
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<MLMap | null>(null)
  const ready = useRef(false)
  // Kept in a ref so the click handler, registered once, always sees the
  // current draw order without being torn down and rebuilt on every toggle.
  const clickOrder = useRef<string[]>([])
  // The map's `load` and `styledata` handlers are registered once, so they'd
  // capture the first render's `redraw`. Routing through a ref that every
  // render refreshes means "the map just became ready" always redraws with the
  // data that exists *now* — without this, layers fetched before the style
  // finished loading are never drawn at all.
  const redrawRef = useRef<() => void>(() => {})

  // ── init ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (map.current || !container.current) return
    const m = new maplibregl.Map({
      container: container.current,
      style: styleFor(basemap),
      center: [-79.3832, 43.7],
      zoom: 10.6,
      maxZoom: 18,
      minZoom: 8,
      attributionControl: false,
      // The basemap is raster and the overlays are flat 2-D data; disabling
      // pitch keeps polygon fills legible and avoids a tilted-map trap where
      // the choropleth reads as terrain.
      pitchWithRotate: false,
      dragRotate: false,
    })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    m.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left')
    m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')

    m.on('load', () => {
      ready.current = true
      m.resize()
      // Frame the whole city regardless of the window's aspect ratio; a fixed
      // centre+zoom leaves a wide window showing half of Lake Ontario.
      m.fitBounds(TORONTO_BOUNDS, { padding: { top: 80, bottom: 40, left: 300, right: 60 }, duration: 0 })
      onMapReady(m)
      redrawRef.current()
    })
    m.on('click', (e) => {
      const ids = clickOrder.current.filter((id) => m.getLayer(id))
      if (!ids.length) return onFeatureClick(null)
      const hits = m.queryRenderedFeatures(e.point, { layers: ids })
      if (!hits.length) return onFeatureClick(null)
      const hit = hits[0]
      onFeatureClick({
        layerId: (hit.layer.id.split('--')[0]) || hit.layer.id,
        props: hit.properties || {},
      })
    })
    map.current = m
    return () => {
      m.remove()
      map.current = null
      ready.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── basemap switching ───────────────────────────────────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready.current) return
    ready.current = false
    m.setStyle(styleFor(basemap))
    // setStyle drops every custom source and layer; `styledata` fires once the
    // new style is in place, which is when they can be added back.
    m.once('styledata', () => {
      ready.current = true
      redrawRef.current()
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basemap])

  // ── redraw on any data/selection change ─────────────────────────────────
  // `pal` rather than `base`: two themes can share a surface (GLASS and MATRIX
  // are both dark) but not their ramps, and it is the ramp that the paint
  // expressions are built from. Keying on the surface repainted neither.
  useEffect(() => {
    redraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, active, opacity, crime, crimeMetric, layerData, pal])

  useEffect(() => {
    if (!flyTo || !map.current) return
    map.current.flyTo({ center: [flyTo.lng, flyTo.lat], zoom: flyTo.zoom ?? 14, duration: 900 })
  }, [flyTo])

  function clearAll(m: MLMap) {
    const style = m.getStyle()
    // Layers first, then sources — MapLibre refuses to drop a source that any
    // layer still references. Our layers are named `<layerId>--<kind>` while
    // our sources are named `tdot-<layerId>`, so the two need different tests;
    // matching layers against the source prefix silently removes nothing.
    for (const l of style.layers || []) {
      if (l.id !== 'base') m.removeLayer(l.id)
    }
    for (const s of Object.keys(style.sources || {})) {
      if (s !== 'base') m.removeSource(s)
    }
  }

  redrawRef.current = redraw

  function redraw() {
    const m = map.current
    if (!m || !ready.current || !catalog) return
    clearAll(m)
    const order: string[] = []

    const defs = new Map(catalog.layers.map((l) => [l.id, l]))
    // Draw order: fills at the bottom, then lines, then points on top, so the
    // choropleth never buries the subway and stations stay clickable.
    const rank = (l: LayerDef) =>
      l.kind === 'choropleth' || l.kind === 'area' || l.kind === 'polygon' ? 0
        : l.kind === 'heatmap' ? 1
        : l.kind === 'outline' || l.kind === 'line' ? 2 : 3
    const ordered = active
      .map((id) => defs.get(id))
      .filter((l): l is LayerDef => !!l)
      .sort((a, b) => rank(a) - rank(b))

    for (const def of ordered) {
      const alpha = opacity[def.id] ?? 1
      try {
        if (def.id === 'crime') {
          if (crime) order.push(...addChoropleth(m, crime, crimeMetric, alpha, pal))
        } else {
          const data = layerData[def.id]
          if (data) order.push(...addOverlay(m, def, data, alpha, pal))
        }
      } catch (err) {
        // A single malformed layer must not take the whole map down.
        console.error(`tdot: failed to draw ${def.id}`, err)
      }
    }
    // Topmost layer first, so a click on a station beats the polygon under it.
    clickOrder.current = order.reverse()
  }

  return <div ref={container} className="absolute inset-0" />
}

// ── choropleth ────────────────────────────────────────────────────────────

function addChoropleth(m: MLMap, fc: Choropleth, metric: string, alpha: number,
                       pal: MapPalette): string[] {
  const src = 'tdot-crime'
  m.addSource(src, { type: 'geojson', data: fc as any })
  const stops = fc.breaks?.stops ?? []
  const diverging = metric === 'change'

  const color = diverging
    ? divergingExpression(metric, Math.max(
        Math.abs(fc.breaks?.min ?? 0), Math.abs(fc.breaks?.max ?? 0)), pal.DIVERGING)
    : stepExpression(metric, stops, pal.SEQUENTIAL)

  // Areas where the metric is unreportable (a thin prior sample for `change`)
  // are drawn as a flat grey rather than the ramp's lowest class — "no data"
  // and "least crime" must not look the same.
  const fill: any = ['case', ['==', ['get', metric], null], pal.NO_DATA, color]

  m.addLayer({
    id: 'crime--fill',
    type: 'fill',
    source: src,
    paint: { 'fill-color': fill, 'fill-opacity': 0.78 * alpha },
  })
  m.addLayer({
    id: 'crime--line',
    type: 'line',
    source: src,
    paint: {
      'line-color': pal.HAIRLINE,
      'line-width': 0.6,
    },
  })
  return ['crime--fill']
}

// ── overlays ──────────────────────────────────────────────────────────────

function addOverlay(m: MLMap, def: LayerDef, data: GeoJSON.FeatureCollection,
                    alpha: number, pal: MapPalette): string[] {
  const src = `tdot-${def.id}`
  m.addSource(src, { type: 'geojson', data: data as any })
  const color = pal.LAYER_COLOR[def.id] || pal.POINT_DEFAULT
  const ids: string[] = []

  const add = (layer: any) => {
    m.addLayer(layer)
    ids.push(layer.id)
  }

  switch (def.id) {
    case 'ttc_lines':
      // Line colour comes from the TTC's own identity — riders read the
      // network by colour, so this is a layer where the palette is inherited.
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['coalesce', ['get', 'color'], pal.LINE_DEFAULT],
          'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.6, 13, 3.4, 16, 6],
          'line-opacity': 0.95 * alpha,
        },
      })
      return ids

    case 'streetcars':
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': color,
          'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1, 13, 2, 16, 4],
          'line-opacity': 0.85 * alpha,
        },
      })
      return ids

    case 'subway_stations':
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2.2, 14, 5, 17, 9],
          'circle-color': color,
          // A 2px surface ring keeps overlapping stations countable.
          'circle-stroke-width': 1.4,
          'circle-stroke-color': pal.HALO,
          'circle-opacity': alpha,
        },
      })
      add({
        id: `${def.id}--label`, type: 'symbol', source: src,
        minzoom: 12.5,
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 11,
          'text-offset': [0, 1.1],
          'text-anchor': 'top',
          'text-font': ['Noto Sans Regular'],
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': pal.LABEL,
          'text-halo-color': pal.HALO,
          'text-halo-width': 1.4,
        },
      })
      return [`${def.id}--circle`]

    case 'cycling_network':
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        layout: { 'line-cap': 'round' },
        paint: {
          'line-color': color,
          // Protected routes are the ones riders plan around — give them weight.
          'line-width': ['interpolate', ['linear'], ['zoom'],
            10, ['case', ['==', ['get', 'cls'], 'I'], 1.2, 0.6],
            15, ['case', ['==', ['get', 'cls'], 'I'], 3.4, 1.6]],
          'line-opacity': 0.85 * alpha,
        },
      })
      return ids

    case 'green_spaces':
      add({
        id: `${def.id}--fill`, type: 'fill', source: src,
        paint: { 'fill-color': color, 'fill-opacity': 0.42 * alpha },
      })
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        paint: { 'line-color': color, 'line-width': 0.6, 'line-opacity': 0.8 * alpha },
      })
      return [`${def.id}--fill`]

    case 'collisions':
      add({
        id: `${def.id}--heat`, type: 'heatmap', source: src,
        maxzoom: 15,
        paint: {
          'heatmap-weight': ['interpolate', ['linear'],
            ['+', ['coalesce', ['get', 'serious'], 0],
                  ['*', 5, ['coalesce', ['get', 'killed'], 0]]],
            0, 0.2, 5, 1],
          // Tuned down at city zoom: 7.5k collisions at full intensity paints
          // the whole city one colour and buries every layer beneath it.
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 9, 0.5, 15, 2.4],
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            ...pal.HEAT.flatMap(([stop, c]) => [stop, c])] as any,
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 9, 5, 15, 26],
          'heatmap-opacity': 0.62 * alpha,
        },
      })
      // Past the heatmap's maxzoom the individual collisions become inspectable.
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        minzoom: 14,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 2.5, 17, 6],
          'circle-color': ['case', ['>', ['coalesce', ['get', 'killed'], 0], 0],
            pal.FATAL, color],
          'circle-opacity': 0.85 * alpha,
          'circle-stroke-width': 0.8,
          'circle-stroke-color': pal.HALO,
        },
      })
      return [`${def.id}--circle`]

    case 'incidents': {
      const match: any[] = ['match', ['get', 'cat']]
      Object.entries(pal.CATEGORY_COLOR).forEach(([k, c]) => match.push(k, c))
      match.push(pal.POINT_DEFAULT)
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 1.6, 14, 3.4, 17, 7],
          'circle-color': match as any,
          'circle-opacity': 0.8 * alpha,
          'circle-stroke-width': 0.4,
          'circle-stroke-color': pal.HALO,
        },
      })
      return ids
    }

    case 'apartments':
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          // Area-proportional: radius scales with √units, so a building twice
          // as big draws twice the ink, not four times.
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            10, ['*', 0.9, ['sqrt', ['/', ['coalesce', ['get', 'units'], 10], 20]]],
            15, ['*', 3.5, ['sqrt', ['/', ['coalesce', ['get', 'units'], 10], 20]]]],
          // Low-scoring buildings surface in the warning hue.
          'circle-color': ['step', ['coalesce', ['get', 'score'], 100],
            pal.SCORE[0], 65, pal.SCORE[1], 80, color],
          'circle-opacity': 0.7 * alpha,
          'circle-stroke-width': 1,
          'circle-stroke-color': pal.HALO,
        },
      })
      return ids

    case 'municipalities':
    case 'neighbourhoods':
    case 'wards':
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        paint: {
          'line-color': color,
          'line-width': def.id === 'municipalities' ? 1.6 : def.id === 'wards' ? 1.0 : 0.7,
          'line-opacity': 0.75 * alpha,
        },
      })
      return ids

    default:
      // Everything else is drawn from its `style` block — the real-estate and
      // housing layers, and anything a person adds from the portal at runtime.
      // Nothing here knows the layer's name, which is the point: a dataset
      // added from the chat or the Add-data panel renders properly on arrival
      // instead of falling back to an anonymous dot.
      return addFromStyle(m, def, data, alpha, pal, add, ids)
  }
}

/**
 * How far from zero a diverging field has to run to saturate the ramp.
 *
 * For a residual — how far a building is from what the model predicts — the
 * meaningful unit is the model's own typical error: twice it is "well outside
 * what this model gets wrong", and that is where the colour should top out.
 * Falling back to the extreme value would put a single -41 building at full
 * saturation and leave every other building sitting in the neutral middle.
 */
function divergingScale(data: GeoJSON.FeatureCollection): number {
  const meta = (data as any).meta
  if (typeof meta?.typical_error === 'number') return meta.typical_error * 2
  const stops: number[] = (data as any).breaks?.stops ?? []
  return stops.length ? Math.max(...stops.map(Math.abs)) : 1
}

/**
 * The diverging ramp, oriented by what the metric's negative arm *means*.
 *
 * The palette's ramp runs blue→red because it was drawn for crime change,
 * where up is bad. A residual is the other way round: below zero is the
 * building doing worse than its peers, so `down_bad` flips it and red lands
 * where it belongs.
 */
function divergingRamp(style: Record<string, any>, pal: MapPalette): string[] {
  return style.diverging === 'down_bad' ? [...pal.DIVERGING].reverse() : pal.DIVERGING
}

/**
 * Generic renderer: geometry decides the mark, `style` decides how it is
 * encoded. Supported encodings are `color_by` + `classes` (categorical),
 * `color_by` + `diverging` (a signed metric centred on zero), `size_by` +
 * `size_norm` (√-scaled radius), and a graduated fill for the per-ward `area`
 * layers, which carry their own class breaks.
 */
function addFromStyle(m: MLMap, def: LayerDef, data: GeoJSON.FeatureCollection,
                      alpha: number, pal: MapPalette,
                      add: (l: any) => void, ids: string[]): string[] {
  const src = `tdot-${def.id}`
  const style = def.style || {}
  const base = pal.LAYER_COLOR[def.id] || seriesColor(def.id, pal)

  if (def.kind === 'area') {
    // A count-per-ward map. Its breaks travel with the data because only the
    // server has seen every value; the alternate ramp keeps it distinguishable
    // from the crime choropleth when both are on.
    const breaks = (data as any).breaks
    const metric = style.color_by || breaks?.metric || 'count'
    const color = breaks?.stops?.length
      ? stepExpression(metric, breaks.stops, pal.SEQUENTIAL_ALT)
      : base
    add({
      id: `${def.id}--fill`, type: 'fill', source: src,
      paint: { 'fill-color': color as any, 'fill-opacity': 0.7 * alpha },
    })
    add({
      id: `${def.id}--line`, type: 'line', source: src,
      paint: { 'line-color': pal.HAIRLINE, 'line-width': 0.6 },
    })
    return [`${def.id}--fill`]
  }

  const color: any = style.color_by && style.classes
    ? classExpression(style.color_by, style.classes, pal, base)
    : style.color_by && style.diverging
    ? divergingExpression(style.color_by, divergingScale(data), divergingRamp(style, pal))
    : base

  if (def.geometry === 'polygon') {
    add({
      id: `${def.id}--fill`, type: 'fill', source: src,
      paint: { 'fill-color': color, 'fill-opacity': 0.42 * alpha },
    })
    add({
      id: `${def.id}--line`, type: 'line', source: src,
      paint: { 'line-color': color, 'line-width': 0.6, 'line-opacity': 0.8 * alpha },
    })
    return [`${def.id}--fill`]
  }

  if (def.geometry === 'line') {
    add({
      id: `${def.id}--line`, type: 'line', source: src,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': color,
        'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.8, 15, 2.4],
        'line-opacity': 0.9 * alpha,
      },
    })
    return ids
  }

  const radius: any = style.size_by
    ? sizeExpression(style.size_by, Number(style.size_norm) || 100)
    : ['interpolate', ['linear'], ['zoom'],
       10, (Number(style.radius) || 3.2) * 0.55, 14, Number(style.radius) || 3.2,
       17, (Number(style.radius) || 3.2) * 2]
  add({
    id: `${def.id}--circle`, type: 'circle', source: src,
    paint: {
      'circle-radius': radius,
      'circle-color': color,
      'circle-opacity': 0.75 * alpha,
      'circle-stroke-width': 0.6,
      'circle-stroke-color': pal.HALO,
    },
  })
  return ids
}
