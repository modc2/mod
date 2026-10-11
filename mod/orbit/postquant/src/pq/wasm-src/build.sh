#!/bin/sh
# Build every wasm verifier and regenerate the manifest their hashes live in.
# Bare rustc, no cargo, no network: the sources are single-crate files that
# share keccak.rs and abi.rs by #[path]. Run from this directory.
#
#   ./build.sh          build + manifest
#
# The manifest hash is what pq/wasmvm.py and the node host both enforce, so
# a rebuilt blob that does not match the manifest simply will not verify —
# regeneration here is what "amending an algorithm" means for a builtin.
set -e
cd "$(dirname "$0")"

FLAGS="--edition 2021 --target wasm32-unknown-unknown --crate-type cdylib
       -C opt-level=3 -C panic=abort -C lto"

rustc $FLAGS --cfg mldsa_44 -o ../wasm/mldsa44.wasm mldsa.rs
rustc $FLAGS --cfg mldsa_65 -o ../wasm/mldsa65.wasm mldsa.rs
rustc $FLAGS --cfg mldsa_87 -o ../wasm/mldsa87.wasm mldsa.rs
rustc $FLAGS -o ../wasm/slhdsa128f.wasm slhdsa128f.rs

python3 - <<'EOF'
import hashlib, json, os
wasm = os.path.join(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.', '..', 'wasm')
wasm = os.path.abspath(os.path.join(os.getcwd(), '..', 'wasm'))
entries = {
    "ML-DSA-44": {"file": "mldsa44.wasm", "source": "wasm-src/mldsa.rs"},
    "ML-DSA-65": {"file": "mldsa65.wasm", "source": "wasm-src/mldsa.rs"},
    "ML-DSA-87": {"file": "mldsa87.wasm", "source": "wasm-src/mldsa.rs"},
    "SLH-DSA-SHAKE-128f": {"file": "slhdsa128f.wasm",
                           "source": "wasm-src/slhdsa128f.rs"},
}
for name, e in entries.items():
    with open(os.path.join(wasm, e["file"]), "rb") as f:
        blob = f.read()
    e["sha3_256"] = hashlib.sha3_256(blob).hexdigest()
    e["bytes"] = len(blob)
    e["abi"] = "pq1"
manifest = {
    "what": ("every builtin key type's wasm verifier, by SHA3-256 — "
             "pq/wasmvm.py refuses to run a blob whose hash is not here"),
    "abi": {"pq1": "exports pq_reset(), pq_alloc(n)->ptr, pq_verify(pk,pk_len,"
                   "msg,msg_len,sig,sig_len,ctx,ctx_len)->i32; the FIPS m' "
                   "context wrapping happens inside the module"},
    "algorithms": entries,
}
path = os.path.join(wasm, "manifest.json")
with open(path, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
print("wrote", path)
for name, e in entries.items():
    print(f"  {name}: {e['file']} {e['bytes']}b sha3:{e['sha3_256'][:16]}…")
EOF
