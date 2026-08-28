'use client'

import { useEffect, useRef } from 'react'
import maplibregl, { Map as MLMap } from 'maplibre-gl'
import type { Catalog, Choropleth, LayerDef } from '@/lib/api'
import { NARROW } from '@/lib/layout'
import {
  DIVERGING, HEAT, LAYER_COLOR, NO_DATA, SEQUENTIAL, ZONE_COLOR,
  divergingExpression, stepExpression,
} from '@/lib/palette'

export type Basemap = 'dark' | 'light' | 'streets'

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
 * Basemap styles. All raster, all key-free: CARTO's free tiles for the muted
 * cartography a data map needs, and OpenStreetMap's own tiles for the "show me
 * the actual streets" case. Attribution is mandatory and is baked into each
 * source rather than left to the caller.
 */
const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
const CARTO_ATTR = `${OSM_ATTR} © <a href="https://carto.com/attributions">CARTO</a>`

const BASEMAPS: Record<Basemap, { tiles: string[]; attribution: string }> = {
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

/** True on a touch screen, where hit-testing needs a bigger target. */
function coarsePointer(): boolean {
  return typeof window !== 'undefined'
    && window.matchMedia('(pointer: coarse)').matches
}

function styleFor(basemap: Basemap): any {
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
  housing: Choropleth | null
  housingMetric: string
  layerData: Record<string, GeoJSON.FeatureCollection>
  basemap: Basemap
  flyTo: { lng: number; lat: number; zoom?: number; nonce: number } | null
  onFeatureClick: (payload: { layerId: string; props: Record<string, any> } | null) => void
  onMapReady: (map: MLMap) => void
}

export default function MapView({
  catalog, active, opacity, housing, housingMetric, layerData,
  basemap, flyTo, onFeatureClick, onMapReady,
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
      // The basemap is raster and the overlays are flat 2-D data; disabling
      // pitch keeps polygon fills legible and avoids a tilted-map trap where
      // the choropleth reads as terrain.
      pitchWithRotate: false,
      dragRotate: false,
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
    // rotated choropleth reads as terrain.
    m.touchZoomRotate.disableRotation()

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basemap])

  // ── redraw on any data/selection change ─────────────────────────────────
  useEffect(() => {
    redraw()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, active, opacity, housing, housingMetric, layerData])

  useEffect(() => {
    if (!flyTo || !map.current) return
    map.current.flyTo({ center: [flyTo.lng, flyTo.lat], zoom: flyTo.zoom ?? 14, duration: 900 })
  }, [flyTo])

  function clearAll(m: MLMap) {
    const style = m.getStyle()
    // Layers first, then sources — MapLibre refuses to drop a source that any
    // layer still references. Our layers are named `<layerId>--<kind>` while
    // our sources are named `nyc-<layerId>`, so the two need different tests;
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
        } else {
          const data = layerData[def.id]
          if (data) order.push(...addOverlay(m, def, data, alpha))
        }
      } catch (err) {
        // A single malformed layer must not take the whole map down.
        console.error(`nyc: failed to draw ${def.id}`, err)
      }
    }
    // Topmost layer first, so a click on a station beats the polygon under it.
    clickOrder.current = order.reverse()
  }

  return <div ref={container} className="absolute inset-0" />
}

// ── choropleth ────────────────────────────────────────────────────────────

function addChoropleth(m: MLMap, fc: Choropleth, metric: string, alpha: number): string[] {
  const src = 'nyc-housing'
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
    id: 'housing_prices--fill',
    type: 'fill',
    source: src,
    paint: { 'fill-color': fill, 'fill-opacity': 0.78 * alpha },
  })
  m.addLayer({
    id: 'housing_prices--line',
    type: 'line',
    source: src,
    paint: {
      'line-color': 'rgba(255,255,255,0.22)',
      'line-width': 0.6,
    },
  })
  return ['housing_prices--fill']
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
            '#f2a0a0', color],
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

    default:
      // A layer added to the catalogue but not styled here still renders,
      // picked by geometry, rather than silently disappearing.
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

/** Fixed price classes for the sales point layer, in dollars. */
const SALE_BREAKS = [0, 400_000, 700_000, 1_000_000, 1_500_000, 2_500_000, 5_000_000]
export { SALE_BREAKS }
