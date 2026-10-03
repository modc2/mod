"""
Parity: does every target actually compute the same thing?

The library's claim is that a formula written once is the same formula in a
notebook, in a Rust service and in a browser. That claim is worth exactly as
much as the harness that tests it, so this module takes each `#example` and
evaluates it four ways —

    reference   the interpreter in ir.py, which *is* the definition of meaning
    python      the generated module, imported and called
    rust        the generated crate, compiled with rustc and run
    js          the generated web code, run under node

— and reports the worst disagreement per definition. A target whose toolchain
is missing is reported as skipped, never as passing: a parity run that quietly
checks one language is worse than no parity run at all.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from . import ir, lower
from .lower.emit import TARGETS


def _flat(defs, only=None) -> list[dict]:
    """Every example in the library, as a flat list of concrete calls."""
    out = []
    for d in lower.order(defs):
        if only and d.name not in only:
            continue
        for c in lower.cases(d, defs):
            out.append({'def': d.name, 'args': c['args'], 'expect': c['expect'],
                        'tol': c['tol'], 'ret': d.ret,
                        'source': d.source.get('key', '')})
    return out


def reference(defs, calls) -> list:
    return [ir.call(defs[c['def']], c['args'], defs) for c in calls]


def run_python(defs, calls) -> list:
    ns: dict = {}
    exec(compile(lower.python(defs), '<leanland.py>', 'exec'), ns)
    return [ns[c['def']](*c['args']) for c in calls]


def run_js(defs, calls, timeout=60) -> list:
    node = shutil.which('node')
    if not node:
        raise FileNotFoundError('node is not installed')
    src = '\n\n'.join(lower.emit.function(d, defs, TARGETS['js'])
                      for d in lower.order(defs)).replace('export function', 'function')
    calls_js = ',\n'.join(f'{c["def"]}({", ".join(json.dumps(a) for a in c["args"])})'
                          for c in calls)
    prog = f'{src}\n\nconsole.log(JSON.stringify([\n{calls_js}\n]));\n'
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'parity.js')
        with open(path, 'w') as f:
            f.write(prog)
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:800])
    return json.loads(r.stdout)


def run_rust(defs, calls, timeout=300) -> list:
    """Compile with rustc directly rather than cargo: no manifest, no registry,
    no network — the generated crate has no dependencies to resolve anyway."""
    rustc = shutil.which('rustc')
    if not rustc:
        raise FileNotFoundError('rustc is not installed')
    tgt = TARGETS['rs']
    lines = []
    for c in calls:
        d = defs[c['def']]
        args = ', '.join(lower._value(v, t, tgt) for v, (_, t) in zip(c['args'], d.params))
        cast = 'if v { 1.0 } else { 0.0 }' if d.ret == 'Bool' else 'v as f64'
        lines.append(f'    {{ let v = {c["def"]}({args}); out.push(format!("{{:?}}", {cast})); }}')
    prog = (lower.rust(defs) + '\n\nfn main() {\n    let mut out: Vec<String> = Vec::new();\n'
            + '\n'.join(lines) + '\n    println!("[{}]", out.join(","));\n}\n')
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'parity.rs')
        exe = os.path.join(tmp, 'parity')
        with open(src, 'w') as f:
            f.write(prog)
        b = subprocess.run([rustc, '--edition', '2021', '-O', '-A', 'warnings',
                            src, '-o', exe], capture_output=True, text=True, timeout=timeout)
        if b.returncode != 0:
            raise RuntimeError(b.stderr.strip()[:800])
        r = subprocess.run([exe], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:800])
    return json.loads(r.stdout)


RUNNERS = {'python': run_python, 'rust': run_rust, 'js': run_js}


def parity(defs, targets=None, only=None) -> dict:
    """Run every #example on every available target and compare to the reference."""
    calls = _flat(defs, only)
    if not calls:
        return {'ok': False, 'cases': 0,
                'note': 'nothing to check — no definition states an #example'}
    ref = reference(defs, calls)

    got, skipped = {}, {}
    for name in (targets or RUNNERS):
        try:
            got[name] = RUNNERS[name](defs, calls)
        except Exception as e:
            skipped[name] = str(e).splitlines()[0][:300]

    rows, worst = [], 0.0
    for i, c in enumerate(calls):
        row = {'def': c['def'], 'args': c['args'], 'source': c['source'],
               'expect': c['expect'], 'tol': c['tol'], 'reference': ref[i], 'targets': {}}
        # The paper's number is checked against the reference; the targets are
        # checked against the reference. Two different questions: "is the
        # definition right?" and "did lowering preserve it?"
        row['matches_source'] = _near(ref[i], c['expect'], c['tol'])
        for name, values in got.items():
            delta = _delta(values[i], ref[i])
            worst = max(worst, delta)
            row['targets'][name] = {'value': values[i], 'delta': delta,
                                    'ok': delta <= max(c['tol'], 1e-12)}
        row['ok'] = row['matches_source'] and all(t['ok'] for t in row['targets'].values())
        rows.append(row)

    bad = [r for r in rows if not r['ok']]
    return {'ok': not bad and not skipped, 'cases': len(calls),
            'targets': sorted(got), 'skipped': skipped,
            'worst_delta': worst, 'mismatches': bad,
            'rows': rows}


def _near(a, b, tol) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return abs(a - b) <= tol


def _delta(a, b) -> float:
    if isinstance(a, bool) or isinstance(b, bool):
        return 0.0 if bool(a) == bool(b) else 1.0
    return abs(float(a) - float(b))


def lean_available() -> dict:
    """Is a real Lean toolchain here? The generated Lean is emitted either way;
    this only says whether anything can check it."""
    lean = shutil.which('lean')
    if not lean:
        return {'available': False,
                'note': 'no `lean` on PATH — out/lean/Leanland.lean is emitted but unchecked'}
    try:
        v = subprocess.run([lean, '--version'], capture_output=True, text=True,
                           timeout=30).stdout.strip()
    except Exception as e:
        return {'available': False, 'note': str(e)}
    return {'available': True, 'version': v}


def lean_check(path: str, timeout: int = 300) -> dict:
    info = lean_available()
    if not info['available']:
        return {'ok': False, **info}
    r = subprocess.run([shutil.which('lean'), path], capture_output=True,
                       text=True, timeout=timeout)
    return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr).strip()[:4000]}
