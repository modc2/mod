#!/usr/bin/env bash
# Build the fixtures every verifier in this module is measured against.
#
# Nothing here is mocked: circom compiles the circuits, snarkjs runs a real
# (single-contributor, therefore NOT production-safe) trusted setup, and the
# proofs that come out are proofs. They exist so that `native`, `snarkjs`,
# `evm` and the browser can each be handed the same bytes and asked the same
# question — and so a regression shows up as a disagreement, which is the only
# failure mode that matters for a verifier.
#
#   ./fixtures/build.sh          rebuilds everything (a few minutes)
set -euo pipefail
cd "$(dirname "$0")/.."
SNARKJS="node_modules/.bin/snarkjs"
F=fixtures
mkdir -p $F

# One power-of-tau ceremony, shared by both circuits and all three protocols.
# 2^15 rather than the 2^12 groth16 alone would need: fflonk blows the domain up
# by ~9x per constraint, and a ptau that is one bit too small fails at setup.
POT=${POT:-15}
if [ ! -f $F/pot_final.ptau ]; then
  $SNARKJS powersoftau new bn128 $POT $F/pot_0.ptau -v
  $SNARKJS powersoftau contribute $F/pot_0.ptau $F/pot_1.ptau --name="0xprof fixture" -v -e="$(head -c 64 /dev/urandom | base64)"
  $SNARKJS powersoftau prepare phase2 $F/pot_1.ptau $F/pot_final.ptau -v
  rm -f $F/pot_0.ptau $F/pot_1.ptau
fi

for C in multiplier threshold; do
  circom circuits/$C.circom --r1cs --wasm -o $F

  # groth16 — per-circuit phase 2, then a proof
  $SNARKJS groth16 setup $F/$C.r1cs $F/pot_final.ptau $F/${C}_g16_0.zkey
  $SNARKJS zkey contribute $F/${C}_g16_0.zkey $F/${C}_g16.zkey --name="0xprof" -e="$(head -c 64 /dev/urandom | base64)"
  rm -f $F/${C}_g16_0.zkey
  $SNARKJS zkey export verificationkey $F/${C}_g16.zkey $F/${C}_g16_vkey.json

  # plonk and fflonk — universal setup, no per-circuit ceremony
  $SNARKJS plonk setup $F/$C.r1cs $F/pot_final.ptau $F/${C}_plonk.zkey
  $SNARKJS zkey export verificationkey $F/${C}_plonk.zkey $F/${C}_plonk_vkey.json
  $SNARKJS fflonk setup $F/$C.r1cs $F/pot_final.ptau $F/${C}_fflonk.zkey
  $SNARKJS zkey export verificationkey $F/${C}_fflonk.zkey $F/${C}_fflonk_vkey.json

  node $F/input.mjs $C > $F/${C}_input.json
  for P in g16:groth16 plonk:plonk fflonk:fflonk; do
    tag=${P%%:*}; proto=${P##*:}
    $SNARKJS $proto fullprove $F/${C}_input.json $F/${C}_js/$C.wasm $F/${C}_${tag}.zkey \
      $F/${C}_${tag}_proof.json $F/${C}_${tag}_public.json
    $SNARKJS $proto verify $F/${C}_${tag}_vkey.json $F/${C}_${tag}_public.json $F/${C}_${tag}_proof.json
  done
done
echo "fixtures rebuilt"
