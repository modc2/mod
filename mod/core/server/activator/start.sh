#!/usr/bin/env bash
# Launch the module activator (scale-to-zero front proxy) under pm2.
#
# Knobs (env): ACTIVATOR_PORT (9000), IDLE_SECONDS (10), SWEEP_SECONDS (60),
# WAKE_TIMEOUT_MS (30000), ACTIVATOR_PIN (csv of modules to never auto-stop),
# ACTIVATOR_MANAGED (csv of modules the sweep may stop, or * = every pm2 module —
# safe because slept ports are held by the activator and wake on connect),
# ACTIVATOR_EXEMPT (csv never slept: infra/front door/UDP), BUSY_CPU (3),
# MIN_FREE_MB (1500, OOM-guard headroom floor), MAX_RUNNING (0 = uncapped).
#
# IMPORTANT: the idle-sweep stops idle modules whether or not the gateway routes
# through this proxy. Only start it AFTER the Caddyfile sends the scale-to-zero
# modules' traffic to :ACTIVATOR_PORT — otherwise a direct hit to a slept module
# has no path to wake it. See README.md for the cutover.
set -euo pipefail
cd "$(dirname "$0")"

: "${ACTIVATOR_PORT:=9000}"
: "${IDLE_SECONDS:=10}"
: "${SWEEP_SECONDS:=60}"
# Pin the front-door/infra modules so they never sleep.
: "${ACTIVATOR_PIN:=web,claude}"
# Every pm2 module (2026-10-16: "pause anything not used in the last minute, hot
# load it when I use it"). A slept module's ports stay bound by the activator
# (socket activation), so gateway, portal AND peer calls to its bare port all
# wake it — no :9000 route cutover needed any more.
: "${ACTIVATOR_MANAGED:=*}"
: "${ACTIVATOR_EXEMPT:=app,api,server,web,claude,build,passport,dns,mcp,agent,updates}"
: "${MIN_FREE_MB:=1500}"
: "${MAX_RUNNING:=0}"
export ACTIVATOR_PORT IDLE_SECONDS SWEEP_SECONDS ACTIVATOR_PIN ACTIVATOR_MANAGED ACTIVATOR_EXEMPT MIN_FREE_MB MAX_RUNNING

pm2 delete activator >/dev/null 2>&1 || true
pm2 start activator.js --name activator --update-env
echo "activator started on :${ACTIVATOR_PORT} (idle=${IDLE_SECONDS}s, pinned=${ACTIVATOR_PIN}, managed=${ACTIVATOR_MANAGED})"
