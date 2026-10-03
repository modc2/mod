// Local (anonymous) sign-in identity — a secp256k1 keypair that exists only
// in this browser. It produces the same EIP-191 `personal_sign` signature
// MetaMask would, so the API's stateless verifier accepts it with no
// special-casing; the recovered address is a pseudonym with no link to any
// real wallet or chain identity.
//
// There are two ways to hold one, and the difference is where the secret
// lives rather than what the key is:
//
//   SAVED   — the key sits in localStorage and signs you in on every visit.
//             It is minted from a BIP-39 recovery phrase that is kept beside
//             it, so the secret behind the address is always copyable: write
//             it down and the same identity comes back in another browser
//             (or in MetaMask — it is a standard m/44'/60'/0'/0/0 phrase).
//
//   TYPED   — nothing is written anywhere. A password you type is stretched
//             into the same private key every time, so the wallet is
//             re-derived on each sign-in and vanishes with the tab. Losing
//             the password loses the identity: there is no copy to recover.
//
// Both paths end in the same LocalIdentity, so signing doesn't care which
// one produced it.
import { generatePrivateKey, privateKeyToAccount, generateMnemonic, mnemonicToAccount, english } from 'viem/accounts'
import { scryptAsync } from '@noble/hashes/scrypt'
// viem's bytesToHex, not @noble's — this one 0x-prefixes, which is what
// privateKeyToAccount wants
import { bytesToHex, type Hex } from 'viem'

const LOCAL_PK_KEY = 'agent_local_pk'
const LOCAL_PHRASE_KEY = 'agent_local_phrase'

/**
 * Password stretching. The salt is a constant rather than per-user because a
 * typed password has to land on the same address in any browser, on any
 * device, with nothing stored to look the salt up in — which is the whole
 * point of this mode. That costs the protection a random salt gives, so the
 * work factor carries it: N=2^17 is a couple of seconds of scrypt in a tab
 * and makes bulk guessing against a known address expensive. A short password is
 * still a weak key; the UI says so.
 */
const KDF_SALT = 'mod.agent.local-wallet.v1'
const KDF = { N: 1 << 17, r: 8, p: 1, dkLen: 32 }

export type LocalIdentity = {
  address: string
  pk: Hex
  /** the secret this key was derived from, when there is one to show */
  phrase?: string
  /** true when phrase is a BIP-39 recovery phrase (importable elsewhere) */
  mnemonic?: boolean
  /** false = typed each time, nothing persisted */
  saved: boolean
}

const addrOf = (pk: Hex) => privateKeyToAccount(pk).address.toLowerCase()

// ── the typed identity ────────────────────────────────────────────
// Held for the life of the page so a re-sign (an expired token, a second
// action) doesn't ask for the password again. Never written to storage.
let typed: LocalIdentity | null = null

/** The password-derived identity this page session is holding, if any. */
export function sessionIdentity(): LocalIdentity | null {
  return typed
}

/** Drop the in-memory identity (sign-out). */
export function clearSessionIdentity(): void {
  typed = null
}

// ── derivation ────────────────────────────────────────────────────

const MNEMONIC_LENGTHS = new Set([12, 15, 18, 21, 24])

function pkFromMnemonic(phrase: string): Hex {
  const key = mnemonicToAccount(phrase).getHdKey().privateKey
  if (!key) throw new Error('phrase produced no key')
  return bytesToHex(key) as Hex
}

/** True when the text is a valid BIP-39 phrase — i.e. a recovery phrase, not a password. */
export function isRecoveryPhrase(secret: string): boolean {
  const words = secret.trim().toLowerCase().split(/\s+/)
  if (!MNEMONIC_LENGTHS.has(words.length)) return false
  try { pkFromMnemonic(words.join(' ')); return true } catch { return false }
}

/**
 * Turn a secret into the identity it always makes. A valid BIP-39 phrase is
 * walked down the standard Ethereum path; anything else is treated as a
 * password and stretched with scrypt. Same input, same address, every time,
 * in any browser — nothing here reads or writes storage.
 */
