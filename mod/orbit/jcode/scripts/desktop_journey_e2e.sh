#!/usr/bin/env bash
set -euo pipefail

# Real-window E2E user-journey test for jcode-desktop under niri.
#
# Launches the desktop app in a real compositor window, replays scripted
# "user journeys" with wtype (typing, overlays, scrolling, resizing), and
# verifies after every step that:
#   - the app process is still alive
#   - the compositor window still exists
# At the end it summarizes no-paint gaps from the persistent performance log
# and fails if any gap during the journey exceeded the budget.
#
# Usage:
#   scripts/desktop_journey_e2e.sh [journey ...]
# Journeys (default: all):
#   typing      type a draft, clear it
#   overlays    open/close hotkey help and model picker
#   scrolling   keyboard scroll up/down/top/bottom
#   resize      shrink and regrow the window via niri
#
# Env:
#   JCODE_DESKTOP_BIN              binary (default target/debug/jcode-desktop)
#   JCODE_JOURNEY_TIMEOUT_SECS     per-wait timeout (default 15)
#   JCODE_JOURNEY_GAP_BUDGET_MS    max acceptable no-paint gap (default 1000)
#   JCODE_JOURNEY_SCREENSHOT_DIR   if set, save a grim screenshot per step
#
# Requirements: niri, jq, wtype, a Wayland session; grim for screenshots.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${JCODE_DESKTOP_BIN:-$ROOT_DIR/target/debug/jcode-desktop}"
TIMEOUT_SECS="${JCODE_JOURNEY_TIMEOUT_SECS:-15}"
GAP_BUDGET_MS="${JCODE_JOURNEY_GAP_BUDGET_MS:-1000}"
SCREENSHOT_DIR="${JCODE_JOURNEY_SCREENSHOT_DIR:-}"
LOG_FILE="$(mktemp -t jcode-desktop-journey.XXXXXX.log)"
PERF_LOG="${XDG_CACHE_HOME:-$HOME/.cache}/jcode/desktop/performance.log"

if [[ ! -x "$BIN" ]]; then
  echo "desktop binary not found: $BIN" >&2
  echo "hint: cargo build -p jcode-desktop --bin jcode-desktop" >&2
  exit 2
fi
for tool in niri jq wtype; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing tool: $tool" >&2; exit 2; }
done
if [[ -n "$SCREENSHOT_DIR" ]]; then
  command -v grim >/dev/null 2>&1 || { echo "missing tool: grim (needed for screenshots)" >&2; exit 2; }
  mkdir -p "$SCREENSHOT_DIR"
fi

