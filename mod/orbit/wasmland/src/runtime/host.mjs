// The host — one implementation, two homes.
//
// The console imports this straight from the server; the node runner imports
// the same file off disk. Whatever runs a module here runs it the same way
// there, which is the only reason a run claimed by a browser tab can be
// checked by replaying it on a server.
//
// DETERMINISM IS THE PRODUCT
//
// A run is verified by running it again and getting the same answer, so
// everything a module could use to tell two identical runs apart is either
// removed or made a function of the seed:
//
//     clock      a fixed epoch plus a counter — `now()` advances 1ms per call
//     randomness one seeded PRNG, feeding both `random()` and WASI random_get
//     network    not imported, and on the server the process has no netns
//     files      no preopens, so WASI path_open has nothing to open
//     threads    none offered
//
// What is left is the module, its input, and the seed. Same three in, same
// bytes out — on this box, on yours, in a tab.
//
// The rule for the registry is "anything runs", so instantiation must not be
// the thing that fails. Unknown imports are synthesised from the module's own
// import list and recorded; a module that wanted something we don't have still
// loads and still tells you exactly what it asked for.

import { Abi, decode, encode } from './abi.mjs';

// 2025-01-01T00:00:00Z. Any fixed instant would do; a recognisable one makes a
// transcript readable instead of looking like a bug.
export const EPOCH_MS = 1735689600000;

const GOLDEN = 0x9e3779b97f4a7c15n;

function mix(z) {
  z = BigInt.asUintN(64, (z ^ (z >> 30n)) * 0xbf58476d1ce4e5b9n);
  z = BigInt.asUintN(64, (z ^ (z >> 27n)) * 0x94d049bb133111ebn);
  return z ^ (z >> 31n);
}

/**
 * splitmix64 — small, seedable, and identical in every JS engine.
 *
 * The seed is scattered before it becomes state. splitmix64 walks its state
 * forward by the golden ratio, so seeding with it directly would leave seed n
 * and seed n+1 producing the *same stream one step apart* — two runs that look
 * independent and aren't. Mixing once up front costs nothing and makes
 * neighbouring seeds unrelated, which is what anyone picking seed=1, seed=2
 * assumes they are getting.
 */
export function prng(seed) {
  let state = mix(BigInt.asUintN(64, BigInt(seed >>> 0) * GOLDEN + 1n));
  return () => {
    state = BigInt.asUintN(64, state + GOLDEN);
    // 53 bits is what a double can hold exactly — the same float everywhere.
    return Number(mix(state) >> 11n) / 9007199254740992;
  };
}

const WASI = 'wasi_snapshot_preview1';

// WASI errno, the handful this host actually returns.
const OK = 0, EBADF = 8, ENOSYS = 52, ENOTCAPABLE = 76;

export class Host {
  constructor({ seed = 0, input = '', limits = {} } = {}) {
    this.seed = seed >>> 0;
    this.random = prng(this.seed);
    this.clock = 0;                       // ms since EPOCH_MS, host-advanced
    this.instance = null;
    this.exports = {};
    this.logs = [];
    this.stdout = '';
    this.stderr = '';
    this.stdin = encode(input);
    this.stdinPos = 0;
    this.exitCode = null;
    this.limits = { output_bytes: 1 << 20, log_lines: 1000, ...limits };
    // What the module reached for. The receipt carries this: a run that had to
    // be given a made-up import is still deterministic, but you should know.
    this.effects = { now: 0, random: 0, stubs: {}, wrote_stderr: false };
  }

  now() {
    this.effects.now += 1;
    return EPOCH_MS + this.clock++;
  }

  memory() {
    const mem = this.exports.memory;
    if (!mem) throw new Error('module exports no memory');
    return new Uint8Array(mem.buffer);
  }

  view() {
    return new DataView(this.exports.memory.buffer);
  }

  log(text) {
    if (this.logs.length < this.limits.log_lines) this.logs.push(text);
  }

  _append(which, text) {
    const cap = this.limits.output_bytes;
    if (this[which].length >= cap) return;
    this[which] = (this[which] + text).slice(0, cap);
  }

  // ── the import object ──────────────────────────────────────────────

