// The wasmland ABI — how a stored module and the host exchange text.
//
// Strings cross the boundary the plainest way there is: the module exports
// `alloc(len) -> ptr`, the host writes UTF-8 there, and anything the module
// returns comes back as one i64 holding `(ptr << 32) | len`. That is the whole
// calling convention. No allocator on the host side, no shared struct layout,
// no glue library — which is what makes it writable from Rust, AssemblyScript,
// Zig or hand-written WAT alike.
//
//     alloc(len)         -> ptr
//     run(ptr, len)      -> packed (ptr << 32) | len
//
// It is deliberately the same convention the arena uses, so a module written
// for one is runnable by the other. A module that exports `_start` instead is
// a WASI command: it reads stdin and writes stdout, and that is its answer.

const enc = new TextEncoder();
const dec = new TextDecoder('utf-8', { fatal: false });

export const encode = (s) => enc.encode(s ?? '');
export const decode = (b) => dec.decode(b);

/** Split the packed `(ptr << 32) | len` a module returns. */
export function unpack(packed) {
  const v = BigInt(packed);
  return { ptr: Number(v >> 32n) >>> 0, len: Number(v & 0xffffffffn) >>> 0 };
}

export class Abi {
  constructor(host) {
    this.host = host;
  }

  get exports() {
    return this.host.exports;
  }

  has(name) {
    return typeof this.exports[name] === 'function';
  }

  /** Copy a string into module memory. Returns `[ptr, len]`, ready to spread. */
  put(text) {
    const raw = enc.encode(text ?? '');
    if (raw.length === 0) return [0, 0];
    if (!this.has('alloc')) {
      throw new Error('module exports no `alloc(i32) -> i32`, so nothing can be passed to it');
    }
    const ptr = this.exports.alloc(raw.length);
    if (!ptr) throw new Error(`alloc(${raw.length}) returned a null pointer`);
    this.host.memory().set(raw, ptr);
    return [ptr, raw.length];
  }

  /** Read a packed return value back as a string. */
  take(packed) {
    const { ptr, len } = unpack(packed);
    if (!len) return '';
    const bytes = this.host.memory();
    if (ptr + len > bytes.length) {
      throw new Error(`module returned a pointer past the end of its memory (${ptr}+${len})`);
    }
    return dec.decode(bytes.subarray(ptr, ptr + len));
  }

  /** Call an export that takes text and returns packed text. */
  callText(name, text) {
    const fn = this.exports[name];
    if (typeof fn !== 'function') throw new Error(`module exports no \`${name}\``);
    const args = fn.length >= 2 ? this.put(text) : [];
    return this.take(fn(...args));
  }
}

/**
 * What a module is, read out of its own binary rather than from whoever
 * uploaded it. The role is a consequence of the exports, so publishing a game
 * is publishing a module — there is nothing to declare.
 */
export function classify(exports) {
  const has = (n) => exports.includes(n);
  const game = ['game_init', 'game_view', 'game_step', 'game_done', 'game_result'];
  if (game.every(has)) return 'game';
  if (has('play')) return 'player';
  if (has('run')) return 'function';
  if (has('_start')) return 'command';
  return 'module';
}
