'use client'

import { useCallback, useEffect, useState } from 'react'

/**
 * Which panel sections the reader has folded away, remembered between visits.
 *
 * The store is read once on mount and written only on a real toggle — this
 * origin is shared with the rest of the fleet, so we never write speculatively
 * and we never let a full storage quota break the map.
 */
const KEY = 'nyc:collapsed'

function read(): string[] {
  try {
    const raw = window.localStorage.getItem(KEY)
    const v = raw ? JSON.parse(raw) : null
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

export function useCollapse() {
  const [closed, setClosed] = useState<string[]>([])

  // Hydration-safe: the server render has nothing folded, so the first paint
  // matches and the stored state lands right after.
  useEffect(() => setClosed(read()), [])

  const toggle = useCallback((id: string) => {
    setClosed((c) => {
      const next = c.includes(id) ? c.filter((x) => x !== id) : [...c, id]
      try {
        window.localStorage.setItem(KEY, JSON.stringify(next))
      } catch {
        /* quota or private mode — folding still works for this session */
      }
      return next
    })
  }, [])

  const isOpen = useCallback((id: string) => !closed.includes(id), [closed])

  return { isOpen, toggle }
}