JOURNEYS=("$@")
if [[ ${#JOURNEYS[@]} -eq 0 ]]; then
  JOURNEYS=(typing overlays scrolling resize)
fi

APP_PID=""
WINDOW_ID=""
STEP_INDEX=0
FAILURES=()

cleanup() {
  if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
    kill "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

window_json() {
  niri msg -j windows | jq -c --argjson pid "$APP_PID" 'map(select(.pid == $pid)) | first // empty'
}

assert_alive() {
  local step="$1"
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "FAIL [$step]: app process died" >&2
    tail -40 "$LOG_FILE" >&2 || true
    FAILURES+=("$step: process died")
    return 1
  fi
  if [[ -z "$(window_json)" ]]; then
    echo "FAIL [$step]: compositor window vanished" >&2
    FAILURES+=("$step: window vanished")
    return 1
  fi
  return 0
}

step() {
  local name="$1"
  STEP_INDEX=$((STEP_INDEX + 1))
  sleep 0.3
  if ! assert_alive "$name"; then
    return 1
  fi
  if [[ -n "$SCREENSHOT_DIR" ]]; then
    local geom
    geom="$(window_json | jq -r '"\(.layout.tile_pos_in_workspace_view[0]),\(.layout.tile_pos_in_workspace_view[1]) \(.layout.window_size[0])x\(.layout.window_size[1])"' 2>/dev/null || true)"
    grim -g "$geom" "$SCREENSHOT_DIR/$(printf '%02d' "$STEP_INDEX")-$name.png" 2>/dev/null \
      || grim "$SCREENSHOT_DIR/$(printf '%02d' "$STEP_INDEX")-$name.png" 2>/dev/null || true
  fi
  echo "ok   [$STEP_INDEX] $name"
}

journey_typing() {
  wtype 'hello from the journey test'
  step typing-draft || return 1
  # clear the draft (Ctrl+U deletes to line start)
  wtype -M ctrl u -m ctrl
  step typing-clear || return 1
}

journey_overlays() {
  # hotkey help: Ctrl+/ toggles, Escape closes
  wtype -M ctrl '/' -m ctrl
  step overlay-hotkey-help || return 1
  wtype -k Escape
  step overlay-hotkey-help-close || return 1
  # model picker via slash command
  wtype '/model'
  wtype -k Return
  step overlay-model-picker || return 1
  wtype -k Escape
  wtype -M ctrl u -m ctrl
  step overlay-model-picker-close || return 1
}

journey_scrolling() {
  for key in Page_Up Page_Up Page_Down End Home End; do
    wtype -k "$key"
  done
  step scrolling-keys || return 1
}

journey_resize() {
  niri msg action set-window-width --id "$WINDOW_ID" "50%" >/dev/null 2>&1 || true
  step resize-narrow || return 1
  niri msg action set-window-width --id "$WINDOW_ID" "100%" >/dev/null 2>&1 || true
  step resize-restore || return 1
}

JOURNEY_STARTED_MS=$(( $(date +%s%N) / 1000000 ))

"$BIN" >"$LOG_FILE" 2>&1 &
APP_PID=$!

deadline=$((SECONDS + TIMEOUT_SECS))
WINDOW_JSON=""
while (( SECONDS < deadline )); do
  kill -0 "$APP_PID" 2>/dev/null || { echo "app exited at startup" >&2; tail -40 "$LOG_FILE" >&2; exit 1; }
  WINDOW_JSON="$(window_json)"
  [[ -n "$WINDOW_JSON" ]] && break
  sleep 0.1
done
[[ -n "$WINDOW_JSON" ]] || { echo "timed out waiting for window" >&2; exit 1; }
WINDOW_ID="$(jq -r '.id' <<<"$WINDOW_JSON")"

niri msg action focus-window --id "$WINDOW_ID" >/dev/null
sleep 0.5
step launch || true

for journey in "${JOURNEYS[@]}"; do
  case "$journey" in
    typing)    journey_typing    || true ;;
    overlays)  journey_overlays  || true ;;
    scrolling) journey_scrolling || true ;;
    resize)    journey_resize    || true ;;
    *) echo "unknown journey: $journey" >&2; exit 2 ;;
  esac
done

cleanup
APP_PID=""

# Smoothness oracle: inspect no-paint gaps recorded during this run.
if [[ -f "$PERF_LOG" ]]; then
  WORST_GAP="$(jq -s --argjson since "$JOURNEY_STARTED_MS" '
    [ .[] | select(.timestamp_unix_ms? >= $since)
          | select(.event? == "no_paint_gap" or (.gap_ms? != null))
          | (.gap_ms? // .duration_ms? // 0) ] | max // 0
  ' "$PERF_LOG" 2>/dev/null || echo 0)"
  echo "worst no-paint gap during journey: ${WORST_GAP}ms (budget ${GAP_BUDGET_MS}ms)"
  if (( $(printf '%.0f' "$WORST_GAP") > GAP_BUDGET_MS )); then
    FAILURES+=("smoothness: no-paint gap ${WORST_GAP}ms exceeded ${GAP_BUDGET_MS}ms")
  fi
fi

if (( ${#FAILURES[@]} > 0 )); then
  echo
  echo "${#FAILURES[@]} failure(s):"
  printf '  %s\n' "${FAILURES[@]}"
  exit 1
fi
echo
echo "all journeys passed ($STEP_INDEX steps)"
