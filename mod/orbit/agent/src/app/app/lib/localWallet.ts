// Local (anonymous) sign-in identity — a secp256k1 keypair generated in this
// browser and kept only in localStorage. It produces the same EIP-191
// `personal_sign` signature MetaMask would, so the API's stateless verifier
// accepts it with no special-casing; the recovered address is a pseudonym with
// no link to any real wallet or chain identity. Forgetting the key destroys
// the identity and orphans whatever server-side state lives under its address.
import { generatePrivateKey, privateKeyToAccount } from 'viem/accounts'
import type { Hex } from 'viem'

const LOCAL_PK_KEY = 'agent_local_pk'

export type LocalIdentity = { address: string; pk: Hex }

/** Load the stored local identity, or null if none exists / not in a browser. */
export function loadLocalIdentity(): LocalIdentity | null {
  if (typeof window === 'undefined') return null
  const pk = localStorage.getItem(LOCAL_PK_KEY) as Hex | null
  if (!pk) return null
  try {
    return { address: privateKeyToAccount(pk).address.toLowerCase(), pk }
  } catch {
    localStorage.removeItem(LOCAL_PK_KEY) // corrupt — drop it
    return null
  }
}

/**
 * Return the existing local identity, or mint a fresh random one and persist
 * it. `generatePrivateKey` draws from the platform CSPRNG (WebCrypto).
 */
export function getOrCreateLocalIdentity(): LocalIdentity {
  const existing = loadLocalIdentity()
  if (existing) return existing
  const pk = generatePrivateKey()
  // modc2 modules share one localStorage origin — a full quota must never
  // crash sign-in; the identity then just doesn't survive a reload
  try { localStorage.setItem(LOCAL_PK_KEY, pk) } catch {}
  return { address: privateKeyToAccount(pk).address.toLowerCase(), pk }
}

/** Permanently forget the local identity (and thus its server-side state). */
export function clearLocalIdentity(): void {
  if (typeof window !== 'undefined') localStorage.removeItem(LOCAL_PK_KEY)
}

/** EIP-191 personal_sign with the local key — same bytes MetaMask would sign. */
export function localSign(id: LocalIdentity, message: string): Promise<string> {
  return privateKeyToAccount(id.pk).signMessage({ message })
}
