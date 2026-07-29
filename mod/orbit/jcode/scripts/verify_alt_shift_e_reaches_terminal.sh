#!/usr/bin/env bash
# Compositor-level verification that Alt+Shift+E is NO LONGER grabbed by niri
# and is delivered to the focused terminal.
#
# This is the other half of the proof: scripts/repro_expand_edit_shortcut.py
# proves jcode's handler reacts to the real Alt+Shift+E bytes; this script
# proves the compositor actually lets those bytes reach the terminal.
#
# Method:
#   1. Launch a dedicated kitty window running a key-capture program.
#   2. Deterministically focus ONLY that window via niri (so injected keys
#      cannot land anywhere else).
#   3. Inject a real Alt+Shift+E with wtype (virtual-keyboard Wayland protocol,
#      which passes through niri's keybind layer exactly like a physical press).
#   4. Read back what the terminal received.
#
# PASS  = the capture window receives an 'e' key event (CSI-u 101;... or ESC e/E)
#         => niri forwarded the key; jcode would see it.
# FAIL  = capture window receives nothing AND semantic-organize.sh fired
#         => niri still grabbing the key.
set -uo pipefail

SOCK=""
for s in /tmp/kitty.sock-*; do
  if kitty @ --to "unix:$s" ls >/dev/null 2>&1; then SOCK="unix:$s"; break; fi
done
if [[ -z "$SOCK" ]]; then echo "❌ no working kitty control socket"; exit 3; fi

OUT=$(mktemp /tmp/altshifte_cap.XXXXXX)
CAP=$(mktemp /tmp/altshifte_capscript.XXXXXX.py)
cat > "$CAP" <<'PY'
import sys, os, termios, tty, select, time
out = sys.argv[1]
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
sys.stdout.write("\x1b[>3u"); sys.stdout.flush()  # enable kitty keyboard protocol
tty.setraw(fd)
buf = b""
deadline = time.time() + 15.0
try:
    while time.time() < deadline:
        r,_,_ = select.select([fd],[],[],0.2)
        if r:
            d = os.read(fd, 1024)
            if not d: break
            buf += d
            deadline = time.time() + 1.0
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write("\x1b[<u"); sys.stdout.flush()
open(out,"w").write(repr(buf)+"\n"+buf.hex()+"\n")
PY

echo "→ launching capture window…"
kitty @ --to "$SOCK" launch --type=os-window --os-window-class altshifte-cap \
    --title altshifte-capture python3 "$CAP" "$OUT" >/dev/null 2>&1
sleep 1.8

# Find the capture window id in niri (match on app_id, which is reliable) and
# focus only it.
WID=$(niri msg -j windows 2>/dev/null | python3 -c '
import sys,json
ws=json.load(sys.stdin)
for w in ws:
    if (w.get("app_id") or "")=="altshifte-cap":
        print(w["id"]); break
')
if [[ -z "${WID:-}" ]]; then echo "❌ could not find capture window in niri"; rm -f "$OUT" "$CAP"; exit 3; fi
echo "→ focusing capture window id=$WID"
niri msg action focus-window --id "$WID" >/dev/null 2>&1
sleep 0.4

FOCUSED=$(niri msg -j windows 2>/dev/null | python3 -c '
import sys,json
ws=json.load(sys.stdin)
print(next((w["id"] for w in ws if w.get("is_focused")), "none"))
')
if [[ "$FOCUSED" != "$WID" ]]; then
  echo "❌ capture window is not focused (focused=$FOCUSED); aborting to avoid stray input"
  kitty @ --to "$SOCK" close-window --match "title:altshifte-capture" >/dev/null 2>&1
  rm -f "$OUT" "$CAP"; exit 3
fi

echo "→ injecting real Alt+Shift+E via wtype (through the compositor)…"
# wtype: press Alt down, Shift down, e, release in reverse.
wtype -M alt -M shift -k e -m shift -m alt
sleep 1.5

# Confirm focus did not jump (semantic-organize would not steal focus, but be safe).
FOCUSED2=$(niri msg -j windows 2>/dev/null | python3 -c '
import sys,json
ws=json.load(sys.stdin)
print(next((w["id"] for w in ws if w.get("is_focused")), "none"))
')

CAPTURED=$(cat "$OUT" 2>/dev/null | head -1)
echo
echo "captured bytes: $CAPTURED"
echo "focus before=$WID after=$FOCUSED2"

kitty @ --to "$SOCK" close-window --match "title:altshifte-capture" >/dev/null 2>&1
rm -f "$CAP"

# Decide. The 'e' codepoint is 101 (0x65). Accept kitty CSI-u (101;...) or ESC e/E.
HEX=$(cat "$OUT" 2>/dev/null | tail -1)
rm -f "$OUT"
echo
if echo "$HEX" | grep -qiE "1b5b313031|1b65|1b45"; then
  echo "✅ PASS: niri forwarded Alt+Shift+E to the focused terminal."
  echo "   (terminal received an 'e' key event; jcode will see it.)"
  exit 0
else
  echo "❌ FAIL: focused terminal received no 'e' key event."
  echo "   niri may still be grabbing Alt+Shift+E."
  exit 2
fi
