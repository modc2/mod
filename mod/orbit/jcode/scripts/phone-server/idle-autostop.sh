#!/bin/bash
# Power off after 30 min with no clients AND no active agent work.
STATE_FILE=/var/tmp/idle-since
CONNS=$(ss -Htn state established "( sport = :7643 or sport = :22 )" | wc -l)
# jcode server doing outbound work (e.g. streaming from Bedrock)?
JPID=$(pgrep -f "jcode.*serve" | head -1)
BUSY=0
if [ -n "$JPID" ]; then
  OUT443=$(ss -Htnp state established "( dport = :443 )" 2>/dev/null | grep -c "pid=$JPID") || true
  [ "$OUT443" -gt 0 ] && BUSY=1
fi
if [ "$CONNS" -gt 0 ] || [ "$BUSY" -gt 0 ]; then
  rm -f "$STATE_FILE"
  exit 0
fi
NOW=$(date +%s)
if [ ! -f "$STATE_FILE" ]; then
  echo "$NOW" > "$STATE_FILE"
  exit 0
fi
IDLE_SECS=$((NOW - $(cat "$STATE_FILE")))
if [ "$IDLE_SECS" -ge 1800 ]; then
  logger "idle-autostop: idle ${IDLE_SECS}s, powering off"
  systemctl poweroff
fi
