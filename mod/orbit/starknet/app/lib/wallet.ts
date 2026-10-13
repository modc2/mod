// Sign in to the contract studio with a browser wallet. One personal_sign,
// no transaction, no gas: the signature becomes a mod-protocol token
// (base64url {data, time, key, signature}) the API checks against the
// studio's owner list. The token lives in localStorage and is sent as the
// `token:` header by lib/api.ts.
import { TOKEN_KEY } from './api';

declare global {
  interface Window { ethereum?: { request: (a: { method: string; params?: any[] }) => Promise<any> } }
}

function b64url(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  bytes.forEach(b => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function hasWallet(): boolean {
  return typeof window !== 'undefined' && !!window.ethereum;
}

export async function signIn(): Promise<string> {
  if (!window.ethereum) throw new Error('No browser wallet found — install MetaMask or Rabby to sign in.');
  const [address] = await window.ethereum.request({ method: 'eth_requestAccounts' });
  if (!address) throw new Error('The wallet returned no account.');
  const data = 'starknet-studio';                       // a string: verifies in Python and Rust alike
  const time = Math.floor(Date.now() / 1000).toString(); // whole seconds
  const signature = await window.ethereum.request({
    method: 'personal_sign',
    params: [JSON.stringify({ data, time }), address],
  });
  const token = b64url(JSON.stringify({ data, time, key: String(address).toLowerCase(), signature }));
  window.localStorage.setItem(TOKEN_KEY, token);
  return token;
}

export function signOut() {
  window.localStorage.removeItem(TOKEN_KEY);
}
