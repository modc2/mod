// The wasm verification host: one long-lived node process, JSON lines on
// stdin/stdout. pq/wasmvm.py keeps one of these alive and feeds it every
// witness the chain checks.
//
//   in : {"id":1, "wasm":"/abs/path.wasm", "sha3":"<hex>",
//         "pk":"<hex>", "msg":"<hex>", "sig":"<hex>", "ctx":"<hex>"}
//   out: {"id":1, "ok":true, "valid":true}
//        {"id":1, "ok":false, "error":"..."}
//
// The module is compiled once per (path, sha3) and re-instantiated per call:
// instantiation is microseconds, and a fresh instance means one verification
// can never leave state behind for the next. The host re-hashes the file with
// SHA3-256 and refuses to run bytes that do not match the hash the registry
// committed to — the python side checks too; two doors, same lock.

import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createInterface } from 'node:readline';

const modules = new Map(); // `${path}:${sha3}` -> WebAssembly.Module

function getModule(path, sha3) {
  const key = `${path}:${sha3}`;
  let mod = modules.get(key);
  if (mod) return mod;
  const bytes = readFileSync(path);
  const digest = createHash('sha3-256').update(bytes).digest('hex');
  if (sha3 && digest !== sha3.toLowerCase()) {
    throw new Error(`wasm at ${path} hashes to ${digest}, registry says ${sha3}`);
  }
  mod = new WebAssembly.Module(bytes);
  modules.set(key, mod);
  return mod;
}

function place(exports, hex) {
  const bytes = Buffer.from(hex || '', 'hex');
  if (bytes.length === 0) return [0, 0];
  const ptr = exports.pq_alloc(bytes.length);
  if (ptr === 0) throw new Error('pq_alloc failed — input larger than the arena');
  new Uint8Array(exports.memory.buffer, ptr, bytes.length).set(bytes);
  return [ptr, bytes.length];
}

function verify(req) {
  const mod = getModule(req.wasm, req.sha3);
  const instance = new WebAssembly.Instance(mod, {});
  const e = instance.exports;
  if (typeof e.pq_verify !== 'function' || typeof e.pq_alloc !== 'function') {
    throw new Error('module does not export the pq1 ABI (pq_alloc/pq_verify)');
  }
  e.pq_reset();
  const [pk, pkLen] = place(e, req.pk);
  const [msg, msgLen] = place(e, req.msg);
  const [sig, sigLen] = place(e, req.sig);
  const [ctx, ctxLen] = place(e, req.ctx);
  return e.pq_verify(pk, pkLen, msg, msgLen, sig, sigLen, ctx, ctxLen) === 1;
}

const rl = createInterface({ input: process.stdin, terminal: false });
rl.on('line', (line) => {
  line = line.trim();
  if (!line) return;
  let req = null;
  try {
    req = JSON.parse(line);
    const valid = verify(req);
    process.stdout.write(JSON.stringify({ id: req.id, ok: true, valid }) + '\n');
  } catch (err) {
    process.stdout.write(JSON.stringify({
      id: req && req.id, ok: false, error: String(err.message || err),
    }) + '\n');
  }
});
rl.on('close', () => process.exit(0));
