'use client'

// /agent/g/… — the old address of the arena pages, before games were called
// arenas. Kept so a shared link still lands: it forwards to /agent/arenas/….

import { useEffect } from 'react'
import { useParams } from 'next/navigation'

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? '/agent'

export default function OldArenaLink() {
  const params = useParams<{ id?: string[] }>()
  const rest = (params?.id || []).join('/')
  useEffect(() => {
    window.location.replace(`${BASE}/arenas${rest ? `/${rest}` : ''}${window.location.search}`)
  }, [rest])
  return null
}
