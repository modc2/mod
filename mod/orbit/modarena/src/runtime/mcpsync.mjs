// Calling an MCP server from inside a wasm module, synchronously.
//
// A wasm call cannot be paused. When a class does `arena::mcp(...)` the engine
// is inside a function that has to return a value, and there is no `await` to
// be had — so the host has to block until the answer is there. Both places
// this runs have a way to do that, and neither of them is elegant:
//
//   node      spawn a child process that does the fetch and prints the reply,
//             and wait for it. `spawnSync` blocks the event loop, which is
//             exactly what is wanted here and would be a bug anywhere else.
//   a Worker  synchronous XMLHttpRequest. Deprecated on the main thread for
//             good reasons, still supported in a worker, and matches run in a
//             worker precisely so that blocking one is harmless.
//
// Neither ever hands the module a URL. It sends `{server, tool, arguments}` to
// the arena's own /mcp/call, and the arena decides what that server is, where
// it lives, and whether the class is allowed to reach it. The module could
// send anything it liked down this pipe and the worst it could name is a
// server somebody already put on the list.
//
// A Python class does not need any of this: `pyhost.mjs` speaks a line
// protocol to a process that is happy to wait, so its bridge is ordinary async
// code. This file exists only because wasm cannot yield.

const TIMEOUT_MS = 30_000;

/** The reply that means "there was no door", in the shape a class expects. */
function closed(why) {
  return JSON.stringify({ error: why });
}

export const NO_MCP = closed(
  "this match was not given MCP access — run it with mcp enabled, or call " +
  "`m modarena/mcp_servers` to see what there is to be given",
);

/**
 * A synchronous MCP caller for node.
 *
 * @param {string} base   the arena, e.g. http://127.0.0.1:50800
 * @param {object} opts
 * @param {string[]} opts.allow  server names this match may call; empty is all
 * @returns {(request: string) => string}
 */
export function nodeMcp(base, { allow = [], timeoutMs = TIMEOUT_MS } = {}) {
  const url = `${String(base).replace(/\/$/, "")}/mcp/call`;
  // Resolved once, off this file, so it works from any cwd.
  const script = new URL("./syncfetch.mjs", import.meta.url).pathname;

  return (request) => {
    let parsed;
    try {
      parsed = JSON.parse(request);
    } catch (e) {
      return closed(`the class sent something that is not JSON: ${e.message}`);
    }
    if (allow.length && !allow.includes(String(parsed.server ?? ""))) {
      return closed(
        `this match may call ${allow.join(", ") || "nothing"} — not \`${parsed.server}\``,
      );
    }
    try {
      // Imported here rather than at the top so a browser bundle of this file
      // never reaches for node's child_process.
      const { spawnSync } = globalThis.process?.getBuiltinModule?.("node:child_process") ?? {};
      if (!spawnSync) return closed("no way to make a synchronous call in this runtime");
      const out = spawnSync(globalThis.process.execPath, [script], {
        input: JSON.stringify({ url, body: parsed, timeoutMs }),
        encoding: "utf8",
        timeout: timeoutMs + 2000,
        maxBuffer: 8 << 20,
      });
      if (out.error) return closed(`the MCP call could not be made: ${out.error.message}`);
      const text = (out.stdout || "").trim();
      if (!text) {
        return closed(`the MCP call returned nothing${out.stderr ? `: ${out.stderr.trim()}` : ""}`);
      }
      return text;
    } catch (e) {
      return closed(e.message || String(e));
    }
  };
}

/**
 * The same, in a browser worker. Sync XHR is the only synchronous fetch the
 * platform has, and a worker is the only place it is still allowed.
 */
export function workerMcp(base, { allow = [], timeoutMs = TIMEOUT_MS } = {}) {
  const url = `${String(base).replace(/\/$/, "")}/mcp/call`;
  return (request) => {
    let parsed;
    try {
      parsed = JSON.parse(request);
    } catch (e) {
      return closed(`the class sent something that is not JSON: ${e.message}`);
    }
    if (allow.length && !allow.includes(String(parsed.server ?? ""))) {
      return closed(`this match may call ${allow.join(", ") || "nothing"} — not \`${parsed.server}\``);
    }
    if (typeof XMLHttpRequest === "undefined") {
      return closed(
        "a class asked for MCP from a context with no synchronous fetch — matches that " +
        "call out have to run in a worker or in the node runner",
      );
    }
    try {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url, false);          // false: the whole point
      xhr.setRequestHeader("content-type", "application/json");
      xhr.timeout = timeoutMs;
      xhr.send(JSON.stringify(parsed));
      return xhr.responseText || closed(`the arena answered ${xhr.status} with no body`);
    } catch (e) {
      return closed(e.message || String(e));
    }
  };
}

/**
 * The same door, for a caller that is allowed to await.
 *
 * A Python class gets this one: `pyhost.mjs` talks to a process that is
 * perfectly happy to sit and wait, so nothing has to be blocked and the whole
 * spawn-a-child dance above is unnecessary.
 *
 * @returns {(request: object|string) => Promise<object>}
 */
export function asyncMcp(base, { allow = [], timeoutMs = TIMEOUT_MS } = {}) {
  const url = `${String(base).replace(/\/$/, "")}/mcp/call`;
  return async (request) => {
    const body = typeof request === "string" ? JSON.parse(request) : request;
    if (allow.length && !allow.includes(String(body.server ?? ""))) {
      return { error: `this match may call ${allow.join(", ") || "nothing"} — not \`${body.server}\`` };
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await r.text();
      try {
        return JSON.parse(text);
      } catch {
        return { error: `the arena answered ${r.status}: ${text.slice(0, 300)}` };
      }
    } catch (e) {
      return { error: e.name === "AbortError" ? `the MCP call ran past ${timeoutMs}ms` : e.message };
    } finally {
      clearTimeout(timer);
    }
  };
}

/** Whichever of the two this runtime can do. */
export function mcpFor(base, opts = {}) {
  const isNode = typeof process !== "undefined" && !!process.versions?.node;
  return isNode ? nodeMcp(base, opts) : workerMcp(base, opts);
}