  /** A real preview1 subset: enough for a compiled command to start, read
   *  its input, print and exit. Everything absent is absent on purpose. */
  wasi() {
    const self = this;
    const iovs = (ptr, count, fn) => {
      const dv = self.view();
      let total = 0;
      for (let i = 0; i < count; i++) {
        const base = dv.getUint32(ptr + i * 8, true);
        const len = dv.getUint32(ptr + i * 8 + 4, true);
        total += fn(base, len);
      }
      return total;
    };
    return {
      fd_write(fd, ptr, count, written) {
        const bytes = self.memory();
        const n = iovs(ptr, count, (base, len) => {
          const text = decode(bytes.subarray(base, base + len));
          if (fd === 2) { self.effects.wrote_stderr = true; self._append('stderr', text); }
          else self._append('stdout', text);
          return len;
        });
        self.view().setUint32(written, n, true);
        return OK;
      },
      fd_read(fd, ptr, count, read) {
        if (fd !== 0) return EBADF;
        const bytes = self.memory();
        const n = iovs(ptr, count, (base, len) => {
          const slice = self.stdin.subarray(self.stdinPos, self.stdinPos + len);
          bytes.set(slice, base);
          self.stdinPos += slice.length;
          return slice.length;
        });
        self.view().setUint32(read, n, true);
        return OK;
      },
      fd_close: () => OK,
      fd_datasync: () => OK,
      fd_sync: () => OK,
      fd_seek: () => ENOSYS,
      fd_fdstat_get(fd, ptr) {
        // A character device with no rights — enough for libc to decide stdout
        // is a terminal and stop asking.
        const dv = self.view();
        dv.setUint8(ptr, 2);
        dv.setUint16(ptr + 2, 0, true);
        dv.setBigUint64(ptr + 8, 0n, true);
        dv.setBigUint64(ptr + 16, 0n, true);
        return OK;
      },
      fd_fdstat_set_flags: () => OK,
      fd_prestat_get: () => EBADF,          // no preopens: nothing to walk into
      fd_prestat_dir_name: () => EBADF,
      path_open: () => ENOTCAPABLE,         // there is no filesystem here
      path_filestat_get: () => ENOTCAPABLE,
      environ_sizes_get(count, size) {
        const dv = self.view();
        dv.setUint32(count, 0, true);
        dv.setUint32(size, 0, true);
        return OK;
      },
      environ_get: () => OK,
      args_sizes_get(count, size) {
        const dv = self.view();
        dv.setUint32(count, 0, true);
        dv.setUint32(size, 0, true);
        return OK;
      },
      args_get: () => OK,
      clock_time_get(id, precision, out) {
        self.view().setBigUint64(out, BigInt(self.now()) * 1000000n, true);
        return OK;
      },
      clock_res_get(id, out) {
        self.view().setBigUint64(out, 1000000n, true);
        return OK;
      },
      random_get(ptr, len) {
        const bytes = self.memory();
        self.effects.random += 1;
        for (let i = 0; i < len; i++) bytes[ptr + i] = Math.floor(self.random() * 256);
        return OK;
      },
      sched_yield: () => OK,
      poll_oneoff: () => ENOSYS,
      proc_exit(code) {
        self.exitCode = code;
        throw new Exit(code);
      },
    };
  }

  /** Hostcalls a module may ask for by name. Text in, text out, same ABI. */
  hostcalls() {
    const self = this;
    return {
      log(ptr, len) {
        self.log(decode(self.memory().subarray(ptr, ptr + len)));
      },
      now: () => self.now(),
      random: () => { self.effects.random += 1; return self.random(); },
      seed: () => self.seed,
    };
  }

  /**
   * Everything the module imports, in the shape it imports it.
   *
   * Anything we don't recognise becomes a counted no-op rather than a failed
   * instantiation — with its name in `effects.stubs`, so "it ran but returned
   * nonsense" is a question you can answer instead of guess at.
   */
  imports(bytes) {
    const wasi = this.wasi();
    const host = this.hostcalls();
    const table = { [WASI]: wasi, wasi_unstable: wasi, wasmland: host, arena: host, env: {} };
    for (const imp of WebAssembly.Module.imports(new WebAssembly.Module(bytes))) {
      const mod = (table[imp.module] ||= {});
      if (imp.name in mod) continue;
      if (imp.kind === 'function') {
        mod[imp.name] = (...args) => {
          const key = `${imp.module}.${imp.name}`;
          this.effects.stubs[key] = (this.effects.stubs[key] || 0) + 1;
          return 0;
        };
      } else if (imp.kind === 'memory') {
        mod[imp.name] = new WebAssembly.Memory({ initial: 17, maximum: 4096 });
      } else if (imp.kind === 'table') {
        mod[imp.name] = new WebAssembly.Table({ initial: 0, element: 'anyfunc' });
      } else if (imp.kind === 'global') {
        mod[imp.name] = new WebAssembly.Global({ value: 'i32', mutable: true }, 0);
      }
    }
    return table;
  }

  async instantiate(bytes) {
    const { instance } = await WebAssembly.instantiate(bytes, this.imports(bytes));
    this.instance = instance;
    this.exports = instance.exports;
    // An imported memory is still the module's memory as far as the ABI cares.
    if (!this.exports.memory) {
      for (const table of Object.values(this.imports(bytes))) {
        for (const value of Object.values(table)) {
          if (value instanceof WebAssembly.Memory) { this.exports = { ...this.exports, memory: value }; break; }
        }
      }
    }
    return new Abi(this);
  }
}

/** proc_exit is not an error — it is how a command says it is finished. */
export class Exit extends Error {
  constructor(code) {
    super(`exit ${code}`);
    this.code = code;
  }
}
