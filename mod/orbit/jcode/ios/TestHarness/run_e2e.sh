#!/usr/bin/env bash
#
# End-to-end iOS harness: builds the app, runs the deterministic mock gateway,
# boots a simulator, seeds a paired-server credential, launches the app, and
# captures a screenshot proving the live connection + transcript render.
#
# This gives agents a verifiable, repeatable target to develop the client
# against without an LLM, network, or manual device steps.
#
# Usage:
#   ./TestHarness/run_e2e.sh [--device "iPhone 17"] [--push-demo]
#
set -euo pipefail

cd "$(dirname "$0")/.."        # ios/
HARNESS="TestHarness"
DEVICE="iPhone 17"
PUSH_DEMO=""
BUNDLE_ID="com.jcode.mobile"
PORT=7643
SHOT_DIR="${TMPDIR:-/tmp}/jcode-ios-e2e"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) DEVICE="$2"; shift 2 ;;
    --push-demo) PUSH_DEMO="--push-demo"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$SHOT_DIR"

log() { printf '\033[36m[e2e]\033[0m %s\n' "$*"; }

cleanup() {
  [[ -n "${GW_PID:-}" ]] && kill "$GW_PID" 2>/dev/null || true
}
trap cleanup EXIT

# 1. Swift unit tests (headless behavior layer).
log "swift test"
swift test 2>&1 | tail -1

# 2. Build the app for the simulator.
log "xcodegen + xcodebuild ($DEVICE)"
xcodegen generate >/dev/null
xcodebuild build \
  -project JCodeMobile.xcodeproj \
  -scheme JCodeMobile \
  -destination "platform=iOS Simulator,name=$DEVICE" \
  -derivedDataPath .build-ios >/dev/null
APP=".build-ios/Build/Products/Debug-iphonesimulator/JCodeMobile.app"

# 3. Start the deterministic mock gateway.
log "starting mock gateway on :$PORT $PUSH_DEMO"
pkill -f mock_gateway.py 2>/dev/null || true
sleep 0.5
python3 "$HARNESS/mock_gateway.py" --port "$PORT" --host 127.0.0.1 $PUSH_DEMO \
  >"$SHOT_DIR/mockgw.log" 2>&1 &
GW_PID=$!
sleep 1.5

# 4. Protocol smoke test (asserts full message/tool/markdown sequence).
log "protocol smoke test"
python3 "$HARNESS/protocol_smoke_test.py" --port "$PORT" | tail -1

# 5. Boot the simulator (idempotent).
log "booting simulator: $DEVICE"
xcrun simctl boot "$DEVICE" 2>/dev/null || true
sleep 3

# 6. Install fresh + seed a paired-server credential so the app auto-connects.
log "installing app + seeding credential"
xcrun simctl uninstall "$DEVICE" "$BUNDLE_ID" 2>/dev/null || true
xcrun simctl install "$DEVICE" "$APP"
CONTAINER="$(xcrun simctl get_app_container "$DEVICE" "$BUNDLE_ID" data)"
APPSUP="$CONTAINER/Library/Application Support"
mkdir -p "$APPSUP"
printf '%s\n' \
  '[{"host":"127.0.0.1","port":7643,"token":"mocktoken0123456789abcdef","serverName":"mock-jcode","serverVersion":"mock-0.32.0","pairedAt":770000000}]' \
  > "$APPSUP/jcode-servers.json"

# 7. Launch + screenshot.
log "launching app"
xcrun simctl launch "$DEVICE" "$BUNDLE_ID" >/dev/null
sleep 6
SHOT="$SHOT_DIR/chat.png"
xcrun simctl io "$DEVICE" screenshot "$SHOT" >/dev/null 2>&1
log "screenshot: $SHOT"
log "gateway log: $SHOT_DIR/mockgw.log"
log "done"
