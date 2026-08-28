// The wasm host — one implementation, two homes.
//
// The browser console imports this straight from the server; the node runner
// imports the same file off disk. Whatever runs a module here runs it the same
// way there, which is the only reason a match played in a tab and a match
// played from the CLI can share a leaderboard.
//
// The rule for the registry is "anything wasm", so instantiation must not be
// the thing that fails. Three shims are stacked to make that true:
//
//   wasi_snapshot_preview1  a real preview1 subset — enough for a compiled
//                           command to start, print and exit
//   arena                   hostcalls a game or player may ask for: log,
//                           random, now
//   anything else           synthesised from the module's own import list and
//                           logged, so an unsupported module still loads and
//                           tells you exactly what it wanted
//
// Nothing here reaches the filesystem or the network. A module gets memory,
// a clock, a seeded PRNG and a place to write text.

const PAGE = 65536;

const enc = new TextEncoder();
const dec = new TextDecoder("utf-8", { fatal: false });

/** WASI errno values we actually return. */
const ESUCCESS = 0, EBADF = 8, EINVAL = 28, ENOSYS = 52, ENOTCAPABLE = 76;

/**
 * A deterministic PRNG. Matches have to be replayable from their seed, so the
 * host never hands a module the real entropy source — not even through
 * `random_get`.
 */
export function rng(seed) {
  let s = (seed >>> 0) || 0x9e3779b9;
  return () => {
    // xorshift32 — small, fast, and identical in every JS engine.
    s ^= s << 13; s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 4294967296;
  };
}

/** The zero value of a wasm result type, for synthesised imports. */
function zeroFor(signature) {
  if (!signature) return 0;
  const result = signature.split("->")[1]?.trim() ?? "";
  if (result.startsWith("i64")) return 0n;
  return 0;
}

/**
 * Run a module.
 *
 * @param {BufferSource} bytes        the module
 * @param {object}       opts
 * @param {object}       opts.info    `wasm::describe` output, when the caller
 *                                    has it — it is what lets an unsupported
 *                                    import be stubbed with the right type
 * @param {string[]}     opts.args    argv for a WASI command
 * @param {string}       opts.stdin   what `fd_read` on fd 0 returns
 * @param {number}       opts.seed    seeds the PRNG behind `random_get`
 */
