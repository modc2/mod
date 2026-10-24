/* PAPER figures: `::: fig <name> | caption` lines in WHITEPAPER.md /
   PAPER_SIMPLE.md become one of these inline SVGs. Strings (not JSX) because
   the paper is rendered as one HTML string. Colors come from CSS classes
   (.fx-*) so the chain accent (--c-rgb) re-tints every figure. */

const W = 720;

const svg = (h: number, body: string, label: string) =>
  `<svg class="fx" viewBox="0 0 ${W} ${h}" role="img" aria-label="${label}" preserveAspectRatio="xMidYMid meet">${body}</svg>`;

const t = (x: number, y: number, s: string, cls = 'fx-t', anchor = 'middle') =>
  `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}">${s}</text>`;

const arrow = `<defs><marker id="fxa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="fx-ah"/></marker></defs>`;

function ring(): string {
  const cx = 230, cy = 150, r = 100, n = 7, hidden = 4;
  let s = arrow;
  s += `<circle cx="${cx}" cy="${cy}" r="${r}" class="fx-orbit"/>`;
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
    s += `<line x1="${x}" y1="${y}" x2="${cx}" y2="${cy}" class="fx-spoke" style="animation-delay:${i * 0.18}s"/>`;
    s += `<g class="fx-node${i === hidden ? ' who' : ''}"><title>${i === hidden ? 'the signer, but the verifier cannot tell' : 'ring member P' + i}</title>`
      + `<circle cx="${x}" cy="${y}" r="17"/>${t(x, y + 4, `P<tspan class="fx-sub" dy="3">${i}</tspan>`, 'fx-t b')}</g>`;
  }
  s += `<circle cx="${cx}" cy="${cy}" r="30" class="fx-core"/>${t(cx, cy + 6, 'Σ', 'fx-t big')}`;
  s += `<path d="M${cx + r + 22} ${cy} C 400 ${cy}, 400 90, 452 90" class="fx-line" marker-end="url(#fxa)"/>`;
  s += `<path d="M${cx + r + 22} ${cy} C 400 ${cy}, 400 210, 452 210" class="fx-line" marker-end="url(#fxa)"/>`;
  s += `<rect x="456" y="60" width="236" height="60" rx="12" class="fx-box hi"/>`
    + t(574, 84, 'one of these 7 signed m', 'fx-t b') + t(574, 104, 'anonymity: 1 in |ring|', 'fx-t m');
  s += `<rect x="456" y="180" width="236" height="60" rx="12" class="fx-box"/>`
    + t(574, 204, 'key image  I = x · Hp(σ)', 'fx-t b mono') + t(574, 224, 'same key + same scope → same I', 'fx-t m');
  return svg(300, s, 'ring signature: every member lights up, the signer is hidden');
}

