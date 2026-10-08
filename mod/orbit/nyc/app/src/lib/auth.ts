/**
 * The owner's sign-in: one wallet signature, one mod-protocol token.
 *
 * A token is `base64url(JSON.stringify({data, time, key, signature}))` where
 * the signature is an EIP-191 `personal_sign` over exactly
 * `JSON.stringify({data, time})` — that key order, no spaces — because the
 * server re-serializes with the same compact separators and recovers the
 * signer address from it. No nonce, no challenge endpoint, no transaction:
 * the same envelope every module in this fleet verifies.
 *
 * The token is held in localStorage and reused until it nears the server's
 * 7-day max age. Signing in does not make you the owner — the server checks
 * the recovered address against the deployment owner; anyone else's token
 * simply verifies to an address with no write rights.
 */

const STORE = 'nyc:token'
const MAX_AGE_S = 6 * 86_400 // refresh a day before the server's 7-day cutoff

function b64url(obj: unknown): string {
  const json = JSON.stringify(obj)
  const bytes = new TextEncoder().encode(json)
  let bin = ''
  bytes.forEach((b) => { bin += String.fromCharCode(b) })
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** The stored token, if it exists and is still comfortably inside max age. */
export function storedToken(): string | null {
  try {
    const t = localStorage.getItem(STORE)
    if (!t) return null
    const pad = t.replace(/-/g, '+').replace(/_/g, '/')
    const { time } = JSON.parse(atob(pad))
    if (Date.now() / 1000 - Number(time) > MAX_AGE_S) {
      localStorage.removeItem(STORE)
      return null
    }
    return t
  } catch {
    return null
  }
}

export function signOut(): void {
  try { localStorage.removeItem(STORE) } catch {}
}

/** The address inside the stored token (who we signed as), or null. */
export function storedAddress(): string | null {
  const t = storedToken()
  if (!t) return null
  try {
    return JSON.parse(atob(t.replace(/-/g, '+').replace(/_/g, '/'))).key ?? null
  } catch {
    return null
  }
}

/** Ask the browser wallet to sign; stores and returns the token. */
export async function signIn(): Promise<string> {
  const eth = (window as any).ethereum
  if (!eth) throw new Error('no browser wallet found (MetaMask or compatible)')
  const [address] = await eth.request({ method: 'eth_requestAccounts' })
  // ASCII only in `data`: the server's serializer escapes non-ASCII.
  const data = { mod: 'nyc' }
  const time = (Date.now() / 1000).toString()
  const signature = await eth.request({
    method: 'personal_sign',
    params: [JSON.stringify({ data, time }), address],
  })
  const token = b64url({ data, time, key: String(address).toLowerCase(), signature })
  try { localStorage.setItem(STORE, token) } catch {}
  return token
}
