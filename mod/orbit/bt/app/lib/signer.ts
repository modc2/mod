/* One on-chain action, signed by the connected browser wallet.
 *
 *   prepare (node) -> signPayload (wallet shows + signs) -> submit (node)
 *
 * The node composes the call and checks the signature against the bytes it
 * built before anything is broadcast, so a wallet that signs something else
 * costs nothing but a retry. */
import { postJSON } from './api';
import { signPayload } from './injected';

export type TxKind = 'transfer' | 'stake' | 'unstake';

export interface Prepared {
  ok: boolean; error?: string; id: string; kind: TxKind; address: string;
  payload: Record<string, unknown> & { address: string }; call: string;
  preview: Record<string, any>; fee_tao: number | null; free_tao: number | null;
  nonce: number; valid_for_blocks: number; block: number;
}

export interface Sent {
  ok: boolean; error?: string; id?: string; extrinsic_hash?: string; block_hash?: string;
  block?: number; call?: string; preview?: Record<string, any>;
}

export async function prepare(kind: TxKind, address: string, args: Record<string, unknown>): Promise<Prepared> {
  const r = await postJSON<Prepared>('tx/prepare', { kind, address, ...args });
  if (!r.ok) throw new Error(r.error || 'could not prepare the transaction');
  return r;
}

export async function signAndSubmit(extId: string, p: Prepared,
                                    onStep?: (s: 'sign' | 'send') => void): Promise<Sent> {
  onStep?.('sign');
  const signature = await signPayload(extId, p.payload);
  onStep?.('send');
  const r = await postJSON<Sent>('tx/submit', { id: p.id, signature });
  if (r.ok === false && r.error && !r.extrinsic_hash) throw new Error(r.error);
  return r;
}
