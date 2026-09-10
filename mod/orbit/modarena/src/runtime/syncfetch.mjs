#!/usr/bin/env node
// One HTTP call, in its own process, so somebody else can wait for it.
//
// `mcpsync.mjs` spawns this and blocks on it. That is the trick that lets a
// wasm module — which cannot yield, cannot await, and must return a value from
// the call it is inside — reach an MCP server at all. Everything asynchronous
// happens over here, where waiting is free.
//
// Reads `{url, body, timeoutMs}` as JSON on stdin. Writes the reply as JSON on
// stdout, always: an unreachable server is `{"error": "..."}`, never a crash
// and never an empty pipe. The class on the other end has to be able to lose
// this call and carry on.

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);

const out = (value) => {
  process.stdout.write(typeof value === "string" ? value : JSON.stringify(value));
  process.exit(0);
};

let request;
try {
  request = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
} catch (e) {
  out({ error: `syncfetch got no readable request: ${e.message}` });
}

const { url, body, timeoutMs = 30_000 } = request;
if (!url) out({ error: "syncfetch needs a url" });

const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), Number(timeoutMs) || 30_000);

try {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal: controller.signal,
  });
  const text = await r.text();
  clearTimeout(timer);
  // Handed through as it came: the arena already answers JSON either way, and
  // re-encoding it here would only be a second place to get it wrong.
  out(text || JSON.stringify({ error: `${url} answered ${r.status} with no body` }));
} catch (e) {
  clearTimeout(timer);
  out({ error: e.name === "AbortError" ? `the MCP call ran past ${timeoutMs}ms` : e.message });
}