function flow(): string {
  const steps = [
    ['SUBMIT', 'miner ring', 'scope (mine, e)', 'image = tag t'],
    ['SCORE', 'validator ring', 'scope (score, e, t)', 'image = nullifier'],
    ['SETTLE', 'anyone', 'public arithmetic', 'Cₜ, Rₜ'],
    ['CLAIM', 'tag owner', 'scope (mine, e) again', 'same t → paid once'],
  ];
  const bw = 158, gap = 26, x0 = (W - (4 * bw + 3 * gap)) / 2, y = 54;
  let s = arrow;
  s += `<path d="M${x0} 30 H${x0 + 2 * bw + gap}" class="fx-brace"/>` + t(x0 + bw + gap / 2, 22, 'during epoch e · ring-signed', 'fx-t m');
  s += `<path d="M${x0 + 2 * (bw + gap)} 30 H${x0 + 4 * bw + 3 * gap}" class="fx-brace dim"/>` + t(x0 + 3 * bw + 2.5 * gap, 22, 'after epoch e', 'fx-t m');
  steps.forEach(([k, who, scope, out], i) => {
    const x = x0 + i * (bw + gap);
    const hi = i !== 2;
    s += `<g class="fx-step"><title>${k.toLowerCase()}: ${who}, ${scope}</title><rect x="${x}" y="${y}" width="${bw}" height="128" rx="14" class="fx-box${hi ? ' hi' : ''}"/>`;
    s += `<circle cx="${x + 24}" cy="${y + 26}" r="11" class="fx-num${hi ? '' : ' dim'}"/>` + t(x + 24, y + 30, String(i + 1), 'fx-t numt');
    s += t(x + 44, y + 31, k, 'fx-t k', 'start');
    s += t(x + bw / 2, y + 64, who, 'fx-t b');
    s += t(x + bw / 2, y + 86, scope, 'fx-t m mono');
    s += `<line x1="${x + 16}" y1="${y + 100}" x2="${x + bw - 16}" y2="${y + 100}" class="fx-rule"/>`;
    s += t(x + bw / 2, y + 118, out, 'fx-t acc mono');
    if (i < 3) s += `<line x1="${x + bw + 3}" y1="${y + 64}" x2="${x + bw + gap - 3}" y2="${y + 64}" class="fx-line" marker-end="url(#fxa)"/>`;
  });
  return svg(200, s, 'one epoch: submit, score, settle, claim');
}

function consensus(): string {
  const scores = [10, 10, 10, 100], C = 10;
  const x0 = 70, base = 230, top = 40, h = base - top, bw = 64, gap = 34;
  const Y = (v: number) => base - (v / 100) * h;
  let s = '';
  for (const v of [0, 25, 50, 75, 100])
    s += `<line x1="${x0 - 8}" x2="${x0 + 4 * (bw + gap)}" y1="${Y(v)}" y2="${Y(v)}" class="fx-grid"/>` + t(x0 - 14, Y(v) + 4, String(v), 'fx-t m', 'end');
  s += `<defs><pattern id="fxh" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="fx-hatch"/></pattern></defs>`;
  scores.forEach((v, i) => {
    const x = x0 + 14 + i * (bw + gap);
    const kept = Math.min(v, C);
    if (v > C) s += `<g><title>validator ${i + 1} gave ${v}; ${v - C} is above consensus and is clipped</title><rect x="${x}" y="${Y(v)}" width="${bw}" height="${Y(C) - Y(v) - 2}" rx="4" class="fx-cut"/><rect x="${x}" y="${Y(v)}" width="${bw}" height="${Y(C) - Y(v) - 2}" rx="4" fill="url(#fxh)"/></g>`
      + t(x + bw / 2, Y(v) + (Y(C) - Y(v)) / 2 - 4, 'clipped', 'fx-t m') + t(x + bw / 2, Y(v) + (Y(C) - Y(v)) / 2 + 12, `−${v - C}`, 'fx-t b');
    s += `<g><title>validator ${i + 1}: ${v} → counts ${kept}</title><rect x="${x}" y="${Y(kept)}" width="${bw}" height="${base - Y(kept)}" rx="4" class="fx-bar"/></g>`;
    s += t(x + bw / 2, base + 18, `v${i + 1}`, 'fx-t m') + t(x + bw / 2, Y(v) - 8, String(v), 'fx-t b');
  });
  const xe = x0 + 4 * (bw + gap);
  s += `<line x1="${x0 - 8}" x2="${xe}" y1="${Y(C)}" y2="${Y(C)}" class="fx-cline"/>`;
  s += t(xe + 10, Y(C) + 4, 'consensus C = 10', 'fx-t acc', 'start');
  s += `<rect x="${xe + 4}" y="70" width="200" height="92" rx="12" class="fx-box hi"/>`
    + t(xe + 104, 96, 'need ⌈V/2⌉ = 2 votes ≥ C', 'fx-t m')
    + t(xe + 104, 122, 'R = 10+10+10+10 = 40', 'fx-t b mono')
    + t(xe + 104, 146, 'unclipped would be 130', 'fx-t m');
  return svg(260, s, 'consensus: scores 10, 10, 10, 100; consensus 10; the 100 is clipped to 10');
}

