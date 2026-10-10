import { Addr } from '@/components/ui';
import { norm, num, TOKENS, units } from './format';

/** Plain-language names for the pool's events. */
export const EV_LABEL: Record<string, string> = {
  Deposit: 'Deposit', Withdrawal: 'Withdrawal', NoteUsed: 'Note spent',
  EncNoteCreated: 'Private note', OpenNoteDeposited: 'Open note',
  ExternalContractInvoked: 'Helper call',
};

export function evTone(name: string): 'ok' | 'err' | 'warn' | 'dim' | 'accent' {
  if (name === 'Deposit' || name === 'OpenNoteDeposited') return 'ok';
  if (name === 'Withdrawal') return 'warn';
  if (name === 'ExternalContractInvoked') return 'accent';
  return 'dim';
}

export function evAmount(e: any): React.ReactNode {
  const f = e.fields || {};
  const tok = TOKENS[norm(f.token)];
  if (f.amount !== undefined && tok) return `${num(units(f.amount, tok.decimals), 4)} ${tok.symbol}`;
  if (f.contract_address) return <Addr a={f.contract_address} />;
  if (f.user) return <Addr a={f.user} />;
  return <span className="dim">—</span>;
}
