/* The browser-wallet bridge — window.injectedWeb3, no library.
 *
 * SubWallet, Talisman and polkadot{.js} all inject the same interface
 * (@polkadot/extension-inject): `injectedWeb3[id].enable(dapp)` returns an
 * injector with `accounts.get/subscribe` and `signer.signPayload`. That is
 * the whole surface this console needs — the node composes the transaction
 * (bt/tx.py), the wallet shows it and signs, the node broadcasts.
 *
 * SubWallet's mobile in-app browser and Nova inject as 'polkadot-js'; any
 * other id found in injectedWeb3 is offered too. */

export interface ExtMeta { id: string; name: string; url?: string }
export interface InjectedAccount { address: string; name?: string; type?: string; genesisHash?: string | null }
export interface SignerPayloadJSON { address: string; [k: string]: unknown }

export const KNOWN: ExtMeta[] = [
  { id: 'subwallet-js', name: 'SubWallet', url: 'https://www.subwallet.app/download.html' },
  { id: 'talisman', name: 'Talisman', url: 'https://talisman.xyz/download' },
  { id: 'polkadot-js', name: 'polkadot{.js}', url: 'https://polkadot.js.org/extension/' },
];

export const DAPP = 'bt · Bittensor explorer';
/* Bittensor finney genesis — accounts pinned to another chain are hidden */
export const FINNEY_GENESIS = '0x2f0555cc76fc2840a25a6ea3b9637146806f1f44b090c175ffde2a7e5ab36c03';

export const injectedWeb3 = (): Record<string, any> =>
  (typeof window !== 'undefined' && (window as any).injectedWeb3) || {};

export const present = (id: string) => !!injectedWeb3()[id];

/* known wallets first (installed or not), then anything else that injected */
export function wallets(): (ExtMeta & { installed: boolean })[] {
  const inj = injectedWeb3();
  const known = KNOWN.map(k => ({ ...k, installed: !!inj[k.id] }));
  const extra = Object.keys(inj).filter(id => !KNOWN.some(k => k.id === id))
    .map(id => ({ id, name: id, installed: true }));
  /* polkadot-js is only worth a button when something actually injected it */
  return [...known.filter(k => k.installed || k.id !== 'polkadot-js'), ...extra];
}

export const metaFor = (idOrName: string | null | undefined): ExtMeta | undefined =>
  idOrName ? (KNOWN.find(k => k.id === idOrName || k.name === idOrName)
              || (injectedWeb3()[idOrName] ? { id: idOrName, name: idOrName } : undefined)) : undefined;

/* Extensions inject asynchronously after page load (SubWallet ~100-800ms).
 * Resolve once `id` (or anything, when no id) is there, or after `ms`. */
export function whenInjected(id?: string, ms = 3000): Promise<boolean> {
  return new Promise(res => {
    const t0 = Date.now();
    const tick = () => {
      const inj = injectedWeb3();
      const ok = id ? !!inj[id] : Object.keys(inj).length > 0;
      if (ok) return res(true);
      if (Date.now() - t0 >= ms) return res(false);
      setTimeout(tick, 150);
    };
    tick();
  });
}

const injectors = new Map<string, Promise<any>>();

/* enable() pops the wallet's "allow this site" prompt the first time; after
 * that SubWallet answers silently, so re-enabling on reload costs nothing. */
export function enable(id: string): Promise<any> {
  let p = injectors.get(id);
  if (!p) {
    const ext = injectedWeb3()[id];
    if (!ext) return Promise.reject(new Error(`${metaFor(id)?.name || id} is not installed in this browser`));
    p = (ext.enable ? ext.enable(DAPP) : Promise.reject(new Error('wallet has no enable()')))
      .catch((e: Error) => { injectors.delete(id); throw e; });
    injectors.set(id, p!);
  }
  return p!;
}

/* Only accounts that can sign Bittensor extrinsics: substrate sr25519/ed25519,
 * not EVM (SubWallet holds both), not pinned to some other chain. */
export function usable(a: InjectedAccount): boolean {
  if (!a || !a.address || a.address.startsWith('0x')) return false;
  if (a.type && !['sr25519', 'ed25519'].includes(a.type)) return false;
  if (a.genesisHash && a.genesisHash !== FINNEY_GENESIS) return false;
  return true;
}

export async function accounts(id: string): Promise<InjectedAccount[]> {
  const inj = await enable(id);
  let list: InjectedAccount[];
  if (inj.accounts?.get) list = await inj.accounts.get();
  else list = await new Promise((res, rej) => {
    let un: any;
    const to = setTimeout(() => rej(new Error('no answer from the wallet')), 8000);
    un = inj.accounts.subscribe((a: InjectedAccount[]) => {
      clearTimeout(to); res(a);
      try { typeof un === 'function' && un(); } catch { /* */ }
    });
  });
  return (list || []).filter(usable);
}

/* Fires whenever the user changes which accounts this site may see. */
export async function subscribe(id: string, cb: (a: InjectedAccount[]) => void): Promise<() => void> {
  const inj = await enable(id);
  if (!inj.accounts?.subscribe) return () => {};
  const un = inj.accounts.subscribe((a: InjectedAccount[]) => cb((a || []).filter(usable)));
  return typeof un === 'function' ? un : () => {};
}

export async function signPayload(id: string, payload: SignerPayloadJSON): Promise<string> {
  const inj = await enable(id);
  if (!inj.signer?.signPayload) throw new Error(`${metaFor(id)?.name || id} cannot sign transactions`);
  const r = await inj.signer.signPayload(payload);
  if (!r?.signature) throw new Error('the wallet returned no signature');
  return r.signature as string;
}

/* Wallets reject with assorted shapes — one plain sentence for the UI */
export function rejectText(e: unknown, name: string): string {
  const m = (e as Error)?.message || String(e || '');
  if (/cancel|reject|denied|declined/i.test(m)) return `Cancelled in ${name}.`;
  if (/not allowed|not authori[sz]ed/i.test(m)) return `${name} has not allowed this site — open ${name} and allow it.`;
  return `${name}: ${m || 'request failed'}`;
}
