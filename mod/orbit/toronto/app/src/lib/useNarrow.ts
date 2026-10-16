'use client'

import { useEffect, useState } from 'react'

/** Matches Tailwind's `md` breakpoint: below it the console lays out as a phone. */
export const NARROW_QUERY = '(max-width: 767px)'

/**
 * True on a phone-sized viewport. Starts false so the server render and the
 * first client render agree, then corrects on mount and tracks resizes.
 */
export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia(NARROW_QUERY)
    const on = () => setNarrow(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return narrow
}
