'use client'

import { useEffect, useRef } from 'react'
import maplibregl, { Map as MLMap } from 'maplibre-gl'
import type { Catalog, Choropleth, LayerDef } from '@/lib/api'
import { NARROW } from '@/lib/layout'
import { matchExpression, matchesHighlight, type AgentOverlay, type ValueFilter } from '@/lib/scene'
import {
  DIVERGING, HEAT, LAYER_COLOR, NEWS_TOPIC, NO_DATA, SEQUENTIAL, SPEED_BAND,
  ZONE_COLOR, divergingExpression, stepExpression,
} from '@/lib/palette'

export type Basemap = 'dark' | 'light' | 'streets' | 'earth'

/** Bounding box of the five boroughs, used to frame the opening view. */
const NYC_BOUNDS: [[number, number], [number, number]] = [[-74.30, 40.47], [-73.68, 40.93]]


/**
 * Framing padding, in pixels. On a wide screen the left rail sits over the map
 * and the boroughs have to clear it; on a phone the panels are drawers, so
 * reserving 300px of gutter would shrink the fit until the map showed half of
 * Pennsylvania. Padding must always stay well under half the viewport.
 */
function framePadding(w: number, h: number) {
  return w < NARROW
    ? { top: Math.min(72, h * 0.12), bottom: Math.min(96, h * 0.14), left: 16, right: 16 }
    : { top: 80, bottom: 40, left: 300, right: 60 }
}

/**
 * Basemap styles: OpenFreeMap's key-free hosted vector styles. (CARTO's
 * "free" raster tiles started shipping an API KEY REQUIRED watermark burned
 * into the imagery in 2026 — status codes stay 200, only your eyes catch it.)
 * The styles bring their own glyphs and attribution.
 */
const BASEMAPS: Record<Exclude<Basemap, 'earth'>, string> = {
  dark: 'https://tiles.openfreemap.org/styles/dark',
  light: 'https://tiles.openfreemap.org/styles/positron',
  streets: 'https://tiles.openfreemap.org/styles/liberty',
}

/**
 * EARTH is the one three-dimensional view, and it is built here rather than
 * fetched: Esri World Imagery (keyless, but the attribution string is a
 * licence condition) under extruded OpenMapTiles buildings, on a globe
 * projection so zooming out shows the planet with an atmosphere instead of a
 * grey void. The glyphs endpoint matches the OpenFreeMap styles so the
 * overlay label layers keep finding Noto Sans. The buildings layer id has no
 * `--` and its source no `nyc-` prefix, which is what keeps `clearAll` off it.
 */
const EARTH_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  projection: { type: 'globe' },
  sky: {
    'sky-color': '#0b1526',
    'horizon-color': '#7d9cc0',
    'fog-color': '#0a0e14',
    'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 8, 0.5, 11, 0] as any,
  },
  light: { anchor: 'viewport', color: '#ffffff', intensity: 0.4 },
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: {
    satellite: {
      type: 'raster',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256,
      maxzoom: 19,
      attribution: 'Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    },
    openmaptiles: { type: 'vector', url: 'https://tiles.openfreemap.org/planet' },
  },
  layers: [
    { id: 'satellite', type: 'raster', source: 'satellite' },
    {
      id: 'earth-buildings',
      type: 'fill-extrusion',
      source: 'openmaptiles',
      'source-layer': 'building',
      minzoom: 13.5,
      filter: ['!=', ['get', 'hide_3d'], true],
      paint: {
        'fill-extrusion-color': '#b9bec6',
        'fill-extrusion-opacity': 0.75,
        // Heights grow in over a zoom level rather than popping; the tiles
        // only carry render_height from ~z14, so earlier zooms get a stub.
        'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'],
          13.5, 0,
          15, ['coalesce', ['get', 'render_height'], 10]],
        'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
      },
    },
  ],
}

/** True on a touch screen, where hit-testing needs a bigger target. */
function coarsePointer(): boolean {
  return typeof window !== 'undefined'
    && window.matchMedia('(pointer: coarse)').matches
}

