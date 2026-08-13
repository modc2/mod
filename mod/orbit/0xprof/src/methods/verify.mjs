// The snarkjs method, as a pipe.
//
// stdin:  {"system":"groth16","proof":{…},"vkey":{…},"publicSignals":[…]}
// stdout: {"ok":true,"detail":{…}}   — one line, always JSON, even on failure
//
// A subprocess rather than a long-lived service on purpose: verification is
// pure, the answer depends on nothing but the bytes on stdin, and a process
// that exits cannot carry state from one caller's proof into the next one's.
import { groth16, plonk, fflonk } from 'snarkjs';

const VERIFIERS = { groth16, plonk, fflonk };

const read = async (stream) => {
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
};

const done = (payload, code = 0) => {
  process.stdout.write(JSON.stringify(payload));
  process.exit(code);
};

try {
  const { system, proof, vkey, publicSignals } = JSON.parse(await read(process.stdin));
  const verifier = VERIFIERS[system];
  if (!verifier) done({ ok: false, error: `snarkjs does not verify ${system}` }, 3);

  // snarkjs takes public signals as strings and is unforgiving about numbers
  // that have been through a float — pin them to strings on the way in.
  const signals = (publicSignals || []).map((s) => String(s));
  const started = Date.now();
  const ok = await verifier.verify(vkey, signals, proof);
  done({
    ok: !!ok,
    detail: {
      implementation: 'snarkjs',
      protocol: vkey?.protocol || proof?.protocol || system,
      curve: vkey?.curve || proof?.curve || 'bn128',
      public_signals: signals.length,
      ms: Date.now() - started,
    },
  });
} catch (e) {
  // A malformed proof and a false proof are different answers. snarkjs throws
  // for the first and returns false for the second, and that distinction is
  // kept all the way up to the caller.
  done({ ok: false, error: String(e?.message || e), malformed: true }, 4);
}