export async function instantiate(bytes, opts = {}) {
  const { info = null, args = [], env = {}, stdin = "", seed = 1 } = opts;
  const out = { stdout: "", stderr: "", log: [], stubbed: [], exit: null };
  const random = rng(seed);
  const t0 = nowMs();

  // A module that declares its own memory exports it; one that imports memory
  // gets this. Either way `mem()` is how everything below reaches the bytes.
  const imported = new WebAssembly.Memory({ initial: 2, maximum: 4096 });
  let instance = null;
  const mem = () => {
    const m = instance?.exports?.memory ?? imported;
    return new DataView(m.buffer);
  };
  const bytesOf = () => new Uint8Array((instance?.exports?.memory ?? imported).buffer);

  const readStr = (ptr, len) => dec.decode(bytesOf().subarray(ptr, ptr + len));
  const write = (stream, text) => {
    out[stream] += text;
    if (out[stream].length > 1 << 20) out[stream] = out[stream].slice(-(1 << 20));
  };

  let stdinAt = 0;
  const stdinBytes = enc.encode(stdin);

  const wasi = {
    args_sizes_get(countPtr, sizePtr) {
      const v = mem();
      v.setUint32(countPtr, args.length, true);
      v.setUint32(sizePtr, args.reduce((n, a) => n + enc.encode(a).length + 1, 0), true);
      return ESUCCESS;
    },
    args_get(ptrsPtr, bufPtr) {
      const v = mem(), b = bytesOf();
      let at = bufPtr;
      args.forEach((a, i) => {
        v.setUint32(ptrsPtr + i * 4, at, true);
        const raw = enc.encode(a + "\0");
        b.set(raw, at);
        at += raw.length;
      });
      return ESUCCESS;
    },
    environ_sizes_get(countPtr, sizePtr) {
      const keys = Object.keys(env), v = mem();
      v.setUint32(countPtr, keys.length, true);
      v.setUint32(sizePtr, keys.reduce((n, k) => n + enc.encode(`${k}=${env[k]}`).length + 1, 0), true);
      return ESUCCESS;
    },
    environ_get(ptrsPtr, bufPtr) {
      const v = mem(), b = bytesOf();
      let at = bufPtr;
      Object.keys(env).forEach((k, i) => {
        v.setUint32(ptrsPtr + i * 4, at, true);
        const raw = enc.encode(`${k}=${env[k]}\0`);
        b.set(raw, at);
        at += raw.length;
      });
      return ESUCCESS;
    },
    clock_time_get(_id, _precision, outPtr) {
      // Nanoseconds since the run started, not since the epoch: a module that
      // times itself must not be able to read the wall clock and break replay.
      mem().setBigUint64(outPtr, BigInt(Math.round((nowMs() - t0) * 1e6)), true);
      return ESUCCESS;
    },
    clock_res_get(_id, outPtr) {
      mem().setBigUint64(outPtr, 1000n, true);
      return ESUCCESS;
    },
    fd_write(fd, iovsPtr, iovsLen, writtenPtr) {
      if (fd !== 1 && fd !== 2) return EBADF;
      const v = mem();
      let n = 0, text = "";
      for (let i = 0; i < iovsLen; i++) {
        const p = v.getUint32(iovsPtr + i * 8, true);
        const l = v.getUint32(iovsPtr + i * 8 + 4, true);
        text += readStr(p, l);
        n += l;
      }
      write(fd === 1 ? "stdout" : "stderr", text);
      v.setUint32(writtenPtr, n, true);
      return ESUCCESS;
    },
    fd_read(fd, iovsPtr, iovsLen, readPtr) {
      if (fd !== 0) return EBADF;
      const v = mem(), b = bytesOf();
      let n = 0;
      for (let i = 0; i < iovsLen && stdinAt < stdinBytes.length; i++) {
        const p = v.getUint32(iovsPtr + i * 8, true);
        const l = v.getUint32(iovsPtr + i * 8 + 4, true);
        const take = Math.min(l, stdinBytes.length - stdinAt);
        b.set(stdinBytes.subarray(stdinAt, stdinAt + take), p);
        stdinAt += take;
        n += take;
      }
      v.setUint32(readPtr, n, true);
      return ESUCCESS;
    },
    fd_close: () => ESUCCESS,
    fd_seek: () => ENOSYS,
    fd_sync: () => ESUCCESS,
    fd_fdstat_get(fd, ptr) {
      if (fd > 2) return EBADF;
      const v = mem();
      v.setUint8(ptr, 2);          // filetype: character device
      v.setUint16(ptr + 2, 0, true);
      v.setBigUint64(ptr + 8, 0n, true);
      v.setBigUint64(ptr + 16, 0n, true);
      return ESUCCESS;
    },
    fd_fdstat_set_flags: () => ESUCCESS,
    // No preopened directories: a module asking for one is told there is none,
    // which is how a WASI runtime says "you have no filesystem".
    fd_prestat_get: () => EBADF,
    fd_prestat_dir_name: () => EBADF,
    path_open: () => ENOTCAPABLE,
    path_filestat_get: () => ENOTCAPABLE,
    poll_oneoff: () => ENOSYS,
    sched_yield: () => ESUCCESS,
    random_get(ptr, len) {
      const b = bytesOf();
      for (let i = 0; i < len; i++) b[ptr + i] = (random() * 256) | 0;
      return ESUCCESS;
    },
    proc_exit(code) {
      out.exit = code;
      // The only way out of a `_start` that never returns. Caught by `start()`.
      throw new ExitSignal(code);
    },
  };

  const arena = {
    log(ptr, len) { out.log.push(readStr(ptr, len)); },
    random,
    now: () => nowMs() - t0,
    abort(ptr, len) { throw new Error(readStr(ptr, len) || "module called arena.abort"); },
  };

  // AssemblyScript emits `abort(msg, file, line, col)` into `env` and nothing
  // else; giving it a real one turns a silent trap into a readable message.
  const envShim = {
    memory: imported,
    abort(msgPtr) {
      throw new Error(msgPtr ? "module aborted" : "module aborted");
    },
  };

  const imports = {
    wasi_snapshot_preview1: wasi,
    wasi_unstable: wasi,
    arena,
    env: envShim,
  };

  // Fill in whatever is left from the module's own import list, so nothing
  // fails to instantiate for want of a function nobody will call.
  for (const need of info?.imports ?? []) {
    const ns = (imports[need.module] ??= {});
    if (need.kind !== "func" || ns[need.name] !== undefined) continue;
    const zero = zeroFor(need.signature);
    ns[need.name] = (...a) => {
      out.stubbed.push(`${need.module}.${need.name}(${a.join(", ")})`);
      return zero;
    };
  }

  const module = await WebAssembly.compile(bytes);
  // A module may import a namespace the description never mentioned (an older
  // upload, a hand-written binary). Ask the engine directly and stub the rest.
  for (const need of WebAssembly.Module.imports(module)) {
    const ns = (imports[need.module] ??= {});
    if (ns[need.name] !== undefined) continue;
    if (need.kind === "memory") { ns[need.name] = imported; continue; }
    if (need.kind === "global") { ns[need.name] = 0; continue; }
    if (need.kind === "table") {
      ns[need.name] = new WebAssembly.Table({ initial: 1, element: "anyfunc" });
      continue;
    }
    ns[need.name] = (...a) => {
      out.stubbed.push(`${need.module}.${need.name}(${a.join(", ")})`);
      return 0;
    };
  }

  instance = await WebAssembly.instantiate(module, imports);
  return { instance, exports: instance.exports, out, memory: () => bytesOf(), random };
}

