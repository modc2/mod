// The witness for each fixture circuit. The threshold circuit needs a real
// Poseidon hash of (value, salt) or the commitment constraint won't hold, so
// the input file is generated rather than checked in with a guessed number.
import { buildPoseidon } from 'circomlibjs';

const which = process.argv[2];
if (which === 'multiplier') {
  console.log(JSON.stringify({ a: '3', b: '11' }));
} else {
  const poseidon = await buildPoseidon();
  const value = 4200n, salt = 987654321n;
  const commitment = poseidon.F.toObject(poseidon([value, salt]));
  console.log(JSON.stringify({
    value: value.toString(), salt: salt.toString(),
    commitment: commitment.toString(), threshold: '1000',
  }));
}
