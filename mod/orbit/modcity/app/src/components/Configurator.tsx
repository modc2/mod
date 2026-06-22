'use client'

import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import {
  ModuleSpec, StyleSpec, Cell, Estimate, Constraints, PortableDoc,
  localEstimate, api, ownerId,
} from '@/lib/modcity'

const STEP = 3
const BRICK = 2.86
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

  const [selected, setSelected] = useState('parlor')
  const [styleId, setStyleId] = useState('brownstone')
  const [tab, setTab] = useState<'all' | 'mine' | 'community'>('all')
  const [stats, setStats] = useState<Estimate | null>(null)
  const [autoRotate, setAutoRotate] = useState(true)
  const [toast, setToast] = useState('')
  const [tick, setTick] = useState(0)

  const [constraints, setConstraints] = useState<Constraints>({ lot_w: 5, lot_d: 5, max_floors: 8 })
  const [showParams, setShowParams] = useState(false)
  const [showDesigner, setShowDesigner] = useState(false)
  const [showSave, setShowSave] = useState(false)

  const style = useMemo(() => styles.find((s) => s.id === styleId) || styles[0], [styles, styleId])
  const selectedRef = useRef(selected); selectedRef.current = selected
  const constraintsRef = useRef(constraints); constraintsRef.current = constraints
  const fileRef = useRef<HTMLInputElement>(null)

  const flash = useCallback((msg: string) => {
    setToast(msg); window.clearTimeout((three.current as any)._t)
    ;(three.current as any)._t = window.setTimeout(() => setToast(''), 2400)
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
    const mount = mountRef.current!
    const scene = new THREE.Scene()
    const W = mount.clientWidth, H = mount.clientHeight
    const camera = new THREE.PerspectiveCamera(40, W / H, 0.1, 1000)
    camera.position.set(20, 17, 24)
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(W, H)
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    mount.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true; controls.dampingFactor = 0.08
    controls.minDistance = 9; controls.maxDistance = 70
    controls.maxPolarAngle = Math.PI / 2.04
    controls.target.set(0, 4, 0)

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

  // faint neighbouring city blocks → urban context (built once)
  function buildContext() {
    const t = three.current; if (!t.context) return
    const ctx = t.context
    while (ctx.children.length) { const c = ctx.children.pop(); c.geometry?.dispose?.(); c.material?.dispose?.() }
    const ring = [-5, -4, -3, 3, 4, 5]
    const rand = (i: number) => ((Math.sin(i * 127.1) * 43758.5) % 1 + 1) % 1
    let i = 0
    for (let gx = -6; gx <= 6; gx++) {
      for (let gz = -6; gz <= 6; gz++) {
        if (Math.abs(gx) <= 2 && Math.abs(gz) <= 2) continue
        if (!ring.includes(gx) && !ring.includes(gz)) continue
        i++
        if (rand(i) < 0.45) continue
        const hgt = 2 + Math.floor(rand(i * 3) * 6)
        const box = new THREE.Mesh(
          new THREE.BoxGeometry(STEP * 0.92, hgt * STEP, STEP * 0.92),
          new THREE.MeshStandardMaterial({ color: new THREE.Color().setHSL(0.62, 0.05, 0.16 + rand(i * 7) * 0.06), roughness: 1 }),
        )
        box.position.set(gx * STEP, (hgt * STEP) / 2, gz * STEP)
        box.castShadow = true; box.receiveShadow = true
        ctx.add(box)
      }
    }
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

  // ── window / facade detail ────────────────────────────────────
  function addFacade(group: THREE.Group, spec: ModuleSpec, baseColor: THREE.Color, style: StyleSpec, neon: boolean) {
    const cat = spec.category
    const glassWin = new THREE.MeshStandardMaterial({
      color: neon ? new THREE.Color(style.accent) : new THREE.Color(0x10151c),
      emissive: neon ? new THREE.Color(style.accent).multiplyScalar(0.9) : new THREE.Color(0x0a0d12),
      emissiveIntensity: neon ? 1.1 : 0.25, roughness: 0.15, metalness: 0.2,
      transparent: true, opacity: neon ? 0.92 : 0.85,
    })
    const faces: Array<[number, number, number, number]> = [
      [0, BRICK / 2 + 0.02, 0, 0], [0, -BRICK / 2 - 0.02, Math.PI, 0],
      [BRICK / 2 + 0.02, 0, 0, Math.PI / 2], [-BRICK / 2 - 0.02, 0, 0, -Math.PI / 2],
    ]
    function pane(w: number, h: number, off: [number, number, number, number]) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), glassWin)
      const [ox, oz, , ry] = off
      // off encodes either z-face (ox=0) or x-face; reuse positions
      m.position.set(off[0], off[1], off[2])
      m.rotation.y = ry
      return m
    }
    if (cat === 'commerce') {
      // big storefront glazing, lower third
      for (const f of [faces[0], faces[1]]) {
        const m = new THREE.Mesh(new THREE.PlaneGeometry(BRICK * 0.82, BRICK * 0.5), glassWin)
        m.position.set(f[0], -BRICK * 0.18, f[2]); m.rotation.y = f[3]; group.add(m)
      }
    } else if (WINDOW_CATS.has(cat)) {
      const tall = spec.id === 'parlor' || spec.id === 'bay'
      const w = tall ? BRICK * 0.3 : BRICK * 0.28
      const h = tall ? BRICK * 0.66 : BRICK * 0.4
      const xs = tall ? [0] : [-BRICK * 0.22, BRICK * 0.22]
      for (const f of faces) {
        for (const sx of xs) {
          const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), glassWin)
          if (f[3] === 0) { m.position.set(sx, 0, f[2]) }
          else if (f[3] === Math.PI) { m.position.set(-sx, 0, f[2]); m.rotation.y = Math.PI }
          else { m.position.set(f[0], 0, sx); m.rotation.y = f[3] }
          group.add(m)
        }
      }
    }
  }

  // ── rebuild bricks + restyle ──────────────────────────────────
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

    cellsRef.current.forEach((stack, k) => {
      const [x, z] = k.split(',').map(Number)
      stack.forEach((id, level) => {
        const spec: ModuleSpec = byId[id]
        if (!spec) return
        const base = new THREE.Color(spec.color || style.palette[spec.tone] || style.accent)
        base.multiplyScalar(1 - Math.min(level, 6) * 0.012)   // subtle vertical shade
        const glass = spec.glass || style.material === 'glass'
        const y = level * STEP + BRICK / 2
        const g = new THREE.Group(); g.position.set(x * STEP, y, z * STEP)

        const isGarden = spec.id === 'garden' || (spec.category === 'outdoor' && !glass)
        if (isGarden) {
          // open deck: slab + railing + shrubs
          const slab = new THREE.Mesh(new THREE.BoxGeometry(BRICK, 0.4, BRICK),
            new THREE.MeshStandardMaterial({ color: base, roughness: 0.9 }))
          slab.position.y = -BRICK / 2 + 0.2; slab.castShadow = slab.receiveShadow = true; g.add(slab)
          const rail = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(BRICK, BRICK * 0.5, BRICK)),
            new THREE.LineBasicMaterial({ color: new THREE.Color(style.accent), transparent: true, opacity: 0.6 }))
          rail.position.y = -BRICK * 0.25; g.add(rail)
          for (let s = 0; s < 4; s++) {
            const shrub = new THREE.Mesh(new THREE.SphereGeometry(0.32, 8, 6),
              new THREE.MeshStandardMaterial({ color: 0x4c8c3f, roughness: 1 }))
            shrub.position.set((s % 2 ? 0.7 : -0.7), -BRICK / 2 + 0.7, (s < 2 ? 0.7 : -0.7))
            shrub.castShadow = true; g.add(shrub)
          }
        } else {
          const mat = new THREE.MeshStandardMaterial({
            color: base, roughness: glass ? 0.12 : (style.material === 'concrete' ? 0.95 : 0.66),
            metalness: neon ? 0.35 : (glass ? 0.1 : 0.04),
            transparent: glass, opacity: glass ? 0.4 : 1,
            emissive: neon ? base.clone().multiplyScalar(0.4) : new THREE.Color(0x000000),
          })
          const mesh = new THREE.Mesh(new THREE.BoxGeometry(BRICK, BRICK, BRICK), mat)
          mesh.castShadow = mesh.receiveShadow = true; g.add(mesh)
          const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(BRICK, BRICK, BRICK)),
            new THREE.LineBasicMaterial({ color: neon ? new THREE.Color(style.accent) : base.clone().multiplyScalar(0.5), transparent: true, opacity: neon ? 0.95 : 0.5 }))
          g.add(edges)
          if (!glass) addFacade(g, spec, base, style, neon)
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
  }, [tick, style, mergedCatalog])

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

  // save
  const doSave = useCallback(async (name: string, isPublic: boolean, desc: string) => {
    const cells = serialize()
    if (!cells.length) { flash('Place some bricks first'); return null }
    try {
      const d = await api('design', { method: 'POST', body: { name, owner, cells, style: styleId, public: isPublic, description: desc, constraints } })
      onSaved?.()
      return d
    } catch (e: any) { flash('Save failed: ' + (e?.message || 'error')); return null }
  }, [serialize, owner, styleId, constraints, onSaved, flash])

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

  const comp = stats?.compliance
  const swatchOf = (m: ModuleSpec) => m.color || style.palette[m.tone] || style.accent

  return (
    <div className="relative w-full h-[72vh] min-h-[560px] rounded-2xl overflow-hidden border border-white/10 bg-black/40 select-none">
      <div ref={mountRef} className="absolute inset-0" />

      <div className="absolute top-3 left-1/2 -translate-x-1/2 text-[11px] tracking-wide text-white/55 bg-black/40 backdrop-blur px-3 py-1.5 rounded-full border border-white/10 pointer-events-none">
        click the lot to stack&nbsp;·&nbsp;right-click to remove&nbsp;·&nbsp;drag to orbit
      </div>

      {/* HUD */}
      {stats && (
        <div className="absolute top-3 right-3 w-[210px] bg-black/55 backdrop-blur-md rounded-xl border border-white/10 p-3 text-white">
          <div className="text-[10px] uppercase tracking-[0.2em] text-white/45 mb-2">Live spec</div>
          <div className="flex items-baseline justify-between mb-1.5">
            <span className="text-2xl font-semibold tracking-tight">{fmtUSD(stats.price_usd)}</span>
            <span className="text-[10px] text-white/40">${fmt(stats.price_per_m2)}/m²</span>
          </div>
          <Row l="Bricks" v={`${stats.module_count}`} />
          <Row l="Floors" v={`${stats.floors}`} />
          <Row l="Floor area" v={`${fmt(stats.floor_area_m2)} m²`} />
          <Row l="Sleeps" v={`${stats.occupancy}`} />
          <Row l="Lead time" v={`${stats.lead_time_days} d`} />
          <Row l="Carbon" v={`${fmt(Math.round(stats.embodied_carbon_kg / 1000))} t`} />
          {comp && Object.keys(comp).some((k) => k !== 'ok') && (
            <div className="mt-2 pt-2 border-t border-white/10">
              <div className="text-[9px] uppercase tracking-[0.2em] text-white/40 mb-1">Constraints</div>
              {(['budget', 'floors', 'carbon', 'occupancy', 'lot'] as const).map((k) => comp[k] && (
                <div key={k} className={`flex items-center justify-between text-[10px] ${comp[k]!.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
                  <span className="capitalize">{comp[k]!.ok ? '✓' : '✕'} {k}</span>
                  <span className="font-mono">{typeof comp[k]!.value === 'number' && k === 'budget' ? fmtUSD(comp[k]!.value as number) : comp[k]!.value}/{typeof comp[k]!.limit === 'number' && k === 'budget' ? fmtUSD(comp[k]!.limit as number) : comp[k]!.limit}</span>
                </div>
              ))}
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
              {tab === 'mine' ? 'No bricks yet — forge one ↓' : 'No shared bricks yet.'}
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
          className="mt-1.5 text-[11px] font-semibold py-1.5 rounded-lg bg-gradient-to-r from-cyan-400/90 to-purple-400/90 text-black hover:from-cyan-300 hover:to-purple-300 transition">✎ Forge a brick</button>
      </div>

      {/* params panel */}
      {showParams && (
        <div className="absolute left-[200px] top-12 w-[230px] bg-black/70 backdrop-blur-md rounded-xl border border-white/10 p-3 text-white z-20">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] uppercase tracking-[0.2em] text-white/50">Parameters & constraints</div>
            <button onClick={() => setShowParams(false)} className="text-white/40 hover:text-white text-xs">✕</button>
          </div>
          <NumRow label="Lot width" value={constraints.lot_w} min={1} max={9} onChange={(v) => setConstraints((c) => ({ ...c, lot_w: v }))} />
          <NumRow label="Lot depth" value={constraints.lot_d} min={1} max={9} onChange={(v) => setConstraints((c) => ({ ...c, lot_d: v }))} />
          <NumRow label="Max floors" value={constraints.max_floors} min={1} max={HARD_MAX} onChange={(v) => setConstraints((c) => ({ ...c, max_floors: v }))} />
          <ConRow label="Budget cap" suffix="$" value={constraints.max_budget} step={50000} onChange={(v) => setConstraints((c) => ({ ...c, max_budget: v }))} />
          <ConRow label="Carbon cap" suffix="kg" value={constraints.max_carbon_kg} step={10000} onChange={(v) => setConstraints((c) => ({ ...c, max_carbon_kg: v }))} />
          <ConRow label="Min sleeps" value={constraints.min_occupancy} step={1} onChange={(v) => setConstraints((c) => ({ ...c, min_occupancy: v }))} />
          <p className="text-[9px] text-white/35 mt-1">The lot fences the grid, floors cap the stack, and the spec turns red when you bust a cap.</p>
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
          <Btn onClick={() => setAutoRotate((v) => !v)} active={autoRotate}>⟳ {autoRotate ? 'Spinning' : 'Rotate'}</Btn>
          <Btn onClick={() => setShowParams((v) => !v)} active={showParams}>⚙ Parameters</Btn>
          <Btn onClick={undo}>↩ Undo</Btn>
          <Btn onClick={clear}>✕ Clear</Btn>
          <Btn onClick={surprise}>✦ Surprise</Btn>
          <Btn onClick={() => fileRef.current?.click()}>⤓ Import</Btn>
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
          <h3 className="text-lg font-bold">✎ Forge a brick</h3>
          <button onClick={onClose} className="text-white/40 hover:text-white">✕</button>
        </div>
        <div className="flex gap-4 mb-4">
          <div className="w-20 h-20 rounded-xl shrink-0 border border-white/15 shadow-inner" style={{ background: color, opacity: glass ? 0.55 : 1 }} />
          <div className="flex-1">
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Brick name (e.g. Sky Pool)"
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