function styleFor(basemap: Basemap): string | maplibregl.StyleSpecification {
  return basemap === 'earth' ? EARTH_STYLE : BASEMAPS[basemap]
}

type Props = {
  catalog: Catalog | null
  active: string[]
  opacity: Record<string, number>
  housing: Choropleth | null
  housingMetric: string
  population: Choropleth | null
  populationMetric: string
  layerData: Record<string, GeoJSON.FeatureCollection>
  basemap: Basemap
  flyTo: { lng: number; lat: number; zoom?: number; nonce: number } | null
  onFeatureClick: (payload: { layerId: string; props: Record<string, any> } | null) => void
  onMapReady: (map: MLMap) => void
  /** What the chat agent drew: any open dataset, outlined areas, a range filter. */
  agentOverlay?: AgentOverlay | null
  highlight?: { names: string[]; nonce: number }
  valueFilter?: ValueFilter
  /** Show only areas matching these names / codes / boroughs. */
  only?: string[]
  /** Fit the camera to these areas (once per nonce, as soon as they are drawn). */
  frame?: { names: string[]; nonce: number }
}

export default function MapView({
  catalog, active, opacity, housing, housingMetric, population, populationMetric, layerData,
  basemap, flyTo, onFeatureClick, onMapReady,
  agentOverlay = null, highlight = { names: [], nonce: 0 }, valueFilter = null, only = [],
  frame = { names: [], nonce: 0 },
}: Props) {
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
      center: [-73.9712, 40.7128],
      zoom: 10.2,
      maxZoom: 18,
      minZoom: 8,
      attributionControl: false,
      // The overlays are flat 2-D data, so on the flat basemaps rotation and
      // pitch stay off — a tilted choropleth reads as terrain. The handlers
      // are only switched on while the EARTH view is up (see the basemap
      // effect); pitchWithRotate is a constructor-only option, so it is set
      // here and stays inert until dragRotate is enabled.
      pitchWithRotate: true,
      dragRotate: false,
      maxPitch: 75,
    })
    // On a phone, pinch is the zoom control and the bottom-left corner is
    // wanted for the legend chip, so the map keeps only its attribution.
    const narrow = (container.current?.clientWidth || window.innerWidth) < NARROW
    if (!narrow) {
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
      m.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-left')
    }
    m.addControl(new maplibregl.AttributionControl({ compact: true }),
                 narrow ? 'bottom-right' : 'bottom-left')
    // Two-finger rotation is easy to trigger by accident while pinching, and a
    // rotated choropleth reads as terrain. Both come back in the EARTH view.
    m.touchZoomRotate.disableRotation()
    m.touchPitch.disable()

    m.on('load', () => {
      ready.current = true
      m.resize()
      // Frame the five boroughs regardless of the window's aspect ratio; a
      // fixed centre+zoom leaves a wide window showing half of New Jersey.
      const c = m.getContainer()
      m.fitBounds(NYC_BOUNDS, {
        padding: framePadding(c.clientWidth, c.clientHeight), duration: 0,
      })
      onMapReady(m)
      redrawRef.current()
    })
    m.on('click', (e) => {
      const ids = clickOrder.current.filter((id) => m.getLayer(id))
      if (!ids.length) return onFeatureClick(null)
      // A fingertip is nowhere near as precise as a cursor, so a tap is matched
      // against a box rather than a single pixel — but the box has to follow
      // the marks, which grow and thin out with zoom. Zoomed out, a station is
      // a 2px dot among five hundred others a few pixels apart: a generous box
      // there would answer every tap in Manhattan with "some station" and the
      // neighbourhood underneath could never be selected, so the tolerance
      // goes away and taps fall through to the choropleth. Zoomed in, the dots
      // are separated and worth aiming at, and the finger gets its allowance.
      const touch = coarsePointer()
      const grown = m.getZoom() >= 12
      const tiers: [string[], number][] = [
        [ids.filter((id) => id.endsWith('--circle')), touch ? (grown ? 14 : 0) : grown ? 6 : 2],
        [ids.filter((id) => id.endsWith('--line')), touch ? (grown ? 6 : 0) : 2],
        [ids.filter((id) => !/--(circle|line)$/.test(id)), 0],
      ]
      for (const [layers, r] of tiers) {
        if (!layers.length) continue
        const at = r === 0
          ? e.point
          : [[e.point.x - r, e.point.y - r], [e.point.x + r, e.point.y + r]]
        // Within a tier the layers keep their draw order, topmost first.
        const hits = m.queryRenderedFeatures(at as any, { layers })
        if (!hits.length) continue
        const hit = hits[0]
        return onFeatureClick({
          layerId: (hit.layer.id.split('--')[0]) || hit.layer.id,
          props: hit.properties || {},
        })
      }
      onFeatureClick(null)
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
    // EARTH is the three-dimensional view: tilt the camera in and hand over
    // the rotate/pitch gestures (right-drag, or two fingers). Leaving it lays
    // the camera flat and faces north again, so the flat basemaps stay the
    // legible 2-D surfaces the rest of the UI assumes.
    if (basemap === 'earth') {
      m.dragRotate.enable()
      m.touchZoomRotate.enableRotation()
      m.touchPitch.enable()
      m.easeTo({ pitch: 55, duration: 1200 })
    } else {
      m.dragRotate.disable()
      m.touchZoomRotate.disableRotation()
      m.touchPitch.disable()
      if (m.getPitch() !== 0 || m.getBearing() !== 0) {
        m.easeTo({ pitch: 0, bearing: 0, duration: 600 })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basemap])

  // ── redraw on any data/selection change ─────────────────────────────────
  useEffect(() => {
    redraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, active, opacity, housing, housingMetric, population, populationMetric, layerData,
      agentOverlay, highlight, valueFilter, only])

  // Outlining or keeping areas without an explicit camera move frames them.
  // The choropleth often lands after the directive (a new metric is a new
  // query), so the frame waits for data and is spent once it has been used.
  const framed = useRef(0)
  useEffect(() => {
    const m = map.current
    if (!m || !frame.names.length || framed.current === frame.nonce) return
    const b = new maplibregl.LngLatBounds()
    for (const fc of [housing, population, agentOverlay?.data]) {
      for (const f of fc?.features ?? []) {
        if (f.geometry?.type !== 'Point'
            && matchesHighlight((f.properties as any) || {}, frame.names)) extend(b, f.geometry)
      }
    }
    if (b.isEmpty()) return
    framed.current = frame.nonce
    const c = m.getContainer()
    m.fitBounds(b, { padding: framePadding(c.clientWidth, c.clientHeight), duration: 900, maxZoom: 14 })
  }, [frame, housing, population, agentOverlay])

  useEffect(() => {
    if (!flyTo || !map.current) return
    map.current.flyTo({ center: [flyTo.lng, flyTo.lat], zoom: flyTo.zoom ?? 14, duration: 900 })
  }, [flyTo])

  function clearAll(m: MLMap) {
    const style = m.getStyle()
    // Layers first, then sources — MapLibre refuses to drop a source that any
    // layer still references. Only OUR layers and sources go: ours are named
    // `<layerId>--<kind>` over sources named `nyc-<layerId>`. The basemap is a
    // full vector style now, dozens of layers of its own — "everything except
    // `base`" would strip the entire city off the screen.
    for (const l of style.layers || []) {
      if (l.id.includes('--')) m.removeLayer(l.id)
    }
    for (const s of Object.keys(style.sources || {})) {
      if (s.startsWith('nyc-')) m.removeSource(s)
    }
  }

  redrawRef.current = redraw

  function redraw() {
    const m = map.current
    if (!m || !ready.current || !catalog) return
    clearAll(m)
    const order: string[] = []

    const defs = new Map(catalog.layers.map((l) => [l.id, l]))
    // Draw order: fills at the bottom, then lines, then points on top, so a
    // choropleth never buries the subway and stations stay clickable.
    const rank = (l: LayerDef) =>
      l.kind === 'choropleth' || l.kind === 'polygon' ? 0
        : l.kind === 'heatmap' ? 1
        : l.kind === 'outline' || l.kind === 'line' ? 2 : 3
    const ordered = active
      .map((id) => defs.get(id))
      .filter((l): l is LayerDef => !!l)
      .sort((a, b) => rank(a) - rank(b))

    for (const def of ordered) {
      const alpha = opacity[def.id] ?? 1
      try {
        if (def.id === 'housing_prices') {
          if (housing) order.push(...addChoropleth(m, housing, housingMetric, alpha))
        } else if (def.id === 'population') {
          if (population) order.push(...addChoropleth(m, population, populationMetric, alpha, 'population'))
        } else {
          const data = layerData[def.id]
          if (data) order.push(...addOverlay(m, def, data, alpha))
        }
      } catch (err) {
        // A single malformed layer must not take the whole map down.
        console.error(`nyc: failed to draw ${def.id}`, err)
      }
    }
    try {
      order.push(...drawAgent(m, { overlay: agentOverlay, highlight: highlight.names,
        valueFilter, only, metric: housingMetric, sources: [housing, population] }))
    } catch (err) {
      console.error('nyc: failed to draw the agent view', err)
    }
    // Topmost layer first, so a click on a station beats the polygon under it.
    clickOrder.current = order.reverse()
  }

  return <div ref={container} className="absolute inset-0" />
}

// ── choropleth ────────────────────────────────────────────────────────────

function addChoropleth(m: MLMap, fc: Choropleth, metric: string, alpha: number,
                       id = 'housing_prices'): string[] {
  const src = `nyc-${id}`
  m.addSource(src, { type: 'geojson', data: fc as any })
  const stops = fc.breaks?.stops ?? []
  const diverging = metric === 'price_change'

  const color = diverging
    ? divergingExpression(metric, Math.max(
        Math.abs(fc.breaks?.min ?? 0), Math.abs(fc.breaks?.max ?? 0)))
    : stepExpression(metric, stops, SEQUENTIAL)

  // Areas with no qualifying sales are drawn as a flat grey rather than the
  // ramp's lowest class — "no data" and "cheapest" must not look the same.
  const fill: any = ['case', ['==', ['get', metric], null], NO_DATA, color]

  m.addLayer({
    id: `${id}--fill`,
    type: 'fill',
    source: src,
    paint: { 'fill-color': fill, 'fill-opacity': 0.78 * alpha },
  })
  m.addLayer({
    id: `${id}--line`,
    type: 'line',
    source: src,
    paint: {
      'line-color': 'rgba(255,255,255,0.22)',
      'line-width': 0.6,
    },
  })
  return [`${id}--fill`]
}

// ── overlays ──────────────────────────────────────────────────────────────

function addOverlay(m: MLMap, def: LayerDef, data: GeoJSON.FeatureCollection,
                    alpha: number): string[] {
  const src = `nyc-${def.id}`
  m.addSource(src, { type: 'geojson', data: data as any })
  const color = LAYER_COLOR[def.id] || '#3987e5'
  const ids: string[] = []

  const add = (layer: any) => {
    m.addLayer(layer)
    ids.push(layer.id)
  }

  switch (def.id) {
    case 'subway_lines':
      // Route colour comes from the MTA's own feed — riders read the network
      // by colour, so this is the one layer where the palette is inherited.
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['coalesce', ['get', 'color'], '#8b93a7'],
          'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.4, 13, 3, 16, 6],
          'line-opacity': 0.95 * alpha,
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
          'circle-stroke-color': '#000000',
          'circle-opacity': alpha,
        },
      })
      add({
        id: `${def.id}--label`, type: 'symbol', source: src,
        minzoom: 13.5,
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 11,
          'text-offset': [0, 1.1],
          'text-anchor': 'top',
          'text-font': ['Noto Sans Regular'],
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#e6e8ee',
          'text-halo-color': '#000000',
          'text-halo-width': 1.4,
        },
      })
      return [`${def.id}--circle`]

    case 'subway_ridership':
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          // Area-proportional: radius scales with √ridership, so a station
          // twice as busy draws twice the ink, not four times.
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            10, ['*', 0.5, ['sqrt', ['/', ['coalesce', ['get', 'riders'], 0], 40000]]],
            14, ['*', 2.2, ['sqrt', ['/', ['coalesce', ['get', 'riders'], 0], 40000]]]],
          'circle-color': color,
          'circle-opacity': 0.62 * alpha,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#000000',
        },
      })
      return ids

    case 'bike_routes':
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        layout: { 'line-cap': 'round' },
        paint: {
          'line-color': color,
          // Protected paths are the ones riders plan around — give them weight.
          'line-width': ['interpolate', ['linear'], ['zoom'],
            10, ['case', ['==', ['get', 'cls'], 'I'], 1.2, 0.6],
            15, ['case', ['==', ['get', 'cls'], 'I'], 3.4, 1.6]],
          'line-opacity': 0.85 * alpha,
        },
      })
      return ids

    case 'traffic_speeds': {
      // Casing under the line: a 2px red thread over a dark basemap beside an
      // amber one is hard to tell apart, and the jams are the whole point.
      const band: any[] = ['match', ['get', 'band']]
      Object.entries(SPEED_BAND).forEach(([k, c]) => band.push(k, c))
      band.push('#8b93a7')
      add({
        id: `${def.id}--case`, type: 'line', source: src,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#000000',
          'line-width': ['interpolate', ['linear'], ['zoom'], 9, 3.2, 13, 6, 16, 10],
          'line-opacity': 0.55 * alpha,
        },
      })
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': band as any,
          'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1.8, 13, 3.6, 16, 6.5],
          'line-opacity': 0.95 * alpha,
        },
      })
      return [`${def.id}--line`]
    }

    case 'traffic_volume':
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          // √-scaled like the other graduated circles, against a 100k/day
          // reference — the busiest locations in the file are the parkways
          // and the East River bridges at roughly that.
          //
          // Floored, unlike the others: DOT's counts run over three orders of
          // magnitude, so a pure √ scale drew the quiet two-thirds of them at
          // under 2px — invisible, and below the map's own hit tolerance, so
          // they could not be clicked open at all. The floor costs the low end
          // its proportionality and buys back its existence.
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            10, ['max', 2.6,
              ['*', 5.5, ['sqrt', ['/', ['coalesce', ['get', 'daily'], 1], 100000]]]],
            15, ['max', 4.5,
              ['*', 16, ['sqrt', ['/', ['coalesce', ['get', 'daily'], 1], 100000]]]]],
          'circle-color': color,
          'circle-opacity': 0.72 * alpha,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#000000',
        },
      })
      return ids

    case 'parks':
      add({
        id: `${def.id}--fill`, type: 'fill', source: src,
        paint: { 'fill-color': color, 'fill-opacity': 0.42 * alpha },
      })
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        paint: { 'line-color': color, 'line-width': 0.6, 'line-opacity': 0.8 * alpha },
      })
      return [`${def.id}--fill`]

    case 'evacuation_zones': {
      const expr: any[] = ['match', ['get', 'zone']]
      Object.entries(ZONE_COLOR).forEach(([z, c]) => expr.push(Number(z), c))
      expr.push('#5f7a52')
      add({
        id: `${def.id}--fill`, type: 'fill', source: src,
        paint: { 'fill-color': expr as any, 'fill-opacity': 0.4 * alpha },
      })
      return ids
    }

    case 'collisions':
      add({
        id: `${def.id}--heat`, type: 'heatmap', source: src,
        maxzoom: 15,
        paint: {
          'heatmap-weight': ['interpolate', ['linear'],
            ['+', ['coalesce', ['get', 'injured'], 0],
                  ['*', 5, ['coalesce', ['get', 'killed'], 0]]],
            0, 0.2, 5, 1],
          // Tuned down at city zoom: 15k crashes at full intensity paints the
          // whole city one colour and buries every layer beneath it, which
          // says nothing beyond "New York is dense".
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 9, 0.35, 15, 2.2],
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            ...HEAT.flatMap(([stop, c]) => [stop, c])] as any,
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 9, 5, 15, 26],
          'heatmap-opacity': 0.62 * alpha,
        },
      })
      // Past the heatmap's maxzoom the individual crashes become inspectable.
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        minzoom: 14,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 2.5, 17, 6],
          'circle-color': ['case', ['>', ['coalesce', ['get', 'killed'], 0], 0],
            '#f0564a', color],
          'circle-opacity': 0.85 * alpha,
          'circle-stroke-width': 0.8,
          'circle-stroke-color': '#000000',
        },
      })
      return [`${def.id}--circle`]

    case 'crime':
    case 'forsale': {
      // A self-describing choropleth: the layer payload carries its own
      // quantile breaks and names its own fill property (meta.metric), so it
      // draws like housing/population without being parameterised — and the
      // next breaks-carrying layer is a case label here, not a new block.
      // "No data" stays distinct from "quietest class".
      const metric: string = (data as any).meta?.metric ?? 'total'
      const stops: number[] = (data as any).breaks?.stops ?? []
      const fill: any = ['case', ['==', ['get', metric], null], NO_DATA,
        stepExpression(metric, stops, SEQUENTIAL)]
      add({
        id: `${def.id}--fill`, type: 'fill', source: src,
        paint: { 'fill-color': fill, 'fill-opacity': 0.72 * alpha },
      })
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        paint: { 'line-color': 'rgba(255,255,255,0.22)', 'line-width': 0.6 },
      })
      return [`${def.id}--fill`]
    }

    case 'shootings':
      add({
        id: `${def.id}--heat`, type: 'heatmap', source: src,
        maxzoom: 15,
        paint: {
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 9, 0.5, 15, 2.4],
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            ...HEAT.flatMap(([stop, c]) => [stop, c])] as any,
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 9, 6, 15, 28],
          'heatmap-opacity': 0.62 * alpha,
        },
      })
      // Past the heatmap's maxzoom each incident becomes inspectable.
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        minzoom: 14,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 2.8, 17, 6],
          'circle-color': ['case', ['==', ['get', 'statistical_murder_flag'], true],
            '#f0564a', color] as any,
          'circle-opacity': 0.85 * alpha,
          'circle-stroke-width': 0.8,
          'circle-stroke-color': '#000000',
        },
      })
      return [`${def.id}--circle`]

    case 'affordable_housing':
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            10, ['*', 0.9, ['sqrt', ['/', ['coalesce', ['get', 'units'], 1], 20]]],
            15, ['*', 3.5, ['sqrt', ['/', ['coalesce', ['get', 'units'], 1], 20]]]],
          'circle-color': color,
          'circle-opacity': 0.7 * alpha,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#000000',
        },
      })
      return ids

    case 'news': {
      const topic: any[] = ['match', ['get', 'topic']]
      Object.entries(NEWS_TOPIC).forEach(([k, c]) => { if (k !== 'other') topic.push(k, c) })
      topic.push(NEWS_TOPIC.other)
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          // A borough-level pin is an approximation, drawn fainter so it never
          // reads as "this happened on this block".
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 4, 13, 7, 16, 10],
          'circle-color': topic as any,
          'circle-opacity': ['case', ['==', ['get', 'precision'], 'borough'],
            0.45 * alpha, 0.9 * alpha],
          // White ring: these dots sit over choropleths that share their hues.
          'circle-stroke-width': 1.4,
          'circle-stroke-color': 'rgba(255,255,255,0.85)',
        },
      })
      return ids
    }

    case 'sales':
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 1.6, 14, 3.4, 17, 7],
          'circle-color': stepExpression('price', SALE_BREAKS, SEQUENTIAL) as any,
          'circle-opacity': 0.8 * alpha,
          'circle-stroke-width': 0.4,
          'circle-stroke-color': 'rgba(11,14,20,0.7)',
        },
      })
      return ids

    case 'affordable_rents':
      add({
        id: `${def.id}--circle`, type: 'circle', source: src,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 1.6, 14, 3.4, 17, 7],
          'circle-color': stepExpression('rent_min', AFFORDABLE_RENT_BREAKS, SEQUENTIAL) as any,
          'circle-opacity': 0.85 * alpha,
          'circle-stroke-width': 0.4,
          'circle-stroke-color': 'rgba(11,14,20,0.7)',
        },
      })
      return [`${def.id}--circle`]

    case 'boroughs':
    case 'neighborhoods':
      add({
        id: `${def.id}--line`, type: 'line', source: src,
        paint: {
          'line-color': color,
          'line-width': def.id === 'boroughs' ? 1.6 : 0.7,
          'line-opacity': 0.75 * alpha,
        },
      })
      return ids

    default: {
      // A layer added to the catalogue but not styled here still renders,
      // picked by geometry, rather than silently disappearing. This is also
      // how every owner-added dataset draws, so two self-describing cases
      // get real treatment: a heatmap kind, and a polygon payload that
      // carries its own quantile breaks (like crime/forsale) ramps on
      // `value` instead of flattening to one colour.
      if (def.kind === 'heatmap' && def.geometry === 'point') {
        add({
          id: `${def.id}--heat`, type: 'heatmap', source: src,
          maxzoom: 15,
          paint: {
            'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 9, 0.5, 15, 2.4],
            'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
              ...HEAT.flatMap(([stop, c]) => [stop, c])] as any,
            'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 9, 6, 15, 28],
            'heatmap-opacity': 0.62 * alpha,
          },
        })
        add({
          id: `${def.id}--circle`, type: 'circle', source: src,
          minzoom: 14,
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 2.8, 17, 6],
            'circle-color': color,
            'circle-opacity': 0.85 * alpha,
            'circle-stroke-width': 0.8,
            'circle-stroke-color': '#000000',
          },
        })
        return [`${def.id}--circle`]
      }
      const stops: number[] = (data as any).breaks?.stops ?? []
      if (def.geometry === 'polygon' && stops.length) {
        const fill: any = ['case', ['==', ['get', 'value'], null], NO_DATA,
          stepExpression('value', stops, SEQUENTIAL)]
        add({
          id: `${def.id}--fill`, type: 'fill', source: src,
          paint: { 'fill-color': fill, 'fill-opacity': 0.72 * alpha },
        })
        add({
          id: `${def.id}--line`, type: 'line', source: src,
          paint: { 'line-color': 'rgba(255,255,255,0.22)', 'line-width': 0.6 },
        })
        return [`${def.id}--fill`]
      }
      if (def.geometry === 'polygon') {
        add({
          id: `${def.id}--fill`, type: 'fill', source: src,
          paint: { 'fill-color': color, 'fill-opacity': 0.4 * alpha },
        })
      } else if (def.geometry === 'line') {
        add({
          id: `${def.id}--line`, type: 'line', source: src,
          paint: { 'line-color': color, 'line-width': 1.2, 'line-opacity': alpha },
        })
      } else {
        add({
          id: `${def.id}--circle`, type: 'circle', source: src,
          paint: { 'circle-radius': 3.2, 'circle-color': color, 'circle-opacity': alpha },
        })
      }
      return ids
    }
  }
}

