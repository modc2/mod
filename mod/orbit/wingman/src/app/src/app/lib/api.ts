const API = process.env.NEXT_PUBLIC_API_URL || '/_api'

// ── types ─────────────────────────────────────────────────────────────────

export interface Photo {
  id: string
  name: string
  size?: number
  width?: number
  height?: number
  audit?: AuditSummary | null
}

export interface AuditSummary {
  score: number
  role: string
  verdict: string
  lead_ok: boolean
  face_count: number
  faces: number
  issues: string[]
}

export interface WingmanSet {
  id: string
  name: string
  created: string
  photos: Photo[]
  photo_count?: number
}

export interface LineupSlot {
  slot: number
  photo: string
  role: string
  score: number
  why: string
  gaps?: string[]
}

export interface LineupResult {
  set: string
  slots: LineupSlot[]
  out: Photo[]
  gaps: string[]
}

export interface AuditResult {
  set: string
  photos: Record<string, PhotoAudit>
}

export interface PhotoAudit {
  id: string
  score: number
  role: string
  verdict: string
  lead_ok: boolean
  face_count: number
  faces: FaceBox[]
  sharpness: number
  exposure: number
  issues: Issue[]
  read_flags?: Record<string, unknown>
}

export interface FaceBox {
  x1: number; y1: number; x2: number; y2: number; score: number
}

export interface Issue {
  code: string
  msg: string
  cost: number
}

export interface ExportResult {
  set: string
  preset: string
  zip: string
  photos: string[]
}

// ── helpers ────────────────────────────────────────────────────────────────

async function req<T>(
  method: string,
  path: string,
  body?: unknown,
  isFormData = false,
): Promise<T> {
  const url = `${API}${path}`
  const opts: RequestInit = { method }

  if (body !== undefined) {
    if (isFormData) {
      opts.body = body as FormData
    } else {
      opts.headers = { 'content-type': 'application/json' }
      opts.body = JSON.stringify(body)
    }
  }

  const res = await fetch(url, opts)
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const j = await res.json()
      msg = j.error || msg
    } catch {}
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

// ── API calls ──────────────────────────────────────────────────────────────

export async function getInfo() {
  return req<Record<string, unknown>>('GET', '/')
}

export async function getHealth() {
  return req<Record<string, unknown>>('GET', '/health')
}

export async function getSets(): Promise<WingmanSet[]> {
  const data = await req<{ sets?: WingmanSet[] } | WingmanSet[]>('GET', '/sets')
  // Engine may return {sets: [...]} or the array directly
  if (Array.isArray(data)) return data
  const d = data as { sets?: WingmanSet[] }
  return d.sets ?? []
}

export async function newSet(name?: string): Promise<WingmanSet> {
  const qs = name ? `?name=${encodeURIComponent(name)}` : ''
  return req<WingmanSet>('POST', `/sets${qs}`)
}

export async function getSet(id: string): Promise<WingmanSet> {
  return req<WingmanSet>('GET', `/sets/${id}`)
}

export async function deleteSet(id: string) {
  return req<{ ok: boolean }>('DELETE', `/sets/${id}`)
}

export async function uploadPhotos(setId: string, files: File[]): Promise<unknown> {
  const form = new FormData()
  for (const f of files) {
    form.append('files[]', f, f.name)
  }
  const url = `${API}/photos?set=${encodeURIComponent(setId)}`
  const res = await fetch(url, { method: 'POST', body: form })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { const j = await res.json(); msg = j.error || msg } catch {}
    throw new Error(msg)
  }
  return res.json()
}

export async function deletePhoto(setId: string, photoId: string) {
  return req<{ ok: boolean }>('DELETE', `/photos/${setId}/${photoId}`)
}

export async function getAudit(setId: string, photo?: string, force = false): Promise<AuditResult> {
  const params = new URLSearchParams({ set: setId })
  if (photo) params.set('photo', photo)
  if (force) params.set('force', '1')
  return req<AuditResult>('GET', `/audit?${params}`)
}

export async function getFaces(setId: string, photoId: string, threshold?: number) {
  const params = new URLSearchParams({ set: setId, photo: photoId })
  if (threshold !== undefined) params.set('threshold', String(threshold))
  return req<{ faces: FaceBox[] }>('GET', `/faces?${params}`)
}

export async function getLineup(
  setId: string,
  n = 6,
  minScore = 35,
  allowGroup = true,
  force = false,
): Promise<LineupResult> {
  const params = new URLSearchParams({
    set: setId,
    n: String(n),
    min_score: String(minScore),
    allow_group: allowGroup ? '1' : '0',
    force: force ? '1' : '0',
  })
  return req<LineupResult>('GET', `/lineup?${params}`)
}

export async function render(
  setId: string,
  opts: {
    photo?: string
    preset?: string
    ratio?: string
    zoom?: string
    polish?: string
    force?: boolean
    onlyLineup?: boolean
    n?: number
  } = {},
) {
  return req('POST', '/render', {
    set: setId,
    ...opts,
    only_lineup: opts.onlyLineup,
  })
}

export async function exportSet(
  setId: string,
  preset = 'tinder',
  n = 6,
): Promise<ExportResult> {
  return req<ExportResult>('POST', '/export', { set: setId, preset, n })
}

export function imgUrl(setId: string, photoId: string, preset?: string, w = 320): string {
  if (preset) {
    return `${API}/img/${setId}/${photoId}/${preset}`
  }
  return `${API}/img/${setId}/${photoId}?w=${w}`
}

export function downloadUrl(setId: string, preset: string): string {
  return `${API}/download/${setId}/${preset}.zip`
}

export async function getVenice() {
  return req<Record<string, unknown>>('GET', '/venice')
}

export async function getRead(setId: string) {
  return req<Record<string, unknown>>('GET', `/read?set=${encodeURIComponent(setId)}`)
}

export async function postRead(setId: string, opts: Record<string, unknown> = {}) {
  return req<Record<string, unknown>>('POST', '/read', { set: setId, ...opts })
}
