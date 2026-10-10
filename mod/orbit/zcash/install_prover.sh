#!/usr/bin/env bash
#
# Build the shielded proving backend, once.
#
# A Zcash shielded spend carries a zk-SNARK proof, which no amount of Python
# will produce. This builds `zcash-devtool` -- the Electric Coin Company's
# reference light client over zcash_client_backend / zcash_client_sqlite /
# zcash_proofs -- and drops the binary in ~/.mod/zcash/bin/, where
# zcash/lightclient.py looks for it.
#
# It is a light client, not a node: it syncs compact blocks from a
# lightwalletd server, so this costs a few hundred MB of build, not 100 GB of
# chain. The Sapling proving parameters are compiled into the binary
# (`bundled-prover`), so there is nothing else to fetch.
#
#   bash install_prover.sh            # build and install
#   ZCASH_PROVER_FORCE=1 bash ...     # rebuild even if it is already there
#
set -euo pipefail

PREFIX="${ZCASH_PROVER_PREFIX:-$HOME/.mod/zcash/bin}"
SRC="${ZCASH_PROVER_SRC:-$HOME/.mod/zcash/src/zcash-devtool}"
REPO="${ZCASH_PROVER_REPO:-https://github.com/zcash/zcash-devtool.git}"
BIN="$PREFIX/zcash-devtool"

if [ -x "$BIN" ] && [ -z "${ZCASH_PROVER_FORCE:-}" ]; then
  echo "already installed: $BIN"
  "$BIN" --version 2>/dev/null || true
  exit 0
fi

command -v cargo >/dev/null 2>&1 || {
  echo "cargo is required. Install Rust first: https://rustup.rs" >&2
  exit 1
}

mkdir -p "$PREFIX" "$(dirname "$SRC")"

if [ -d "$SRC/.git" ]; then
  echo "updating $SRC"
  git -C "$SRC" fetch --depth 1 origin && git -C "$SRC" reset --hard FETCH_HEAD
else
  echo "cloning $REPO"
  git clone --depth 1 "$REPO" "$SRC"
fi

echo "building (this takes a while the first time -- it is a whole Zcash light client)"
cd "$SRC"
cargo build --release

install -m 0755 "$SRC/target/release/zcash-devtool" "$BIN"
echo "installed: $BIN"
"$BIN" --version 2>/dev/null || true
