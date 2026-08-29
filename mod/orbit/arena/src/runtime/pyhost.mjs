// The class host, from the node side — `host.mjs`'s twin.
//
// `host.mjs` gives the match loop an object it can call `view`, `step`, `done`
// and `result` on. So does this. The difference is only where the code runs:
// there, a WebAssembly instance inside this process; here, a python subprocess
// running `host.py`, spoken to in JSON lines.
//
// That is the whole trick to classes in this arena. `match.mjs` never learns
// which one it got, so a class game and a wasm game are played by the same
// loop, scored by the same rules and rated on the same leaderboard.
//
// The line protocol runs in both directions. The host asks the class to do
// something; the class, if it was given a door, asks the host to make an MCP
// call for it. That inversion is why a Python class can consult a model or
// another arena mid-move without the sandbox ever growing a socket — see
// `_mcp` in host.py and `mcpsync.mjs` for the wasm half of the same idea.
//
// Node only. A browser tab cannot start a python process, so the console runs
// wasm and hands class matches to the runner — see `openGame` in match.mjs.

const DEFAULT_CALL_TIMEOUT_MS = 15_000;
const START_TIMEOUT_MS = 10_000;

/** Every python process this module started, so a runner can leave none behind. */
const live = new Set();

export function closeAll() {
  for (const s of [...live]) s.close();
}

function hostScript() {
  // Resolved off this file, so the runner works from any cwd.
  return new URL("./host.py", import.meta.url).pathname;
}

/**
 * Start a python process and load one class into it.
 *
 * @param {string} source      the class, as text
 * @param {object} opts
 * @param {number} opts.seed   seeds `random` before `__init__` runs
 * @param {number} opts.seats  how many seats the match has, for `turn`
 * @param {function} opts.mcp  an async `({server, tool, arguments}) => object`,
 *                             or nothing at all, which is the default and
 *                             means the class has no way out
 */
export async function openClass(source, opts = {}) {
  const { seed = 1, seats = 0, timeoutMs = DEFAULT_CALL_TIMEOUT_MS, mcp = null } = opts;
  const { spawn } = await import("node:child_process");

  const python = process.env.ARENA_PYTHON || "python3";
  // -I isolates: no PYTHONPATH, no user site-packages, no cwd on sys.path.
  const child = spawn(python, ["-I", "-B", hostScript()], {
    stdio: ["pipe", "pipe", "pipe"],
    env: { PATH: process.env.PATH || "", LC_ALL: "C.UTF-8" },
  });

  const pending = [];       // resolvers waiting for a line
  const inbox = [];         // lines that arrived before anyone waited
  let stderr = "";
  let dead = null;

  const deliver = (line) => {
    if (pending.length) pending.shift().resolve(line);
    else inbox.push(line);
  };
  const fail = (err) => {
    dead ??= err;
    while (pending.length) pending.shift().reject(err);
  };

  let buffered = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    buffered += chunk;
    let at;
    while ((at = buffered.indexOf("\n")) >= 0) {
      const line = buffered.slice(0, at).trim();
      buffered = buffered.slice(at + 1);
      if (line) deliver(line);
    }
  });
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr = (stderr + chunk).slice(-4000);
  });
  child.on("error", (e) => fail(new Error(`could not start ${python}: ${e.message}`)));
  // Writing to a class we just killed for running too long is EPIPE, and an
  // unhandled `error` on a pipe takes the whole runner down with it. The
  // process is already gone; that is the answer, not a crash.
  child.stdin.on("error", (e) => fail(new Error(`the class stopped reading: ${e.code || e.message}`)));
  child.on("exit", (code, signal) => {
    live.delete(session);
    const why = signal === "SIGKILL" && killedForTime
      ? "the class was killed for running too long"
      : `the class host exited (${signal || `code ${code}`})`;
    fail(new Error(stderr.trim() ? `${why}: ${stderr.trim().split("\n").pop()}` : why));
  });

  let killedForTime = false;

  const nextLine = (ms) =>
    new Promise((resolve, reject) => {
      if (inbox.length) return resolve(inbox.shift());
      if (dead) return reject(dead);
      const entry = { resolve, reject };
      pending.push(entry);
      if (!ms) return;
      setTimeout(() => {
        if (!pending.includes(entry)) return;
        pending.splice(pending.indexOf(entry), 1);
        killedForTime = true;
        try { child.kill("SIGKILL"); } catch { /* already gone */ }
        reject(new TimeoutError(ms));
      }, ms).unref?.();
    });

  const NO_DOOR = {
    error: "this match was not given MCP access — nothing was called",
  };

  const send = async (msg, ms) => {
    if (dead) throw dead;
    if (!child.stdin.writable) throw new Error("the class host is not listening");
    child.stdin.write(`${JSON.stringify(msg)}\n`);
    // The class may interrupt its own answer to ask for something. Keep
    // reading until a line arrives that is a reply rather than a request.
    for (;;) {
      const reply = JSON.parse(await nextLine(ms));
      if (reply.mcp) {
        let value;
        try {
          value = mcp ? await mcp(reply.mcp) : NO_DOOR;
        } catch (e) {
          value = { error: e.message || String(e) };
        }
        session.mcp.push({ request: reply.mcp, reply: value });
        if (!child.stdin.writable) throw new Error("the class host stopped listening mid-call");
        child.stdin.write(`${JSON.stringify({ op: "mcp_result", value })}\n`);
        continue;
      }
      if (!reply.ok) {
        const e = new Error(reply.error || "the class failed");
        e.log = reply.log || "";
        throw e;
      }
      return reply;
    }
  };

  const session = {
    child,
    log: [],
    /** Every MCP call this class made, for the transcript. */
    mcp: [],
    async call(method, args = [], ms = timeoutMs) {
      const reply = await send({ op: "call", method, args }, ms);
      if (reply.log) this.log.push(reply.log.trim());
      return reply.value;
    },
    close() {
      live.delete(session);
      // Ask it to go, then make sure. Both are best-effort: by the time a
      // match is over the process may already have been killed for time.
      try {
        if (child.stdin.writable) child.stdin.end(JSON.stringify({ op: "close" }) + "\n");
      } catch { /* already gone */ }
      try { child.kill(); } catch { /* already gone */ }
    },
  };
  live.add(session);

  // A class that refuses to load still left a process running, and node will
  // not exit while its pipes are open. Everything from here on closes it.
  try {
    // The banner the host writes on startup — proof python came up at all.
    const hello = JSON.parse(await nextLine(START_TIMEOUT_MS));
    session.python = hello.python;
    session.limits = hello.limits;

    const loaded = await send({ op: "load", source, seed, seats }, Math.max(timeoutMs, 10_000));
    session.role = loaded.role;
    session.className = loaded.class;
    session.methods = loaded.methods || [];
    session.info = loaded.info || null;
    return session;
  } catch (e) {
    session.close();
    throw e;
  }
}

