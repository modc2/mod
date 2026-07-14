'use client'

import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js'
import { OBJExporter } from 'three/examples/jsm/exporters/OBJExporter.js'
import { STLExporter } from 'three/examples/jsm/exporters/STLExporter.js'
import {
  ModuleSpec, StyleSpec, Cell, Estimate, Constraints, PortableDoc, PanelSpec,
  localEstimate, api, ownerId, CODES, CODE_INDEX, LANEWAY_TEMPLATES,
  FALLBACK_PANELS, PANEL_INDEX_FALLBACK, buildFabSchedule, fabScheduleCSV,
  AuditResult, AUDIT_PROVIDERS, DEFAULT_AUDIT_PROVIDER, runAudit,
} from '@/lib/modcity'

const STEP = 3
const BRICK = 2.86      // module footprint / floor-to-floor height (m)
const PANEL_T = 0.12    // thickness of a wall / floor / curtain-wall panel
const HARD_MAX = 14
const CATEGORIES = ['living', 'service', 'work', 'commerce', 'outdoor', 'light', 'structure', 'roof']
const PRESET_COLORS = ['#9c6b46', '#e63946', '#f4a261', '#457b9d', '#00f5d4', '#c77dff', '#80b918', '#ffd166', '#e9ecef', '#3a3330']

type CellMap = Map<string, string[]>
const key = (x: number, z: number) => `${x},${z}`
const fmt = (n: number) => (n || 0).toLocaleString()
function fmtUSD(n: number) {
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M'
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(0) + 'k'
  return '$' + Math.round(n || 0)
}

const SEED: Array<[number, number, string[]]> = [
  [0, 0, ['stoop', 'parlor', 'bedroom', 'bay', 'cornice']],
  [0, 1, ['garden', 'kitchen', 'bedroom', 'bedroom', 'solar']],
  [-1, 0, ['stoop', 'parlor', 'bedroom', 'cornice']],
  [-1, 1, ['garden', 'kitchen', 'bath', 'solar']],
  [1, 0, ['stoop', 'parlor', 'bay', 'cornice']],
  [1, 1, ['garden', 'kitchen', 'bedroom', 'solar']],
]

const WINDOW_CATS = new Set(['living', 'work', 'commerce', 'service'])

const AUDIT_VERDICT_CLS: Record<string, string> = {
  pass: 'border-emerald-400/40 bg-emerald-400/10',
  conditional: 'border-amber-400/40 bg-amber-400/10',
  fail: 'border-rose-400/40 bg-rose-400/10',
}
const AUDIT_STATUS_CLS: Record<string, string> = {
  pass: 'text-emerald-300', warn: 'text-amber-300', fail: 'text-rose-300', unknown: 'text-white/50',
}
function statusIcon(s: string) { return s === 'pass' ? '✓' : s === 'fail' ? '✕' : s === 'warn' ? '!' : '?' }

