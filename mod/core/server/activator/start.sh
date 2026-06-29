#!/usr/bin/env bash
# Launch the module activator (scale-to-zero front proxy) under pm2.
#
# Knobs (env): ACTIVATOR_PORT (9000), IDLE_MINUTES (10), SWEEP_SECONDS (60),
# WAKE_TIMEOUT_MS (30000), ACTIVATOR_PIN (csv of modules to never auto-stop).
#
# IMPORTANT: the idle-sweep stops idle modules whether or not the gateway routes
# through this proxy. Only start it AFTER the Caddyfile sends the scale-to-zero
# modules' traffic to :ACTIVATOR_PORT — otherwise a direct hit to a slept module
# has no path to wake it. See README.md for the cutover.
set -euo pipefail
cd "$(dirname "$0")"

: "${ACTIVATOR_PORT:=9000}"
: "${IDLE_MINUTES:=10}"
: "${SWEEP_SECONDS:=60}"
# Pin the front-door/infra modules so they never sleep.
: "${ACTIVATOR_PIN:=web,claude}"
export ACTIVATOR_PORT IDLE_MINUTES SWEEP_SECONDS ACTIVATOR_PIN

pm2 delete activator >/dev/null 2>&1 || true
pm2 start activator.js --name activator --update-env
echo "activator started on :${ACTIVATOR_PORT} (idle=${IDLE_MINUTES}m, pinned=${ACTIVATOR_PIN})"
