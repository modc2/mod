#!/usr/bin/env bash
# Launch the module activator (scale-to-zero front proxy) under pm2.
#
# Knobs (env): ACTIVATOR_PORT (9000), IDLE_SECONDS (10), SWEEP_SECONDS (60),
# WAKE_TIMEOUT_MS (30000), ACTIVATOR_PIN (csv of modules to never auto-stop),
# ACTIVATOR_MANAGED (csv of modules the sweep may stop — ONLY the ones routed
# through :9000; empty = all, which sleeps the whole fleet including build),
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
# The sweep only ever stops these (they're the ones Caddy / the portal route via
# :9000, so they have a wake path). Add a module here only after cutting its
# route over — see README "Going live".
: "${ACTIVATOR_MANAGED:=bridge,dev,hyperliquid,polymarket,store,venice,openplay,modcity,openevent,neartensor,whitepaper}"
: "${MIN_FREE_MB:=1500}"
: "${MAX_RUNNING:=0}"
export ACTIVATOR_PORT IDLE_SECONDS SWEEP_SECONDS ACTIVATOR_PIN ACTIVATOR_MANAGED MIN_FREE_MB MAX_RUNNING

pm2 delete activator >/dev/null 2>&1 || true
pm2 start activator.js --name activator --update-env
echo "activator started on :${ACTIVATOR_PORT} (idle=${IDLE_SECONDS}s, pinned=${ACTIVATOR_PIN}, managed=${ACTIVATOR_MANAGED})"
