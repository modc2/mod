// The arena ABI — how a wasm game and a wasm player talk to the arena.
//
// Strings cross the boundary the plainest way there is: the module exports
// `alloc(len) -> ptr`, the host writes UTF-8 there, and anything the module
// returns comes back as one i64 holding `(ptr << 32) | len`. That is the whole
// calling convention. It needs no allocator on the host side, no shared
// struct layout, and no glue library — which is what makes it writable from
// Rust, AssemblyScript, Zig or hand-written WAT alike.
//
// State is a JSON string the *host* holds between calls. The module is a set
// of pure functions over it:
//
//   game_init(seed)                   -> state
//   game_turn(state)                  -> { seats: [n], turn }        (optional)
//   game_view(state, seat)            -> the text that seat can see
//   game_step(state, moves)           -> { state, legal, note }
//   game_done(state)                  -> 0 | 1
//   game_result(state)                -> { scores: [n], summary }
//   game_info()                       -> { name, players, max_turns } (optional)
//
//   play(view, seat)                  -> the move, as text
//
// Because the module never keeps state, a match is exactly its seed plus its
// list of moves — which is why transcripts here can be replayed and checked
// by anyone, on any engine.

const enc = new TextEncoder();
const dec = new TextDecoder("utf-8", { fatal: false });

/** Split the packed `(ptr << 32) | len` a module returns. */
export function unpack(packed) {
  const v = BigInt(packed);
  return { ptr: Number(v >> 32n) >>> 0, len: Number(v & 0xffffffffn) >>> 0 };
}

export class Abi {
  constructor(host) {
    this.host = host;
    this.exports = host.exports;
  }

  has(name) {
    return typeof this.exports[name] === "function";
  }

  /** Copy a string into module memory. Returns `[ptr, len]`, ready to spread. */
  put(text) {
    const raw = enc.encode(text ?? "");
    if (raw.length === 0) return [0, 0];
    if (!this.has("alloc")) {
      throw new Error("module exports no `alloc(i32) -> i32`, so nothing can be passed to it");
    }
    const ptr = this.exports.alloc(raw.length);
    if (!ptr) throw new Error(`alloc(${raw.length}) returned a null pointer`);
    this.host.memory().set(raw, ptr);
    return [ptr, raw.length];
  }

  /** Read a packed return value back as a string. */
  take(packed) {
    const { ptr, len } = unpack(packed);
    if (!len) return "";
    const bytes = this.host.memory();
    if (ptr + len > bytes.length) {
      throw new Error(`module returned a pointer past the end of its memory (${ptr}+${len})`);
    }
    return dec.decode(bytes.subarray(ptr, ptr + len));
  }

  /** Call an export that returns a packed string. */
  callText(name, ...args) {
    const fn = this.exports[name];
    if (typeof fn !== "function") throw new Error(`module exports no \`${name}\``);
    return this.take(fn(...args));
  }

  callJson(name, ...args) {
    const text = this.callText(name, ...args);
    if (!text.trim()) return null;
    try {
      return JSON.parse(text);
    } catch (e) {
      throw new Error(`\`${name}\` did not return JSON: ${e.message} — got ${clip(text)}`);
    }
  }
}

/** The five exports that make a module a game. */
export const GAME_EXPORTS = ["game_init", "game_view", "game_step", "game_done", "game_result"];
export const PLAYER_EXPORTS = ["play"];

export function isGame(exports) {
  return GAME_EXPORTS.every((n) => typeof exports[n] === "function");
}

export function isPlayer(exports) {
  return PLAYER_EXPORTS.every((n) => typeof exports[n] === "function");
}

/**
 * A game module wrapped so the match loop can talk to it in plain JS. Every
 * method here is one wasm call plus the string marshalling around it.
 */
export class Game {
  constructor(abi) {
    this.abi = abi;
    const missing = GAME_EXPORTS.filter((n) => !abi.has(n));
    if (missing.length) {
      throw new Error(`not a game module — missing ${missing.join(", ")}`);
    }
    this.state = null;
  }

  /** The module's own description of itself, or defaults if it offers none. */
  info() {
    const raw = this.abi.has("game_info") ? this.abi.callJson("game_info") : null;
    const players = raw?.players ?? raw?.max_players ?? 2;
    return {
      name: raw?.name ?? "",
      description: raw?.description ?? "",
      min_players: raw?.min_players ?? (Array.isArray(players) ? players[0] : players),
      max_players: raw?.max_players ?? (Array.isArray(players) ? players[1] : players),
      max_turns: raw?.max_turns ?? 200,
      ...raw,
    };
  }

  init(seed) {
    this.state = this.abi.callText("game_init", seed | 0);
    return this.state;
  }

  /**
   * Whose move it is. A game that does not export `game_turn` is taken to be
   * strictly alternating, which covers most board games and costs its author
   * one fewer function to write.
   */
  turn(seats, turnNo) {
    if (this.abi.has("game_turn")) {
      const r = this.abi.callJson("game_turn", ...this.abi.put(this.state));
      const list = Array.isArray(r) ? r : (r?.seats ?? []);
      return list.map(Number).filter((n) => Number.isInteger(n) && n >= 0 && n < seats);
    }
    return [turnNo % seats];
  }

  view(seat) {
    return this.abi.callText("game_view", ...this.abi.put(this.state), seat | 0);
  }

  /**
   * Apply one round of moves, keyed by seat. Returns `{ legal, note }`; the
   * new state is kept here. A game that rejects a move says so in `legal` and
   * the arena counts it against the player — that number is most of what
   * separates a model that can play from one that can only talk about playing.
   */
  step(moves) {
    const r = this.abi.callJson(
      "game_step",
      ...this.abi.put(this.state),
      ...this.abi.put(JSON.stringify(moves)),
    );
    if (r && typeof r === "object" && "state" in r) {
      this.state = typeof r.state === "string" ? r.state : JSON.stringify(r.state);
      return { legal: r.legal ?? {}, note: r.note ?? "" };
    }
    // A game that just hands back the next state is taken at its word.
    this.state = typeof r === "string" ? r : JSON.stringify(r);
    return { legal: {}, note: "" };
  }

  done() {
    return !!this.abi.exports.game_done(...this.abi.put(this.state));
  }

  result() {
    const r = this.abi.callJson("game_result", ...this.abi.put(this.state)) ?? {};
    return { scores: r.scores ?? [], summary: r.summary ?? "", ...r };
  }
}

function clip(s, n = 120) {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