/** Thrown by `proc_exit`; a clean end, not a failure. */
export class ExitSignal extends Error {
  constructor(code) {
    super(`exited ${code}`);
    this.code = code;
  }
}

/**
 * Run a module as a command: call `_start` (or `main`, or a named export) and
 * report what it printed. This is the "run any wasm" path — the one the
 * console's RUN button uses.
 */
export async function run(bytes, opts = {}) {
  const t0 = nowMs();
  let host;
  try {
    host = await instantiate(bytes, opts);
  } catch (e) {
    return { ok: false, error: `instantiate: ${e.message}`, ms: Math.round(nowMs() - t0) };
  }

  const name = opts.entry || (host.exports._start ? "_start" : host.exports.main ? "main" : null);
  if (!name) {
    return {
      ok: true, ms: Math.round(nowMs() - t0), ...host.out,
      note: "no entry point — the module exports no `_start` or `main`, so nothing was called",
      exports: Object.keys(host.exports),
    };
  }
  const fn = host.exports[name];
  if (typeof fn !== "function") {
    return { ok: false, error: `\`${name}\` is not an exported function`, ms: Math.round(nowMs() - t0),
             exports: Object.keys(host.exports) };
  }

  let value = null, error = "", text;
  try {
    value = fn(...(opts.callArgs ?? []));
    if (typeof value === "bigint") {
      // An i64 return is usually the arena ABI's packed (ptr << 32) | len, so
      // try to read a string out of it. Best effort: a module that really did
      // mean to return a number keeps `value` either way.
      text = unpackText(value, host.memory());
      value = value.toString();
    }
  } catch (e) {
    if (e instanceof ExitSignal) {
      // fine — a command that called proc_exit
    } else {
      error = e.message || String(e);
    }
  }
  return {
    ok: !error, error, entry: name, value,
    ...(text === undefined ? {} : { text }),
    ms: Math.round(nowMs() - t0),
    ...host.out,
    exports: Object.keys(host.exports),
  };
}

/**
 * Read an arena-ABI packed return value as text, or return undefined if it
 * does not look like one. Every check here is a way the guess can be wrong:
 * a pointer past the end of memory, an absurd length, or bytes that are not
 * UTF-8 all mean the module returned a number and meant it.
 */
function unpackText(packed, bytes) {
  const ptr = Number(packed >> 32n) >>> 0;
  const len = Number(packed & 0xffffffffn) >>> 0;
  if (!len || len > 1 << 20 || ptr + len > bytes.length) return undefined;
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(ptr, ptr + len));
  } catch {
    return undefined;
  }
}

export function nowMs() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

export { PAGE };
