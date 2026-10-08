'use client';
import { Position } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { fmt, fmtPrice, short } from '@/lib/format';
import { Stat } from './ui';

/* free / staked / total (/ positions) — the account headline */
export function BalanceStats({ free, staked, total, n }:
  { free?: number | null; staked?: number | null; total?: number | null; n?: number }) {
  const { rate } = useData();
  return (
    <div className="stats left">
      <Stat left label="Free" value={`τ ${fmt(free, 4)}`} />
      <Stat left label="Staked" value={`τ ${fmt(staked, 4)}`} />
      <Stat left label={rate != null && total != null ? `Total · ≈ $${fmt(total * rate, 2)}` : 'Total'}
            value={`τ ${fmt(total, 4)}`} />
      {n != null && <Stat left label="Positions" value={n} />}
    </div>
  );
}

export default function Positions({ rows, hotkeys }: { rows: Position[]; hotkeys?: boolean }) {
  const { names } = useData();
  const { openSubnet } = useOverlay();
  if (!rows.length) return <p className="muted" style={{ marginTop: 14 }}>No alpha positions — free TAO only.</p>;
  return (
    <div className="scroll-x" style={{ marginTop: 14 }}>
      <table><thead><tr><th>Subnet</th>{hotkeys && <th>Hotkey</th>}<th className="num">Alpha</th>
        <th className="num">Price τ</th><th className="num">Value τ</th></tr></thead>
        <tbody>{rows.map((p, i) => (
          <tr key={i} className="click" onClick={() => openSubnet(p.netuid)}>
            <td>{p.name || names[p.netuid] || ''} <span className="sn-sym">#{p.netuid}</span></td>
            {hotkeys && <td className="num" title={p.hotkey}>{short(p.hotkey)}</td>}
            <td className="num">{fmt(p.alpha, 4)}</td>
            <td className="num">{fmtPrice(p.price)}</td>
            <td className="num">{p.value_tao != null ? fmt(p.value_tao, 4) : '—'}</td>
          </tr>
        ))}</tbody></table>
    </div>
  );
}
