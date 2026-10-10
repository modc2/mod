// Two doors to the same room.
//
// An address is the only account this module has. There are two ways to prove
// you hold one, and everything downstream — publishing, buying, re-verifying,
// the credits ledger — is written against the address and never against how it
// got here:
//
//     wallet     an extension signs the challenge. The key is somebody else's
//                problem, which is the good kind of problem to have.
//     local      keys.js makes a key in this tab and signs with it. No
//                extension, no install, no email, no account to create.
//
// The local door is not a lesser session and it is not "anonymous mode" in the
// sense of skipping the gate: it is a real secp256k1 key producing real
// personal_sign signatures, and the server both cannot and should not be able
// to tell the two doors apart. What is different is where the key lives — in
// this browser's localStorage, which is a place that gets cleared. So the key
// is exportable, importable, and the console says out loud that losing the
// browser profile loses the account.

import { addressOf, newKey, personalSign, scalarOf } from './keys.js';

const KEY_AT = 'zkprof_key';
const KIND_AT = 'zkprof_kind';

export const hasWallet = () => !!globalThis.ethereum;

// ── the key that lives here ──────────────────────────────────────────

export function localKey() {
  return localStorage.getItem(KEY_AT) || '';
}

export function localAddress() {
  const key = localKey();
  if (!key) return '';
  try { return addressOf(key); } catch { return ''; }
}

export function createLocalKey() {
  const key = newKey();
  localStorage.setItem(KEY_AT, key);
  return { key, address: addressOf(key) };
}

export function importLocalKey(text) {
  const key = '0x' + scalarOf(text).toString(16).padStart(64, '0');   // throws if it is not one
  localStorage.setItem(KEY_AT, key);
  return { key, address: addressOf(key) };
}

export function forgetLocalKey() {
  localStorage.removeItem(KEY_AT);
}

// ── which door this session came through ─────────────────────────────

export const kind = () => localStorage.getItem(KIND_AT) || '';
export const setKind = (value) => {
  if (value) localStorage.setItem(KIND_AT, value);
  else localStorage.removeItem(KIND_AT);
};

/**
 * A thing that can sign, whichever door is in use.
 *
 * `want` picks a door explicitly — that is sign-in. Left out, it follows the
 * door this session already came through, because a re-verification has to be
 * signed by the address the run will be filed under, and quietly signing with
 * the other key would file it under a stranger.
 */
export async function signer(want) {
  const door = want || kind() || (localKey() ? 'local' : (hasWallet() ? 'wallet' : ''));

  if (door === 'local') {
    const key = localKey() || createLocalKey().key;
    return {
      kind: 'local',
      address: addressOf(key),
      sign: async (message) => personalSign(message, key),
    };
  }

  if (door === 'wallet') {
    if (!hasWallet()) throw new Error('no wallet in this browser');
    const [address] = await globalThis.ethereum.request({ method: 'eth_requestAccounts' });
    return {
      kind: 'wallet',
      address,
      sign: (message) => globalThis.ethereum.request({
        method: 'personal_sign', params: [message, address] }),
    };
  }

  throw new Error('nothing to sign with yet — sign in first');
}