export default function Configurator({
  catalog, styles, owner, loadDoc, onSaved, onBrickCreated,
}: {
  catalog: ModuleSpec[]; styles: StyleSpec[]; owner: string
  loadDoc?: PortableDoc | null
  onSaved?: () => void; onBrickCreated?: () => void
}) {
  const mountRef = useRef<HTMLDivElement>(null)
  const three = useRef<any>({})
  const cellsRef = useRef<CellMap>(new Map())
  const historyRef = useRef<string[]>([])

  const [localBricks, setLocalBricks] = useState<ModuleSpec[]>([])
  const mergedCatalog = useMemo(() => {
    const seen = new Set<string>(); const out: ModuleSpec[] = []
    for (const m of [...catalog, ...localBricks]) { if (!seen.has(m.id)) { seen.add(m.id); out.push(m) } }
    return out
  }, [catalog, localBricks])

  // ── façade panels (designable, shareable) ──
  const [remotePanels, setRemotePanels] = useState<PanelSpec[]>([])
  const [localPanels, setLocalPanels] = useState<PanelSpec[]>([])
  const mergedPanels = useMemo(() => {
    const seen = new Set<string>(); const out: PanelSpec[] = []
    for (const p of [...FALLBACK_PANELS, ...remotePanels, ...localPanels]) { if (!seen.has(p.id)) { seen.add(p.id); out.push(p) } }
    return out
  }, [remotePanels, localPanels])
  useEffect(() => {
    api('panel_catalog' + (owner ? `?owner=${owner}` : '')).then((p) => Array.isArray(p) && setRemotePanels(p)).catch(() => {})
  }, [owner])

  const [selected, setSelected] = useState('parlor')
  const [styleId, setStyleId] = useState('brownstone')
  const [tab, setTab] = useState<'all' | 'mine' | 'community'>('all')
  const [stats, setStats] = useState<Estimate | null>(null)
  const [autoRotate, setAutoRotate] = useState(true)
  const [toast, setToast] = useState('')
  const [tick, setTick] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  const [glFailed, setGlFailed] = useState(false)
  const [constraints, setConstraints] = useState<Constraints>({ lot_w: 5, lot_d: 5, max_floors: 8 })
  const [showParams, setShowParams] = useState(false)
  const [showDesigner, setShowDesigner] = useState(false)
  const [showSave, setShowSave] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [showTemplates, setShowTemplates] = useState(false)
  const [showPanels, setShowPanels] = useState(false)
  const [panelTab, setPanelTab] = useState<'all' | 'mine' | 'community'>('all')
  const [showPanelDesigner, setShowPanelDesigner] = useState(false)
  // ── building-standards audit (pluggable agent) ──
  const [showAudit, setShowAudit] = useState(false)
  const [auditProvider, setAuditProvider] = useState(DEFAULT_AUDIT_PROVIDER)
  const [auditing, setAuditing] = useState(false)
  const [audit, setAudit] = useState<AuditResult | null>(null)

  const style = useMemo(() => styles.find((s) => s.id === styleId) || styles[0], [styles, styleId])
  const activePanel = useMemo(() => {
    const id = constraints.skin
    if (!id || id === 'none') return undefined
    return mergedPanels.find((p) => p.id === id) || PANEL_INDEX_FALLBACK[id]
  }, [constraints.skin, mergedPanels])
  const selectedRef = useRef(selected); selectedRef.current = selected
  const constraintsRef = useRef(constraints); constraintsRef.current = constraints
  const fileRef = useRef<HTMLInputElement>(null)

  const flash = useCallback((msg: string) => {
    setToast(msg); window.clearTimeout((three.current as any)._t)
    ;(three.current as any)._t = window.setTimeout(() => setToast(''), 2400)
  }, [])

  // ── full-screen builder mode ──────────────────────────────────
  // CSS fixed-overlay always fills the viewport (works inside the gateway
  // iframe); we also request the native Fullscreen API so it takes over the
  // whole screen when the embedding allows it. The ResizeObserver on the mount
  // re-sizes the WebGL canvas automatically when the container grows/shrinks.
  const toggleFullscreen = useCallback(() => {
    const el = rootRef.current
    const doc: any = document
    const next = !fullscreen
    try {
      if (next) {
        const req = el && ((el as any).requestFullscreen || (el as any).webkitRequestFullscreen)
        if (req) req.call(el).catch?.(() => {})
      } else if (doc.fullscreenElement || doc.webkitFullscreenElement) {
        const exit = doc.exitFullscreen || doc.webkitExitFullscreen
        exit?.call(doc)?.catch?.(() => {})
      }
    } catch {}
    setFullscreen(next)
  }, [fullscreen])

  useEffect(() => {
    const doc: any = document
    const onFsChange = () => { if (!(doc.fullscreenElement || doc.webkitFullscreenElement)) setFullscreen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFullscreen(false) }
    document.addEventListener('fullscreenchange', onFsChange)
    document.addEventListener('webkitfullscreenchange', onFsChange)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('fullscreenchange', onFsChange)
      document.removeEventListener('webkitfullscreenchange', onFsChange)
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const serialize = useCallback((): Cell[] => {
    const out: Cell[] = []
    cellsRef.current.forEach((stack, k) => {
      if (!stack.length) return
      const [x, z] = k.split(',').map(Number)
      out.push({ x, z, stack: [...stack] })
    })
    return out
  }, [])

  const lotBounds = () => {
    const c = constraintsRef.current
    return { hw: Math.floor((c.lot_w || 99) / 2), hd: Math.floor((c.lot_d || 99) / 2),
             maxF: Math.min(HARD_MAX, c.max_floors || HARD_MAX) }
  }

  // ── Scene (once) ──────────────────────────────────────────────
  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    // WebGL is unavailable in some embeds / headless browsers — three.js throws
    // on construction there, which would take down the whole page. Probe first
    // and degrade to the 2D elevation view instead.
    let renderer: THREE.WebGLRenderer
    try {
      const probe = document.createElement('canvas')
      if (!probe.getContext('webgl2') && !probe.getContext('webgl')) throw new Error('webgl unavailable')
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      if (cellsRef.current.size === 0 && !loadDoc) {
        for (const [x, z, stack] of SEED) cellsRef.current.set(key(x, z), [...stack])
      }
      setGlFailed(true)
      setTick((t) => t + 1)
      return
    }
    const scene = new THREE.Scene()
    const W = mount.clientWidth, H = mount.clientHeight
    const camera = new THREE.PerspectiveCamera(40, W / H, 0.1, 1000)
    camera.position.set(15, 12, 18)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(W, H)
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    mount.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true; controls.dampingFactor = 0.08
    controls.minDistance = 9; controls.maxDistance = 70
    controls.maxPolarAngle = Math.PI / 2.04
    controls.target.set(0, 5, 0)

    const hemi = new THREE.HemisphereLight(0xffffff, 0x444455, 0.85); scene.add(hemi)
    const sun = new THREE.DirectionalLight(0xffffff, 1.5)
    sun.position.set(22, 36, 18); sun.castShadow = true
    sun.shadow.mapSize.set(2048, 2048)
    const d = 34
    Object.assign(sun.shadow.camera, { left: -d, right: d, top: d, bottom: -d, near: 1, far: 100 })
    sun.shadow.bias = -0.0004; scene.add(sun)
    const rim = new THREE.DirectionalLight(0x88aaff, 0.4); rim.position.set(-18, 12, -20); scene.add(rim)

    const ground = new THREE.Mesh(new THREE.CircleGeometry(90, 64),
      new THREE.MeshStandardMaterial({ color: 0x14141d, roughness: 1 }))
    ground.rotation.x = -Math.PI / 2; ground.position.y = -0.02; ground.receiveShadow = true
    scene.add(ground)

    const padGroup = new THREE.Group(); scene.add(padGroup)
    const context = new THREE.Group(); scene.add(context)
    const bricks = new THREE.Group(); scene.add(bricks)

    const ghost = new THREE.Mesh(new THREE.BoxGeometry(BRICK, BRICK, BRICK),
      new THREE.MeshStandardMaterial({ color: 0x66ffcc, transparent: true, opacity: 0.28, depthWrite: false }))
    ghost.visible = false; scene.add(ghost)

    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
    const hit = new THREE.Vector3()

    function pickCell(ev: any) {
      const r = renderer.domElement.getBoundingClientRect()
      pointer.x = ((ev.clientX - r.left) / r.width) * 2 - 1
      pointer.y = -((ev.clientY - r.top) / r.height) * 2 + 1
      raycaster.setFromCamera(pointer, camera)
      if (!raycaster.ray.intersectPlane(plane, hit)) return null
      const x = Math.round(hit.x / STEP), z = Math.round(hit.z / STEP)
      const { hw, hd } = lotBounds()
      if (Math.abs(x) > hw || Math.abs(z) > hd) return null
      return { x, z }
    }
    function onMove(ev: PointerEvent) {
      const c = pickCell(ev)
      if (!c) { ghost.visible = false; return }
      const h = (cellsRef.current.get(key(c.x, c.z)) || []).length
      if (h >= lotBounds().maxF) { ghost.visible = false; return }
      ghost.visible = true
      ghost.position.set(c.x * STEP, h * STEP + BRICK / 2, c.z * STEP)
    }
    function onLeave() { ghost.visible = false }
    function onDown(ev: PointerEvent) {
      if (ev.button === 2) return
      const c = pickCell(ev); if (!c) return
      const k = key(c.x, c.z)
      const stack = cellsRef.current.get(k) || []
      if (stack.length >= lotBounds().maxF) { setToast(`Height cap: ${lotBounds().maxF} floors`); window.clearTimeout((three.current as any)._t); (three.current as any)._t = window.setTimeout(() => setToast(''), 1800); return }
      stack.push(selectedRef.current); cellsRef.current.set(k, stack)
      historyRef.current.push(k); setTick((t) => t + 1)
    }
    function onContext(ev: MouseEvent) {
      ev.preventDefault()
      const c = pickCell(ev); if (!c) return
      const k = key(c.x, c.z); const stack = cellsRef.current.get(k)
      if (stack && stack.length) { stack.pop(); if (!stack.length) cellsRef.current.delete(k); setTick((t) => t + 1) }
    }
    const el = renderer.domElement
    el.addEventListener('pointermove', onMove); el.addEventListener('pointerleave', onLeave)
    el.addEventListener('pointerdown', onDown); el.addEventListener('contextmenu', onContext)

    let raf = 0; const clock = new THREE.Clock()
    function animate() {
      raf = requestAnimationFrame(animate)
      controls.update()
      ;(ghost.material as THREE.MeshStandardMaterial).opacity = 0.18 + Math.sin(clock.getElapsedTime() * 4) * 0.12
      renderer.render(scene, camera)
    }
    animate()
    const ro = new ResizeObserver(() => {
      const w = mount.clientWidth, h = mount.clientHeight; if (!w || !h) return
      camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h)
    })
    ro.observe(mount)

    three.current = { scene, camera, renderer, controls, bricks, padGroup, context, ghost, sun, hemi, ground }

    if (cellsRef.current.size === 0 && !loadDoc) {
      for (const [x, z, stack] of SEED) cellsRef.current.set(key(x, z), [...stack])
    }
    buildContext()
    setTick((t) => t + 1)

    return () => {
      cancelAnimationFrame(raf); ro.disconnect()
      el.removeEventListener('pointermove', onMove); el.removeEventListener('pointerleave', onLeave)
      el.removeEventListener('pointerdown', onDown); el.removeEventListener('contextmenu', onContext)
      controls.dispose(); renderer.dispose()
      if (el.parentNode) el.parentNode.removeChild(el)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // surrounding buildings removed — focus is the single building on its lot
  function buildContext() {
    const t = three.current; if (!t.context) return
    const ctx = t.context
    while (ctx.children.length) { const c = ctx.children.pop(); c.geometry?.dispose?.(); c.material?.dispose?.() }
  }

  // pad / street rebuilt when the lot changes
  function buildPad() {
    const t = three.current; if (!t.padGroup) return
    const g = t.padGroup
    while (g.children.length) { const c = g.children.pop(); c.geometry?.dispose?.(); (c.material?.dispose?.() ?? 0) }
    const c = constraintsRef.current
    const cols = c.lot_w || 5, rows = c.lot_d || 5
    const pw = cols * STEP, pd = rows * STEP
    const street = new THREE.Mesh(new THREE.BoxGeometry(pw + STEP * 2.4, 0.2, pd + STEP * 2.4),
      new THREE.MeshStandardMaterial({ color: 0x101018, roughness: 1 }))
    street.position.y = -0.12; street.receiveShadow = true; g.add(street)
    const plate = new THREE.Mesh(new THREE.BoxGeometry(pw + 0.5, 0.5, pd + 0.5),
      new THREE.MeshStandardMaterial({ color: 0x1d1d2a, roughness: 0.9 }))
    plate.position.y = -0.25; plate.receiveShadow = true; g.add(plate)
    const grid = new THREE.GridHelper(Math.max(pw, pd), Math.max(cols, rows), 0x4a4a6a, 0x2a2a40)
    grid.position.y = 0.02
    ;(grid.material as THREE.Material).transparent = true; (grid.material as THREE.Material).opacity = 0.55
    // clip grid to lot via scale
    grid.scale.set(pw / Math.max(pw, pd), 1, pd / Math.max(pw, pd))
    g.add(grid)
  }

  // surface finish (roughness/metalness) for a panel material
  function matProps(material: string) {
    switch (material) {
      case 'glass': return { rough: 0.12, metal: 0.25 }
      case 'metal': return { rough: 0.4, metal: 0.7 }
      case 'concrete': return { rough: 0.95, metal: 0.04 }
      case 'wood': return { rough: 0.72, metal: 0.02 }
      default: return { rough: 0.6, metal: 0.1 }   // composite
    }
  }

  // ── rebuild panels + restyle ──────────────────────────────────
  useEffect(() => {
    const t = three.current; if (!t.scene) return
    const byId = Object.fromEntries(mergedCatalog.map((m) => [m.id, m]))
    buildPad()

    const sky = new THREE.Color(style.sky).lerp(new THREE.Color(0x0a0a0f), 0.55)
    t.scene.background = sky
    t.scene.fog = new THREE.Fog(sky.getHex(), 60, 130)
    const neon = style.material === 'neon'

    const bricks = t.bricks
    bricks.traverse((o: any) => { if (o.isMesh || o.isLineSegments) { o.geometry?.dispose?.(); if (Array.isArray(o.material)) o.material.forEach((m: any) => m.dispose()); else o.material?.dispose?.() } })
    while (bricks.children.length) bricks.remove(bricks.children[0])

    // neighbour lookup so panels are only built on EXPOSED faces — adjacent
    // modules share a party wall (no double wall), which reads as real architecture.
    const specAt = (cx: number, cz: number, level: number): ModuleSpec | undefined =>
      byId[(cellsRef.current.get(key(cx, cz)) || [])[level]]
    const isOpenSpec = (s?: ModuleSpec) =>
      !s || s.id === 'garden' || (s.category === 'outdoor' && !(s.glass || style.material === 'glass'))
    const faceOpen = (cx: number, cz: number, level: number) => isOpenSpec(specAt(cx, cz, level))

    const half = BRICK / 2
    const PW = BRICK * 0.98, PH = BRICK * 0.96, T = PANEL_T

    // detailed exposed-face builder — driven by the active façade panel (skin)
    // or a style-default. Adds cladding + window grid w/ frames & sills, curtain
    // mullions, louver slats, board reveals, and a ground entry door.
    type FaceOpts = {
      kind: 'solid' | 'window' | 'curtain' | 'louver'
      glazing: number; winC: number; winR: number; reveal: boolean; isEntry: boolean
      cladMat: THREE.Material; glazeMat: THREE.Material; frameMat: THREE.Material
    }
    const buildFace = (g: THREE.Group, d: { isX: boolean; sign: number }, o: FaceOpts) => {
      const isX = d.isX, sign = d.sign, off = half * sign
      const pos = (u: number, v: number, dep: number): [number, number, number] =>
        isX ? [off + sign * dep, v, u] : [u, v, off + sign * dep]
      const box = (w: number, h: number, th: number) =>
        isX ? new THREE.BoxGeometry(th, h, w) : new THREE.BoxGeometry(w, h, th)
      const add = (geo: THREE.BufferGeometry, mat: THREE.Material, u: number, v: number, dep: number, shadow = false) => {
        const m = new THREE.Mesh(geo, mat); m.position.set(...pos(u, v, dep)); if (shadow) { m.castShadow = true; m.receiveShadow = true } g.add(m); return m
      }
      if (o.kind === 'curtain') {
        add(box(PW, PH, T), o.glazeMat, 0, 0, 0, true)
        const cols = Math.max(1, o.winC), rows = Math.max(1, o.winR)
        for (let i = 1; i < cols; i++) add(box(0.06, PH, T * 1.6), o.frameMat, (i / cols - 0.5) * PW, 0, 0.02)
        for (let j = 1; j < rows; j++) add(box(PW, 0.06, T * 1.6), o.frameMat, 0, (j / rows - 0.5) * PH, 0.02)
        return
      }
      add(box(PW, PH, T), o.cladMat, 0, 0, 0, true)   // cladding
      if (o.reveal) for (let r = 1; r <= 3; r++) add(box(PW, 0.04, T * 0.5), o.frameMat, 0, PH * (r / 4 - 0.5), T * 0.5)
      if (o.kind === 'louver') {
        for (let r = 0; r < 6; r++) add(box(PW * 0.94, PH * 0.085, T * 0.9), o.frameMat, 0, PH * ((r + 0.5) / 6 - 0.5), T * 0.6)
        return
      }
      if (o.glazing > 0 && o.winC > 0 && o.winR > 0 && o.kind !== 'solid') {
        const cols = o.winC, rows = o.winR
        const cellW = (PW * 0.84) / cols, cellH = (PH * 0.82) / rows
        const winW = cellW * 0.74, winH = cellH * (0.5 + o.glazing * 0.45)
        for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
          const u = (-(cols - 1) / 2 + i) * cellW
          const v = (-(rows - 1) / 2 + j) * cellH + (o.isEntry ? PH * 0.14 : 0)
          add(box(winW + 0.12, winH + 0.12, T * 0.4), o.frameMat, u, v, T * 0.25)  // frame
          add(box(winW, winH, T * 0.5), o.glazeMat, u, v, T * 0.45)                 // glazing
          add(box(winW + 0.18, 0.07, T * 1.2), o.frameMat, u, v - winH / 2 - 0.06, T * 0.5)  // sill
        }
      }
      if (o.isEntry) {
        const dh = PH * 0.6, dw = Math.min(PW * 0.4, 1.1)
        add(box(dw + 0.16, dh + 0.12, T * 0.5), o.frameMat, 0, -(PH - dh) / 2, T * 0.3)  // door frame
        add(box(dw, dh, T * 0.6), o.glazeMat, 0, -(PH - dh) / 2, T * 0.5, true)          // door leaf
      }
    }

    let entryDone = false
    cellsRef.current.forEach((stack, k) => {
      const [x, z] = k.split(',').map(Number)
      stack.forEach((id, level) => {
        const spec: ModuleSpec = byId[id]
        if (!spec) return
        const base = new THREE.Color(spec.color || style.palette[spec.tone] || style.accent)
        base.multiplyScalar(1 - Math.min(level, 6) * 0.012)   // subtle vertical shade
        const glass = spec.glass || style.material === 'glass'
        const isTop = level === stack.length - 1
        const y = level * STEP + half
        const g = new THREE.Group(); g.position.set(x * STEP, y, z * STEP)

        const isGarden = spec.id === 'garden' || (spec.category === 'outdoor' && !glass)
        if (isGarden) {
          // open deck: slab + railing + shrubs
          const slab = new THREE.Mesh(new THREE.BoxGeometry(BRICK, 0.4, BRICK),
            new THREE.MeshStandardMaterial({ color: base, roughness: 0.9 }))
          slab.position.y = -half + 0.2; slab.castShadow = slab.receiveShadow = true; g.add(slab)
          const rail = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(BRICK, BRICK * 0.5, BRICK)),
            new THREE.LineBasicMaterial({ color: new THREE.Color(style.accent), transparent: true, opacity: 0.6 }))
          rail.position.y = -BRICK * 0.25; g.add(rail)
          for (let s = 0; s < 4; s++) {
            const shrub = new THREE.Mesh(new THREE.SphereGeometry(0.32, 8, 6),
              new THREE.MeshStandardMaterial({ color: 0x4c8c3f, roughness: 1 }))
            shrub.position.set((s % 2 ? 0.7 : -0.7), -half + 0.7, (s < 2 ? 0.7 : -0.7))
            shrub.castShadow = true; g.add(shrub)
          }
        } else {
          // ── panelised module: floor slab + detailed façade panels ──
          const skin = activePanel
          const winCat = WINDOW_CATS.has(spec.category)
          const tall = spec.id === 'parlor' || spec.id === 'bay' || spec.category === 'commerce'
          const faceKind: 'solid' | 'window' | 'curtain' | 'louver' =
            glass ? 'curtain' : skin ? skin.kind : winCat ? 'window' : 'solid'
          const glazing = glass ? 0.92 : skin ? skin.glazing : winCat ? (tall ? 0.5 : 0.32) : 0
          const winC = skin ? Math.max(1, skin.win_cols || 1) : spec.category === 'commerce' ? 1 : 2
          const winR = skin ? Math.max(1, skin.win_rows || 1) : 1
          const reveal = skin ? skin.reveal : style.material === 'concrete' || style.material === 'stone'
          const mp = matProps(skin ? skin.material : style.material === 'neon' ? 'metal' : style.material)
          const cladColor = skin ? new THREE.Color(skin.color) : base
          const frameColor = skin ? new THREE.Color(skin.frame) : base.clone().multiplyScalar(neon ? 1 : 0.55)

          const cladMat = new THREE.MeshStandardMaterial({
            color: cladColor, roughness: mp.rough, metalness: mp.metal,
            emissive: neon ? cladColor.clone().multiplyScalar(0.3) : new THREE.Color(0x000000),
          })
          const glazeMat = new THREE.MeshStandardMaterial({
            color: neon ? new THREE.Color(style.accent) : new THREE.Color(0x121821),
            emissive: neon ? new THREE.Color(style.accent).multiplyScalar(0.8) : new THREE.Color(0x0a1018),
            emissiveIntensity: neon ? 1.0 : 0.28, roughness: 0.1, metalness: 0.3,
            transparent: true, opacity: neon ? 0.9 : 0.82,
          })
          const frameMat = new THREE.MeshStandardMaterial({ color: frameColor, roughness: 0.5, metalness: 0.4 })

          // floor slab (every module) + roof slab + parapet coping on top
          const slab = new THREE.Mesh(new THREE.BoxGeometry(BRICK, 0.16, BRICK),
            new THREE.MeshStandardMaterial({ color: base.clone().multiplyScalar(0.78), roughness: 0.85 }))
          slab.position.y = -half + 0.08; slab.castShadow = slab.receiveShadow = true; g.add(slab)
          if (isTop && id !== 'cornice') {
            const roof = new THREE.Mesh(new THREE.BoxGeometry(BRICK, 0.14, BRICK),
              new THREE.MeshStandardMaterial({ color: base.clone().multiplyScalar(0.68), roughness: 0.9 }))
            roof.position.y = half - 0.07; roof.castShadow = roof.receiveShadow = true; g.add(roof)
            // parapet coping ring
            for (const [w, dpt, ox, oz] of [[BRICK + 0.1, 0.12, 0, half], [BRICK + 0.1, 0.12, 0, -half], [0.12, BRICK + 0.1, half, 0], [0.12, BRICK + 0.1, -half, 0]] as const) {
              const cope = new THREE.Mesh(new THREE.BoxGeometry(w, 0.16, dpt), frameMat)
              cope.position.set(ox, half + 0.06, oz); cope.castShadow = true; g.add(cope)
            }
          }

          const dirs = [
            { dx: 1, dz: 0, isX: true, sign: 1 }, { dx: -1, dz: 0, isX: true, sign: -1 },
            { dx: 0, dz: 1, isX: false, sign: 1 }, { dx: 0, dz: -1, isX: false, sign: -1 },
          ]
          for (const d of dirs) {
            if (!faceOpen(x + d.dx, z + d.dz, level)) continue   // interior face → shared party wall, skip
            // one entry door on the building, on a camera-facing ground face
            const isEntry = !entryDone && level === 0 && faceKind !== 'curtain' && (d.sign === 1)
            if (isEntry) entryDone = true
            buildFace(g, d, { kind: faceKind, glazing, winC, winR, reveal, isEntry, cladMat, glazeMat, frameMat })
          }

          // corner posts only where the corner is exposed → framed prefab read
          for (const sx of [1, -1]) for (const sz of [1, -1]) {
            if (!(faceOpen(x + sx, z, level) || faceOpen(x, z + sz, level))) continue
            const post = new THREE.Mesh(new THREE.BoxGeometry(0.14, BRICK, 0.14), frameMat)
            post.position.set(sx * half, 0, sz * half); post.castShadow = true; g.add(post)
          }
        }

        // roof + ground details
        if (id === 'solar') {
          const panel = new THREE.Mesh(new THREE.BoxGeometry(BRICK * 0.92, 0.18, BRICK * 0.92),
            new THREE.MeshStandardMaterial({ color: 0x0c0f16, metalness: 0.6, roughness: 0.3, emissive: new THREE.Color(style.accent).multiplyScalar(0.22) }))
          panel.position.y = BRICK / 2 + 0.1; panel.castShadow = true; g.add(panel)
        } else if (id === 'cornice') {
          const cap = new THREE.Mesh(new THREE.BoxGeometry(BRICK * 1.12, 0.45, BRICK * 1.12),
            new THREE.MeshStandardMaterial({ color: base.clone().multiplyScalar(0.8), roughness: 0.8 }))
          cap.position.y = BRICK / 2 + 0.1; cap.castShadow = true; g.add(cap)
        } else if (id === 'stoop' && level === 0) {
          for (let s = 0; s < 4; s++) {
            const step = new THREE.Mesh(new THREE.BoxGeometry(BRICK * 0.5, 0.34, 0.5),
              new THREE.MeshStandardMaterial({ color: base.clone().multiplyScalar(0.92), roughness: 0.9 }))
            step.position.set(0, -BRICK / 2 + 0.17 + s * 0.34, BRICK / 2 + 0.4 + s * 0.45)
            step.castShadow = step.receiveShadow = true; g.add(step)
          }
        }
        bricks.add(g)
      })
    })

    t.hemi.intensity = neon ? 0.5 : 0.9
    t.sun.intensity = neon ? 1.1 : 1.5
    ;(t.ground.material as THREE.MeshStandardMaterial).color.set(neon ? 0x07070d : 0x14141d)

    setStats(localEstimate(serialize(), style, mergedCatalog, constraints))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, style, mergedCatalog, activePanel])

  // recompute compliance when constraints change (also clamp out-of-lot cells)
  useEffect(() => {
    const { hw, hd } = { hw: Math.floor((constraints.lot_w || 99) / 2), hd: Math.floor((constraints.lot_d || 99) / 2) }
    let changed = false
    cellsRef.current.forEach((_, k) => {
      const [x, z] = k.split(',').map(Number)
      if (Math.abs(x) > hw || Math.abs(z) > hd) { cellsRef.current.delete(k); changed = true }
    })
    if (changed) setTick((t) => t + 1)
    else { buildPad(); setStats(localEstimate(serialize(), style, mergedCatalog, constraints)) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [constraints])

  useEffect(() => {
    const c = three.current.controls
    if (c) { c.autoRotate = autoRotate; c.autoRotateSpeed = 0.85 }
  }, [autoRotate, tick])

  // ── load a shared / imported building ─────────────────────────
  const loadedRef = useRef<string>('')
  useEffect(() => {
    if (!loadDoc) return
    const sig = (loadDoc.cid || '') + loadDoc.name + (loadDoc.cells?.length || 0)
    if (sig === loadedRef.current) return
    loadedRef.current = sig
    applyDoc(loadDoc)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadDoc])

  function applyDoc(doc: PortableDoc) {
    const extra = Object.values(doc.bricks || {})
    if (extra.length) setLocalBricks((prev) => {
      const ids = new Set(prev.map((b) => b.id)); return [...prev, ...extra.filter((b) => !ids.has(b.id))]
    })
    const exPanels = Object.values(doc.panels || {})
    if (exPanels.length) setLocalPanels((prev) => {
      const ids = new Set(prev.map((p) => p.id)); return [...prev, ...exPanels.filter((p) => !ids.has(p.id))]
    })
    cellsRef.current = new Map((doc.cells || []).map((c) => [key(c.x, c.z), [...c.stack]]))
    historyRef.current = []
    if (doc.style) setStyleId(doc.style)
    if (doc.constraints) setConstraints({ lot_w: 5, lot_d: 5, max_floors: 8, ...doc.constraints })
    setTick((t) => t + 1)
    flash(`Loaded “${doc.name}” — remix & save as your own`)
    setTimeout(() => mountRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50)
  }

  // ── toolbar actions ───────────────────────────────────────────
  const undo = useCallback(() => {
    const k = historyRef.current.pop(); if (!k) return
    const stack = cellsRef.current.get(k)
    if (stack && stack.length) { stack.pop(); if (!stack.length) cellsRef.current.delete(k) }
    setTick((t) => t + 1)
  }, [])
  const clear = useCallback(() => { cellsRef.current.clear(); historyRef.current = []; setTick((t) => t + 1); flash('Cleared the lot') }, [flash])

  // load a pre-approved laneway template (instant, offline) → private draft
  const loadTemplate = useCallback((tpl: typeof LANEWAY_TEMPLATES[number]) => {
    cellsRef.current = new Map(tpl.cells.map((c) => [key(c.x, c.z), [...c.stack]]))
    historyRef.current = []
    setStyleId(tpl.style)
    setConstraints((c) => ({ ...c, lot_w: 5, lot_d: 5, max_floors: 8, ...tpl.constraints }))
    setShowTemplates(false)
    setTick((t) => t + 1)
    flash(`Loaded “${tpl.name}” — your private draft. Edit & Save (private by default).`)
  }, [flash])

  const surprise = useCallback(() => {
    cellsRef.current.clear(); historyRef.current = []
    const { hw, hd, maxF } = lotBounds()
    const ids = mergedCatalog.map((m) => m.id).filter((i) => !['solar', 'garden', 'cornice', 'stoop'].includes(i))
    const r = (n: number) => ((Math.sin(n * 91.7) * 9301 + 49297) % 233280) / 233280
    let i = 0
    for (let x = -hw; x <= hw; x++) for (let z = -hd; z <= hd; z++) {
      i++; if (r(i) < 0.25) continue
      const floors = Math.min(maxF, 2 + Math.floor(r(i * 2) * 5))
      const stack: string[] = []
      const brownish = styleId === 'brownstone'
      stack.push(brownish ? 'stoop' : (r(i * 5) < 0.5 ? 'retail' : 'living'))
      for (let f = 1; f < floors - 1; f++) stack.push(ids[Math.floor(r(i * 7 + f) * ids.length)])
      stack.push(brownish ? 'cornice' : (r(i * 9) < 0.6 ? 'solar' : 'garden'))
      cellsRef.current.set(key(x, z), stack)
    }
    setTick((t) => t + 1); flash('Generated a block ✦')
  }, [mergedCatalog, styleId, flash])

  // create a custom brick
  const createBrick = useCallback(async (draft: any) => {
    try {
      const b: ModuleSpec = await api('brick', { method: 'POST', body: { ...draft, owner } })
      setLocalBricks((prev) => [b, ...prev.filter((x) => x.id !== b.id)])
      setSelected(b.id); setShowDesigner(false)
      flash(`Forged “${b.name}”${draft.public ? ' (shared)' : ' (private)'}`)
      onBrickCreated?.()
    } catch (e: any) { flash('Forge failed: ' + (e?.message || 'error')) }
  }, [owner, onBrickCreated, flash])

  // set the active façade panel (building skin); persisted via constraints.skin
  const setSkin = useCallback((id?: string) => {
    setConstraints((c) => ({ ...c, skin: id }))
  }, [])

  // forge a custom façade panel → private by default, optional publish
  const createPanel = useCallback(async (draft: any) => {
    try {
      const p: PanelSpec = await api('panel', { method: 'POST', body: { ...draft, owner } })
      setLocalPanels((prev) => [p, ...prev.filter((x) => x.id !== p.id)])
      setConstraints((c) => ({ ...c, skin: p.id }))
      setShowPanelDesigner(false)
      flash(`Forged panel “${p.name}”${draft.public ? ' — shared' : ' — private'} & applied`)
    } catch (e: any) { flash('Panel forge failed: ' + (e?.message || 'error')) }
  }, [owner, flash])

  // save
  const doSave = useCallback(async (name: string, isPublic: boolean, desc: string) => {
    const cells = serialize()
    if (!cells.length) { flash('Place some panels first'); return null }
    try {
      const d = await api('design', { method: 'POST', body: { name, owner, cells, style: styleId, public: isPublic, description: desc, constraints } })
      onSaved?.()
      return d
    } catch (e: any) { flash('Save failed: ' + (e?.message || 'error')); return null }
  }, [serialize, owner, styleId, constraints, onSaved, flash])

  // ── run the building-standards audit through the chosen agent ──
  const doAudit = useCallback(async () => {
    const cells = serialize()
    if (!cells.length) { flash('Place some panels first'); return }
    setShowAudit(true); setAuditing(true)
    try {
      const res = await runAudit({ cells, style: styleId, constraints, provider: auditProvider, persist: false })
      setAudit(res)
      if (res.agent?.fallback_from) flash(`Fell back to built-in auditor (${res.agent.used})`)
    } catch (e: any) {
      setAudit({ verdict: 'fail', score: 0, summary: '', checks: [], red_flags: [], recommendations: [], error: e?.message || 'audit failed' })
    } finally { setAuditing(false) }
  }, [serialize, styleId, constraints, auditProvider, flash])

  // ── export the building mesh to a production 3D format ────────
  // glTF/GLB is the de-facto interchange format (Blender, Unreal, Unity,
  // Spline, three.js); OBJ/STL are universal fallbacks for CAD / printing.
  const exportModel = useCallback((format: 'glb' | 'obj' | 'stl' | 'fab') => {
    const grp: THREE.Group | undefined = three.current.bricks
    if (!grp || grp.children.length === 0) { flash('Place some panels first'); return }
    setShowExport(false)
    const fname = 'modcity-building'
    const dl = (data: BlobPart, ext: string, mime: string, msg: string) => {
      const url = URL.createObjectURL(new Blob([data], { type: mime }))
      const a = document.createElement('a'); a.href = url; a.download = `${fname}.${ext}`; a.click()
      URL.revokeObjectURL(url); flash(msg)
    }
    try {
      if (format === 'glb') {
        new GLTFExporter().parse(grp, (res) => dl(res as ArrayBuffer, 'glb', 'model/gltf-binary', 'Exported GLB — open in Blender / Unreal / any DCC tool'),
          () => flash('GLB export failed'), { binary: true })
      } else if (format === 'obj') {
        dl(new OBJExporter().parse(grp), 'obj', 'text/plain', 'Exported OBJ')
      } else if (format === 'stl') {
        dl(new STLExporter().parse(grp, { binary: false }) as unknown as string, 'stl', 'model/stl', 'Exported STL')
      } else {
        // prefab fabrication schedule — the factory panel takeoff
        const rows = buildFabSchedule(serialize(), mergedCatalog, activePanel)
        const csv = fabScheduleCSV(rows, { name: 'ModCity building', skin: activePanel?.name })
        dl(csv, 'fab.csv', 'text/csv', `Exported fab schedule — ${rows.length} prefab panels`)
      }
    } catch (e: any) { flash('Export failed: ' + (e?.message || 'error')) }
  }, [flash, serialize, mergedCatalog, activePanel])

  // import a .modcity.json file
  const onImportFile = useCallback((file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const doc = JSON.parse(String(reader.result))
        if (doc.kind !== 'building') throw new Error('not a modcity building')
        applyDoc(doc)
      } catch (e: any) { flash('Import failed: ' + (e?.message || 'bad file')) }
    }
    reader.readAsText(file)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flash])

  // palette filtering
  const visibleBricks = useMemo(() => {
    if (tab === 'mine') return mergedCatalog.filter((m) => m.custom && (m.mine || m.owner === owner))
    if (tab === 'community') return mergedCatalog.filter((m) => m.custom && m.public && m.owner !== owner)
    return mergedCatalog
  }, [mergedCatalog, tab, owner])

  const visiblePanels = useMemo(() => {
    if (panelTab === 'mine') return mergedPanels.filter((p) => p.custom && (p.mine || p.owner === owner))
    if (panelTab === 'community') return mergedPanels.filter((p) => p.custom && p.public && p.owner !== owner)
    return mergedPanels
  }, [mergedPanels, panelTab, owner])

  const comp = stats?.compliance
  const activeCode = constraints.code && constraints.code !== 'none' ? CODE_INDEX[constraints.code] : undefined
  const swatchOf = (m: ModuleSpec) => m.color || style.palette[m.tone] || style.accent

  // no WebGL (headless capture, GPU-less embed) — show a 2D elevation of the
  // design instead of the interactive foundry, and never crash the page.
  if (glFailed) {
    const cells = serialize()
    const est = localEstimate(cells, style, mergedCatalog, constraints)
    const byId = Object.fromEntries(mergedCatalog.map((m) => [m.id, m]))
    const cols = [...cells].sort((a, b) => a.x - b.x || a.z - b.z)
    return (
      <div className="relative w-full h-[72vh] min-h-[560px] rounded-2xl overflow-hidden border border-white/10 grid place-items-center"
        style={{ background: `linear-gradient(180deg, ${style?.sky || '#0a0a0f'}26, rgba(5,5,10,0.85))` }}>
        <div className="text-center px-6">
          <div className="flex items-end justify-center gap-[5px] mb-8 min-h-[120px]">
            {cols.map((c, i) => (
              <div key={i} className="flex flex-col-reverse gap-[3px]">
                {c.stack.slice(0, HARD_MAX).map((id, j) => {
                  const spec = byId[id]
                  return <span key={j} className="w-6 h-6 rounded-[3px] border border-black/40 shadow-lg"
                    style={{ background: spec?.color || style?.palette[spec?.tone || 'warm'] || '#888', opacity: spec?.glass ? 0.55 : 1 }} />
                })}
              </div>
            ))}
          </div>
          <div className="text-lg font-bold tracking-tight">
            {est.module_count} panels · {est.floors} floors · {fmtUSD(est.price_usd)}
          </div>
          <div className="mt-1 text-[11px] uppercase tracking-[0.2em] text-white/40">{style?.name} · elevation view</div>
          <p className="mt-4 text-sm text-white/50 max-w-md mx-auto leading-relaxed">
            The interactive 3D foundry needs WebGL, which this browser or embed doesn&apos;t expose —
            open the page in a regular browser to stack panels, forge modules and save designs.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div ref={rootRef} className={`overflow-hidden border border-white/10 bg-black/40 select-none ${fullscreen ? 'fixed inset-0 z-[80] w-screen h-screen rounded-none' : 'relative w-full h-[72vh] min-h-[560px] rounded-2xl'}`}>
      <div ref={mountRef} className="absolute inset-0" />

      <div className="absolute top-3 left-1/2 -translate-x-1/2 text-[11px] tracking-wide text-white/55 bg-black/40 backdrop-blur px-3 py-1.5 rounded-full border border-white/10 pointer-events-none">
        click the lot to stack panels&nbsp;·&nbsp;right-click to remove&nbsp;·&nbsp;drag to orbit
      </div>

      {/* HUD */}
      {stats && (
        <div className="absolute top-3 right-3 w-[210px] bg-black/55 backdrop-blur-md rounded-xl border border-white/10 p-3 text-white">
          <div className="text-[10px] uppercase tracking-[0.2em] text-white/45 mb-2">Live spec</div>
          <div className="flex items-baseline justify-between mb-1.5">
            <span className="text-2xl font-semibold tracking-tight">{fmtUSD(stats.price_usd)}</span>
            <span className="text-[10px] text-white/40">${fmt(stats.price_per_m2)}/m²</span>
          </div>
          <Row l="Modules" v={`${stats.module_count}`} />
          <Row l="Floors" v={`${stats.floors}`} />
          <Row l="Floor area" v={`${fmt(stats.floor_area_m2)} m²`} />
          <Row l="Sleeps" v={`${stats.occupancy}`} />
          <Row l="Lead time" v={`${stats.lead_time_days} d`} />
          <Row l="Carbon" v={`${fmt(Math.round(stats.embodied_carbon_kg / 1000))} t`} />
          {comp && Object.keys(comp).some((k) => k !== 'ok') && (
            <div className="mt-2 pt-2 border-t border-white/10">
              <div className="text-[9px] uppercase tracking-[0.2em] text-white/40 mb-1">Constraints</div>
              {(['budget', 'floors', 'carbon', 'occupancy', 'lot', 'plot_fit', 'coverage', 'fsr'] as const).map((k) => comp[k] && (
                <div key={k} className={`flex items-center justify-between text-[10px] ${comp[k]!.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
                  <span className="capitalize">{comp[k]!.ok ? '✓' : '✕'} {k.replace('_', ' ')}</span>
                  <span className="font-mono">{typeof comp[k]!.value === 'number' && k === 'budget' ? fmtUSD(comp[k]!.value as number) : comp[k]!.value}/{typeof comp[k]!.limit === 'number' && k === 'budget' ? fmtUSD(comp[k]!.limit as number) : comp[k]!.limit}</span>
                </div>
              ))}
            </div>
          )}
          {activeCode && (
            <div className="mt-2 pt-2 border-t border-white/10">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[9px] uppercase tracking-[0.2em] text-white/40">{activeCode.region}</span>
                <span className={`text-[9px] font-semibold px-1.5 rounded ${comp?.ok ? 'bg-emerald-400/20 text-emerald-200' : 'bg-rose-400/20 text-rose-200'}`}>{comp?.ok ? 'AS-OF-RIGHT' : 'OVER ENVELOPE'}</span>
              </div>
              {([['height_m', 'Height', 'm'], ['storeys', 'Storeys', ''], ['gfa', 'Floor area', 'm²'], ['footprint', 'Footprint', 'm²']] as const).map(([k, lbl, unit]) => comp?.[k] && (
                <div key={k} className={`flex items-center justify-between text-[10px] ${comp[k]!.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
                  <span>{comp[k]!.ok ? '✓' : '✕'} {lbl}</span>
                  <span className="font-mono">{comp[k]!.value}/{comp[k]!.limit}{unit}</span>
                </div>
              ))}
              {activeCode.source && (
                <a href={activeCode.source} target="_blank" rel="noreferrer" className="block mt-1 text-[9px] text-cyan-300/80 hover:text-cyan-200 truncate">code reference ↗</a>
              )}
            </div>
          )}
          {stats.net_positive_energy && (
            <div className="mt-2 text-[10px] font-medium text-emerald-300 bg-emerald-400/10 border border-emerald-400/20 rounded px-2 py-1 text-center">☀ NET-POSITIVE ENERGY</div>
          )}
        </div>
      )}

      {/* palette */}
      <div className="absolute left-3 top-12 bottom-24 flex flex-col w-[186px]">
        <div className="flex gap-1 mb-1.5">
          {(['all', 'mine', 'community'] as const).map((tb) => (
            <button key={tb} onClick={() => setTab(tb)}
              className={`flex-1 text-[10px] py-1 rounded-md capitalize border transition ${tab === tb ? 'bg-white/15 border-white/40 text-white' : 'bg-black/40 border-white/10 text-white/55 hover:bg-white/10'}`}>{tb}</button>
          ))}
        </div>
        <div className="flex-1 overflow-auto pr-1 flex flex-col gap-1.5">
          {visibleBricks.length === 0 && (
            <div className="text-[11px] text-white/40 p-3 text-center">
              {tab === 'mine' ? 'No modules yet — forge one ↓' : 'No shared modules yet.'}
            </div>
          )}
          {visibleBricks.map((mdl) => (
            <button key={mdl.id} onClick={() => setSelected(mdl.id)} title={mdl.blurb}
              className={`group flex items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-all border ${selected === mdl.id ? 'bg-white/15 border-white/40' : 'bg-black/40 border-white/10 hover:bg-white/10'}`}>
              <span className="w-5 h-5 rounded-[5px] shrink-0 border border-black/30 shadow" style={{ background: swatchOf(mdl) }} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1">
                  <span className="block text-[11px] font-medium text-white truncate leading-tight">{mdl.name}</span>
                  {mdl.custom && <span className="text-[8px] px-1 rounded bg-cyan-400/20 text-cyan-200">{mdl.mine || mdl.owner === owner ? 'mine' : 'shared'}</span>}
                </span>
                <span className="block text-[9px] text-white/40">{fmtUSD(mdl.price)} · {mdl.category}</span>
              </span>
            </button>
          ))}
        </div>
        <button onClick={() => setShowDesigner(true)}
          className="mt-1.5 text-[11px] font-semibold py-1.5 rounded-lg bg-gradient-to-r from-cyan-400/90 to-purple-400/90 text-black hover:from-cyan-300 hover:to-purple-300 transition">✎ Forge a module</button>
      </div>

      {/* params panel */}
      {showParams && (
        <div className="absolute left-[200px] top-12 w-[230px] max-h-[80vh] overflow-auto bg-black/70 backdrop-blur-md rounded-xl border border-white/10 p-3 text-white z-20">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] uppercase tracking-[0.2em] text-white/50">Parameters & constraints</div>
            <button onClick={() => setShowParams(false)} className="text-white/40 hover:text-white text-xs">✕</button>
          </div>
          <label className="block text-[11px] text-white/60 mb-2">Building code / ADU envelope
            <select value={constraints.code || 'none'}
              onChange={(e) => {
                const id = e.target.value
                const cd = CODE_INDEX[id]
                setConstraints((c) => ({ ...c, code: id, ...(cd?.recommend || {}) }))
              }}
              className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-[12px] outline-none focus:border-cyan-400">
              {CODES.map((cd) => <option key={cd.id} value={cd.id} className="bg-[#13131c]">{cd.name}</option>)}
            </select>
          </label>
          {activeCode && <p className="text-[9px] text-cyan-200/70 mb-2 leading-snug">{activeCode.note}</p>}
          <NumRow label="Lot width" value={constraints.lot_w} min={1} max={9} onChange={(v) => setConstraints((c) => ({ ...c, lot_w: v }))} />
          <NumRow label="Lot depth" value={constraints.lot_d} min={1} max={9} onChange={(v) => setConstraints((c) => ({ ...c, lot_d: v }))} />
          <NumRow label="Max floors" value={constraints.max_floors} min={1} max={HARD_MAX} onChange={(v) => setConstraints((c) => ({ ...c, max_floors: v }))} />
          <ConRow label="Budget cap" suffix="$" value={constraints.max_budget} step={50000} onChange={(v) => setConstraints((c) => ({ ...c, max_budget: v }))} />
          <ConRow label="Carbon cap" suffix="kg" value={constraints.max_carbon_kg} step={10000} onChange={(v) => setConstraints((c) => ({ ...c, max_carbon_kg: v }))} />
          <ConRow label="Min sleeps" value={constraints.min_occupancy} step={1} onChange={(v) => setConstraints((c) => ({ ...c, min_occupancy: v }))} />

          {/* realistic parcel — metres + setbacks + zoning caps */}
          <div className="mt-2 pt-2 border-t border-white/10">
            <div className="text-[9px] uppercase tracking-[0.18em] text-white/40 mb-1.5">Realistic plot (metres)</div>
            <MRow label="Plot width" value={constraints.plot_w_m} step={0.5} onChange={(v) => setConstraints((c) => ({ ...c, plot_w_m: v }))} />
            <MRow label="Plot depth" value={constraints.plot_d_m} step={0.5} onChange={(v) => setConstraints((c) => ({ ...c, plot_d_m: v }))} />
            <MRow label="Setback front" value={constraints.setback_front_m} step={0.5} onChange={(v) => setConstraints((c) => ({ ...c, setback_front_m: v }))} />
            <MRow label="Setback rear" value={constraints.setback_rear_m} step={0.5} onChange={(v) => setConstraints((c) => ({ ...c, setback_rear_m: v }))} />
            <MRow label="Setback side" value={constraints.setback_side_m} step={0.5} onChange={(v) => setConstraints((c) => ({ ...c, setback_side_m: v }))} />
            <MRow label="Max coverage" value={constraints.max_coverage_pct} step={5} suffix="%" onChange={(v) => setConstraints((c) => ({ ...c, max_coverage_pct: v }))} />
            <MRow label="Max FSR" value={constraints.max_fsr} step={0.05} suffix="×" onChange={(v) => setConstraints((c) => ({ ...c, max_fsr: v }))} />
            {stats?.plot && (
              <p className="text-[9px] text-cyan-200/70 leading-snug mt-0.5">
                Buildable {stats.plot.buildable_w_m}×{stats.plot.buildable_d_m} m → {stats.plot.build_cells_w}×{stats.plot.build_cells_d} grid · coverage {stats.plot.coverage_pct}% · FSR {stats.plot.fsr}
              </p>
            )}
          </div>
          <p className="text-[9px] text-white/35 mt-1">Setbacks carve a buildable envelope out of the real lot; coverage & FSR are checked live and feed the audit.</p>
        </div>
      )}

      {/* building-standards audit panel — pluggable agent */}
      {showAudit && (
        <div className="absolute left-1/2 top-10 -translate-x-1/2 w-[360px] max-h-[82vh] overflow-auto bg-[#0d0d14]/95 backdrop-blur-md rounded-2xl border border-white/15 p-4 text-white z-40 shadow-2xl">
          <div className="flex items-center justify-between mb-3">
            <div className="text-[11px] uppercase tracking-[0.2em] text-white/55">Building-standards audit</div>
            <button onClick={() => setShowAudit(false)} className="text-white/40 hover:text-white text-sm">✕</button>
          </div>
          <div className="text-[9px] uppercase tracking-[0.18em] text-white/40 mb-1.5">Audit agent</div>
          <div className="flex items-center gap-1.5 mb-2">
            {AUDIT_PROVIDERS.map((p) => (
              <button key={p.id} onClick={() => setAuditProvider(p.id)} title={p.blurb}
                className={`flex-1 text-[11px] py-1.5 rounded-lg border transition ${auditProvider === p.id ? 'bg-white/15 border-white/40 text-white' : 'bg-black/40 border-white/10 text-white/60 hover:bg-white/10'}`}>{p.label}</button>
            ))}
          </div>
          <button disabled={auditing} onClick={doAudit}
            className="w-full py-2 mb-3 rounded-lg text-[12px] font-semibold text-black bg-gradient-to-r from-amber-300 to-cyan-300 hover:from-amber-200 hover:to-cyan-200 disabled:opacity-50 transition">
            {auditing ? 'Auditing…' : `Run audit · ${AUDIT_PROVIDERS.find((p) => p.id === auditProvider)?.label}`}
          </button>
          {audit?.error && <div className="text-[12px] text-rose-300 bg-rose-400/10 border border-rose-400/30 rounded-lg p-2">{audit.error}</div>}
          {audit && !audit.error && (
            <>
              <div className={`rounded-xl p-3 mb-2 border ${AUDIT_VERDICT_CLS[audit.verdict] || 'border-white/15 bg-white/5'}`}>
                <div className="flex items-center justify-between">
                  <span className="text-base font-bold uppercase tracking-wide">{audit.verdict}</span>
                  <span className="text-2xl font-mono">{audit.score}<span className="text-xs text-white/40">/100</span></span>
                </div>
                <p className="text-[11px] text-white/75 mt-1 leading-snug">{audit.summary}</p>
              </div>
              <div className="text-[9px] text-white/40 mb-2">
                agent: {audit.agent?.used || audit.provider}{audit.model ? ` · ${audit.model}` : ''}
                {audit.agent?.fallback_from ? ` · fell back from ${audit.agent.fallback_from}` : ''}
              </div>
              {stats?.cost_breakdown && stats.cost_breakdown.modules.length > 0 && (
                <div className="mb-2 rounded-lg border border-white/10 bg-black/30 p-2">
                  <div className="text-[9px] uppercase tracking-[0.18em] text-white/40 mb-1">Units cost · bill of quantities</div>
                  {stats.cost_breakdown.modules.map((it) => (
                    <div key={it.id} className="flex items-center justify-between text-[10px] text-white/70">
                      <span>{it.qty}× {it.name}</span>
                      <span className="font-mono">{fmtUSD(it.unit_price)} · {fmtUSD(it.line_total)}</span>
                    </div>
                  ))}
                  <div className="flex items-center justify-between text-[10px] font-semibold text-white mt-1 pt-1 border-t border-white/10">
                    <span>{stats.cost_breakdown.unit_count} units · {style.name}</span>
                    <span className="font-mono">{fmtUSD(stats.price_usd)}</span>
                  </div>
                </div>
              )}
              <div className="flex flex-col gap-1.5">
                {audit.checks.map((c, i) => (
                  <div key={i} className="rounded-lg border border-white/10 bg-black/30 p-2">
                    <div className="flex items-start justify-between gap-2">
                      <span className={`text-[11px] font-medium ${AUDIT_STATUS_CLS[c.status] || 'text-white'}`}>{statusIcon(c.status)} {c.standard}</span>
                      <span className="text-[8px] uppercase px-1 rounded bg-white/10 text-white/50 shrink-0">{c.category}</span>
                    </div>
                    <p className="text-[10px] text-white/60 mt-0.5 leading-snug">{c.detail}</p>
                    {c.recommendation && <p className="text-[10px] text-cyan-200/70 mt-0.5 leading-snug">→ {c.recommendation}</p>}
                  </div>
                ))}
              </div>
              {audit.disclaimer && <p className="text-[9px] text-white/35 mt-3 leading-snug">{audit.disclaimer}</p>}
            </>
          )}
        </div>
      )}

      {/* bottom controls */}
      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 w-[calc(100%-24px)] max-w-[900px]">
        <div className="flex flex-wrap items-center justify-center gap-1.5 mb-2">
          {styles.map((s) => (
            <button key={s.id} onClick={() => setStyleId(s.id)} title={s.vibe}
              className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-all border ${styleId === s.id ? 'text-black border-transparent' : 'text-white/70 border-white/15 bg-black/40 hover:bg-white/10'}`}
              style={styleId === s.id ? { background: s.accent } : {}}>{s.name}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-center gap-1.5">
          <div className="relative">
            <Btn onClick={() => setShowTemplates((v) => !v)} active={showTemplates}>🏠 Laneway</Btn>
            {showTemplates && (
              <div className="absolute bottom-full mb-1.5 left-1/2 -translate-x-1/2 w-[230px] bg-[#13131c] border border-white/15 rounded-xl p-1.5 shadow-xl z-30">
                <div className="text-[9px] uppercase tracking-[0.18em] text-white/40 px-2 pt-1 pb-1.5">pre-approved ADU templates</div>
                {LANEWAY_TEMPLATES.map((tpl) => (
                  <button key={tpl.id} onClick={() => loadTemplate(tpl)}
                    className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-white/10 transition">
                    <span className="block text-[12px] font-medium text-white">{tpl.name}</span>
                    <span className="block text-[9px] text-white/40">{CODE_INDEX[tpl.code]?.region} · {tpl.description}</span>
                  </button>
                ))}
                <div className="text-[9px] text-white/35 px-2 pt-1.5 pb-1 leading-snug">Loads as your private draft — fits the code envelope out of the box.</div>
              </div>
            )}
          </div>
          <div className="relative">
            <Btn onClick={() => setShowPanels((v) => !v)} active={showPanels}>▦ Panels</Btn>
            {showPanels && (
              <div className="absolute bottom-full mb-1.5 left-1/2 -translate-x-1/2 w-[260px] bg-[#13131c] border border-white/15 rounded-xl p-2 shadow-xl z-30">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[9px] uppercase tracking-[0.18em] text-white/40">façade panel · building skin</span>
                </div>
                <div className="flex gap-1 mb-1.5">
                  {(['all', 'mine', 'community'] as const).map((tb) => (
                    <button key={tb} onClick={() => setPanelTab(tb)}
                      className={`flex-1 text-[10px] py-1 rounded-md capitalize border transition ${panelTab === tb ? 'bg-white/15 border-white/40 text-white' : 'bg-black/40 border-white/10 text-white/55 hover:bg-white/10'}`}>{tb}</button>
                  ))}
                </div>
                <div className="max-h-[230px] overflow-auto flex flex-col gap-1">
                  <button onClick={() => setSkin(undefined)}
                    className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-left border ${!constraints.skin ? 'bg-white/15 border-white/40' : 'bg-black/40 border-white/10 hover:bg-white/10'}`}>
                    <span className="w-5 h-5 rounded-[5px] shrink-0 border border-black/30" style={{ background: `linear-gradient(135deg, ${style.accent}, #222)` }} />
                    <span className="text-[11px] font-medium text-white">Style default</span>
                  </button>
                  {visiblePanels.map((pn) => (
                    <button key={pn.id} onClick={() => setSkin(pn.id)} title={pn.fab_notes}
                      className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-left border ${constraints.skin === pn.id ? 'bg-white/15 border-white/40' : 'bg-black/40 border-white/10 hover:bg-white/10'}`}>
                      <span className="w-5 h-5 rounded-[5px] shrink-0 border border-black/30" style={{ background: pn.color }} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1">
                          <span className="block text-[11px] font-medium text-white truncate">{pn.name}</span>
                          {pn.custom && <span className="text-[8px] px-1 rounded bg-cyan-400/20 text-cyan-200">{pn.mine || pn.owner === owner ? 'mine' : 'shared'}</span>}
                        </span>
                        <span className="block text-[9px] text-white/40 truncate">{pn.assembly} · {pn.thickness_mm}mm · RSI {pn.r_value}</span>
                      </span>
                    </button>
                  ))}
                </div>
                <button onClick={() => { setShowPanels(false); setShowPanelDesigner(true) }}
                  className="w-full mt-1.5 text-[11px] font-semibold py-1.5 rounded-lg bg-gradient-to-r from-amber-300/90 to-cyan-300/90 text-black hover:from-amber-200 hover:to-cyan-200 transition">✎ Forge a panel</button>
              </div>
            )}
          </div>
          <Btn onClick={toggleFullscreen} active={fullscreen}>{fullscreen ? '⤡ Exit full screen' : '⛶ Full screen'}</Btn>
          <Btn onClick={() => setAutoRotate((v) => !v)} active={autoRotate}>⟳ {autoRotate ? 'Spinning' : 'Rotate'}</Btn>
          <Btn onClick={() => setShowParams((v) => !v)} active={showParams}>⚙ Parameters</Btn>
          <Btn onClick={() => { setShowAudit((v) => !v); if (!showAudit && !audit) doAudit() }} active={showAudit}>🛡 Audit standards</Btn>
          <Btn onClick={undo}>↩ Undo</Btn>
          <Btn onClick={clear}>✕ Clear</Btn>
          <Btn onClick={surprise}>✦ Surprise</Btn>
          <Btn onClick={() => fileRef.current?.click()}>⤓ Import</Btn>
          <div className="relative">
            <Btn onClick={() => setShowExport((v) => !v)} active={showExport}>⬚ Export 3D</Btn>
            {showExport && (
              <div className="absolute bottom-full mb-1.5 left-1/2 -translate-x-1/2 w-[210px] bg-[#13131c] border border-white/15 rounded-xl p-1.5 shadow-xl z-30">
                <div className="text-[9px] uppercase tracking-[0.18em] text-white/40 px-2 pt-1 pb-1.5">production 3D formats</div>
                {([['glb', 'glTF / GLB', 'Blender · Unreal · Unity'], ['obj', 'OBJ + mesh', 'universal DCC / CAD'], ['stl', 'STL', '3D print / mesh'], ['fab', 'Fab schedule (CSV)', 'prefab factory panel takeoff']] as const).map(([f, t, d]) => (
                  <button key={f} onClick={() => exportModel(f)}
                    className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-white/10 transition">
                    <span className="block text-[12px] font-medium text-white">{t}</span>
                    <span className="block text-[9px] text-white/40">{d}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button onClick={() => setShowSave(true)}
            className="px-4 py-1.5 rounded-lg text-[12px] font-semibold text-black bg-gradient-to-r from-emerald-300 to-cyan-300 hover:from-emerald-200 hover:to-cyan-200 transition-all shadow-lg shadow-cyan-500/20">⬇ Save & share</button>
        </div>
      </div>

      <input ref={fileRef} type="file" accept=".json,application/json" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onImportFile(f); e.target.value = '' }} />

      {toast && (
        <div className="absolute bottom-28 left-1/2 -translate-x-1/2 bg-white text-black text-[12px] font-medium px-4 py-2 rounded-full shadow-xl z-30">{toast}</div>
      )}

      {showDesigner && <BrickDesigner style={style} onClose={() => setShowDesigner(false)} onCreate={createBrick} />}
      {showSave && <SaveModal onClose={() => setShowSave(false)} onSave={doSave} flash={flash} />}
      {showPanelDesigner && <PanelDesigner onClose={() => setShowPanelDesigner(false)} onCreate={createPanel} />}
    </div>
  )
}

function Row({ l, v }: { l: string; v: string }) {
  return <div className="flex items-center justify-between text-[11px] py-0.5"><span className="text-white/45">{l}</span><span className="text-white/90 font-medium">{v}</span></div>
}
function Btn({ children, onClick, active }: { children: any; onClick: () => void; active?: boolean }) {
  return <button onClick={onClick} className={`px-3 py-1.5 rounded-lg text-[12px] font-medium border transition-all ${active ? 'bg-white/20 border-white/40 text-white' : 'bg-black/40 border-white/15 text-white/75 hover:bg-white/10'}`}>{children}</button>
}
function NumRow({ label, value, min, max, onChange }: { label: string; value?: number; min: number; max: number; onChange: (v: number) => void }) {
  const v = value ?? min
  return (
    <div className="flex items-center justify-between mb-1.5">
      <span className="text-[11px] text-white/60">{label}</span>
      <div className="flex items-center gap-1">
        <button onClick={() => onChange(Math.max(min, v - 1))} className="w-5 h-5 rounded bg-white/10 hover:bg-white/20 text-xs">−</button>
        <span className="w-7 text-center text-[12px] font-mono">{v}</span>
        <button onClick={() => onChange(Math.min(max, v + 1))} className="w-5 h-5 rounded bg-white/10 hover:bg-white/20 text-xs">+</button>
      </div>
    </div>
  )
}
function MRow({ label, value, step = 1, suffix = 'm', onChange }: { label: string; value?: number; step?: number; suffix?: string; onChange: (v: number | undefined) => void }) {
  return (
    <div className="flex items-center justify-between mb-1.5">
      <span className="text-[11px] text-white/60">{label}</span>
      <div className="flex items-center gap-1">
        <input type="number" step={step} value={value ?? ''} placeholder="—"
          onChange={(e) => { const v = e.target.value; onChange(v === '' ? undefined : Number(v)) }}
          className="w-16 bg-black/40 border border-white/15 rounded px-1.5 py-0.5 text-[11px] font-mono text-right outline-none focus:border-cyan-400" />
        <span className="text-[10px] text-white/35 w-4">{suffix}</span>
      </div>
    </div>
  )
}
function ConRow({ label, value, step, suffix, onChange }: { label: string; value?: number; step: number; suffix?: string; onChange: (v: number | undefined) => void }) {
  const on = value != null
  return (
    <div className="flex items-center justify-between mb-1.5">
      <label className="text-[11px] text-white/60 flex items-center gap-1.5">
        <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked ? step * (suffix === '$' ? 6 : suffix === 'kg' ? 5 : 4) : undefined)} className="accent-cyan-400" />
        {label}
      </label>
      {on && (
        <div className="flex items-center gap-1">
          <button onClick={() => onChange(Math.max(0, (value || 0) - step))} className="w-5 h-5 rounded bg-white/10 hover:bg-white/20 text-xs">−</button>
          <span className="text-[11px] font-mono text-white/80 min-w-[44px] text-right">{suffix === '$' ? '$' + ((value || 0) / 1000) + 'k' : (value || 0) + (suffix === 'kg' ? '' : '')}</span>
          <button onClick={() => onChange((value || 0) + step)} className="w-5 h-5 rounded bg-white/10 hover:bg-white/20 text-xs">+</button>
        </div>
      )}
    </div>
  )
}

/* ── Brick forge modal ─────────────────────────────────────────── */
function BrickDesigner({ style, onClose, onCreate }: { style: StyleSpec; onClose: () => void; onCreate: (d: any) => void }) {
  const [name, setName] = useState('')
  const [category, setCategory] = useState('living')
  const [color, setColor] = useState('#9c6b46')
  const [price, setPrice] = useState(18000)
  const [carbon, setCarbon] = useState(2000)
  const [lead, setLead] = useState(21)
  const [glass, setGlass] = useState(false)
  const [blurb, setBlurb] = useState('')
  const [isPublic, setIsPublic] = useState(false)
  return (
    <div className="absolute inset-0 z-40 bg-black/70 backdrop-blur-sm grid place-items-center p-4" onClick={onClose}>
      <div className="w-full max-w-md bg-[#13131c] rounded-2xl border border-white/10 p-6 text-white" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold">✎ Forge a panel</h3>
          <button onClick={onClose} className="text-white/40 hover:text-white">✕</button>
        </div>
        <div className="flex gap-4 mb-4">
          <div className="w-20 h-20 rounded-xl shrink-0 border border-white/15 shadow-inner" style={{ background: color, opacity: glass ? 0.55 : 1 }} />
          <div className="flex-1">
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Panel name (e.g. Sky Pool)"
              className="w-full bg-black/40 border border-white/15 rounded-lg px-3 py-2 text-sm mb-2 focus:border-cyan-400 outline-none" />
            <input value={blurb} onChange={(e) => setBlurb(e.target.value)} placeholder="One-line description"
              className="w-full bg-black/40 border border-white/15 rounded-lg px-3 py-1.5 text-[12px] outline-none focus:border-cyan-400" />
          </div>
        </div>
        <div className="flex gap-1.5 mb-3 flex-wrap">
          {PRESET_COLORS.map((c) => <button key={c} onClick={() => setColor(c)} className={`w-6 h-6 rounded-md border ${color === c ? 'border-white' : 'border-black/30'}`} style={{ background: c }} />)}
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="w-6 h-6 rounded-md bg-transparent border border-white/20 cursor-pointer" />
        </div>
        <div className="grid grid-cols-2 gap-2 mb-3">
          <label className="text-[11px] text-white/60">Category
            <select value={category} onChange={(e) => setCategory(e.target.value)} className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-sm capitalize outline-none">
              {CATEGORIES.map((c) => <option key={c} value={c} className="bg-[#13131c]">{c}</option>)}
            </select>
          </label>
          <Field label="Price ($)" value={price} step={1000} onChange={setPrice} />
          <Field label="Carbon (kg)" value={carbon} step={100} onChange={setCarbon} />
          <Field label="Lead (days)" value={lead} step={1} onChange={setLead} />
        </div>
        <div className="flex items-center justify-between mb-4 text-[12px]">
          <label className="flex items-center gap-2"><input type="checkbox" checked={glass} onChange={(e) => setGlass(e.target.checked)} className="accent-cyan-400" /> Glass / translucent</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} className="accent-cyan-400" /> Share publicly</label>
        </div>
        <button disabled={!name.trim()} onClick={() => onCreate({ name, category, color, price, carbon_kg: carbon, lead_days: lead, glass, blurb, public: isPublic })}
          className="w-full py-2.5 rounded-lg font-semibold text-black bg-gradient-to-r from-cyan-300 to-purple-300 disabled:opacity-40 hover:from-cyan-200 hover:to-purple-200 transition">
          {isPublic ? 'Forge & publish to library' : 'Forge (private)'}
        </button>
      </div>
    </div>
  )
}
function Field({ label, value, step, onChange }: { label: string; value: number; step: number; onChange: (v: number) => void }) {
  return (
    <label className="text-[11px] text-white/60">{label}
      <input type="number" value={value} step={step} onChange={(e) => onChange(Number(e.target.value))}
        className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-sm outline-none focus:border-cyan-400" />
    </label>
  )
}

/* ── Panel forge modal (designable, shareable façade + prefab spec) ── */
const PANEL_KINDS = ['solid', 'window', 'curtain', 'louver'] as const
const PANEL_MATERIALS = ['wood', 'metal', 'concrete', 'glass', 'composite']
const PANEL_ASSEMBLIES = ['CLT (cross-laminated timber)', 'SIP (structural insulated panel)', 'Unitised aluminium curtain wall', 'Precast concrete sandwich', 'Steel-stud + rainscreen', 'Mass-timber + timber clad']
function PanelDesigner({ onClose, onCreate }: { onClose: () => void; onCreate: (d: any) => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<typeof PANEL_KINDS[number]>('window')
  const [color, setColor] = useState('#caa472')
  const [frame, setFrame] = useState('#5c3d22')
  const [glazing, setGlazing] = useState(0.35)
  const [winCols, setWinCols] = useState(2)
  const [winRows, setWinRows] = useState(2)
  const [material, setMaterial] = useState('wood')
  const [reveal, setReveal] = useState(true)
  const [assembly, setAssembly] = useState(PANEL_ASSEMBLIES[0])
  const [thickness, setThickness] = useState(180)
  const [weight, setWeight] = useState(200)
  const [rValue, setRValue] = useState(4.0)
  const [connection, setConnection] = useState('screwed (clip rail)')
  const [fabNotes, setFabNotes] = useState('')
  const [price, setPrice] = useState(3500)
  const [carbon, setCarbon] = useState(350)
  const [lead, setLead] = useState(15)
  const [isPublic, setIsPublic] = useState(false)
  return (
    <div className="absolute inset-0 z-40 bg-black/70 backdrop-blur-sm grid place-items-center p-4" onClick={onClose}>
      <div className="w-full max-w-lg bg-[#13131c] rounded-2xl border border-white/10 p-6 text-white max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold">✎ Forge a façade panel</h3>
          <button onClick={onClose} className="text-white/40 hover:text-white">✕</button>
        </div>
        <div className="flex gap-4 mb-3">
          <div className="w-20 h-20 rounded-xl shrink-0 border border-white/15 grid place-items-center" style={{ background: color }}>
            {kind !== 'solid' && <span className="w-9 h-9 rounded" style={{ background: kind === 'curtain' ? '#0a1018cc' : '#121821cc', border: `2px solid ${frame}` }} />}
          </div>
          <div className="flex-1">
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Panel name (e.g. Charred Cedar 2×2)"
              className="w-full bg-black/40 border border-white/15 rounded-lg px-3 py-2 text-sm mb-2 focus:border-amber-400 outline-none" />
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[11px] text-white/60">Type
                <select value={kind} onChange={(e) => setKind(e.target.value as any)} className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-sm capitalize outline-none">
                  {PANEL_KINDS.map((k) => <option key={k} value={k} className="bg-[#13131c]">{k}</option>)}
                </select>
              </label>
              <label className="text-[11px] text-white/60">Material
                <select value={material} onChange={(e) => setMaterial(e.target.value)} className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-sm capitalize outline-none">
                  {PANEL_MATERIALS.map((mt) => <option key={mt} value={mt} className="bg-[#13131c]">{mt}</option>)}
                </select>
              </label>
            </div>
          </div>
        </div>
        <div className="flex gap-1.5 mb-3 flex-wrap items-center">
          <span className="text-[10px] text-white/50">Cladding</span>
          {PRESET_COLORS.map((c) => <button key={c} onClick={() => setColor(c)} className={`w-6 h-6 rounded-md border ${color === c ? 'border-white' : 'border-black/30'}`} style={{ background: c }} />)}
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="w-6 h-6 rounded-md bg-transparent border border-white/20 cursor-pointer" />
          <span className="text-[10px] text-white/50 ml-2">Frame</span>
          <input type="color" value={frame} onChange={(e) => setFrame(e.target.value)} className="w-6 h-6 rounded-md bg-transparent border border-white/20 cursor-pointer" />
        </div>
        {kind !== 'solid' && kind !== 'louver' && (
          <div className="grid grid-cols-3 gap-2 mb-3 items-end">
            <label className="text-[11px] text-white/60 col-span-1">Glazing {Math.round(glazing * 100)}%
              <input type="range" min={0} max={1} step={0.05} value={glazing} onChange={(e) => setGlazing(Number(e.target.value))} className="w-full mt-1 accent-cyan-400" />
            </label>
            <Field label="Window cols" value={winCols} step={1} onChange={setWinCols} />
            <Field label="Window rows" value={winRows} step={1} onChange={setWinRows} />
          </div>
        )}
        <label className="flex items-center gap-2 text-[12px] mb-3"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} className="accent-amber-400" /> Cladding reveals (board lines)</label>

        <div className="text-[10px] uppercase tracking-[0.2em] text-amber-300/70 mb-2">Prefab fabrication spec</div>
        <label className="text-[11px] text-white/60 block mb-2">Assembly
          <select value={assembly} onChange={(e) => setAssembly(e.target.value)} className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-sm outline-none">
            {PANEL_ASSEMBLIES.map((a) => <option key={a} value={a} className="bg-[#13131c]">{a}</option>)}
          </select>
        </label>
        <div className="grid grid-cols-3 gap-2 mb-2">
          <Field label="Thickness (mm)" value={thickness} step={10} onChange={setThickness} />
          <Field label="Weight (kg)" value={weight} step={10} onChange={setWeight} />
          <Field label="R-value (RSI)" value={rValue} step={0.1} onChange={setRValue} />
        </div>
        <label className="text-[11px] text-white/60 block mb-2">Connection
          <input value={connection} onChange={(e) => setConnection(e.target.value)} placeholder="e.g. bolted (cast-in ferrules)"
            className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-sm outline-none focus:border-amber-400" />
        </label>
        <label className="text-[11px] text-white/60 block mb-3">Fabrication notes
          <input value={fabNotes} onChange={(e) => setFabNotes(e.target.value)} placeholder="factory notes — glazing, sealing, finish…"
            className="w-full mt-1 bg-black/40 border border-white/15 rounded-lg px-2 py-1.5 text-[12px] outline-none focus:border-amber-400" />
        </label>
        <div className="grid grid-cols-3 gap-2 mb-3">
          <Field label="Price ($)" value={price} step={100} onChange={setPrice} />
          <Field label="Carbon (kg)" value={carbon} step={50} onChange={setCarbon} />
          <Field label="Lead (days)" value={lead} step={1} onChange={setLead} />
        </div>
        <label className="flex items-center gap-2 text-[12px] mb-4"><input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} className="accent-amber-400" /> Share publicly to the panel library (off = private)</label>
        <button disabled={!name.trim()} onClick={() => onCreate({ name, kind, color, frame, glazing, win_cols: winCols, win_rows: winRows, material, reveal, thickness_mm: thickness, weight_kg: weight, r_value: rValue, connection, assembly, fab_notes: fabNotes, price, carbon_kg: carbon, lead_days: lead, public: isPublic })}
          className="w-full py-2.5 rounded-lg font-semibold text-black bg-gradient-to-r from-amber-300 to-cyan-300 disabled:opacity-40 hover:from-amber-200 hover:to-cyan-200 transition">
          {isPublic ? 'Forge & publish panel' : 'Forge panel (private)'}
        </button>
      </div>
    </div>
  )
}

/* ── Save / share modal ────────────────────────────────────────── */
function SaveModal({ onClose, onSave, flash }: { onClose: () => void; onSave: (n: string, p: boolean, d: string) => Promise<any>; flash: (m: string) => void }) {
  const [name, setName] = useState('My ModCity Building')
  const [desc, setDesc] = useState('')
  const [isPublic, setIsPublic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<any>(null)

  const shareLink = saved ? `${typeof window !== 'undefined' ? window.location.origin : ''}/modcity/?${saved.cid ? 'cid=' + saved.cid : 'd=' + saved.id}` : ''

  const submit = async () => {
    setBusy(true)
    const d = await onSave(name, isPublic, desc)
    setBusy(false)
    if (d) { setSaved(d); flash(isPublic ? 'Saved & published ✓' : 'Saved (private) ✓') }
  }
  const copy = (text: string, what: string) => { navigator.clipboard?.writeText(text); flash(`${what} copied`) }
  const download = async () => {
    try {
      const exp = await api(`design/${saved.id}/export`)
      const blob = new Blob([JSON.stringify(exp.doc, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob); const a = document.createElement('a')
      a.href = url; a.download = exp.filename || 'building.modcity.json'; a.click(); URL.revokeObjectURL(url)
      flash('Downloaded .modcity.json')
    } catch { flash('Export failed') }
  }

  return (
    <div className="absolute inset-0 z-40 bg-black/70 backdrop-blur-sm grid place-items-center p-4" onClick={onClose}>
      <div className="w-full max-w-md bg-[#13131c] rounded-2xl border border-white/10 p-6 text-white" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold">{saved ? '✓ Saved' : '⬇ Save & share'}</h3>
          <button onClick={onClose} className="text-white/40 hover:text-white">✕</button>
        </div>
        {!saved ? (
          <>
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Building name"
              className="w-full bg-black/40 border border-white/15 rounded-lg px-3 py-2 text-sm mb-2 outline-none focus:border-emerald-400" />
            <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description (optional)"
              className="w-full bg-black/40 border border-white/15 rounded-lg px-3 py-1.5 text-[12px] mb-3 outline-none focus:border-emerald-400" />
            <label className="flex items-start gap-2 text-[12px] mb-4 cursor-pointer">
              <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} className="accent-emerald-400 mt-0.5" />
              <span><span className="font-medium">Publish to the shared city</span><br /><span className="text-white/45">Off by default — your building stays private until you flip this. Public buildings can be copied & remixed by anyone.</span></span>
            </label>
            <button disabled={busy} onClick={submit} className="w-full py-2.5 rounded-lg font-semibold text-black bg-gradient-to-r from-emerald-300 to-cyan-300 disabled:opacity-50 hover:from-emerald-200 hover:to-cyan-200 transition">
              {busy ? 'Saving…' : isPublic ? 'Save & publish' : 'Save (private)'}
            </button>
          </>
        ) : (
          <>
            <div className="text-[13px] text-white/70 mb-3">“{saved.name}” · {saved.public ? 'public' : 'private'} · {fmtUSD(saved.stats.price_usd)} · {saved.stats.floors} floors</div>
            {saved.cid && (
              <div className="mb-2">
                <div className="text-[10px] uppercase tracking-wider text-white/40 mb-1">localfs CID</div>
                <div className="flex gap-2"><code className="flex-1 bg-black/40 border border-white/10 rounded px-2 py-1.5 text-[11px] font-mono truncate">{saved.cid}</code>
                  <button onClick={() => copy(saved.cid, 'CID')} className="px-2 rounded bg-white/10 hover:bg-white/20 text-[11px]">copy</button></div>
              </div>
            )}
            <div className="mb-3">
              <div className="text-[10px] uppercase tracking-wider text-white/40 mb-1">Share link</div>
              <div className="flex gap-2"><code className="flex-1 bg-black/40 border border-white/10 rounded px-2 py-1.5 text-[11px] font-mono truncate">{shareLink}</code>
                <button onClick={() => copy(shareLink, 'Link')} className="px-2 rounded bg-white/10 hover:bg-white/20 text-[11px]">copy</button></div>
            </div>
            <div className="flex gap-2">
              <button onClick={download} className="flex-1 py-2 rounded-lg text-[12px] font-medium bg-white/10 hover:bg-white/20">⤓ Download .json</button>
              <button onClick={onClose} className="flex-1 py-2 rounded-lg text-[12px] font-semibold text-black bg-emerald-300 hover:bg-emerald-200">Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
