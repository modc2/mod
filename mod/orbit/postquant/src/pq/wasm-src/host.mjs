// The wasm key-type host: one long-lived node process, JSON lines on
// stdin/stdout. pq/wasmvm.py keeps one of these alive and feeds it every
// witness the chain checks — and, for wasm-defined key types, every keygen
// and signature too.
//
//   in : {"id":1, "op":"verify", "wasm":"/abs/path.wasm", "sha3":"<hex>",
//         "pk":"<hex>", "msg":"<hex>", "sig":"<hex>", "ctx":"<hex>"}
//   out: {"id":1, "ok":true, "valid":true}
//
//   in : {"id":2, "op":"keygen", "wasm":..., "sha3":..., "pure":true,
//         "seed":"<hex>", "pk_bytes":N, "sk_bytes":N}
//   out: {"id":2, "ok":true, "pk":"<hex>", "sk":"<hex>"}
//
//   in : {"id":3, "op":"sign", "wasm":..., "sha3":..., "pure":true,
//         "sk":"<hex>", "msg":"<hex>", "ctx":"<hex>", "sig_bytes":N}
//   out: {"id":3, "ok":true, "sig":"<hex>"}
//
//   in : {"id":4, "op":"run", "wasm":..., "sha3":..., "pure":true,
//         "state":"<hex>", "input":"<hex>", "out_cap":N}
//   out: {"id":4, "ok":true, "ret":<i32>, "out":"<hex>", "ms":<float>}
//
//   any: {"id":n, "ok":false, "error":"..."}
//
// op defaults to "verify" (the pq1 ABI); keygen/sign are the pq2 extension
// user-registered key types must export; run is the pqc1 contract ABI
// (pqc_run(state,state_len,input,input_len,out,out_cap) -> i32 = next state
// length, or <0 to revert) that off-chain contract execution goes through. "pure":true refuses to run a module
// that declares ANY import — inside an importless module there is no clock,
// no randomness and no host call, so the exports are deterministic functions
// of their inputs. That check is what lets this chain accept a stranger's
// key type: the blob can be re-run byte-identically by every node.
//
// The module is compiled once per (path, sha3) and re-instantiated per call:
// instantiation is microseconds, and a fresh instance means one call can
// never leave state behind for the next. The host re-hashes the file with
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

function outBuf(exports, n) {
  const ptr = exports.pq_alloc(n);
  if (ptr === 0) throw new Error('pq_alloc failed — output larger than the arena');
  return ptr;
}

function readBuf(exports, ptr, n) {
  return Buffer.from(new Uint8Array(exports.memory.buffer, ptr, n)).toString('hex');
}

function need(e, name, abi) {
  if (typeof e[name] !== 'function') {
    throw new Error(`module does not export ${name} (${abi} ABI)`);
  }
}

function run(req) {
  const mod = getModule(req.wasm, req.sha3);
  if (req.pure && WebAssembly.Module.imports(mod).length > 0) {
    throw new Error('module declares imports — a deterministic key type may '
      + 'import nothing (no clock, no randomness, no host)');
  }
  const instance = new WebAssembly.Instance(mod, {});
  const e = instance.exports;
  need(e, 'pq_alloc', 'pq1');
  need(e, 'pq_reset', 'pq1');
  e.pq_reset();
  const op = req.op || 'verify';
  if (op === 'verify') {
    need(e, 'pq_verify', 'pq1');
    const [pk, pkLen] = place(e, req.pk);
    const [msg, msgLen] = place(e, req.msg);
    const [sig, sigLen] = place(e, req.sig);
    const [ctx, ctxLen] = place(e, req.ctx);
    return { valid: e.pq_verify(pk, pkLen, msg, msgLen, sig, sigLen, ctx, ctxLen) === 1 };
  }
  if (op === 'keygen') {
    need(e, 'pq_keygen', 'pq2');
    const [seed, seedLen] = place(e, req.seed);
    const pkPtr = outBuf(e, req.pk_bytes);
    const skPtr = outBuf(e, req.sk_bytes);
    if (e.pq_keygen(seed, seedLen, pkPtr, skPtr) !== 1) {
      throw new Error('pq_keygen refused the seed');
    }
    return { pk: readBuf(e, pkPtr, req.pk_bytes), sk: readBuf(e, skPtr, req.sk_bytes) };
  }
  if (op === 'sign') {
    need(e, 'pq_sign', 'pq2');
    const [sk, skLen] = place(e, req.sk);
    const [msg, msgLen] = place(e, req.msg);
    const [ctx, ctxLen] = place(e, req.ctx);
    const sigPtr = outBuf(e, req.sig_bytes);
    if (e.pq_sign(sk, skLen, msg, msgLen, ctx, ctxLen, sigPtr) !== 1) {
      throw new Error('pq_sign refused');
    }
    return { sig: readBuf(e, sigPtr, req.sig_bytes) };
  }
  if (op === 'run') {
    // pqc1: a contract is a pure state transition. The host hands it the
    // current state and one input, gets back the next state. A negative
    // return is a revert (the contract's own reason code); a trap
    // (panic -> unreachable) surfaces as ok:false and is a revert too.
    need(e, 'pqc_run', 'pqc1');
    const [st, stLen] = place(e, req.state);
    const [inp, inpLen] = place(e, req.input);
    const cap = req.out_cap | 0;
    const outPtr = outBuf(e, cap);
    const t0 = process.hrtime.bigint();
    const ret = e.pqc_run(st, stLen, inp, inpLen, outPtr, cap);
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    if (ret < 0) return { ret, out: '', ms };
    if (ret > cap) {
      throw new Error(`pqc_run claims ${ret} output bytes over the ${cap}-byte cap`);
    }
    return { ret, out: readBuf(e, outPtr, ret), ms };
  }
  if (op === 'prove') {
    // pqp1: a proof system is a pure verdict over (state_in, input,
    // state_out, proof). 1 = valid; the host never interprets anything else.
    need(e, 'pqp_verify', 'pqp1');
    const [si, siLen] = place(e, req.state_in);
    const [inp, inpLen] = place(e, req.input);
    const [so, soLen] = place(e, req.state_out);
    const [pr, prLen] = place(e, req.proof);
    const t0 = process.hrtime.bigint();
    const ret = e.pqp_verify(si, siLen, inp, inpLen, so, soLen, pr, prLen);
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    return { valid: ret === 1, ret, ms };
  }
  throw new Error(`unknown op ${op}`);
}

const rl = createInterface({ input: process.stdin, terminal: false });
rl.on('line', (line) => {
  line = line.trim();
  if (!line) return;
  let req = null;
  try {
    req = JSON.parse(line);
    const out = run(req);
    process.stdout.write(JSON.stringify({ id: req.id, ok: true, ...out }) + '\n');
  } catch (err) {
    process.stdout.write(JSON.stringify({
      id: req && req.id, ok: false, error: String(err.message || err),
    }) + '\n');
  }
});
rl.on('close', () => process.exit(0));