export async function identityFromSecret(secret: string): Promise<LocalIdentity> {
  const text = secret.trim()
  if (!text) throw new Error('enter a password')
  if (isRecoveryPhrase(text)) {
    const phrase = text.toLowerCase().split(/\s+/).join(' ')
    const pk = pkFromMnemonic(phrase)
    return { address: addrOf(pk), pk, phrase, mnemonic: true, saved: false }
  }
  const dk = await scryptAsync(new TextEncoder().encode(text), new TextEncoder().encode(KDF_SALT), KDF)
  let pk = bytesToHex(dk) as Hex
  try { addrOf(pk) } catch {
    // astronomically unlikely (dk ≥ the curve order); rehash rather than fail
    const again = await scryptAsync(dk, new TextEncoder().encode(KDF_SALT), KDF)
    pk = bytesToHex(again) as Hex
  }
  return { address: addrOf(pk), pk, saved: false }
}

// ── the saved identity ────────────────────────────────────────────

/** Load the identity kept in this browser, or null if none exists. */
export function loadLocalIdentity(): LocalIdentity | null {
  if (typeof window === 'undefined') return null
  const pk = localStorage.getItem(LOCAL_PK_KEY) as Hex | null
  if (!pk) return null
  try {
    const phrase = localStorage.getItem(LOCAL_PHRASE_KEY) || undefined
    return { address: addrOf(pk), pk, phrase, mnemonic: !!phrase, saved: true }
  } catch {
    localStorage.removeItem(LOCAL_PK_KEY) // corrupt — drop it
    localStorage.removeItem(LOCAL_PHRASE_KEY)
    return null
  }
}

/**
 * Write an identity into this browser so it signs in on every visit. The
 * phrase is stored beside the key only when it is a recovery phrase we
 * minted — a password the person chose is theirs to remember, and keeping a
 * copy of it here would put a reused password on disk for no gain (the key
 * beside it already signs).
 */
export function saveLocalIdentity(id: LocalIdentity): LocalIdentity {
  const saved = { ...id, saved: true }
  // modc2 modules share one localStorage origin — a full quota must never
  // crash sign-in; the identity then just doesn't survive a reload
  try {
    localStorage.setItem(LOCAL_PK_KEY, id.pk)
    if (id.mnemonic && id.phrase) localStorage.setItem(LOCAL_PHRASE_KEY, id.phrase)
    else localStorage.removeItem(LOCAL_PHRASE_KEY)
  } catch { return { ...id, saved: false } }
  return saved
}

/**
 * Return the existing saved identity, or mint a fresh one and persist it.
 * New wallets come from a generated BIP-39 phrase (WebCrypto entropy) rather
 * than raw key bytes, so there is always something a person can copy down
 * and restore the identity from somewhere else.
 */
export function getOrCreateLocalIdentity(): LocalIdentity {
  const existing = loadLocalIdentity()
  if (existing) return existing
  let minted: LocalIdentity
  try {
    const phrase = generateMnemonic(english)
    const pk = pkFromMnemonic(phrase)
    minted = { address: addrOf(pk), pk, phrase, mnemonic: true, saved: false }
  } catch {
    const pk = generatePrivateKey() // no phrase, but an identity beats none
    minted = { address: addrOf(pk), pk, saved: false }
  }
  return saveLocalIdentity(minted)
}

/** Permanently forget the browser-held identity (and thus its server-side state). */
export function clearLocalIdentity(): void {
  typed = null
  if (typeof window === 'undefined') return
  localStorage.removeItem(LOCAL_PK_KEY)
  localStorage.removeItem(LOCAL_PHRASE_KEY)
}

/**
 * Adopt an identity for this sign-in: `remember` puts it in this browser,
 * otherwise it is held in memory for the life of the page and nowhere else.
 */
export function useIdentity(id: LocalIdentity, remember: boolean): LocalIdentity {
  if (remember) { typed = null; return saveLocalIdentity(id) }
  typed = { ...id, saved: false }
  return typed
}

/** EIP-191 personal_sign with the local key — same bytes MetaMask would sign. */
export function localSign(id: LocalIdentity, message: string): Promise<string> {
  return privateKeyToAccount(id.pk).signMessage({ message })
}
