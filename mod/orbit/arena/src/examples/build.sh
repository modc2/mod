#!/usr/bin/env bash
# Compile the example pack to wasm/ and write the sidecar metadata beside it.
#
# Each example is one .rs file compiled straight by rustc — no Cargo.toml, no
# workspace, no bindgen. That is not minimalism for its own sake: a game here
# should be something you can write in one file and drop on the console, and
# the pack has to be built the same way anyone else would build one.
#
#     ./build.sh          all of them
#     ./build.sh ttt      just one
#
# Needs: rustup target add wasm32-unknown-unknown wasm32-wasip1

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p wasm

# name|target|description|tags
EXAMPLES=(
  "rps|unknown|Rock, paper, scissors — best of five, both seats throwing at once. The simplest game with simultaneous moves.|example,game"
  "ttt|unknown|Tic-tac-toe. Solved, so a perfect opponent exists and a loss is a mistake rather than bad luck.|example,game"
  "nim|unknown|Nim with twenty-one stones, take one to three. A game whose move is a number and whose rule is arithmetic.|example,game"
  "bot_random|unknown|Reads the Legal moves: line and picks one at random. The floor every rating is measured against.|example,player,baseline"
  "bot_ttt|unknown|Perfect tic-tac-toe by full minimax. The reference opponent — it never loses.|example,player,baseline"
  "mlp|unknown|A 2-2-1 neural network computing XOR, weights and all, nine parameters. A model is just another module here.|example,model"
  "markov|unknown|An order-2 character Markov chain over a baked corpus. Seed in, text out.|example,model"
  "hello|wasip1|An ordinary WASI command — argv, stdin, stdout, no arena ABI at all. Proof that anything wasm runs.|example,command,wasi"
)

only="${1:-}"
built=0

for row in "${EXAMPLES[@]}"; do
  IFS='|' read -r name target description tags <<< "$row"
  [ -n "$only" ] && [ "$only" != "$name" ] && continue

  triple="wasm32-unknown-unknown"
  crate_args=(--crate-type cdylib)
  if [ "$target" = "wasip1" ]; then
    triple="wasm32-wasip1"
    crate_args=()   # a WASI command is a binary with a main, not a cdylib
  fi

  echo "  $name → wasm/$name.wasm ($triple)"
  rustc --target "$triple" "${crate_args[@]}" \
    -C opt-level=s -C lto=yes -C panic=abort -C strip=symbols \
    --crate-name "$name" \
    -o "wasm/$name.wasm" "$name.rs"

  # The sidecar the server reads when it plants the pack.
  python3 - "$name" "$description" "$tags" <<'PY' > "wasm/$name.json"
import json, sys
name, description, tags = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "name": name.replace("_", "-"),
    "description": description,
    "author": "arena",
    "tags": tags.split(","),
}, indent=2))
PY
  built=$((built + 1))
done

echo "built $built module(s):"
ls -la wasm/*.wasm | awk '{ printf "  %-28s %8d bytes\n", $9, $5 }'