function overview(): string {
  const chains = [
    ['Starknet', 'Cairo · native EC'],
    ['NEAR', 'Rust → wasm'],
    ['Ethereum', 'Solidity + Yul'],
    ['Solana', 'sBPF, split in units'],
    ['postquant', 'pqc1 wasm + ML-DSA'],
  ];
  let s = arrow;
  s += `<rect x="24" y="96" width="200" height="88" rx="14" class="fx-box hi"/>`
    + t(124, 126, 'one signer', 'fx-t k') + t(124, 148, 'lsag.py ≡ lsag.cairo', 'fx-t m mono') + t(124, 166, 'bit-identical, ~80 ms', 'fx-t m');
  s += `<rect x="290" y="116" width="120" height="48" rx="24" class="fx-box"/>` + t(350, 145, 'Σ = (c₀, s, I)', 'fx-t b mono');
  s += `<line x1="226" y1="140" x2="286" y2="140" class="fx-line" marker-end="url(#fxa)"/>`;
  chains.forEach(([n, d], i) => {
    const y = 18 + i * 52;
    s += `<path d="M412 140 C 450 140, 450 ${y + 21}, 486 ${y + 21}" class="fx-line" marker-end="url(#fxa)"/>`;
    s += `<g class="fx-step"><title>${n}: ${d}</title><rect x="490" y="${y}" width="206" height="42" rx="10" class="fx-box"/>`
      + `<circle cx="508" cy="${y + 21}" r="4.5" class="fx-dot"/>` + t(522, y + 18, n, 'fx-t b', 'start') + t(522, y + 33, d, 'fx-t m', 'start') + '</g>';
  });
  return svg(282, s, 'one signer, a verifier contract per chain');
}

function costs(): string {
  const rows: [string, number, string][] = [
    ['Starknet', 64, 'contract MAX_RING'],
    ['postquant', 64, '5 s replay budget'],
    ['NEAR', 32, '300 Tgas per call'],
    ['Ethereum', 32, 'EIP-7825: 2²⁴ gas per tx'],
    ['Solana', 32, 'session account layout'],
  ];
  const x0 = 110, x1 = 560, y0 = 34, rh = 38;
  const X = (v: number) => x0 + (v / 64) * (x1 - x0);
  let s = '';
  for (const v of [0, 16, 32, 48, 64])
    s += `<line x1="${X(v)}" x2="${X(v)}" y1="${y0 - 10}" y2="${y0 + rows.length * rh}" class="fx-grid"/>` + t(X(v), y0 + rows.length * rh + 16, String(v), 'fx-t m');
  s += t(x0, 16, 'largest ring a chain can verify (members)', 'fx-t m', 'start');
  rows.forEach(([n, v, why], i) => {
    const y = y0 + i * rh;
    s += t(x0 - 12, y + 18, n, 'fx-t b', 'end');
    s += `<g><title>${n}: up to ${v} members, bound by ${why}</title><rect x="${x0}" y="${y + 4}" width="${X(v) - x0}" height="20" rx="4" class="fx-bar"/></g>`;
    s += t(X(v) + 8, y + 18, `${v}`, 'fx-t b', 'start') + t(X(v) + 34, y + 18, why, 'fx-t m', 'start');
  });
  return svg(y0 + rows.length * rh + 26, s, 'max ring size per chain');
}

export const FIGS: Record<string, () => string> = { ring, flow, consensus, overview, costs, cost: costs };

export function figure(name: string, caption: string, n: number, cap: (s: string) => string): string {
  const f = FIGS[name];
  if (!f) return '';
  return `<figure class="pfig"><div class="pfig-art">${f()}</div>${caption ? `<figcaption><b>Figure ${n}.</b> ${cap(caption)}</figcaption>` : ''}</figure>`;
}