export class TimeoutError extends Error {
  constructor(ms) {
    super(`the class took longer than ${ms}ms`);
    this.timeout = true;
  }
}

/**
 * A class game, wearing the same face as `abi.mjs`'s `Game`.
 *
 * The state lives in the python object rather than in a string the host holds,
 * which is the one real difference from the wasm ABI — a class is written the
 * way Python is written, with `self`. Replay still works, because the process
 * starts from the seed and is fed the recorded moves in order.
 */
export class PyGame {
  constructor(session) {
    if (session.role !== "game") {
      throw new Error(
        `class ${session.className} is not a game — it defines ` +
        `${session.methods.join(", ") || "nothing"}, and a game needs view, step, done, result`,
      );
    }
    this.session = session;
  }

  info() {
    const raw = this.session.info ?? {};
    return {
      name: raw.name ?? "",
      description: raw.description ?? "",
      min_players: raw.min_players ?? 2,
      max_players: raw.max_players ?? 2,
      max_turns: raw.max_turns ?? 200,
      ...raw,
    };
  }

  /** Already done: the constructor ran with the seed when the class loaded. */
  init() {
    return "";
  }

  turn(seats, turnNo) {
    return this.session.call("turn", [seats, turnNo]);
  }

  view(seat) {
    return this.session.call("view", [seat]);
  }

  async step(moves) {
    const r = await this.session.call("step", [moves]);
    return { legal: r?.legal ?? {}, note: r?.note ?? "" };
  }

  done() {
    return this.session.call("done", []);
  }

  result() {
    return this.session.call("result", []);
  }

  close() {
    this.session.close();
  }
}

/** A class player, as the match loop's driver shape. */
export function pyDriver(session, { name = "class", timeout = DEFAULT_CALL_TIMEOUT_MS } = {}) {
  if (session.role !== "player") {
    throw new Error(
      `class ${session.className} is not a player — it defines ` +
      `${session.methods.join(", ") || "nothing"}, and a player needs \`play(self, view, seat)\``,
    );
  }
  return {
    label: `class ${session.className}`,
    timeout,
    session,
    async move(view, seat) {
      const move = await session.call("play", [view, seat], timeout);
      // Whatever the class printed goes into the transcript, like arena.log.
      const note = session.log.splice(0).join(" ");
      // And whatever it asked the outside world goes on the record, so the
      // seat that consulted something is distinguishable from the one that
      // worked it out.
      return { move: move ?? "", note, mcp: session.mcp.splice(0) };
    },
    close: () => session.close(),
  };
}

/** Run a class once, with no match around it — the `run` path for a class. */
export async function runClass(source, opts = {}) {
  const t0 = Date.now();
  let session;
  try {
    session = await openClass(source, opts);
  } catch (e) {
    return { ok: false, error: e.message, ms: Date.now() - t0 };
  }
  const { method = "", args = [] } = opts;
  const entry = method || (session.methods.includes("play") ? "play" : "");
  try {
    if (!entry) {
      return {
        ok: true, ms: Date.now() - t0, class: session.className, role: session.role,
        methods: session.methods, python: session.python,
        note: "no method named — pass `entry` to call one",
      };
    }
    const value = await session.call(entry, args);
    return {
      ok: true, entry, value, class: session.className, role: session.role,
      methods: session.methods, python: session.python,
      log: session.log, stdout: session.log.join("\n"), ms: Date.now() - t0,
    };
  } catch (e) {
    return { ok: false, error: e.message, entry, ms: Date.now() - t0,
             log: e.log ? [e.log] : session.log };
  } finally {
    session.close();
  }
}