/** Fixed price classes for the sales point layer, in dollars. */
const SALE_BREAKS = [0, 400_000, 700_000, 1_000_000, 1_500_000, 2_500_000, 5_000_000]
/** Monthly rent breaks for the affordable_rents layer, in dollars. */
const AFFORDABLE_RENT_BREAKS = [0, 800, 1_200, 1_600, 2_000, 2_500, 3_200]
export { SALE_BREAKS, AFFORDABLE_RENT_BREAKS }

// ── the agent's view ──────────────────────────────────────────────────────

const HIGHLIGHT = '#e8b64c'   // the HUD's gold accent: chrome, never a data class
const AGENT_POINT = '#22d3ee'

function extend(b: maplibregl.LngLatBounds, g: any) {
  const walk = (c: any) => {
    if (typeof c?.[0] === 'number') b.extend([c[0], c[1]])
    else for (const x of c ?? []) walk(x)
  }
  walk(g?.coordinates)
}

/**
 * Everything the chat agent asked for, drawn over the user's own layers:
 * a range filter that dims the housing choropleth outside it, an overlay of
 * any open dataset, and a bright outline around the areas it is talking about.
 */
function drawAgent(m: MLMap, a: {
  overlay: AgentOverlay | null
  highlight: string[]
  valueFilter: ValueFilter
  only: string[]
  metric: string
  sources: (GeoJSON.FeatureCollection | null)[]
}): string[] {
  const ids: string[] = []

  const vf = a.valueFilter
  if (vf && m.getLayer('housing_prices--fill')) {
    const v: any = ['coalesce', ['get', a.metric], -1e15]
    const inside: any[] = ['all']
    if (vf.min !== null) inside.push(['>=', v, vf.min])
    if (vf.max !== null) inside.push(['<=', v, vf.max])
    m.setPaintProperty('housing_prices--fill', 'fill-opacity', ['case', inside, 0.85, 0.12])
  }

  const ov = a.overlay
  const onlyFilter = a.only.length ? matchExpression(a.only) : null

  if (ov?.data) {
    m.addSource('nyc-agent', { type: 'geojson', data: ov.data as any })
    if (ov.spec.mode === 'areas') {
      const stops = ov.data.breaks?.stops ?? []
      m.addLayer({
        id: 'agent--fill', type: 'fill', source: 'nyc-agent',
        paint: {
          'fill-color': ['case', ['==', ['get', 'value'], null], NO_DATA,
            stops.length ? stepExpression('value', stops, SEQUENTIAL) : SEQUENTIAL[3]] as any,
          'fill-opacity': 0.8,
        },
      })
      m.addLayer({ id: 'agent--line', type: 'line', source: 'nyc-agent',
        paint: { 'line-color': 'rgba(255,255,255,0.22)', 'line-width': 0.6 } })
      ids.push('agent--fill')
    } else if (ov.spec.mode === 'heat') {
      m.addLayer({
        id: 'agent--heat', type: 'heatmap', source: 'nyc-agent', maxzoom: 16,
        paint: {
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 9, 0.5, 15, 2.5],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 9, 6, 15, 22],
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            ...HEAT.flatMap(([stop, c]) => [stop, c])] as any,
          'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0.9, 16, 0.2],
        },
      })
      m.addLayer({
        id: 'agent--circle', type: 'circle', source: 'nyc-agent', minzoom: 14,
        paint: { 'circle-radius': 4, 'circle-color': HEAT[4][1] as string,
                 'circle-stroke-color': '#000', 'circle-stroke-width': 1 },
      })
      ids.push('agent--circle')
    } else {
      m.addLayer({
        id: 'agent--circle', type: 'circle', source: 'nyc-agent',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 2.5, 14, 5, 17, 8],
          'circle-color': AGENT_POINT, 'circle-opacity': 0.9,
          'circle-stroke-color': '#000', 'circle-stroke-width': 1,
        },
      })
      ids.push('agent--circle')
    }
  }

  // "Only Brooklyn": every area layer drops what does not match. Point layers
  // carry no borough, so they are left alone rather than emptied.
  if (onlyFilter) {
    for (const id of ['housing_prices--fill', 'housing_prices--line', 'population--fill',
                      'population--line', 'agent--fill', 'agent--line']) {
      if (m.getLayer(id)) m.setFilter(id, onlyFilter as any)
    }
  }

  if (a.highlight.length) {
    const feats: GeoJSON.Feature[] = []
    for (const fc of [...a.sources, ov?.data ?? null]) {
      for (const f of fc?.features ?? []) {
        if (f.geometry?.type !== 'Point' && matchesHighlight((f.properties as any) || {}, a.highlight)) feats.push(f)
      }
    }
    // Nothing on the map to outline (the choropleth is off): fall back to
    // nothing rather than guessing; the camera move still says where.
    if (feats.length) {
      m.addSource('nyc-agent-hl', { type: 'geojson', data: { type: 'FeatureCollection', features: feats } as any })
      m.addLayer({ id: 'agent-hl--glow', type: 'line', source: 'nyc-agent-hl',
        paint: { 'line-color': '#000', 'line-width': 6, 'line-opacity': 0.6 } })
      m.addLayer({ id: 'agent-hl--line', type: 'line', source: 'nyc-agent-hl',
        paint: { 'line-color': HIGHLIGHT, 'line-width': 2.5 } })
    }
  }
  return ids
}
