'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { userData, type SavedDataset, type AddDataBody } from '@/lib/api'
import { signIn, signOut, storedToken, storedAddress } from '@/lib/auth'
import { Coin } from './Sprites'

type Props = {
  /** The catalogue changed (a layer was added or removed) — refetch it. */
  onChanged: () => void
}

const DATASET_ID = /^[a-z0-9]{4}-[a-z0-9]{4}$/
const PORTAL_PAGE = /(?:cityofnewyork\.us|data\.ny\.gov)\/.*?\/([a-z0-9]{4}-[a-z0-9]{4})/

/**
 * YOUR DATA — the owner adds datasets; everyone sees them as layers.
 *
 * One source field takes all three shapes: a Socrata dataset id (or a portal
 * page URL, the id is pulled out of it), a URL to a GeoJSON file, or a local
 * .geojson picked from disk. Saving needs the owner's wallet signature; the
 * server decides whether the signer IS the owner, so this panel never has to.
 * The saved layers themselves appear in the LAYERS list above under
 * "Your data" — this panel only manages them.
 */
export default function DataPanel({ onChanged }: Props) {
  const [datasets, setDatasets] = useState<SavedDataset[]>([])
  const [writable, setWritable] = useState(false)
  const [title, setTitle] = useState('')
  const [source, setSource] = useState('')
  const [mode, setMode] = useState<'points' | 'heat' | 'areas'>('points')
  const [where, setWhere] = useState('')
  const [fileFc, setFileFc] = useState<GeoJSON.FeatureCollection | null>(null)
  const [fileName, setFileName] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    userData.list(storedToken())
      .then((r) => { setDatasets(r.datasets); setWritable(r.writable) })
      .catch(() => {})
  }, [])
  useEffect(load, [load])

  const sourceKind = fileFc ? 'file'
    : DATASET_ID.test(source.trim()) || PORTAL_PAGE.test(source) ? 'dataset'
    : source.trim().startsWith('https://') ? 'url'
    : source.trim() ? 'unknown' : ''

  const pickFile = (f: File | undefined) => {
    setError('')
    if (!f) return
    f.text().then((t) => {
      try {
        const fc = JSON.parse(t)
        if (fc?.type !== 'FeatureCollection') throw new Error()
        setFileFc(fc)
        setFileName(f.name)
        setSource('')
        if (!title) setTitle(f.name.replace(/\.(geo)?json$/i, '').replace(/[_-]+/g, ' '))
      } catch {
        setError('that file is not a GeoJSON FeatureCollection')
      }
    })
  }

  const save = async () => {
    setError('')
    setNote('')
    const body: AddDataBody = { title: title.trim() }
    if (fileFc) body.geojson = fileFc
    else if (sourceKind === 'dataset') {
      body.dataset = (source.trim().match(PORTAL_PAGE)?.[1] ?? source.trim()).toLowerCase()
      body.mode = mode
      if (mode === 'areas') { body.by = 'zip'; body.value = 'count(*)' }
      if (where.trim()) body.where = where.trim()
    } else if (sourceKind === 'url') body.url = source.trim()
    else { setError('paste a dataset id (like erm2-nwe9), a GeoJSON URL, or pick a file'); return }
    if (!body.title) { setError('give the layer a title'); return }

    setBusy(true)
    try {
      const token = storedToken() ?? await signIn()
      await userData.add(body, token)
      setTitle(''); setSource(''); setWhere(''); setFileFc(null); setFileName('')
      setNote('saved — it is now in the LAYERS list under "Your data"')
      load()
      onChanged()
    } catch (e: any) {
      setError(String(e?.message ?? e).slice(0, 220))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (slug: string) => {
    setError('')
    setBusy(true)
    try {
      const token = storedToken() ?? await signIn()
      await userData.remove(slug, token)
      load()
      onChanged()
    } catch (e: any) {
      setError(String(e?.message ?? e).slice(0, 220))
    } finally {
      setBusy(false)
    }
  }

  const address = storedAddress()

  return (
    <div className="px-4 pb-3 text-[12px]">
      {datasets.length > 0 && (
        <ul className="mb-2">
          {datasets.map((d) => (
            <li key={d.slug} className="flex items-center gap-2 py-1">
              <span className="flex-1 truncate text-nes-ink2">{d.title}</span>
              <span className="pixel text-[9px] uppercase text-nes-ink3">{d.kind}</span>
              {writable && (
                <button
                  onClick={() => remove(d.slug)}
                  disabled={busy}
                  aria-label={`Remove ${d.title}`}
                  className="tap px-1 text-nes-ink3 hover:text-nes-red"
                >✕</button>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-lg border border-white/10 bg-black/40 p-2.5">
        <p className="pixel mb-2 text-[9px] uppercase tracking-wide text-nes-ink3">
          Add a dataset
        </p>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title, e.g. Rat sightings 2026"
          className="mb-1.5 w-full rounded border border-white/15 bg-black/40 px-2 py-1.5
                     text-[12px] text-white placeholder:text-nes-ink3 focus:outline-none"
        />
        <input
          value={source}
          onChange={(e) => { setSource(e.target.value); setFileFc(null); setFileName('') }}
          placeholder="Dataset id (erm2-nwe9), portal link, or GeoJSON URL"
          className="mb-1.5 w-full rounded border border-white/15 bg-black/40 px-2 py-1.5
                     text-[12px] text-white placeholder:text-nes-ink3 focus:outline-none"
        />
        {sourceKind === 'dataset' && (
          <div className="mb-1.5 flex items-center gap-2">
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as any)}
              className="rounded border border-white/15 bg-black/40 px-1.5 py-1 text-[11px] text-nes-ink2"
            >
              <option value="points">points</option>
              <option value="heat">heat</option>
              <option value="areas">by ZIP</option>
            </select>
            <input
              value={where}
              onChange={(e) => setWhere(e.target.value)}
              placeholder="filter (SoQL), optional"
              className="min-w-0 flex-1 rounded border border-white/15 bg-black/40 px-2 py-1
                         text-[11px] text-white placeholder:text-nes-ink3 focus:outline-none"
            />
          </div>
        )}
        <div className="flex items-center gap-2">
          <button onClick={save} disabled={busy}
                  className="btn pixel tap px-2.5 py-1.5 text-[10px]">
            {busy ? <span className="coin-spin inline-block align-middle"><Coin size={11} /></span>
                  : address ? 'SAVE' : 'SIGN & SAVE'}
          </button>
          <button
            onClick={() => fileInput.current?.click()}
            disabled={busy}
            className="tap text-[11px] text-nes-sky hover:underline"
          >
            {fileName ? `file: ${fileName}` : 'or pick a .geojson file'}
          </button>
          <input ref={fileInput} type="file" accept=".geojson,.json,application/geo+json"
                 className="hidden"
                 onChange={(e) => pickFile(e.target.files?.[0])} />
        </div>
        {error && <p className="mt-1.5 text-[11px] text-nes-red">{error}</p>}
        {note && <p className="mt-1.5 text-[11px] text-nes-coin">{note}</p>}
        <p className="mt-2 leading-snug text-nes-ink3">
          Saving needs the owner&apos;s wallet signature. You can also just ask
          the agent — &ldquo;save rat complaints as a layer&rdquo; works once
          you are signed in.
          {address && (
            <>
              {' '}Signed as {address.slice(0, 6)}…{address.slice(-4)}{' · '}
              <button onClick={() => { signOut(); load() }} className="underline hover:text-nes-ink2">
                sign out
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  )
}
