/** Host keys are shown once, at listing time — the browser is where they live.
 *  Shared-origin safe: read on demand, write only on an actual user action. */
export function loadKeys(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem('obnb_keys') || '{}') } catch { return {} }
}

export function saveKey(listingId: string, key: string) {
  const all = loadKeys()
  all[listingId] = key
  localStorage.setItem('obnb_keys', JSON.stringify(all))
}
