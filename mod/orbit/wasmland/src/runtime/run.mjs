// The server venue: one job in on stdin, one result out on stdout.
//
// Deliberately a short-lived child process rather than a worker inside the
// API. The isolation that matters here is the operating system's — an empty
// network namespace, address-space and CPU rlimits, a wall-clock kill — and
// none of that is available to a thread. src/sandbox.py is what starts this,
// and what enforces the limits this file cannot enforce on itself.
//
//     echo '{"engine":"js","artifact":"function run(x){return x}","input":"hi"}' \
//       | node run.mjs
//
// stdout carries exactly one JSON object and nothing else, which is why the
// guest's own printing is captured into the result rather than let through.

import { execute } from './engines.mjs';

function read(stream) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    stream.on('data', (c) => chunks.push(c));
    stream.on('end', () => resolve(Buffer.concat(chunks)));
    stream.on('error', reject);
  });
}

const raw = await read(process.stdin);
let job;
try {
  job = JSON.parse(raw.toString('utf8') || '{}');
} catch (e) {
  process.stdout.write(JSON.stringify({ ok: false, error: `job is not JSON: ${e.message}` }));
  process.exit(1);
}

// Artifacts arrive base64 because the job is JSON. A js artifact may also
// arrive as plain source, which is the same thing minus a round trip.
const artifact = job.artifact_b64
  ? new Uint8Array(Buffer.from(job.artifact_b64, 'base64'))
  : job.artifact;

try {
  const result = await execute({ ...job, artifact });
  process.stdout.write(JSON.stringify({ ...result, venue: 'server' }));
} catch (e) {
  process.stdout.write(JSON.stringify({
    ok: false,
    venue: 'server',
    engine: job.engine || 'wasm',
    error: `${e.constructor?.name || 'Error'}: ${e.message}`,
  }));
  process.exit(1);
}
