// The compute types, as code.
//
// An engine is one object with one method: given an artifact, an input and a
// seed, produce an output. That is the entire contract, and it is deliberately
// small — wasm is what runs today, but nothing above this file knows that.
// Adding a compute type is adding an entry here and the matching descriptor in
// ../engines.py; nothing in the marketplace, the receipts or the console needs
// to learn a new shape.
//
//     wasm   WebAssembly on the platform's own engine, deterministic host
//     js     JavaScript with the nondeterministic globals shadowed out
//
// Both live here rather than in the venue because "the browser and the server
// agree" is a claim about this file: a run and its replay differ only in which
// process imported it.

import { Exit, Host, EPOCH_MS } from './host.mjs';
import { classify } from './abi.mjs';

/** Everything a guest is allowed to see, and nothing that varies per run. */
function guestGlobals(host) {
  const random = () => { host.effects.random += 1; return host.random(); };
  // A frozen clock. `new Date()` and `Date.now()` are the two doors and both
  // lead to the host's counter, so a guest that timestamps its own output
  // still produces the same bytes on replay.
  class FixedDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(host.now());
      else super(...args);
    }
    static now() { return host.now(); }
  }
  const SafeMath = Object.create(Math);
  SafeMath.random = random;
  return {
    Math: SafeMath,
    Date: FixedDate,
    // The named hostcalls, so a guest can be explicit rather than clever.
    wasmland: {
      seed: host.seed,
      random,
      now: () => host.now(),
      log: (...parts) => host.log(parts.map(String).join(' ')),
    },
    console: {
      log: (...parts) => host.log(parts.map(String).join(' ')),
      error: (...parts) => host.log(parts.map(String).join(' ')),
      warn: (...parts) => host.log(parts.map(String).join(' ')),
      info: (...parts) => host.log(parts.map(String).join(' ')),
      debug: () => {},
    },
  };
}

// Shadowed to `undefined` in the guest's scope. Not a security boundary — the
// server's boundary is the process it runs in — but a determinism one: a guest
// that can't reach the network can't return a different answer tomorrow.
//
// `eval` and `arguments` are absent because a strict-mode function may not
// name a parameter either one; a guest that reaches for eval gets the real
// one, and gets it inside the same shadowed scope, which is the point.
const DENIED = [
  'fetch', 'XMLHttpRequest', 'WebSocket', 'process', 'require', 'module',
  'globalThis', 'global', 'self', 'window', 'importScripts',
  'Function', 'WebAssembly', 'performance', 'crypto', 'localStorage',
  'indexedDB', 'navigator', 'document', 'Worker', 'SharedArrayBuffer',
];

export const ENGINES = {
  /** WebAssembly, on whichever engine the venue already has. */
  async wasm(artifact, { entry = 'run', input = '', host }) {
    const abi = await host.instantiate(artifact);
    const role = classify(Object.keys(host.exports));
    const name = entry || 'run';

    if (abi.has(name)) return { output: abi.callText(name, input), role };
    if (abi.has('_start')) {
      // A command's answer is what it printed. proc_exit is how it says done.
      try {
        host.exports._start();
      } catch (e) {
        if (!(e instanceof Exit)) throw e;
      }
      return { output: host.stdout, role };
    }
    const callable = Object.keys(host.exports)
      .filter((k) => typeof host.exports[k] === 'function');
    throw new Error(
      `module exports no \`${name}\` and no \`_start\` — it exports: ` +
      `${callable.join(', ') || 'nothing callable'}` +
      (role === 'game'
        ? '. This is a game: play it in the arena, or name one of its exports as the entry.'
        : ''));
  },

  /** JavaScript source that defines `run(input, ctx)`. */
  async js(artifact, { entry = 'run', input = '', host }) {
    const source = typeof artifact === 'string'
      ? artifact : new TextDecoder().decode(artifact);
    const globals = guestGlobals(host);
    const names = [...Object.keys(globals), ...DENIED];
    const values = [...Object.values(globals), ...DENIED.map(() => undefined)];
    const name = entry || 'run';

    // eslint-disable-next-line no-new-func
    const build = new Function(...names, `"use strict";\n${source}\n;return typeof ${name} === "function" ? ${name} : null;`);
    const fn = build(...values);
    if (typeof fn !== 'function') {
      throw new Error(`source defines no \`${name}(input, ctx)\` — that is the entry point a js artifact needs`);
    }
    const out = await fn(input, globals.wasmland);
    return {
      output: typeof out === 'string' ? out : JSON.stringify(out ?? null),
      role: 'function',
    };
  },
};

/**
 * Run one job. The only entry point the venues use.
 *
 * Returns the same shape whoever calls it, because the shape is what gets
 * hashed into a receipt: differ here and a browser run could never match its
 * own replay.
 */
export async function execute(job) {
  const { engine = 'wasm', artifact, entry, input = '', seed = 0, limits = {} } = job;
  const impl = ENGINES[engine];
  if (!impl) {
    throw new Error(`unknown engine "${engine}" — this runtime carries: ${Object.keys(ENGINES).join(', ')}`);
  }
  const host = new Host({ seed, input, limits });
  const started = Date.now();
  const { output, role } = await impl(artifact, { entry, input, host });
  return {
    ok: true,
    engine,
    entry: entry || 'run',
    role,
    output: String(output ?? ''),
    logs: host.logs,
    stdout: host.stdout,
    stderr: host.stderr,
    exit_code: host.exitCode,
    effects: host.effects,
    seed,
    // Wall-clock is reported, never hashed — it is the one number that is
    // allowed to differ between a run and its replay.
    ms: Date.now() - started,
    epoch_ms: EPOCH_MS,
  };
}
