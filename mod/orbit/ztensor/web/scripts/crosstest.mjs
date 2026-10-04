// crosstest.mjs — prove the browser signer (lib/lsag.mjs) is wire-compatible
// with the reference python implementation (ring.py): node signs, python
// verifies, and vice versa via the committed Rust fixture.
// Run from web/:  node scripts/crosstest.mjs
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { webcrypto } from 'node:crypto';

// node 18 has no global WebCrypto; the browser always does
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const { keygen, sign, verify, fromHex, toHex } = await import('../lib/lsag.mjs');

const moduleDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

// 1. JS roundtrip
const keys = Array.from({ length: 4 }, () => keygen());
const ring = keys.map((k) => k.pub);
const topic = 'cross-epoch';
const msg = 'cross-epoch|minerX';
const sig = await sign(keys[2].secret, ring, topic, msg);
if (!(await verify(ring, topic, msg, sig))) throw new Error('JS roundtrip failed');

// 2. JS sign -> python verify
const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(moduleDir)})
import ring
d = json.loads(sys.stdin.read())
rng = [int(x, 16) for x in d["ring"]]
sig = {"c0": int(d["sig"]["c0"]), "s": [int(x) for x in d["sig"]["s"]], "tag": int(d["sig"]["tag"])}
ok = ring.verify(rng, d["topic"].encode(), d["msg"].encode(), sig)
bad = ring.verify(rng, d["topic"].encode(), b"tampered", sig)
print(json.dumps({"ok": ok, "tamper_rejected": not bad}))
`;
const out = JSON.parse(
  execFileSync('python3', ['-c', py], {
    input: JSON.stringify({ ring: ring.map(toHex), topic, msg, sig }),
  }).toString()
);
if (!out.ok || !out.tamper_rejected) throw new Error('python rejected a JS signature: ' + JSON.stringify(out));

// 3. python sign (committed fixture) -> JS verify
const fx = (await import('node:fs')).readFileSync(path.join(moduleDir, 'api/tests/fixtures/lsag_python.json'));
const f = JSON.parse(fx);
const fring = f.ring.map(fromHex);
if (!(await verify(fring, f.topic, f.msg, f.sig))) throw new Error('JS failed to verify python fixture');
if (await verify(fring, f.topic, 'tampered', f.sig)) throw new Error('JS accepted a tampered fixture');

console.log('crosstest: JS<->python LSAG wire compatibility OK');
