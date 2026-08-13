// Turn a verification key into the Solidity verifier a chain would run, and
// compile it.
//
// snarkjs ships the templates it uses for `zkey export solidityverifier`, and
// those templates read exactly the fields that are already in a
// verification_key.json — so the contract can be produced from the vkey alone,
// without the proving key, which is the only artifact a verifier ever has.
//
// stdin:  {"system":"groth16","vkey":{…},"proof":{…},"publicSignals":[…]}
// stdout: {"bytecode":"0x…","abi":[…],"args":[…],"contract":"Groth16Verifier"}
//
// The bytecode is the *deployed* (runtime) bytecode, because it is going to be
// injected into an eth_call state override rather than deployed. `args` is
// whatever snarkjs itself would have passed to verifyProof, parsed back into
// arrays — encoding it is the caller's job.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import * as snarkjs from 'snarkjs';
import ejs from 'ejs';

const require = createRequire(import.meta.url);
const solc = require('solc');
// snarkjs's package.json is not an exported subpath, so the templates are
// found from the module root this script lives under rather than by resolving
// into the package.
const TEMPLATES = new URL('../../node_modules/snarkjs/templates/', import.meta.url);

// bn128's scalar field. The fflonk template wants powers of the roots of
// unity that the verification key only stores the generators of, and snarkjs
// derives them at export time rather than storing them — so they are derived
// here too, with the same arithmetic, or the template renders undefined and
// solc gets a file full of holes.
const FR = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;
const mul = (a, b) => ((BigInt(a) * BigInt(b)) % FR).toString();

const prepare = (system, vkey) => {
  if (system !== 'fflonk') return vkey;
  const vk = { ...vkey };
  vk.w3_2 = mul(vk.w3, vk.w3);
  vk.w4_2 = mul(vk.w4, vk.w4);
  vk.w4_3 = mul(vk.w4_2, vk.w4);
  let acc = 1n;
  for (let i = 1; i < 8; i += 1) {
    acc = (acc * BigInt(vk.w8)) % FR;
    vk[`w8_${i}`] = acc.toString();
  }
  return vk;
};

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
  const { system, vkey, proof, publicSignals } = JSON.parse(await read(process.stdin));
  if (!['groth16', 'plonk', 'fflonk'].includes(system)) {
    done({ error: `no solidity verifier for ${system}` }, 3);
  }

  const template = readFileSync(new URL(`verifier_${system}.sol.ejs`, TEMPLATES), 'utf8');
  const source = ejs.render(template, prepare(system, vkey));

  const compiled = JSON.parse(solc.compile(JSON.stringify({
    language: 'Solidity',
    sources: { 'verifier.sol': { content: source } },
    settings: {
      optimizer: { enabled: true, runs: 1 },
      outputSelection: { '*': { '*': ['abi', 'evm.deployedBytecode.object'] } },
    },
  })));
  const fatal = (compiled.errors || []).filter((e) => e.severity === 'error');
  if (fatal.length) done({ error: fatal.map((e) => e.formattedMessage).join('\n') }, 5);

  const [name, contract] = Object.entries(compiled.contracts['verifier.sol'])[0];

  // Let snarkjs produce the arguments: it is the implementation that decides
  // the order of a plonk proof's 24 words, and copying that order by hand is
  // how an "independent" verifier quietly becomes a wrong one.
  let args = null;
  if (proof) {
    const signals = (publicSignals || []).map(String);
    // fflonk takes its two arguments the other way round to groth16 and plonk.
    const raw = system === 'fflonk'
      ? await snarkjs.fflonk.exportSolidityCallData(signals, proof)
      : await snarkjs[system].exportSolidityCallData(proof, signals);
    // Three functions, three dialects of almost-JSON: groth16 separates its
    // arguments with a comma and plonk does not, fflonk quotes nothing at all.
    // Normalise rather than special-case — every value is a 0x word.
    const normalised = raw
      .replace(/\]\s*\[/g, '],[')
      .replace(/"/g, '')
      .replace(/0x[0-9a-fA-F]+/g, '"$&"');
    args = JSON.parse(`[${normalised}]`);
  }

  done({
    contract: name,
    bytecode: '0x' + contract.evm.deployedBytecode.object,
    abi: contract.abi,
    args,
    solc: solc.version(),
    source_bytes: source.length,
  });
} catch (e) {
  done({ error: String(e?.stack || e?.message || e) }, 4);
}
