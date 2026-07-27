#!/bin/bash
# One-shot cutover: claude -> dev (fork). Launched detached by the migration job.
# Waits for all running claude jobs to drain (so no user work is killed), then:
#   1. rewrites /etc/caddy/mod_site.caddy: /claude -> redirect to /dev (302 app,
#      308 api so method+path survive), root redirect / -> /dev; validate+reload
#   2. pm2 stop+delete claude-api claude-app; pm2 save
#   3. mv orbit/claude -> orbit/archive/claude
# Log: /tmp/claude-cutover.log
set -u
LOG=/tmp/claude-cutover.log
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:$PATH"

log "cutover started; waiting for claude jobs to drain"
# Drain: no `claude --print` children of claude-api for 2 consecutive checks.
empty=0
while [ "$empty" -lt 2 ]; do
  sleep 20
  CAPID=$(pm2 pid claude-api 2>/dev/null | tr -d '[:space:]')
  if [ -z "$CAPID" ] || [ "$CAPID" = "0" ]; then
    log "claude-api not running; proceeding"
    break
  fi
  if pgrep -P "$CAPID" -f 'claude --print' > /dev/null 2>&1; then
    empty=0
  else
    empty=$((empty + 1))
  fi
done
log "jobs drained"

# --- 1. Caddy: /claude -> /dev redirects ---------------------------------
python3 - <<'PYEOF' >> "$LOG" 2>&1
src = "/etc/caddy/mod_site.caddy"
import shutil, subprocess, time
bak = src + ".bak.cutover"
shutil.copy(src, bak)
t = open(src).read()
r1_old = """    @claude_api path /api/claude /api/claude/*
    handle @claude_api {
        uri strip_prefix /api/claude
        reverse_proxy localhost:8820
    }"""
r1_new = """    @claude_api path /api/claude /api/claude/*
    handle @claude_api {
        uri strip_prefix /api/claude
        redir * /api/dev{uri} 308
    }"""
r2_old = """    @claude_app path /claude /claude/*
    handle @claude_app {
        reverse_proxy localhost:8823
    }"""
r2_new = """    @claude_app path /claude /claude/*
    handle @claude_app {
        uri strip_prefix /claude
        redir * /dev{uri} 302
    }"""
r3_old = "redir @root /claude 302"
r3_new = "redir @root /dev 302"
counts = (t.count(r1_old), t.count(r2_old), t.count(r3_old))
print("caddy match counts (api, app, root):", counts)
t = t.replace(r1_old, r1_new).replace(r2_old, r2_new).replace(r3_old, r3_new)
open(src, "w").write(t)
v = subprocess.run(["caddy", "validate", "--config", "/etc/caddy/Caddyfile"],
                   capture_output=True, text=True)
if v.returncode != 0:
    print("caddy validate FAILED, restoring backup:", v.stderr[-500:])
    shutil.copy(bak, src)
else:
    r = subprocess.run(["systemctl", "reload", "caddy"], capture_output=True, text=True)
    print("caddy reloaded rc=", r.returncode, r.stderr[-200:])
PYEOF

# --- 2. stop claude ------------------------------------------------------
pm2 stop claude-api claude-app >> "$LOG" 2>&1
pm2 delete claude-api claude-app >> "$LOG" 2>&1
pm2 save --force >> "$LOG" 2>&1
log "claude pm2 processes stopped and deleted"

# --- 3. archive the module directory -------------------------------------
if [ -d /root/mod/mod/orbit/claude ]; then
  mv /root/mod/mod/orbit/claude /root/mod/mod/orbit/archive/claude \
    && log "orbit/claude moved to orbit/archive/claude" \
    || log "ERROR: failed to move orbit/claude"
fi
log "cutover COMPLETE — console now lives at /dev"
